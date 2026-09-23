"""v0.11 bounded self-inspection and proposal contract tests."""

import copy

import pytest
from pydantic import ValidationError

from app.self_improvement import (
    ImprovementEvaluation,
    ImprovementProposal,
    ImprovementProposalDraft,
    RepositoryInspection,
    RepositoryInspector,
    SelfImprovementError,
)
from tests.developer_helpers import fixture_commit, git


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")

    (repo / "app").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs" / "reviews").mkdir(parents=True)
    (repo / "README.md").write_text("# Zelo fixture\n", encoding="utf-8")
    (repo / "app" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_core.py").write_text(
        "def test_value():\n    assert 1 == 1\n", encoding="utf-8"
    )
    (repo / "docs" / "reviews" / "private-review.md").write_text(
        "review evidence that should not enter model content\n", encoding="utf-8"
    )
    sha = fixture_commit(repo)
    return repo, sha


def test_repository_inspector_builds_deterministic_bounded_snapshot(tmp_path):
    repo, sha = make_repo(tmp_path)
    inspector = RepositoryInspector(repo)

    first = inspector.inspect()
    second = inspector.inspect()

    assert first.base_sha == sha
    assert first.branch == "main"
    assert first.clean is True
    assert first.fingerprint == second.fingerprint
    assert first.tracked_paths == tuple(sorted(first.tracked_paths))
    assert "docs/reviews/private-review.md" in first.tracked_paths

    contents = {item.path: item.text for item in first.files}
    assert contents["README.md"] == "# Zelo fixture\n"
    assert contents["app/core.py"] == "VALUE = 1\n"
    assert "docs/reviews/private-review.md" not in contents
    assert first.total_content_bytes == sum(len(item.text.encode("utf-8")) for item in first.files)


def test_repository_inspector_refuses_dirty_checkout(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "UNTRACKED.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(SelfImprovementError, match="inspection_repository_dirty"):
        RepositoryInspector(repo).inspect()


def test_repository_inspector_refuses_tracked_forbidden_paths(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "secrets").mkdir()
    (repo / "secrets" / "credential.txt").write_text("nope\n", encoding="utf-8")
    fixture_commit(repo)

    with pytest.raises(SelfImprovementError, match="inspection_path_invalid"):
        RepositoryInspector(repo).inspect()


def test_repository_inspector_bounds_content_without_losing_inventory(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "app" / "large.py").write_text("x" * 5000, encoding="utf-8")
    fixture_commit(repo)

    snapshot = RepositoryInspector(
        repo,
        max_content_files=2,
        max_file_bytes=2048,
        max_total_bytes=4096,
    ).inspect()

    assert "app/large.py" in snapshot.tracked_paths
    assert "app/large.py" not in {item.path for item in snapshot.files}
    assert len(snapshot.files) <= 2
    assert snapshot.content_truncated is True


def test_inspection_fingerprint_rejects_tampering(tmp_path):
    repo, _ = make_repo(tmp_path)
    snapshot = RepositoryInspector(repo).inspect()
    payload = snapshot.model_dump(mode="json")
    payload["branch"] = "tampered"

    with pytest.raises(ValidationError, match="inspection_fingerprint_mismatch"):
        RepositoryInspection.model_validate(payload)


def proposal_draft(**changes):
    values = {
        "title": "Reduce duplicated routing checks",
        "problem": "The same routing invariant is checked in more than one place.",
        "evidence": ("app/core.py",),
        "expected_benefit": "Keep one authoritative routing check.",
        "implementation_instruction": "Refactor the duplicated check without widening authority.",
        "acceptance_criteria": ("Existing tests remain green.", "Routing behavior is unchanged."),
        "risk": "low",
        "required_capabilities": ("zelo_core_development",),
        "scope_paths": ("app/core.py", "tests/test_core.py"),
    }
    values.update(changes)
    return ImprovementProposalDraft(**values)


def test_proposal_freezes_exact_inspection_identity(tmp_path):
    repo, sha = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()

    proposal = ImprovementProposal.freeze(
        proposal_draft(),
        inspection,
        planner_config_version="self-improvement-planner-v1",
    )

    assert proposal.base_sha == sha
    assert proposal.inspection_fingerprint == inspection.fingerprint
    assert proposal.draft.required_capabilities == ("zelo_core_development",)

    tampered = proposal.model_dump(mode="json")
    tampered["draft"]["problem"] = "different"

    with pytest.raises(ValidationError, match="proposal_fingerprint_mismatch"):
        ImprovementProposal.model_validate(tampered)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (
            "required_capabilities",
            ("zelo_core_development", "python_development"),
            "required_capabilities_not_canonical",
        ),
        (
            "scope_paths",
            ("tests/test_core.py", "app/core.py"),
            "scope_paths_not_canonical",
        ),
        ("scope_paths", ("../outside.py",), "repository_path_invalid"),
    ],
)
def test_proposal_rejects_noncanonical_or_escaping_authority_fields(field, value, error):
    values = proposal_draft().model_dump(mode="python")
    values[field] = value

    with pytest.raises((ValidationError, ValueError), match=error):
        ImprovementProposalDraft.model_validate(values)


def test_accepted_evaluation_requires_validation_and_clean_review():
    base = {
        "proposal_fingerprint": "a" * 64,
        "developer_task_id": "task",
        "developer_attempt_id": "attempt",
        "validation_passed": True,
        "reviewer_high_or_critical": False,
        "status": "accepted_candidate",
    }

    accepted = ImprovementEvaluation(**base)
    assert accepted.status == "accepted_candidate"

    for change in (
        {"validation_passed": False},
        {"reviewer_high_or_critical": True},
    ):
        invalid = copy.deepcopy(base)
        invalid.update(change)
        with pytest.raises(ValidationError, match="improvement_evaluation_invalid"):
            ImprovementEvaluation(**invalid)
