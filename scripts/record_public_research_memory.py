"""Admit one explicitly reviewed public report through normal Core memory.

This operator command uses only the completed run's fixed private paths. It does
not run Mind, Research, Browser, Docker, migrations, or a memory backend service.
The persistent run lock serializes admission; task/report provenance recovers a
commit that happened before the separate immutable receipt was published.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.api_contracts import ApiEndpointRegistry
from app.browser_operator import RECEIPT_LIMIT, load_receipt
from app.config import ROOT
from app.database import Database
from app.memory import MemoryService
from app.models import Campaign, Conversation, ConversationTurn, Goal, MemoryRecord, Project
from app.research_evidence import (
    BROWSER_SOURCE_MANIFEST_DIGEST,
    MODEL,
    MODEL_REQUEST_SETTINGS,
    PUBLIC_RESEARCH_BUDGETS,
    RUN_SCHEMA,
    EvidenceError,
    canonical,
    digest,
    load_source_manifest,
    private_directory,
    read_regular,
    validate_candidate,
)
from app.schemas import (
    MemoryCreate,
    MemoryProvenance,
    MemoryResponse,
    MemoryRetrievalResponse,
    MemoryRetrieveRequest,
    MemoryScope,
)
from app.tasks import TaskService
from scripts.qualify_public_research import MODEL_DIGEST, research_tools, workflow_settings

MEMORY_SCHEMA = "zelo-public-research-memory-v1"
REPORT_LIMIT = 262144
MARKER_PREFIX = "Operator-reviewed public research; "
IDENTITY_FIELDS = (
    "project_id",
    "exclusion_project_id",
    "goal_id",
    "campaign_id",
    "conversation_id",
    "mind_turn_id",
    "research_task_id",
)


@dataclass(frozen=True)
class RunPaths:
    root: Path
    database: Path
    workspace: Path
    report: Path

    @property
    def receipt(self) -> Path:
        return self.root / "memory-receipt.json"


def _private_file(path: Path, mode: int, *, allow_empty: bool = False) -> os.stat_result:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise EvidenceError("memory_path_not_canonical")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != mode
        or (not allow_empty and info.st_size == 0)
    ):
        raise EvidenceError("memory_file_invalid")
    return info


def _run_paths(report: Path) -> RunPaths:
    run_name = report.parent.name
    if (
        not re.fullmatch(r"proof-[0-9a-f]{32}", run_name)
        or report != ROOT / "runtime" / "public-research" / run_name / "report.json"
    ):
        raise EvidenceError("memory_report_path_invalid")
    run_root = report.parent
    workspace = ROOT / "workspaces" / ("public-research-" + run_name.removeprefix("proof-"))
    for directory in (run_root, run_root / "core", workspace):
        private_directory(directory)
    database = run_root / "core" / "zelo.sqlite3"
    _private_file(database, 0o600)
    # SQLite can open these alongside its database; none may redirect a write.
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _private_file(sidecar, 0o600, allow_empty=True)
    _private_file(report, 0o400)
    return RunPaths(root=run_root, database=database, workspace=workspace, report=report)


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceError("memory_json_invalid")
        value[key] = item
    return value


def _invalid_constant(_value: str) -> None:
    raise EvidenceError("memory_json_invalid")


def _read_json(path: Path, limit: int) -> dict:
    _private_file(path, 0o400)
    value = json.loads(
        read_regular(path, limit),
        object_pairs_hook=_unique,
        parse_constant=_invalid_constant,
    )
    if not isinstance(value, dict):
        raise EvidenceError("memory_json_invalid")
    return value


def _reviewed_report(paths: RunPaths, expected_sha256: str) -> dict:
    _private_file(paths.report, 0o400)
    raw = read_regular(paths.report, REPORT_LIMIT)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise EvidenceError("memory_report_sha256_mismatch")
    report = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if (
        not isinstance(report, dict)
        or report.get("schema") != RUN_SCHEMA
        or report.get("run_id") != paths.root.name.removeprefix("proof-")
        or report.get("status") != "passed"
        or report.get("model") != MODEL
        or canonical(report.get("model_request_settings")) != canonical(MODEL_REQUEST_SETTINGS)
        or canonical(report.get("budgets")) != canonical(PUBLIC_RESEARCH_BUDGETS)
        or report.get("model_digest_before") != MODEL_DIGEST
        or report.get("model_digest_after") != MODEL_DIGEST
        or report.get("mind_selected_alias") != "local_mind"
        or report.get("mind_selected_model") != MODEL
        or report.get("runtime_cleanup_confirmed") is not True
        or report.get("setup_cleanup_errors") != []
    ):
        raise EvidenceError("memory_report_not_eligible")
    for key in IDENTITY_FIELDS:
        value = report.get(key)
        try:
            valid = isinstance(value, str) and str(UUID(value)) == value
        except ValueError:
            valid = False
        if not valid:
            raise EvidenceError("memory_report_identity_invalid")
    if report["project_id"] == report["exclusion_project_id"]:
        raise EvidenceError("memory_project_binding_invalid")
    return report


def _verify_prepared_run(paths: RunPaths, report: dict) -> None:
    """Read the retained operator receipt; this performs no Docker or network work."""
    manifest = load_source_manifest()
    registry = ApiEndpointRegistry.from_entries((manifest.endpoint_id + "=" + manifest.origin,))
    prepared = paths.root / "prepared"
    bundle = prepared / "build" / "bundle"
    receipt_path = prepared / "verified-receipt.json"
    contract, receipt_identity = load_receipt(receipt_path, bundle, registry)
    receipt = json.loads(read_regular(receipt_path, RECEIPT_LIMIT))
    operator_identity = digest(
        {
            "receipt": receipt_identity,
            "bundle": str(bundle),
            "runtime": str(prepared / "runtime"),
            "admission": str(prepared / "admission"),
            "contract": contract.digest,
            "registry": registry.version,
            "adapter": "zelo-browser-core-v1",
        }
    )
    if (
        digest(receipt) != receipt_identity
        or report.get("source_bundle_manifest_digest") != BROWSER_SOURCE_MANIFEST_DIGEST
        or report.get("prepared_bundle_manifest_digest") != contract.manifest.digest
        or report.get("contract_digest") != contract.digest
        or report.get("broker_policy_digest") != contract.execution.broker_digest
        or report.get("browser_operator_identity") != operator_identity
        or report.get("image_identity") != contract.execution.image_identity
        or report.get("post_run_image_payload_digest") != receipt["image_payload_digest"]
    ):
        raise EvidenceError("memory_browser_contract_mismatch")


@contextmanager
def _run_lock(run_root: Path) -> Iterator[None]:
    path = run_root / "reviewed-memory.lock"
    private_directory(run_root)
    fd = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        0o600,
    )
    try:
        info = _private_file(path, 0o600, allow_empty=True)
        opened = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise EvidenceError("memory_lock_changed")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvidenceError("memory_admission_busy") from None
        yield
    finally:
        os.close(fd)


def _verify_database_binding(database: Database, report: dict) -> None:
    with database.read() as session:
        project = session.get(Project, report["project_id"])
        exclusion = session.get(Project, report["exclusion_project_id"])
        goal = session.get(Goal, report["goal_id"])
        campaign = session.get(Campaign, report["campaign_id"])
        conversation = session.get(Conversation, report["conversation_id"])
        turn = session.get(ConversationTurn, report["mind_turn_id"])
        if (
            project is None
            or exclusion is None
            or goal is None
            or goal.project_id != project.id
            or campaign is None
            or campaign.goal_id != goal.id
            or conversation is None
            or turn is None
            or turn.conversation_id != conversation.id
            or turn.core_task_id != report["research_task_id"]
            or turn.selected_alias != "local_mind"
            or turn.selected_model != MODEL
        ):
            raise EvidenceError("memory_project_binding_invalid")


def _recover_memory(
    database: Database, memory: MemoryService, body: MemoryCreate
) -> MemoryResponse | None:
    # Search across scopes and report digests: a changed report for this same task
    # must conflict with its earlier admission instead of creating another memory.
    with database.read() as session:
        ids = list(
            session.scalars(
                select(MemoryRecord.id)
                .where(
                    MemoryRecord.provenance_kind == "task",
                    MemoryRecord.provenance_ref == body.provenance.ref,
                    MemoryRecord.provenance_note.startswith(MARKER_PREFIX, autoescape=True),
                )
                .limit(2)
            )
        )
    if len(ids) > 1:
        raise EvidenceError("memory_admission_duplicate")
    if not ids:
        return None
    result = memory.get(ids[0])
    if (
        result.scope != body.scope
        or result.kind != body.kind
        or result.content != body.content
        or result.provenance != body.provenance
        or result.status != "active"
        or result.expires_at is not None
        or result.replaces_id is not None
        or result.replaced_by_id is not None
    ):
        raise EvidenceError("memory_admission_conflict")
    return result


def _verify_recall(
    recall: MemoryRetrievalResponse,
    exclusion: MemoryRetrievalResponse,
    report: dict,
    memory_id: str,
    query: str,
) -> None:
    if (
        recall.query != query
        or exclusion.query != query
        or memory_id not in {item.id for item in recall.items}
        or memory_id in {item.id for item in exclusion.items}
        or any(
            item.scope != MemoryScope(kind="project", id=report["project_id"])
            for item in recall.items
        )
        or any(
            item.scope != MemoryScope(kind="project", id=report["exclusion_project_id"])
            for item in exclusion.items
        )
    ):
        raise EvidenceError("memory_scope_recall_failed")


def _publish_receipt(path: Path, receipt: dict) -> None:
    """Publish atomically while holding the persistent run lock.

    As with Browser operator receipts, the same-UID operator and private ancestor
    directories are trusted. Every admission uses this lock and fixed destination.
    A process crash before rename leaves only an unused temporary file; after
    rename it leaves a complete immutable receipt.
    """
    private_directory(path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".memory-receipt-", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(canonical(receipt) + b"\n")
            output.flush()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())
        if path.exists() or path.is_symlink():
            raise EvidenceError("memory_receipt_exists")
        os.rename(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_reviewed_memory(
    report_path: Path,
    *,
    expected_report_sha256: str,
    reviewed_summary: str,
    recall_query: str,
) -> dict:
    """Validate an operator-reviewed report, admit once, and prove project recall."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_report_sha256):
        raise EvidenceError("memory_expected_sha256_invalid")
    if (
        not 1 <= len(reviewed_summary) <= 8192
        or not reviewed_summary.strip()
        or not 1 <= len(recall_query) <= 2048
        or not recall_query.strip()
    ):
        raise EvidenceError("memory_review_input_invalid")
    summary = reviewed_summary.strip()
    query = " ".join(recall_query.split())
    paths = _run_paths(Path(report_path))
    _reviewed_report(paths, expected_report_sha256)

    with _run_lock(paths.root):
        report = _reviewed_report(paths, expected_report_sha256)
        _verify_prepared_run(paths, report)
        previous = (
            _read_json(paths.receipt, REPORT_LIMIT)
            if paths.receipt.exists() or paths.receipt.is_symlink()
            else None
        )
        settings = workflow_settings(paths.root, paths.workspace)
        if (
            settings.database_path != paths.database
            or settings.workspace_root != paths.workspace
            or settings.browser_worker_enabled
        ):
            raise EvidenceError("memory_settings_path_mismatch")
        # Database creates/chmods its path, so all path checks precede construction.
        _run_paths(paths.report)
        database = Database(paths.database)
        try:
            database.check_schema()
            tools = research_tools(settings)
            tasks = TaskService(database, settings, tool_gateway=tools)
            _verify_database_binding(database, report)
            validate_candidate(report, tasks, tools)
            body = MemoryCreate(
                scope=MemoryScope(kind="project", id=report["project_id"]),
                kind="experience",
                content=summary,
                provenance=MemoryProvenance(
                    kind="task",
                    ref=report["research_task_id"],
                    note=(
                        MARKER_PREFIX + f"run_id={report['run_id']}; "
                        f"report_sha256={expected_report_sha256}"
                    ),
                ),
            )
            memory = MemoryService(database)
            admitted = _recover_memory(database, memory, body)
            if previous is not None and admitted is None:
                raise EvidenceError("memory_receipt_without_memory")
            # Refuse replacement of the reviewed bytes during validation.
            _reviewed_report(paths, expected_report_sha256)
            if admitted is None:
                admitted = memory.create(body)

            recall = memory.retrieve(
                MemoryRetrieveRequest(query=query, scopes=[body.scope], limit=20)
            )
            excluded = memory.retrieve(
                MemoryRetrieveRequest(
                    query=query,
                    scopes=[MemoryScope(kind="project", id=report["exclusion_project_id"])],
                    limit=20,
                )
            )
            _verify_recall(recall, excluded, report, admitted.id, query)
            binding = {
                "schema": MEMORY_SCHEMA,
                "status": "passed",
                "run_id": report["run_id"],
                "report_sha256": expected_report_sha256,
                "project_id": report["project_id"],
                "exclusion_project_id": report["exclusion_project_id"],
                "research_task_id": report["research_task_id"],
                "memory_id": admitted.id,
                "reviewed_summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                "reviewed_summary_characters": len(summary),
                "recall_query": query,
            }
            receipt = {
                **binding,
                "recall": recall.model_dump(mode="json"),
                "exclusion_recall": excluded.model_dump(mode="json"),
            }
            if previous is not None:
                if set(previous) != set(receipt) or any(
                    previous[key] != value for key, value in binding.items()
                ):
                    raise EvidenceError("memory_receipt_conflict")
                _verify_recall(
                    MemoryRetrievalResponse.model_validate(previous["recall"]),
                    MemoryRetrievalResponse.model_validate(previous["exclusion_recall"]),
                    report,
                    admitted.id,
                    query,
                )
                return previous
            _publish_receipt(paths.receipt, receipt)
            return receipt
        finally:
            database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record an explicitly reviewed public report in its owned project memory."
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--expected-report-sha256", required=True)
    parser.add_argument("--reviewed-summary", required=True)
    parser.add_argument("--recall-query", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = record_reviewed_memory(
            args.report,
            expected_report_sha256=args.expected_report_sha256,
            reviewed_summary=args.reviewed_summary,
            recall_query=args.recall_query,
        )
    except EvidenceError as exc:
        print(canonical({"status": "refused", "reason": exc.code}).decode("ascii"))
        return 1
    except Exception:
        # Never print model text, operator summary, SQL parameters or raw exceptions.
        print(canonical({"status": "refused", "reason": "memory_record_failed"}).decode("ascii"))
        return 1
    print(
        canonical(
            {
                "status": "passed",
                "memory_id": receipt["memory_id"],
                "receipt": str(args.report.parent / "memory-receipt.json"),
            }
        ).decode("ascii")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
