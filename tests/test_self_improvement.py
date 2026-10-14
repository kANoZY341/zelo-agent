"""v0.11 bounded self-inspection and proposal contract tests."""

import copy

import pytest
from pydantic import ValidationError

from app.developer_process import DeveloperError
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


def test_repository_inspector_keeps_full_inventory_when_git_output_exceeds_cap(tmp_path):
    repo, _ = make_repo(tmp_path)
    doc_paths = [f"docs/inventory-{number:04d}-{'d' * 80}.md" for number in range(460)]
    test_paths = [f"tests/inventory-{number:04d}-{'t' * 80}.txt" for number in range(250)]
    for relative in doc_paths + test_paths:
        target = repo / relative
        target.write_text("tracked fixture\n", encoding="utf-8")
    fixture_commit(repo)

    # One raw Git inventory would exceed the subprocess's fixed 64 KiB cap.
    assert sum(len(path.encode("utf-8")) + 1 for path in doc_paths + test_paths) > 65_536

    inspection = RepositoryInspector(repo).inspect()

    assert set(doc_paths + test_paths).issubset(inspection.tracked_paths)
    assert len(inspection.tracked_paths) == len(doc_paths) + len(test_paths) + 4


def test_repository_inspector_accepts_real_registered_linked_worktree(tmp_path):
    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "--quiet", "-b", "linked-demo", str(linked), sha)
    assert (linked / ".git").is_file() and not (linked / ".git").is_symlink()

    inspection = RepositoryInspector(linked).inspect()
    assert inspection.base_sha == sha
    assert inspection.branch == "linked-demo"
    assert inspection.clean is True
    assert "README.md" in inspection.tracked_paths
    assert {item.path: item.text for item in inspection.files}["app/core.py"] == "VALUE = 1\n"
    assert "docs/reviews/private-review.md" not in {item.path for item in inspection.files}


def test_repository_inspector_refuses_dirty_linked_worktree(tmp_path):
    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "linked-dirty"
    git(repo, "worktree", "add", "--quiet", "-b", "linked-dirty", str(linked), sha)
    (linked / "app" / "core.py").write_text("VALUE = 5\n")
    with pytest.raises(SelfImprovementError, match="inspection_repository_dirty"):
        RepositoryInspector(linked).inspect()


def test_repository_inspector_rejects_forged_gitfile_pointing_to_other_repository(tmp_path):
    repo, _ = make_repo(tmp_path)
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {repo / '.git'}\n")
    (forged / "README.md").write_text("unrelated files\n")
    with pytest.raises(SelfImprovementError, match="inspection_repository_unavailable"):
        RepositoryInspector(forged).inspect()


def test_repository_inspector_rejects_copied_linked_pointer_not_registered(tmp_path):
    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "registered"
    git(repo, "worktree", "add", "--quiet", "-b", "registered", str(linked), sha)
    fake = tmp_path / "copied"
    fake.mkdir()
    (fake / ".git").write_bytes((linked / ".git").read_bytes())
    with pytest.raises(SelfImprovementError, match="inspection_repository_unavailable"):
        RepositoryInspector(fake).inspect()


def test_repository_inspector_refuses_symlinked_git_metadata(tmp_path):
    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "registered"
    git(repo, "worktree", "add", "--quiet", "-b", "registered", str(linked), sha)
    fake = tmp_path / "git-symlink"
    fake.mkdir()
    (fake / ".git").symlink_to(linked / ".git")
    with pytest.raises(SelfImprovementError, match="inspection_repository_unavailable"):
        RepositoryInspector(fake).inspect()


def test_repository_inspector_rejects_group_writable_linked_pointer(tmp_path):
    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "unsafe-pointer"
    git(repo, "worktree", "add", "--quiet", "-b", "unsafe-pointer", str(linked), sha)
    (linked / ".git").chmod(0o664)
    with pytest.raises(SelfImprovementError, match="inspection_repository_unavailable"):
        RepositoryInspector(linked).inspect()


def test_repository_inspector_rejects_symlinked_worktree_admin_directory(tmp_path):
    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "admin-symlink"
    git(repo, "worktree", "add", "--quiet", "-b", "admin-symlink", str(linked), sha)
    admin = repo / ".git" / "worktrees" / "admin-symlink"
    detached = tmp_path / "detached-git-admin"
    admin.rename(detached)
    admin.symlink_to(detached, target_is_directory=True)
    with pytest.raises(SelfImprovementError, match="inspection_repository_unavailable"):
        RepositoryInspector(linked).inspect()


def test_repository_inspector_refuses_worktree_gitfile_swap_during_inspection(
    tmp_path, monkeypatch
):
    from app import self_improvement

    repo, sha = make_repo(tmp_path)
    linked = tmp_path / "swap-between-reads"
    git(repo, "worktree", "add", "--quiet", "-b", "swap-between-reads", str(linked), sha)
    pointer = linked / ".git"
    original_read = self_improvement._read_regular_utf8
    mutated = False

    def swap_pointer(source, relative, bound):
        nonlocal mutated
        if not mutated:
            mutated = True
            pointer.write_text("gitdir: /invalid/admin/worktrees/forged\n")
        return original_read(source, relative, bound)

    monkeypatch.setattr(self_improvement, "_read_regular_utf8", swap_pointer)
    with pytest.raises(SelfImprovementError, match="inspection_repository_changed"):
        RepositoryInspector(linked).inspect()
    assert mutated


