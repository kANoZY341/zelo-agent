"""Offline acceptance of explicit, scoped and replay-safe public memory admission.

The candidate fixture runs real Core/Mind/Research/Browser coordination with only
HTTP, native rendering and model responses replaced. These tests retain the real
prepared-receipt and evidence validators, SQLite database, lock and MemoryService.
"""

import hashlib
import os
import stat
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.research_evidence as evidence
import scripts.record_public_research_memory as command
from app.database import Database
from app.memory import MemoryService
from app.models import ConversationTurn, Goal, MemoryRecord, Task
from app.research_evidence import EvidenceError, canonical
from app.schemas import MemoryCreate, MemoryProvenance, MemoryScope
from tests.test_public_research_workflow import public_fixture, run_candidate

SUMMARY = (
    "Reviewed synthetic evidence compares RSS, web, and video retrieval. "
    "Preserve the Core project scope when retaining these findings."
)
QUERY = "synthetic evidence"


@pytest.fixture
def reviewed_case(tmp_path, monkeypatch) -> Iterator[SimpleNamespace]:
    with public_fixture(tmp_path) as case:
        case.report_path = run_candidate(case)
        case.report_sha256 = hashlib.sha256(case.report_path.read_bytes()).hexdigest()
        # Only public fixture identities change. Prepared-receipt validation,
        # persisted task validation and typed workspace reads remain real.
        monkeypatch.setattr(command, "load_source_manifest", lambda: case.manifest)
        monkeypatch.setattr(evidence, "load_source_manifest", lambda: case.manifest)
        yield case


def record(case, **overrides):
    arguments = {
        "report_path": case.report_path,
        "expected_report_sha256": case.report_sha256,
        "reviewed_summary": SUMMARY,
        "recall_query": QUERY,
        **overrides,
    }
    return command.record_reviewed_memory(**arguments)


def memory_ids(case):
    database = Database(case.settings.database_path)
    try:
        with database.read() as session:
            return list(session.scalars(select(MemoryRecord.id)))
    finally:
        database.close()


def replace_report(case, **changes):
    case.report.update(changes)
    raw = canonical(case.report) + b"\n"
    case.report_path.chmod(0o600)
    case.report_path.write_bytes(raw)
    case.report_path.chmod(0o400)
    case.report_sha256 = hashlib.sha256(raw).hexdigest()


def test_reviewed_summary_uses_normal_memory_and_explicit_project_recall(reviewed_case):
    case = reviewed_case
    raw_report = case.report_path.read_bytes()
    database = Database(case.settings.database_path)
    try:
        control = MemoryService(database).create(
            MemoryCreate(
                scope=MemoryScope(kind="project", id=case.report["exclusion_project_id"]),
                kind="experience",
                content=SUMMARY,
                provenance=MemoryProvenance(kind="operator", note="Owned exclusion fixture"),
            )
        )
    finally:
        database.close()

    receipt = record(case)
    database = Database(case.settings.database_path)
    try:
        admitted = MemoryService(database).get(receipt["memory_id"])
    finally:
        database.close()

    assert admitted.content == SUMMARY
    assert admitted.content != case.report["research_result"]["answer"]
    assert admitted.kind == "experience"
    assert admitted.scope == MemoryScope(kind="project", id=case.report["project_id"])
    assert admitted.provenance.kind == "task"
    assert admitted.provenance.ref == case.report["research_task_id"]
    assert admitted.provenance.note.startswith("Operator-reviewed public research; ")
    assert case.report_sha256 in admitted.provenance.note
    assert receipt["reviewed_summary_sha256"] == hashlib.sha256(SUMMARY.encode()).hexdigest()
    assert receipt["recall"]["retrieval"] == "bounded_lexical_v1"
    assert [item["id"] for item in receipt["recall"]["items"]] == [admitted.id]
    assert [item["id"] for item in receipt["exclusion_recall"]["items"]] == [control.id]
    assert case.report_path.read_bytes() == raw_report
    assert set(memory_ids(case)) == {admitted.id, control.id}
    receipt_path = case.run_root / "memory-receipt.json"
    assert receipt_path.stat().st_mode & 0o777 == 0o400
    assert receipt_path.read_bytes() == canonical(receipt) + b"\n"


