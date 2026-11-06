"""Skill publication evidence must not expose an alternate SQLite rowid identity."""

import sqlite3

import pytest
from alembic import command
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app.auth import OperatorPrincipal
from app.database import Database, migration_config
from app.models import Base
from app.skill_publication_claim import SkillPublicationClaimService
from app.skill_publication_intent import prepare_skill_publication_intent
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_intent import accepted_case

_SKILL_LEDGER_TABLES = (
    "skill_publication_claims",
    "skill_publication_operator_decisions",
    "skill_publication_dispatch_fences",
    "skill_publication_merge_attempts",
    "skill_publication_merge_receipts",
    "skill_publication_merge_failures",
    "skill_publication_post_attempt_reconciliations",
    "skill_publication_promotion_receipt_links",
)
_PREVIOUS = "0050_channel_ledger_replace_guards"
_HEAD = "0053_whatsapp_reply_intents"


def _upgrade(database: Database, target: str) -> None:
    with database.engine.connect() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, target)


@pytest.mark.parametrize("table", _SKILL_LEDGER_TABLES)
def test_skill_publication_metadata_disables_rowid(table: str) -> None:
    assert Base.metadata.tables[table].dialect_options["sqlite"]["with_rowid"] is False


@pytest.mark.parametrize("table", _SKILL_LEDGER_TABLES)
def test_skill_publication_history_has_no_replaceable_rowid(database, table: str) -> None:
    with database.engine.connect() as connection:
        create_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:table"),
            {"table": table},
        ).scalar_one()
        assert "WITHOUT ROWID" in create_sql.upper()
        with pytest.raises(OperationalError, match="no such column: rowid"):
            connection.execute(text(f"SELECT rowid FROM {table}")).all()


def test_link_fingerprint_replace_guard_isolated(database) -> None:
    with database.read() as session:
        definition = session.scalar(
            text(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='skill_publication_promotion_receipt_links_no_duplicate_insert'"
            )
        )
    assert definition is not None
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.execute(
            "CREATE TABLE skill_publication_promotion_receipt_links ("
            "id TEXT PRIMARY KEY, claim_id TEXT UNIQUE, proposal_id TEXT UNIQUE, "
            "receipt_id TEXT UNIQUE, link_fingerprint TEXT UNIQUE, "
            "agent_instance_id TEXT, source_skill_id TEXT, procedure_id TEXT, "
            "proposed_version INTEGER, "
            "UNIQUE(agent_instance_id,source_skill_id,procedure_id,proposed_version)"
            ") WITHOUT ROWID"
        )
        connection.execute(definition)
        statement = (
            "INSERT OR REPLACE INTO skill_publication_promotion_receipt_links "
            "(id,claim_id,proposal_id,receipt_id,link_fingerprint,"
            "agent_instance_id,source_skill_id,procedure_id,proposed_version) "
            "VALUES (?,?,?,?,?,?,?,?,?)"
        )
        first = (
            "id-a",
            "claim-a",
            "proposal-a",
            "receipt-a",
            "digest-shared",
            "agent-a",
            "source-a",
            "procedure-a",
            1,
        )
        second = (
            "id-b",
            "claim-b",
            "proposal-b",
            "receipt-b",
            "digest-shared",
            "agent-b",
            "source-b",
            "procedure-b",
            2,
        )
        connection.execute(statement, first)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement, second)
        assert connection.execute(
            "SELECT id,claim_id FROM skill_publication_promotion_receipt_links"
        ).fetchall() == [("id-a", "claim-a")]
    finally:
        connection.close()


def test_populated_claim_survives_upgrade_with_schema_and_references(settings) -> None:
    database = Database(settings.database_path)
    try:
        _upgrade(database, _PREVIOUS)
        proposal_case = learning_store_tests.proposal_case.__wrapped__(database, settings)
        store, reviewed, _agent, _revision, _reference = accepted_case(database, proposal_case)
        intent = prepare_skill_publication_intent(
            store, reviewed.proposal.proposal_id, OperatorPrincipal()
        )
        service = SkillPublicationClaimService(store)
        claim, created = service.reserve(
            intent.proposal_id, intent.intent_fingerprint, OperatorPrincipal()
        )
        assert created
        with database.read() as session:
            before_objects = {
                table: set(
                    session.scalars(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE tbl_name=:table AND type IN ('index','trigger') "
                            "AND sql IS NOT NULL"
                        ),
                        {"table": table},
                    )
                )
                for table in _SKILL_LEDGER_TABLES
            }
        database.migrate()
        database.check_schema()
        with database.read() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == _HEAD
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
            assert session.scalar(text("SELECT count(*) FROM skill_publication_claims")) == 1
            for table, names in before_objects.items():
                after = set(
                    session.scalars(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE tbl_name=:table AND type IN ('index','trigger') "
                            "AND sql IS NOT NULL"
                        ),
                        {"table": table},
                    )
                )
                assert after == names
        assert service.get(claim.id, OperatorPrincipal()) == claim
        with database.engine.connect() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="Destructive downgrade is disabled"):
                command.downgrade(config, "-1")
        database.check_schema()
        assert service.get(claim.id, OperatorPrincipal()) == claim
    finally:
        database.close()


def test_skill_ledger_rebuild_rolls_back_on_mid_ddl_failure(settings) -> None:
    database = Database(settings.database_path)
    try:
        _upgrade(database, _PREVIOUS)
        with database.engine.connect() as connection:

            @event.listens_for(connection, "before_cursor_execute")
            def fail_midway(_conn, _cursor, statement, _params, _context, _many):
                if (
                    "DROP TABLE" in statement.upper()
                    and "SKILL_PUBLICATION_MERGE_ATTEMPTS" in statement.upper()
                ):
                    raise RuntimeError("injected_0051_failure")

            config = migration_config()
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="injected_0051_failure"):
                command.upgrade(config, "head")
        with database.read() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == _PREVIOUS
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM sqlite_master "
                        "WHERE name GLOB '_v015_skill_publication_*'"
                    )
                )
                == 0
            )
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM sqlite_master "
                        "WHERE name='skill_learning_promotion_links_no_publication_conflict'"
                    )
                )
                == 1
            )
            assert session.execute(text("SELECT rowid FROM skill_publication_claims")).all() == []
        database.migrate()
        database.check_schema()
    finally:
        database.close()
