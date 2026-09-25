"""Trusted self-improvement candidate evaluation tests."""

import hashlib
from copy import deepcopy

import pytest

from app.schemas import DeveloperRunResult
from app.self_improvement import ImprovementProposal, RepositoryInspector, SelfImprovementError
from app.self_improvement_evaluation import evaluate_improvement_candidate
from tests.test_self_improvement import make_repo, proposal_draft

NOW = "2026-01-01T00:00:00Z"


def frozen_proposal(tmp_path):
    repo, _ = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    draft = proposal_draft(
        risk="medium",
        evidence=("app/core.py",),
        scope_paths=("app/core.py",),
    )
    return ImprovementProposal.freeze(
        draft,
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version="planner-test",
    )


def developer_result(proposal, **changes) -> DeveloperRunResult:
    payload = {
        "worker": "fake",
        "worker_version": "fake-1",
        "worker_config_version": "1",
        "base_sha": proposal.base_sha,
        "worktree": "developer/attempt/repo",
        "worker_exit_code": 0,
        "worker_summary": "done",
        "usage": {},
        "nonfatal_error_items": 0,
        "changed_files": ["app/core.py"],
        "ignored_generated_files": [],
        "git_status": " M app/core.py",
        "diff_stat": "app/core.py | 1 +",
        "diff": "diff",
        "candidate_content_sha256": "c" * 64,
        "head": proposal.base_sha,
        "head_changed": False,
        "validation": [],
        "validation_passed": True,
        "independent_review": {
            "worker_id": "reviewer",
            "implementation": "fake",
            "worker_version": "fake-1",
            "candidate_content_sha256": "c" * 64,
            "report": {"summary": "Looks good.", "findings": []},
            "usage": {},
            "event_count": 1,
            "started_at": NOW,
            "completed_at": NOW,
            "duration_seconds": 0.0,
        },
        "started_at": NOW,
        "worker_started_at": NOW,
        "worker_completed_at": NOW,
        "validation_completed_at": NOW,
        "completed_at": NOW,
        "duration_seconds": 0.0,
        "error": None,
        "protocol_diagnostic": None,
        "boundary_error": None,
        "original_error": None,
        "cancelled": False,
    }
    payload.update(changes)
    return DeveloperRunResult.model_validate(payload)


def evaluate(proposal, result):
    return evaluate_improvement_candidate(
        proposal,
        developer_task_id="developer-task",
        developer_attempt_id="developer-attempt",
        worker_id="developer_worker",
        worker_implementation="fake",
        review_context_bound=True,
        result=result,
    )


def test_clean_validated_independently_reviewed_candidate_is_accepted(tmp_path):
    proposal = frozen_proposal(tmp_path)

    evaluation = evaluate(proposal, developer_result(proposal))

    assert evaluation.status == "accepted_candidate"
    assert evaluation.worker_id == "developer_worker"
    assert evaluation.worker_implementation == "fake"
    assert evaluation.changed_files == ("app/core.py",)
    assert evaluation.final_diff_sha256 == hashlib.sha256(b"diff").hexdigest()
    assert evaluation.candidate_content_sha256 == "c" * 64
    assert evaluation.validation_passed is True
    assert evaluation.scope_valid is True
    assert evaluation.execution_clean is True
    assert evaluation.independent_review_present is True
    assert evaluation.acceptance_criteria_proven is True
    assert evaluation.reviewer_finding_count == 0
    assert evaluation.reviewer_high_or_critical is False
    assert evaluation.changed_file_count == 1


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"validation_passed": False}, "rejected_candidate"),
        ({"changed_files": ["tests/outside.py"]}, "rejected_candidate"),
        ({"worker_exit_code": 1}, "rejected_candidate"),
        ({"boundary_error": "boundary"}, "rejected_candidate"),
        ({"head_changed": True}, "rejected_candidate"),
        ({"cancelled": True}, "rejected_candidate"),
        ({"changed_files": []}, "rejected_candidate"),
        ({"candidate_content_sha256": None}, "rejected_candidate"),
        (
            {
                "independent_review": {
                    **developer_result(proposal).model_dump(mode="json")["independent_review"],
                    "candidate_content_sha256": "d" * 64,
                }
            },
            "rejected_candidate",
        ),
        ({"independent_review": None}, "inconclusive"),
    ],
)
def test_evaluator_fails_closed_on_untrusted_or_unproven_candidate(tmp_path, changes, expected):
    proposal = frozen_proposal(tmp_path)

    evaluation = evaluate(proposal, developer_result(proposal, **changes))

    assert evaluation.status == expected