@pytest.mark.parametrize("crash_point", ["before_receipt", "after_receipt"])
def test_crash_after_memory_commit_recovers_without_duplicate(
    reviewed_case, monkeypatch, crash_point
):
    case = reviewed_case
    publish = command._publish_receipt

    def crash(path, receipt):
        if crash_point == "after_receipt":
            publish(path, receipt)
        raise RuntimeError("owned_fixture_crash")

    monkeypatch.setattr(command, "_publish_receipt", crash)
    with pytest.raises(RuntimeError, match="owned_fixture_crash"):
        record(case)
    committed_ids = memory_ids(case)
    assert len(committed_ids) == 1
    assert (case.run_root / "memory-receipt.json").exists() == (crash_point == "after_receipt")
    monkeypatch.setattr(command, "_publish_receipt", publish)

    recovered = record(case)
    receipt_bytes = (case.run_root / "memory-receipt.json").read_bytes()
    assert recovered["memory_id"] == committed_ids[0]
    assert record(case) == recovered
    assert memory_ids(case) == committed_ids
    assert (case.run_root / "memory-receipt.json").read_bytes() == receipt_bytes


def test_another_lock_holder_refuses_admission_before_memory_write(reviewed_case):
    import fcntl

    case = reviewed_case
    descriptor = os.open(case.run_root / "reviewed-memory.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EvidenceError, match="memory_admission_busy"):
            record(case)
    finally:
        os.close(descriptor)
    assert memory_ids(case) == []
    assert not (case.run_root / "memory-receipt.json").exists()


def test_replay_with_changed_summary_refuses_without_second_memory(reviewed_case):
    case = reviewed_case
    admitted = record(case)
    original = (case.run_root / "memory-receipt.json").read_bytes()
    with pytest.raises(EvidenceError, match="memory_admission_conflict"):
        record(case, reviewed_summary=SUMMARY + " A different reviewed conclusion.")
    assert memory_ids(case) == [admitted["memory_id"]]
    assert (case.run_root / "memory-receipt.json").read_bytes() == original


def test_ambiguous_existing_admissions_are_not_replayed_or_extended(reviewed_case):
    case = reviewed_case
    admitted = record(case)
    database = Database(case.settings.database_path)
    try:
        memory = MemoryService(database)
        existing = memory.get(admitted["memory_id"])
        memory.create(
            MemoryCreate(
                scope=existing.scope,
                kind=existing.kind,
                content=existing.content,
                provenance=existing.provenance,
            )
        )
    finally:
        database.close()
    before = memory_ids(case)
    with pytest.raises(EvidenceError, match="memory_admission_duplicate"):
        record(case)
    assert len(before) == 2
    assert memory_ids(case) == before


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"expected_report_sha256": "0" * 64}, "memory_report_sha256_mismatch"),
        ({"reviewed_summary": "x" * 8193}, "memory_review_input_invalid"),
        ({"reviewed_summary": " "}, "memory_review_input_invalid"),
        ({"recall_query": " "}, "memory_review_input_invalid"),
    ],
)
def test_unreviewed_hash_or_unbounded_input_has_no_memory_effect(reviewed_case, overrides, reason):
    with pytest.raises(EvidenceError, match=reason):
        record(reviewed_case, **overrides)
    assert memory_ids(reviewed_case) == []
    assert not (reviewed_case.run_root / "memory-receipt.json").exists()


@pytest.mark.parametrize("link_kind", ["database", "sqlite_sidecar", "receipt"])
def test_symlink_paths_cannot_redirect_database_or_receipt_writes(
    reviewed_case, tmp_path, link_kind
):
    case = reviewed_case
    outside = tmp_path / "unrelated-data"
    outside.write_bytes(b"preserve unrelated file")
    outside.chmod(0o644)
    backup = None
    if link_kind == "database":
        target = case.settings.database_path
        backup = target.with_name("fixture-database-original")
        target.rename(backup)
    elif link_kind == "sqlite_sidecar":
        target = case.settings.database_path.with_name("zelo.sqlite3-wal")
        assert not target.exists()
    else:
        target = case.run_root / "memory-receipt.json"
    target.symlink_to(outside)
    try:
        with pytest.raises(EvidenceError, match="memory_path_not_canonical"):
            record(case)
        assert outside.read_bytes() == b"preserve unrelated file"
        assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    finally:
        target.unlink()
        if backup is not None:
            backup.rename(target)
    assert memory_ids(case) == []


