"""Verified Git anchors are produced by Core from commits, not model output."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.community_review import OcrEvidenceRefusal
from app.community_review_git_scope import (
    CommunityGitScopeRefusal,
    bind_ocr_review_from_git,
    verified_git_review_scope,
)


def git(path: Path, *argv: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", *argv],
        cwd=path,
        check=True,
        capture_output=True,
        timeout=10,
    )
    return result.stdout.decode().strip()


@pytest.fixture
def revisions(tmp_path: Path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Zelo Fixture")
    git(repo, "config", "user.email", "zelo-fixture@example.invalid")
    (repo / "alpha.py").write_text("first\nold\nthird\n")
    (repo / "removed.py").write_text("retired\n")
    (repo / "space path.py").write_text("same\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "alpha.py").write_text("first\nnew\nthird\nfourth\n")
    (repo / "space path.py").write_text("updated\n")
    (repo / "fresh.py").write_text("a\nb\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD")
    return repo, base, head


def raw(path: str = "alpha.py", line: int = 2) -> bytes:
    return json.dumps(
        {
            "schema_version": "zelo-ocr-line-review-input-v1",
            "summary": "Review of exact verified Git candidate only",
            "comments": [
                {
                    "path": path,
                    "start_line": line,
                    "end_line": line,
                    "severity": "high",
                    "category": "bug",
                    "content": "Add an explicit input boundary",
                }
            ],
        }
    ).encode()


def test_committed_changed_lines_only_with_exact_head_and_digest(revisions):
    repo, base, head = revisions
    scope = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    assert scope.base_sha == base and scope.head_sha == head
    assert hashlib.sha256(scope.review_patch).hexdigest() == scope.diff_sha256
    assert scope.allowed_lines()["alpha.py"] == frozenset({2, 4})
    assert scope.allowed_lines()["fresh.py"] == frozenset({1, 2})
    assert scope.allowed_lines()["space path.py"] == frozenset({1})
    assert "removed.py" not in scope.allowed_lines()
    assert not scope.worker_registered and not scope.git_write_authority
    assert not scope.review_approval_authority
    report, evidence, same_scope = bind_ocr_review_from_git(
        raw(), repo, base_sha=base, head_sha=head
    )
    assert report.findings[0].path == "alpha.py"
    assert evidence.diff_sha256 == scope.diff_sha256
    assert same_scope == scope
    with pytest.raises(OcrEvidenceRefusal):
        bind_ocr_review_from_git(raw(line=3), repo, base_sha=base, head_sha=head)
    with pytest.raises(OcrEvidenceRefusal):
        bind_ocr_review_from_git(raw(path="removed.py"), repo, base_sha=base, head_sha=head)


def test_unrelated_root_commit_cannot_mint_line_anchor_evidence(revisions):
    repo, base, head = revisions
    # Forge a root commit containing identical base-tree bytes but no actual
    # ancestry relationship to the reviewed HEAD.
    tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    unrelated = git(repo, "commit-tree", tree, "-m", "unrelated history")
    assert unrelated != base
    assert git(repo, "rev-parse", "HEAD") == head
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_base_unrelated"):
        verified_git_review_scope(repo, base_sha=unrelated, head_sha=head)


def test_checkout_head_change_invalidates_pending_review(revisions):
    repo, base, head = revisions
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head)
    (repo / "another.py").write_text("later\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "advance")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_head_stale"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)


def test_reject_invalid_identity_empty_scope_and_unsupported_path(revisions):
    repo, base, head = revisions
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_identity_invalid"):
        verified_git_review_scope(repo, base_sha="x", head_sha=head)
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_identity_invalid"):
        verified_git_review_scope(repo, base_sha=head, head_sha=head)
    git(repo, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "empty")
    empty_head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_empty"):
        verified_git_review_scope(repo, base_sha=head, head_sha=empty_head)
    # A filename that could trigger Git pathspec parsing must fail closed.
    (repo / ":maybe.py").write_text("a\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "unsupported")
    newer = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_diff_path_unsupported"):
        verified_git_review_scope(repo, base_sha=head, head_sha=newer)


def test_refuses_only_deleted_file_without_new_line_anchors(revisions):
    repo, _base, head = revisions
    (repo / "removed.py").unlink()
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "delete-only")
    newer = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_deleted_file_unsupported"):
        verified_git_review_scope(repo, base_sha=head, head_sha=newer)


def test_refuses_deleted_file_even_when_text_edit_also_exists(revisions):
    repo, _base, head = revisions
    (repo / "removed.py").unlink()
    (repo / "alpha.py").write_text("first\nchange\nthird\nfourth\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "mixed delete/edit")
    newer = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_deleted_file_unsupported"):
        verified_git_review_scope(repo, base_sha=head, head_sha=newer)


def test_refuses_symlink_file_in_git_tree(revisions):
    repo, _base, head = revisions
    (repo / "linked.py").symlink_to("alpha.py")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "symlink")
    new_head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_review_file_not_regular"):
        verified_git_review_scope(repo, base_sha=head, head_sha=new_head)


def test_refuses_unreviewed_parent_directory(tmp_path: Path, revisions):
    repo, base, head = revisions
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_root_invalid"):
        verified_git_review_scope(tmp_path, base_sha=base, head_sha=head)


def test_refuses_binary_changes(revisions):
    repo, _base, head = revisions
    (repo / "binary.png").write_bytes(bytes(range(256)))
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "binary")
    new_head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_binary_diff_unsupported"):
        verified_git_review_scope(repo, base_sha=head, head_sha=new_head)


def test_embedded_carriage_return_cannot_inject_hunk_anchor(revisions):
    repo, _base, head = revisions
    (repo / "alpha.py").write_bytes(b"\r@@ -1 +500 @@ hostile\nnew\nthird\nfourth\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "carriage")
    new_head = git(repo, "rev-parse", "HEAD")
    scope = verified_git_review_scope(repo, base_sha=head, head_sha=new_head)
    assert scope.allowed_lines()["alpha.py"] == frozenset({1})
    with pytest.raises(OcrEvidenceRefusal):
        bind_ocr_review_from_git(raw(line=500), repo, base_sha=head, head_sha=new_head)


def test_git_interhunk_config_cannot_authorize_unchanged_lines(revisions):
    repo, _base, head = revisions
    (repo / "long.py").write_text("".join(f"line-{n}\n" for n in range(1, 21)))
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "interhunk base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "config", "diff.interHunkContext", "100")
    (repo / "long.py").write_text(
        "UPDATED-1\n" + "".join(f"line-{n}\n" for n in range(2, 20)) + "UPDATED-20\n"
    )
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "interhunk head")
    newest = git(repo, "rev-parse", "HEAD")
    scope = verified_git_review_scope(repo, base_sha=base, head_sha=newest)
    assert scope.allowed_lines()["long.py"] == frozenset({1, 20})
    with pytest.raises(OcrEvidenceRefusal):
        bind_ocr_review_from_git(raw(path="long.py", line=10), repo, base_sha=base, head_sha=newest)


def test_binary_marker_words_in_normal_source_do_not_deny_text_diff(revisions):
    repo, _base, head = revisions
    (repo / "alpha.py").write_text(
        "first\nBinary files alpha and beta differ\nthird\nfourth\nGIT binary patch\n"
    )
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "ordinary source")
    newer = git(repo, "rev-parse", "HEAD")
    scope = verified_git_review_scope(repo, base_sha=head, head_sha=newer)
    assert scope.allowed_lines()["alpha.py"] == frozenset({2, 5})


def test_file_count_and_patch_size_limits_fail_closed(monkeypatch, revisions):
    repo, base, head = revisions
    from app import community_review_git_scope as module

    monkeypatch.setattr(module, "MAX_FILES", 1)
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_diff_file_limit"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)
    monkeypatch.setattr(module, "MAX_FILES", 64)
    monkeypatch.setattr(module, "MAX_PATCH", 80)
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_diff_output_limit"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)


def test_line_limit_refuses_large_hunk(monkeypatch, revisions):
    repo, base, head = revisions
    from app import community_review_git_scope as module

    monkeypatch.setattr(module, "MAX_CHANGED_LINES", 1)
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_diff_line_limit"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)


def test_worktree_dirt_never_influences_committed_object_scope(revisions):
    repo, base, head = revisions
    original = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    (repo / "untracked.txt").write_text("not part of the reviewed commit\n")
    (repo / "alpha.py").write_text("dirty\n")
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original


def test_configured_clean_filters_are_never_executed(revisions):
    repo, base, head = revisions
    marker = repo / "executed-filter.marker"
    # Git filters can execute arbitrary commands on git status/diff worktree.
    # This review implementation may read ONLY committed Git objects.
    git(repo, "config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'")
    (repo / ".gitattributes").write_text("*.py filter=evil\n")
    scope = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    assert scope.allowed_lines()["alpha.py"] == frozenset({2, 4})
    assert not marker.exists()


def test_regular_to_symlink_type_change_cannot_be_skipped(revisions):
    repo, _base, head = revisions
    (repo / "alpha.py").unlink()
    (repo / "alpha.py").symlink_to("fresh.py")
    (repo / "space path.py").write_text("also modified\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "type change")
    latest = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_review_file_not_regular"):
        verified_git_review_scope(repo, base_sha=head, head_sha=latest)


def test_checkout_moving_mid_read_refuses_instead_of_stale_scope(monkeypatch, revisions):
    repo, base, head = revisions
    from app import community_review_git_scope as module

    real = module._git
    head_checks = 0

    def simulate(repo_root, *args, limit, attr_source=None):
        nonlocal head_checks
        if args == ("rev-parse", "HEAD"):
            head_checks += 1
            if head_checks >= 2:
                return b"0" * 40 + b"\n"
        return real(repo_root, *args, limit=limit, attr_source=attr_source)

    monkeypatch.setattr(module, "_git", simulate)
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_head_changed"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)
    assert head_checks == 2


def test_dirty_attributes_cannot_reclassify_committed_text_as_binary(revisions):
    repo, base, head = revisions
    original = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    attrs = repo / ".gitattributes"
    attrs.write_text("*.py -diff\n")
    changed = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    assert changed == original
    attrs.write_text("*.py diff=anything\n")
    changed_again = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    assert changed_again == original


def test_configured_core_attributes_file_cannot_mutate_committed_diff(revisions):
    repo, base, head = revisions
    original = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    attribute_file = repo / "mutable_attributes"
    attribute_file.write_text("*.py -diff\n")
    git(repo, "config", "core.attributesFile", str(attribute_file))
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original
    attribute_file.write_text("*.py diff=anything\n")
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original


def test_git_info_attributes_file_is_refused_before_diff(revisions):
    repo, base, head = revisions
    raw = git(repo, "rev-parse", "--git-path", "info/attributes")
    info_path = Path(raw)
    if not info_path.is_absolute():
        info_path = repo / info_path
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text("*.py -diff\n")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_mutable_info_attributes"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)


def test_host_system_attributes_are_disabled():
    from app import community_review_git_scope as module

    assert module._GIT_ENV["GIT_ATTR_NOSYSTEM"] == "1"


def test_ignored_submodule_changes_cannot_bypass_special_file_refusal(revisions):
    repo, _base, head = revisions
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},fake-submodule")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "gitlink base")
    gitlink_base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{gitlink_base},fake-submodule")
    (repo / "alpha.py").write_text("changed line\nnew\nthird\nfourth\n")
    git(repo, "add", "alpha.py")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "gitlink changed too")
    newest = git(repo, "rev-parse", "HEAD")
    git(repo, "config", "diff.ignoreSubmodules", "all")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_review_file_not_regular"):
        verified_git_review_scope(repo, base_sha=gitlink_base, head_sha=newest)


def test_mutable_git_order_file_cannot_reorder_bound_evidence(revisions):
    repo, base, head = revisions
    original = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    order_file = repo / "order-rules.txt"
    git(repo, "config", "diff.orderFile", str(order_file))
    order_file.write_text("space path.py\nalpha.py\nfresh.py\n")
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original
    order_file.write_text("fresh.py\nalpha.py\nspace path.py\n")
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original


def test_mutable_diff_algorithm_config_cannot_change_review_scope(revisions):
    repo, base, head = revisions
    original = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    git(repo, "config", "diff.algorithm", "histogram")
    git(repo, "config", "diff.indentHeuristic", "true")
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original


def test_patch_hash_is_independent_of_mutable_header_configuration(revisions):
    repo, base, head = revisions
    original = verified_git_review_scope(repo, base_sha=base, head_sha=head)
    for key, value in (
        ("diff.noprefix", "true"),
        ("diff.mnemonicPrefix", "true"),
        ("core.abbrev", "7"),
        ("diff.relative", "true"),
        ("diff.suppressBlankEmpty", "true"),
    ):
        git(repo, "config", key, value)
    assert verified_git_review_scope(repo, base_sha=base, head_sha=head) == original
    assert original.diff_sha256 == hashlib.sha256(original.review_patch).hexdigest()


def test_old_git_version_refused_before_reading_revision_data(monkeypatch, revisions):
    repo, base, head = revisions
    from app import community_review_git_scope as module

    real = module._git

    def old_git(repo_root, *args, limit, attr_source=None):
        if args == ("--version",):
            return b"git version 2.39.3\n"
        return real(repo_root, *args, limit=limit, attr_source=attr_source)

    monkeypatch.setattr(module, "_git", old_git)
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_version_unsupported"):
        verified_git_review_scope(repo, base_sha=base, head_sha=head)


def test_committed_named_diff_driver_is_rejected_before_patch_generation(revisions):
    repo, _base, head = revisions
    (repo / ".gitattributes").write_text("*.py diff=untrusted_driver\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "pin attributes")
    pinned = git(repo, "rev-parse", "HEAD")
    git(repo, "config", "diff.untrusted_driver.xfuncname", "^changed")
    (repo / "alpha.py").write_text("changed\nnew\nthird\nfourth\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "change reviewed file")
    newer = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_custom_diff_attribute_unsupported"):
        verified_git_review_scope(repo, base_sha=pinned, head_sha=newer)


def test_deletion_only_hunk_in_modified_text_is_refused(revisions):
    repo, _base, head = revisions
    (repo / "alpha.py").write_text("first\nnew\nthird\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "remove end line")
    newer = git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_unanchorable_deletion"):
        verified_git_review_scope(repo, base_sha=head, head_sha=newer)
