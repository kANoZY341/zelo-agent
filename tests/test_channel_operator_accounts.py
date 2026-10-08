"""Inert operator-selected WABA metadata and permanent account-key refusal."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth import OperatorPrincipal
from app.channel_operator_accounts import ChannelOperatorAccountStore
from app.models import (
    ChannelOperatorAccountRevocationRecord,
    ChannelOperatorAccountSelectionRecord,
    ConversationTurn,
)

ACCOUNT = "a" * 64
SECRET_ID = "b" * 64
OPERATOR = OperatorPrincipal()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def store(database):
    return ChannelOperatorAccountStore(database)


def select_account(database):
    record, created = store(database).select(
        account_key=ACCOUNT, app_secret_identity=SECRET_ID, principal=OPERATOR
    )
    assert created
    return record


def test_selection_is_opaque_inert_and_idempotent(database):
    first = select_account(database)
    again, created = store(database).select(
        account_key=ACCOUNT, app_secret_identity=SECRET_ID, principal=OPERATOR
    )
    assert not created
    assert again == first
    assert first.currently_selected
    assert not first.provider_authenticated
    assert not first.mind_admission_authority
    assert not first.delivery_authority
    with database.read() as session:
        assert count(session, ChannelOperatorAccountSelectionRecord) == 1
        assert count(session, ConversationTurn) == 0


def test_secret_fingerprint_change_is_refused_without_new_record(database):
    first = select_account(database)
    with pytest.raises(ValueError, match="channel_account_selection_conflict"):
        store(database).select(
            account_key=ACCOUNT, app_secret_identity="c" * 64, principal=OPERATOR
        )
    assert store(database).get(ACCOUNT, principal=OPERATOR) == first


def test_revocation_is_final_and_exact_retry_is_idempotent(database):
    first = select_account(database)
    assert store(database).revoke(account_key=ACCOUNT, principal=OPERATOR)
    assert not store(database).revoke(account_key=ACCOUNT, principal=OPERATOR)
    later = store(database).get(ACCOUNT, principal=OPERATOR)
    assert later is not None
    assert later.account_key == first.account_key
    assert not later.currently_selected
    assert not later.provider_authenticated
    with pytest.raises(ValueError, match="channel_account_selection_revoked"):
        store(database).select(
            account_key=ACCOUNT, app_secret_identity=SECRET_ID, principal=OPERATOR
        )
    with pytest.raises(ValueError, match="channel_account_selection_revoked"):
        store(database).select(
            account_key=ACCOUNT, app_secret_identity="c" * 64, principal=OPERATOR
        )
    with database.read() as session:
        assert count(session, ChannelOperatorAccountSelectionRecord) == 1
        assert count(session, ChannelOperatorAccountRevocationRecord) == 1
        assert count(session, ConversationTurn) == 0


def test_revocation_cannot_create_account_implicitly(database):
    with pytest.raises(LookupError, match="channel_account_not_selected"):
        store(database).revoke(account_key=ACCOUNT, principal=OPERATOR)
    assert store(database).get(ACCOUNT, principal=OPERATOR) is None


@pytest.mark.parametrize(
    "bad",
    [None, "", "A" * 64, "x" * 64, "a" * 63, "a" * 65, "0" * 64, b"a" * 64, "a" * 63 + "\n"],
)
def test_malformed_account_and_secret_identity_refused(database, bad):
    with pytest.raises(ValueError, match="channel_account_selection_identity_invalid"):
        store(database).select(account_key=bad, app_secret_identity=SECRET_ID, principal=OPERATOR)
    with pytest.raises(ValueError, match="channel_account_selection_identity_invalid"):
        store(database).select(account_key=ACCOUNT, app_secret_identity=bad, principal=OPERATOR)
    with pytest.raises(ValueError, match="channel_account_selection_identity_invalid"):
        store(database).get(bad, principal=OPERATOR)
    with pytest.raises(ValueError, match="channel_account_selection_identity_invalid"):
        store(database).revoke(account_key=bad, principal=OPERATOR)


@pytest.mark.parametrize("principal", [None, "ahmad", "owner", {"subject": "ahmad"}])
def test_non_operator_cannot_read_or_select_or_revoke(database, principal):
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store(database).get(ACCOUNT, principal=principal)
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store(database).select(
            account_key=ACCOUNT, app_secret_identity=SECRET_ID, principal=principal
        )
    with pytest.raises(PermissionError, match="channel_operator_required"):
        store(database).revoke(account_key=ACCOUNT, principal=principal)


def test_two_simultaneous_equal_selections_are_one_record(database):
    barrier = Barrier(2)
    registry = store(database)

    def run():
        barrier.wait(timeout=10)
        return registry.select(
            account_key=ACCOUNT, app_secret_identity=SECRET_ID, principal=OPERATOR
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(item[1] for item in results) == [False, True]
    assert results[0][0] == results[1][0]


def test_two_simultaneous_different_secret_selections_fail_closed(database):
    barrier = Barrier(2)
    registry = store(database)

    def run(secret_identity):
        barrier.wait(timeout=10)
        try:
            record, created = registry.select(
                account_key=ACCOUNT, app_secret_identity=secret_identity, principal=OPERATOR
            )
            return ("created" if created else "duplicate", record.app_secret_identity)
        except ValueError as error:
            return ("conflict", str(error))

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, SECRET_ID)
        b = pool.submit(run, "c" * 64)
        results = [a.result(timeout=30), b.result(timeout=30)]
    assert sorted(item[0] for item in results) == ["conflict", "created"]
    assert [r[1] for r in results if r[0] == "conflict"] == ["channel_account_selection_conflict"]
    with database.read() as session:
        assert count(session, ChannelOperatorAccountSelectionRecord) == 1


def test_simultaneous_revocation_is_once_and_stays_refused(database):
    select_account(database)
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=10)
        return store(database).revoke(account_key=ACCOUNT, principal=OPERATOR)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        outcomes = [future.result(timeout=30) for future in futures]
    assert sorted(outcomes) == [False, True]
    assert not store(database).get(ACCOUNT, principal=OPERATOR).currently_selected


def test_database_refuses_mutation_of_selection_and_revocation(database):
    select_account(database)
    registry = store(database)
    assert registry.revoke(account_key=ACCOUNT, principal=OPERATOR)
    for statement in (
        "UPDATE channel_operator_account_selections "
        "SET app_secret_identity=:replacement WHERE account_key=:key",
        "DELETE FROM channel_operator_account_selections WHERE account_key=:key",
        "UPDATE channel_operator_account_revocations "
        "SET operator_id='other' WHERE account_key=:key",
        "DELETE FROM channel_operator_account_revocations WHERE account_key=:key",
    ):
        with pytest.raises(IntegrityError, match="append-only"):
            with database.transaction() as session:
                session.execute(text(statement), {"key": ACCOUNT, "replacement": "c" * 64})


def test_database_constraints_refuse_forged_authority(database):
    statements = (
        "INSERT INTO channel_operator_account_selections (account_key,app_secret_identity,"
        "operator_id,selected_at,provider_authenticated,mind_admission_authority,"
        "delivery_authority) VALUES (:key,:secret,'ahmad',CURRENT_TIMESTAMP,1,0,0)",
        "INSERT INTO channel_operator_account_selections (account_key,app_secret_identity,"
        "operator_id,selected_at,provider_authenticated,mind_admission_authority,"
        "delivery_authority) VALUES (:key,:secret,'ahmad',CURRENT_TIMESTAMP,0,1,0)",
        "INSERT INTO channel_operator_account_selections (account_key,app_secret_identity,"
        "operator_id,selected_at,provider_authenticated,mind_admission_authority,"
        "delivery_authority) VALUES (:key,:secret,'ahmad',CURRENT_TIMESTAMP,0,0,1)",
        "INSERT INTO channel_operator_account_selections (account_key,app_secret_identity,"
        "operator_id,selected_at,provider_authenticated,mind_admission_authority,"
        "delivery_authority) VALUES (:key,:secret,'unauthorized',CURRENT_TIMESTAMP,0,0,0)",
    )
    for statement in statements:
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(text(statement), {"key": ACCOUNT, "secret": SECRET_ID})
    assert store(database).get(ACCOUNT, principal=OPERATOR) is None


def test_raw_unowned_revocation_fails_foreign_key(database):
    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT INTO channel_operator_account_revocations"
                    " (account_key,operator_id,revoked_at) VALUES (:key,'ahmad',CURRENT_TIMESTAMP)"
                ),
                {"key": ACCOUNT},
            )


def test_sensitive_provider_information_never_written_by_service(database):
    select_account(database)
    registry = store(database)
    assert registry.revoke(account_key=ACCOUNT, principal=OPERATOR)
    with database.read() as session:
        selections = session.scalars(select(ChannelOperatorAccountSelectionRecord)).all()
        refusals = session.scalars(select(ChannelOperatorAccountRevocationRecord)).all()
        assert len(selections) == len(refusals) == 1
        assert selections[0].account_key == ACCOUNT
        assert selections[0].app_secret_identity == SECRET_ID
        assert not selections[0].provider_authenticated
        assert not selections[0].mind_admission_authority
        assert not selections[0].delivery_authority
        assert count(session, ConversationTurn) == 0


@pytest.mark.parametrize("insert_mode", ["INSERT OR REPLACE", "REPLACE", "INSERT OR IGNORE"])
def test_raw_duplicate_selection_insert_modes_cannot_rewrite_history(database, insert_mode):
    """SQLite REPLACE must not bypass the historical selection's DELETE guard."""

    first = select_account(database)
    with database.read() as session:
        recursion = session.execute(text("PRAGMA recursive_triggers")).scalar_one()
        assert recursion == 0

    statement = (
        f"{insert_mode} INTO channel_operator_account_selections "
        "(account_key,app_secret_identity,operator_id,selected_at,"
        "provider_authenticated,mind_admission_authority,delivery_authority) "
        "VALUES (:key,:secret,'ahmad','2030-01-01 00:00:00',0,0,0)"
    )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(text(statement), {"key": ACCOUNT, "secret": "c" * 64})

    assert store(database).get(ACCOUNT, principal=OPERATOR) == first
    with database.read() as session:
        assert count(session, ChannelOperatorAccountSelectionRecord) == 1