def test_a_report_outside_its_fixed_run_path_cannot_select_a_database(reviewed_case, tmp_path):
    case = reviewed_case
    outside = tmp_path / "report.json"
    outside.write_bytes(case.report_path.read_bytes())
    outside.chmod(0o400)
    with pytest.raises(EvidenceError, match="memory_report_path_invalid"):
        record(case, report_path=outside)
    assert memory_ids(case) == []
    assert not (tmp_path / "core").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "refused"},
        {"runtime_cleanup_confirmed": False},
        {"setup_cleanup_errors": ["owned_fixture_cleanup_failed"]},
        {"model_digest_before": "0" * 64},
        {"model_digest_after": "0" * 64},
    ],
)
def test_an_explicitly_reviewed_but_ineligible_report_is_refused(reviewed_case, changes):
    replace_report(reviewed_case, **changes)
    with pytest.raises(EvidenceError, match="memory_report_not_eligible"):
        record(reviewed_case)
    assert memory_ids(reviewed_case) == []


@pytest.mark.parametrize("settings_change", ["missing", "changed_context"])
def test_model_request_settings_are_verified_before_database_open(
    reviewed_case, monkeypatch, settings_change
):
    case = reviewed_case
    if settings_change == "missing":
        case.report.pop("model_request_settings")
    else:
        case.report["model_request_settings"] = {
            **evidence.MODEL_REQUEST_SETTINGS,
            "num_ctx": 8192,
        }
    replace_report(case)

    def database_must_not_open(_path):
        pytest.fail("ineligible model request settings reached Database construction")

    monkeypatch.setattr(command, "Database", database_must_not_open)
    with pytest.raises(EvidenceError, match="memory_report_not_eligible"):
        record(case)
    assert memory_ids(case) == []
    assert not (case.run_root / "memory-receipt.json").exists()


@pytest.mark.parametrize("binding", ["goal_project", "conversation_task"])
def test_persisted_project_and_conversation_must_bind_the_report(reviewed_case, binding):
    case = reviewed_case
    database = Database(case.settings.database_path)
    try:
        with database.transaction() as session:
            if binding == "goal_project":
                goal = session.get(Goal, case.report["goal_id"])
                goal.project_id = case.report["exclusion_project_id"]
            else:
                turn = session.get(ConversationTurn, case.report["mind_turn_id"])
                turn.core_task_id = case.report["evidence"][0]["browser_task_id"]
    finally:
        database.close()
    with pytest.raises(EvidenceError, match="memory_project_binding_invalid"):
        record(case)
    assert memory_ids(case) == []


def test_report_cannot_self_assert_a_different_operator_identity(reviewed_case):
    case = reviewed_case
    replace_report(case, browser_operator_identity="0" * 64)
    with pytest.raises(EvidenceError, match="memory_browser_contract_mismatch"):
        record(case)
    assert memory_ids(case) == []


@pytest.mark.parametrize("drift", ["research_result", "staged_source"])
def test_actual_candidate_evidence_is_rechecked_before_memory_admission(reviewed_case, drift):
    case = reviewed_case
    if drift == "research_result":
        database = Database(case.settings.database_path)
        try:
            with database.transaction() as session:
                task = session.get(Task, case.report["research_task_id"])
                task.result = {**task.result, "answer": "Changed after review [S1] [S2] [S3]."}
        finally:
            database.close()
        reason = "public_research_task_mismatch"
    else:
        artifact = case.workspace / case.report["evidence"][0]["artifact"]
        original = artifact.read_bytes()
        artifact.chmod(0o600)
        artifact.write_bytes(original + b"Changed after review.\n")
        artifact.chmod(0o400)
        reason = "public_source_artifact_changed"
    with pytest.raises(EvidenceError, match=reason):
        record(case)
    assert memory_ids(case) == []


def test_failed_recall_retains_one_memory_for_a_corrected_query_retry(reviewed_case):
    case = reviewed_case
    with pytest.raises(EvidenceError, match="memory_scope_recall_failed"):
        record(case, recall_query="unrelated pineapple")
    created = memory_ids(case)
    assert len(created) == 1
    assert not (case.run_root / "memory-receipt.json").exists()
    receipt = record(case)
    assert receipt["memory_id"] == created[0]
    assert memory_ids(case) == created


def test_restored_database_cannot_replay_a_receipt_as_new_memory(reviewed_case):
    case = reviewed_case
    database_path = case.settings.database_path
    # Simulate restoring an operator's checkpointed fixture backup, without
    # disabling Core's append-only memory trigger or deleting a memory row.
    assert not database_path.with_name("zelo.sqlite3-wal").exists()
    before_admission = database_path.read_bytes()
    record(case)
    assert not database_path.with_name("zelo.sqlite3-wal").exists()
    database_path.write_bytes(before_admission)
    with pytest.raises(EvidenceError, match="memory_receipt_without_memory"):
        record(case)
    assert memory_ids(case) == []