def test_high_or_critical_reviewer_finding_rejects_candidate(tmp_path):
    proposal = frozen_proposal(tmp_path)
    review = deepcopy(developer_result(proposal).model_dump(mode="json")["independent_review"])
    review["report"]["findings"] = [
        {
            "severity": "high",
            "message": "Authority boundary regression.",
            "path": "app/core.py",
        }
    ]

    evaluation = evaluate(proposal, developer_result(proposal, independent_review=review))

    assert evaluation.status == "rejected_candidate"
    assert evaluation.reviewer_high_or_critical is True


def test_evaluator_rejects_candidate_bound_to_different_base(tmp_path):
    proposal = frozen_proposal(tmp_path)
    result = developer_result(proposal, base_sha="f" * 40)

    with pytest.raises(SelfImprovementError, match="improvement_evaluation_binding_invalid"):
        evaluate(proposal, result)


@pytest.mark.parametrize("severity", ["low", "medium"])
def test_low_or_medium_review_finding_is_inconclusive(tmp_path, severity):
    proposal = frozen_proposal(tmp_path)
    review = deepcopy(developer_result(proposal).model_dump(mode="json")["independent_review"])
    review["report"]["findings"] = [
        {
            "severity": severity,
            "message": "Candidate still needs follow-up.",
            "path": "app/core.py",
        }
    ]

    evaluation = evaluate(proposal, developer_result(proposal, independent_review=review))

    assert evaluation.status == "inconclusive"
    assert evaluation.acceptance_criteria_proven is False
    assert evaluation.reviewer_finding_count == 1


def test_trusted_validator_caches_do_not_widen_candidate_scope(tmp_path):
    proposal = frozen_proposal(tmp_path)
    result = developer_result(
        proposal,
        changed_files=[
            ".pytest_cache/CACHEDIR.TAG",
            ".ruff_cache/CACHEDIR.TAG",
            "app/core.py",
        ],
        ignored_generated_files=[
            {"path": ".pytest_cache/CACHEDIR.TAG", "type": "file", "size": 10},
            {"path": ".ruff_cache/CACHEDIR.TAG", "type": "file", "size": 10},
        ],
    )

    evaluation = evaluate(proposal, result)

    assert evaluation.status == "accepted_candidate"
    assert evaluation.scope_valid is True
    assert evaluation.changed_files == ("app/core.py",)
    assert evaluation.changed_file_count == 1


def test_arbitrary_ignored_artifact_still_fails_scope(tmp_path):
    proposal = frozen_proposal(tmp_path)
    result = developer_result(
        proposal,
        changed_files=["app/core.py", "hidden/worker-secret.txt"],
        ignored_generated_files=[{"path": "hidden/worker-secret.txt", "type": "file", "size": 10}],
    )

    evaluation = evaluate(proposal, result)

    assert evaluation.status == "rejected_candidate"
    assert evaluation.scope_valid is False
    assert evaluation.changed_files == ("app/core.py", "hidden/worker-secret.txt")


@pytest.mark.parametrize("protected_path", ["app/auth", "app/auth/__init__.py"])
def test_evaluator_reapplies_protected_namespace_scope(tmp_path, protected_path):
    repo, _ = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()
    draft = proposal_draft(
        risk="medium",
        evidence=("app/core.py",),
        scope_paths=(protected_path,),
    )
    proposal = ImprovementProposal.freeze(
        draft,
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version="planner-test",
    )
    result = developer_result(
        proposal,
        changed_files=[protected_path],
        git_status=f"?? {protected_path}",
        diff_stat=f"{protected_path} | 1 +",
    )

    evaluation = evaluate(proposal, result)

    assert evaluation.status == "rejected_candidate"
    assert evaluation.scope_valid is False
