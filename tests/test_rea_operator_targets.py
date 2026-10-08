"""Durable inert operator REA choices: append-only, no execution or authenticity."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorPrincipal
from app.models import (
    ReaOperatorTargetRevocationRecord,
    ReaOperatorTargetSelectionRecord,
    Task,
)
from app.rea_operator_targets import (
    _CATALOG,
    _MANIFEST,
    _SOURCE,
    ReaOperatorReviewClaim,
    ReaOperatorTargetStore,
)
from tests.conftest import AUTH

OPERATOR = OperatorPrincipal()
SNAPSHOT_ID = "target-" + "a" * 32


def review_dict(*, content: str = "b" * 64) -> dict:
    """Fake but hash-coherent submitted preview, explicitly NOT target verification."""

    value = {
        "schema_version": "zelo-rea-target-preflight-v1",
        "snapshot_id": SNAPSHOT_ID,
        "receipt_sha256": "c" * 64,
        "content_sha256": content,
        "file_count": 2,
        "total_bytes": 42,
        "source_commit": _SOURCE,
        "source_manifest_sha256": _MANIFEST,
        "tool_catalog_sha256": _CATALOG,
        "tool_name": "analyze_javascript_application",
        "tool_qualification": "fixed_fixture_only_not_target_qualified",
        "manual_operator_opt_in": True,
        "execution_enabled": False,
        "core_approval": False,
        "runtime_qualified": False,
        "worker_registered": False,
        "tool_grants": [],
    }
    value["review_fingerprint"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return value


def claim(*, content: str = "b" * 64) -> ReaOperatorReviewClaim:
    return ReaOperatorReviewClaim.model_validate_json(
        json.dumps(review_dict(content=content)), strict=True
    )


def store(database) -> ReaOperatorTargetStore:
    return ReaOperatorTargetStore(database)


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_select_records_only_unverified_metadata_not_analyzer_authority(database):
    record, created = store(database).select(claim(), principal=OPERATOR)
    assert created
    assert record.snapshot_id == SNAPSHOT_ID
    assert record.currently_selected
    assert record.origin == "operator_asserted_unverified"
    assert record.target_verified_by_core is False
    assert record.core_approval is False
    assert record.execution_authority is False
    assert record.worker_authority is False
    assert record.tool_grants == ()
    with database.read() as session:
        assert count(session, ReaOperatorTargetSelectionRecord) == 1
        assert count(session, ReaOperatorTargetRevocationRecord) == 0
        assert count(session, Task) == 0


def test_exact_retries_and_conflicting_review_refused(database):
    first, created = store(database).select(claim(), principal=OPERATOR)
    assert created
    again, created = store(database).select(claim(), principal=OPERATOR)
    assert not created
    assert again == first
    with pytest.raises(ValueError, match="rea_operator_target_selection_conflict"):
        store(database).select(claim(content="d" * 64), principal=OPERATOR)
    assert store(database).get(SNAPSHOT_ID, principal=OPERATOR) == first


def test_revoke_is_irreversible_even_if_review_changes(database):
    store(database).select(claim(), principal=OPERATOR)
    assert store(database).revoke(SNAPSHOT_ID, principal=OPERATOR)
    assert not store(database).revoke(SNAPSHOT_ID, principal=OPERATOR)
    record = store(database).get(SNAPSHOT_ID, principal=OPERATOR)
    assert record is not None and not record.currently_selected
    assert record.origin == "operator_asserted_unverified"
    assert record.core_approval is False and record.execution_authority is False
    for newer in (claim(), claim(content="d" * 64)):
        with pytest.raises(ValueError, match="rea_operator_target_revoked"):
            store(database).select(newer, principal=OPERATOR)
    with database.read() as session:
        assert count(session, ReaOperatorTargetSelectionRecord) == 1
        assert count(session, ReaOperatorTargetRevocationRecord) == 1


def test_missing_and_invalid_id_refuse_without_creating_selection(database):
    assert store(database).get(SNAPSHOT_ID, principal=OPERATOR) is None
    with pytest.raises(LookupError, match="rea_operator_target_not_selected"):
        store(database).revoke(SNAPSHOT_ID, principal=OPERATOR)
    for malformed in (None, "", "target-" + "A" * 32, "/etc/passwd", SNAPSHOT_ID + "0"):
        with pytest.raises(ValueError):
            store(database).get(malformed, principal=OPERATOR)
    with database.read() as session:
        assert count(session, ReaOperatorTargetSelectionRecord) == 0


@pytest.mark.parametrize("principal", [None, "ahmad", {}, {"subject": "ahmad"}])
def test_operator_required_for_read_select_and_revoke(database, principal):
    with pytest.raises(PermissionError, match="rea_operator_required"):
        store(database).select(claim(), principal=principal)
    with pytest.raises(PermissionError, match="rea_operator_required"):
        store(database).get(SNAPSHOT_ID, principal=principal)
    with pytest.raises(PermissionError, match="rea_operator_required"):
        store(database).revoke(SNAPSHOT_ID, principal=principal)


@pytest.mark.parametrize(
    "invalid",
    [
        {"execution_enabled": True},
        {"core_approval": True},
        {"runtime_qualified": True},
        {"worker_registered": True},
        {"tool_grants": ["analyze_javascript_application"]},
        {"tool_name": "execute_native_binary"},
        {"manual_operator_opt_in": False},
        {"source_commit": "f" * 40},
        {"snapshot_id": "/etc/passwd"},
        {"review_fingerprint": "f" * 64},
    ],
)
def test_forged_grants_or_bad_fingerprint_are_rejected(invalid):
    values = {**review_dict(), **invalid}
    with pytest.raises(ValidationError):
        ReaOperatorReviewClaim.model_validate_json(json.dumps(values), strict=True)


def test_two_parallel_identical_selections_are_idempotent(database):
    barrier = Barrier(2)
    target_store = store(database)

    def run():
        barrier.wait(timeout=10)
        return target_store.select(claim(), principal=OPERATOR)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = [item.result(timeout=30) for item in (pool.submit(run), pool.submit(run))]
    assert sorted([a[1], b[1]]) == [False, True]
    assert a[0] == b[0]


def test_two_parallel_distinct_selections_refuse_conflict(database):
    barrier = Barrier(2)
    target_store = store(database)

    def run(content):
        barrier.wait(timeout=10)
        try:
            target_store.select(claim(content=content), principal=OPERATOR)
            return "created"
        except ValueError as error:
            assert str(error) == "rea_operator_target_selection_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = [
            item.result(timeout=30)
            for item in (pool.submit(run, "b" * 64), pool.submit(run, "d" * 64))
        ]
    assert sorted([a, b]) == ["conflict", "created"]


def test_parallel_revoke_is_exact_once(database):
    store(database).select(claim(), principal=OPERATOR)
    barrier = Barrier(2)
    target_store = store(database)

    def run():
        barrier.wait(timeout=10)
        return target_store.revoke(SNAPSHOT_ID, principal=OPERATOR)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = [item.result(timeout=30) for item in (pool.submit(run), pool.submit(run))]
    assert sorted([a, b]) == [False, True]


def test_db_tables_are_append_only_including_replace_and_ignore(database):
    store(database).select(claim(), principal=OPERATOR)
    store(database).revoke(SNAPSHOT_ID, principal=OPERATOR)
    for statement in (
        "UPDATE rea_operator_target_selections SET content_sha256=:digest WHERE snapshot_id=:id",
        "DELETE FROM rea_operator_target_selections WHERE snapshot_id=:id",
        "UPDATE rea_operator_target_revocations SET operator_id='other' WHERE snapshot_id=:id",
        "DELETE FROM rea_operator_target_revocations WHERE snapshot_id=:id",
        "INSERT OR REPLACE INTO rea_operator_target_selections "
        "(snapshot_id, receipt_sha256, content_sha256, review_fingerprint, file_count, "
        "total_bytes,operator_id,selected_at,execution_authority,worker_authority, "
        "approval_authority) "
        "SELECT snapshot_id,receipt_sha256,content_sha256,review_fingerprint,file_count,"
        "total_bytes,operator_id,selected_at,execution_authority,worker_authority, "
        "approval_authority "
        "FROM rea_operator_target_selections",
    ):
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(text(statement), {"id": SNAPSHOT_ID, "digest": "d" * 64})
    with database.read() as session:
        assert count(session, ReaOperatorTargetSelectionRecord) == 1
        assert count(session, ReaOperatorTargetRevocationRecord) == 1
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []

    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text(
                    "INSERT OR IGNORE INTO rea_operator_target_revocations "
                    "(snapshot_id,operator_id,revoked_at) "
                    "SELECT snapshot_id,operator_id,revoked_at "
                    "FROM rea_operator_target_revocations"
                )
            )


def test_core_api_requires_operator_and_never_provides_a_execution_route(client, database):
    url = "/integration-candidates/rea/operator-targets"
    posted = {"review": review_dict(), "confirm_inert_selection": True}
    assert (
        client.post(url, json=posted, headers={"Authorization": "Bearer invalid"}).status_code
        == 401
    )
    assert client.post(url, json={**posted, "confirm_inert_selection": False}).status_code == 422
    selected = client.post(url, headers=AUTH, json=posted)
    assert selected.status_code == 200, selected.text
    assert selected.json()["currently_selected"] is True
    assert selected.json()["target_verified_by_core"] is False
    assert selected.json()["execution_authority"] is False
    assert selected.json()["worker_authority"] is False
    assert selected.json()["tool_grants"] == []
    assert client.post(url, headers=AUTH, json=posted).status_code == 200
    assert (
        client.post(
            url,
            headers=AUTH,
            json={"review": review_dict(content="d" * 64), "confirm_inert_selection": True},
        ).status_code
        == 409
    )
    read = client.get(f"{url}/{SNAPSHOT_ID}", headers=AUTH)
    assert read.status_code == 200
    assert read.json()["currently_selected"] is True
    assert (
        client.get(f"{url}/{SNAPSHOT_ID}", headers={"Authorization": "Bearer invalid"}).status_code
        == 401
    )
    revoke = client.post(f"{url}/{SNAPSHOT_ID}/revoke", headers=AUTH)
    assert revoke.status_code == 200
    assert revoke.json() == {"revoked": True}
    assert client.post(f"{url}/{SNAPSHOT_ID}/revoke", headers=AUTH).json() == {"revoked": False}
    assert client.get(f"{url}/{SNAPSHOT_ID}", headers=AUTH).json()["currently_selected"] is False
    assert client.post(url, headers=AUTH, json=posted).status_code == 409
    with database.read() as session:
        assert count(session, Task) == 0


def test_real_offline_preview_is_schema_compatible_but_still_not_core_proof(
    tmp_path, monkeypatch, database
):
    """Trusted contract compatibility never elevates local snapshot to execution."""

    from qualification import rea_target_admission as admission
    from qualification.rea_target_preflight import ReaPreflightInput, prepare_rea_target_preflight

    inbox = tmp_path / "inbox"
    staging = tmp_path / "staging"
    inbox.mkdir(mode=0o700)
    staging.mkdir(mode=0o700)
    monkeypatch.setattr(admission, "INBOX", inbox)
    monkeypatch.setattr(admission, "STAGING", staging)
    folder = inbox / "sample"
    folder.mkdir(mode=0o700)
    (folder / "index.js").write_text("export const answer = 42;\n", encoding="utf-8")
    receipt = admission.admit_target("sample", operator_opt_in=True)
    preview = prepare_rea_target_preflight(
        ReaPreflightInput(
            snapshot_id=receipt["snapshot_id"], receipt_sha256=receipt["receipt_sha256"]
        ),
        operator_opt_in=True,
    )
    claim_from_real_local_preview = ReaOperatorReviewClaim.model_validate_json(
        json.dumps(preview.model_dump(mode="json")), strict=True
    )
    stored, created = store(database).select(claim_from_real_local_preview, principal=OPERATOR)
    assert created
    assert stored.snapshot_id == receipt["snapshot_id"]
    assert stored.review_fingerprint == preview.review_fingerprint
    assert not stored.target_verified_by_core
    assert not stored.execution_authority and not stored.worker_authority
