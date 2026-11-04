"""Real Git review quality needs semantic adjudication, not just valid anchors."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.community_review_git_scope import CommunityGitScopeRefusal
from app.ocr_real_diff_quality import (
    OcrCommentAdjudication,
    OcrRealDiffCase,
    OcrRealDiffRefusal,
    _evaluate_case,
    evaluate_pinned_ocr_case,
)


def _git(repo: Path, *args: str) -> str:
    return (
        subprocess.run(
            ["/usr/bin/git", *args], cwd=repo, check=True, capture_output=True, timeout=10
        )
        .stdout.decode()
        .strip()
    )


@pytest.fixture
def real_git_case(tmp_path: Path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Zelo Fixture")
    _git(repo, "config", "user.email", "zelo-fixture@example.invalid")
    (repo / "migration.py").write_text("def downgrade():\n    pass\n")
    _git(repo, "add", "--all")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "migration.py").write_text(
        "def downgrade():\n    drop_table('records')\n    audit('downgrade')\n"
    )
    _git(repo, "add", "--all")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "head")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, OcrRealDiffCase(
        case_id="fixture_destructive_migration",
        base_sha=base,
        head_sha=head,
        known_defect_path="migration.py",
        known_defect_span=(2, 3),
        historical_case=False,
    )


def _raw(comments: list[dict] | None = None) -> bytes:
    return json.dumps(
        {
            "schema_version": "zelo-ocr-line-review-input-v1",
            "summary": "Review of committed change",
            "comments": [] if comments is None else comments,
        }
    ).encode()


def _comment(
    content: str = "Downgrade drops the populated table and loses existing records",
    line: int = 2,
) -> dict:
    return {
        "path": "migration.py",
        "start_line": line,
        "end_line": line,
        "severity": "high",
        "category": "bug",
        "content": content,
    }


def _comment_hash(comment: dict) -> str:
    return hashlib.sha256(
        json.dumps(comment, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()


def test_valid_anchor_requires_explicit_semantic_adjudication(real_git_case):
    repo, case = real_git_case
    result = _evaluate_case(_raw([_comment()]), repo, case=case, adjudications=())
    assert result.adapter_status == "accepted"
    assert result.target_status == "needs_adjudication"
    assert result.unadjudicated_count == 1
    assert not result.independent_semantic_quality_proven
    assert not result.runtime_authority_granted


def test_adjudication_is_bound_to_exact_comment_and_target_line(real_git_case):
    repo, case = real_git_case
    comment = _comment()
    adjudication = OcrCommentAdjudication(
        comment_sha256=_comment_hash(comment),
        verdict="known_defect",
        rationale="This specifically identifies loss of persisted records on downgrade.",
    )
    result = _evaluate_case(_raw([comment]), repo, case=case, adjudications=(adjudication,))
    assert result.target_status == "detected_with_manual_adjudication"
    assert result.unadjudicated_count == 0
    assert not result.independent_semantic_quality_proven
    assert (
        result.receipt_sha256
        == hashlib.sha256(
            json.dumps(
                result.model_dump(mode="json", exclude={"receipt_sha256"}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
    )

    with pytest.raises(OcrRealDiffRefusal, match="ocr_adjudication_comment_unknown"):
        _evaluate_case(
            _raw([comment]),
            repo,
            case=case,
            adjudications=(adjudication.model_copy(update={"comment_sha256": "0" * 64}),),
        )


def test_known_defect_can_be_anchored_elsewhere_in_changed_block(real_git_case):
    repo, case = real_git_case
    comment = _comment(line=3)
    result = _evaluate_case(
        _raw([comment]),
        repo,
        case=case,
        adjudications=(
            OcrCommentAdjudication(
                comment_sha256=_comment_hash(comment),
                verdict="known_defect",
                rationale="The comment identifies destructive record loss in this downgrade block.",
            ),
        ),
    )
    assert result.target_status == "detected_with_manual_adjudication"


def test_valid_but_wrong_finding_can_be_marked_unsupported(real_git_case):
    repo, case = real_git_case
    comment = _comment("Rename the function for style consistency")
    result = _evaluate_case(
        _raw([comment]),
        repo,
        case=case,
        adjudications=(
            OcrCommentAdjudication(
                comment_sha256=_comment_hash(comment),
                verdict="unsupported",
                rationale="The name follows the migration interface and is not a defect.",
            ),
        ),
    )
    assert result.target_status == "missed"
    assert result.unsupported_count == 1
    assert not result.clean_negative


def test_invalid_review_is_not_a_clean_negative_or_a_semantic_miss(real_git_case):
    repo, case = real_git_case
    negative = case.model_copy(
        update={
            "case_id": "fixture_clean",
            "known_defect_path": None,
            "known_defect_span": None,
        }
    )
    invalid = _evaluate_case(b"{invalid", repo, case=negative, adjudications=())
    assert invalid.adapter_status == "invalid"
    assert invalid.target_status == "invalid_review"
    assert not invalid.clean_negative
    assert invalid.raw_review_sha256 == hashlib.sha256(b"{invalid").hexdigest()

    clean = _evaluate_case(_raw(), repo, case=negative, adjudications=())
    assert clean.adapter_status == "accepted"
    assert clean.target_status == "not_applicable"
    assert clean.clean_negative


def test_real_git_identity_is_required_before_scoring(real_git_case):
    repo, case = real_git_case
    with pytest.raises(CommunityGitScopeRefusal, match="ocr_git_review_head_stale"):
        _evaluate_case(
            _raw(), repo, case=case.model_copy(update={"head_sha": "0" * 40}), adjudications=()
        )
    with pytest.raises(OcrRealDiffRefusal, match="ocr_real_diff_case_unknown"):
        evaluate_pinned_ocr_case(_raw(), repo, case_id="fixture_destructive_migration")