@pytest.mark.parametrize("insert_mode", ["INSERT OR REPLACE", "REPLACE", "INSERT OR IGNORE"])
def test_raw_duplicate_revocation_insert_modes_cannot_rewrite_history(database, insert_mode):
    """Once revoked, raw SQL may not replace the irrevocable record timestamp."""

    select_account(database)
    assert store(database).revoke(account_key=ACCOUNT, principal=OPERATOR)
    with database.read() as session:
        original = session.execute(
            text(
                "SELECT revoked_at FROM channel_operator_account_revocations WHERE account_key=:key"
            ),
            {"key": ACCOUNT},
        ).scalar_one()

    statement = (
        f"{insert_mode} INTO channel_operator_account_revocations "
        "(account_key,operator_id,revoked_at) "
        "VALUES (:key,'ahmad','2030-01-01 00:00:00')"
    )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(text(statement), {"key": ACCOUNT})

    with database.read() as session:
        assert count(session, ChannelOperatorAccountRevocationRecord) == 1
        persisted = session.execute(
            text(
                "SELECT revoked_at FROM channel_operator_account_revocations WHERE account_key=:key"
            ),
            {"key": ACCOUNT},
        ).scalar_one()
    assert persisted == original
    assert not store(database).get(ACCOUNT, principal=OPERATOR).currently_selected


@pytest.mark.parametrize(
    "table",
    ["channel_operator_account_selections", "channel_operator_account_revocations"],
)
def test_append_only_account_tables_have_no_replaceable_hidden_rowid(database, table):
    """WITHOUT ROWID removes the alternate raw SQL collision route."""

    with database.read() as session:
        create_sql = session.execute(
            text("SELECT sql FROM sqlite_master WHERE name=:name AND type='table'"),
            {"name": table},
        ).scalar_one()
    assert "WITHOUT ROWID" in create_sql.upper()
    with database.read() as session:
        with pytest.raises(OperationalError, match="rowid"):
            session.execute(text(f"SELECT rowid FROM {table}"))