def test_repository_inspector_ignores_untracked_private_review_evidence(tmp_path):
    repo, sha = make_repo(tmp_path)
    review = repo / "docs" / "reviews" / "untracked-adversarial-review.md"
    review.write_text("private review evidence\n", encoding="utf-8")

    snapshot = RepositoryInspector(repo).inspect()

    assert snapshot.base_sha == sha
    assert "docs/reviews/untracked-adversarial-review.md" not in snapshot.tracked_paths
    assert "docs/reviews/untracked-adversarial-review.md" not in {
        item.path for item in snapshot.files
    }


def test_repository_inspector_preserves_untracked_protected_d(tmp_path):
    repo, sha = make_repo(tmp_path)
    protected = repo / "d"
    protected.mkdir()
    (protected / "opaque.bin").write_bytes(b"\xff\x00private")

    snapshot = RepositoryInspector(repo).inspect()

    assert snapshot.base_sha == sha
    assert all(path != "d" and not path.startswith("d/") for path in snapshot.tracked_paths)
    assert all(item.path != "d" and not item.path.startswith("d/") for item in snapshot.files)


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


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env.local",
        "credentials.json",
        "credentials/service-account.json",
        "docs/credentials.json",
        "docs/nested/.env.local",
    ],
)
def test_repository_inspector_refuses_common_tracked_credential_paths(tmp_path, relative_path):
    repo, _ = make_repo(tmp_path)
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private credential\n", encoding="utf-8")
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
        "affected_components": ("routing",),
        "estimated_cost": "One bounded developer attempt; no external spend expected.",
        "rollback_approach": "Discard the isolated candidate and retain the current base commit.",
        "implementation_instruction": "Refactor the duplicated check without widening authority.",
        "acceptance_criteria": ("Existing tests remain green.", "Routing behavior is unchanged."),
        "risk": "low",
        "required_capabilities": ("zelo_core_development",),
        "scope_paths": ("app/core.py",),
    }
    values.update(changes)
    return ImprovementProposalDraft(**values)


def test_proposal_freezes_exact_inspection_identity(tmp_path):
    repo, sha = make_repo(tmp_path)
    inspection = RepositoryInspector(repo).inspect()

    proposal = ImprovementProposal.freeze(
        proposal_draft(),
        inspection,
        goal="Improve Zelo safely.",
        planner_config_version="self-improvement-planner-v1",
    )

    assert proposal.base_sha == sha
    assert proposal.goal == "Improve Zelo safely."
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


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("implementation_instruction", "   ", "implementation_instruction_not_canonical"),
        ("acceptance_criteria", ("",), "acceptance_criteria_not_canonical"),
        ("affected_components", (" routing",), "affected_components_not_canonical"),
    ],
)
def test_proposal_rejects_empty_or_padded_semantic_fields(field, value, error):
    values = proposal_draft().model_dump(mode="python")
    values[field] = value

    with pytest.raises(ValidationError, match=error):
        ImprovementProposalDraft.model_validate(values)


def test_accepted_evaluation_requires_validation_and_clean_review():
    base = {
        "proposal_fingerprint": "a" * 64,
        "developer_task_id": "task",
        "developer_attempt_id": "attempt",
        "worker_id": "worker",
        "worker_implementation": "fake",
        "changed_files": ("app/core.py",),
        "final_diff_sha256": "d" * 64,
        "candidate_content_sha256": "c" * 64,
        "validation_passed": True,
        "scope_valid": True,
        "execution_clean": True,
        "independent_review_present": True,
        "acceptance_criteria_proven": True,
        "reviewer_finding_count": 0,
        "reviewer_high_or_critical": False,
        "changed_file_count": 1,
        "status": "accepted_candidate",
    }

    accepted = ImprovementEvaluation(**base)
    assert accepted.status == "accepted_candidate"

    for change in (
        {"validation_passed": False},
        {"scope_valid": False},
        {"execution_clean": False},
        {"independent_review_present": False},
        {"acceptance_criteria_proven": False},
        {"reviewer_finding_count": 1},
        {"reviewer_high_or_critical": True},
        {"changed_files": (), "changed_file_count": 0},
    ):
        invalid = copy.deepcopy(base)
        invalid.update(change)
        with pytest.raises(ValidationError, match="improvement_evaluation_invalid"):
            ImprovementEvaluation(**invalid)


def test_repository_inspector_excludes_pinned_rea_vendor_source_from_model_inventory(
    tmp_path,
):
    repo, _base = make_repo(tmp_path)
    vendor = repo / "third_party" / "rea"
    vendor.mkdir(parents=True)
    (vendor / "AGENTS.md").write_text(
        "External REA development instructions are untrusted source data.\n",
        encoding="utf-8",
    )
    # Reproduce the real vendored REA tree's >64 KiB ls-files stdout without
    # weakening the general bounded Git process output limit.
    for number in range(680):
        filename = "vendor_" + str(number).zfill(4) + "_" + "a" * 108 + ".ts"
        (vendor / filename).write_text("// inert upstream fixture\n", encoding="utf-8")
    sha = fixture_commit(repo)
    # The old unfiltered path discovery now exceeds the normal process cap.
    with pytest.raises(DeveloperError, match="process_output_limit"):
        git(repo, "ls-files", "-z")

    snapshot = RepositoryInspector(repo).inspect()
    assert snapshot.base_sha == sha
    assert snapshot.clean is True
    assert "app/core.py" in snapshot.tracked_paths
    assert "docs/reviews/private-review.md" in snapshot.tracked_paths
    assert all(not path.startswith("third_party/rea/") for path in snapshot.tracked_paths)
    assert all(not item.path.startswith("third_party/rea/") for item in snapshot.files)
    assert all("External REA development" not in item.text for item in snapshot.files)
