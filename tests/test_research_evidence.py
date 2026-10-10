"""Evidence identities at the persisted Browser/Research acceptance boundary."""

import copy
import hashlib
import json

import pytest

from app.database import Database
from app.models import Task
from app.research_evidence import EvidenceError, digest, validate_candidate
from app.tasks import TaskService
from scripts.qualify_public_research import research_tools
from tests.test_public_research_workflow import public_fixture, run_candidate


def test_raw_artifact_and_complete_typed_tool_hashes_are_distinct(tmp_path):
    with public_fixture(tmp_path) as case:
        run_candidate(case)
        tools = research_tools(case.settings)
        for source, receipt in zip(case.manifest.sources, case.report["evidence"], strict=True):
            artifact = (case.workspace / receipt["artifact"]).read_bytes()
            output = tools.execute("read_workspace_file", {"path": receipt["artifact"]})
            encoded = json.dumps(
                output, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode("ascii")
            assert receipt["artifact_sha256"] == hashlib.sha256(artifact).hexdigest()
            assert receipt["tool_result_sha256"] == hashlib.sha256(encoded).hexdigest()
            assert (
                len({source.sha256, receipt["artifact_sha256"], receipt["tool_result_sha256"]}) == 3
            )


@pytest.mark.parametrize(
    "change,code",
    [
        ("truncated", "public_research_citation_incomplete"),
        ("list_tool", "public_research_citation_incomplete"),
        ("raw_file_hash", "public_research_citation_mismatch"),
    ],
)
def test_replay_rejects_incomplete_citations_even_with_consistent_report_result_hash(
    tmp_path, change, code
):
    with public_fixture(tmp_path) as case:
        run_candidate(case)
        changed = copy.deepcopy(case.report["research_result"])
        if change == "truncated":
            changed["sources"][0]["truncated"] = True
        elif change == "list_tool":
            changed["sources"][0]["tool"] = "list_workspace_files"
        else:
            changed["sources"][0]["sha256"] = case.report["evidence"][0]["artifact_sha256"]
        # Simulate a stored result that Core's broad schema accepts. Recomputing the
        # report hash must not make truncated/list/raw-file evidence acceptable.
        db = Database(case.settings.database_path)
        try:
            with db.transaction() as session:
                session.get(Task, case.report["research_task_id"]).result = changed
            report = {
                **case.report,
                "research_result": changed,
                "research_result_sha256": digest(changed),
            }
            with pytest.raises(EvidenceError, match=code):
                validate_candidate(
                    report,
                    TaskService(db, case.settings),
                    research_tools(case.settings),
                    case.manifest,
                )
        finally:
            db.close()


def test_staged_artifact_drift_is_rejected_against_persisted_browser_result(tmp_path):
    with public_fixture(tmp_path) as case:
        run_candidate(case)
        artifact = case.workspace / case.report["evidence"][0]["artifact"]
        artifact.chmod(0o600)
        artifact.write_text("Altered synthetic evidence.\n")
        artifact.chmod(0o400)
        db = Database(case.settings.database_path)
        try:
            with pytest.raises(EvidenceError, match="public_source_artifact_changed"):
                validate_candidate(
                    case.report,
                    TaskService(db, case.settings),
                    research_tools(case.settings),
                    case.manifest,
                )
        finally:
            db.close()
