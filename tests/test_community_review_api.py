"""Authenticated operator OCR preview preserves exact Git evidence and no authority."""

import json

import pytest
from sqlalchemy import func, select

from app.community_review import MAX_BYTES
from app.models import Approval, AuditEvent, Task, TaskAttempt
from tests.conftest import AUTH
from tests.test_community_review_git_scope import git, raw


@pytest.fixture
def revisions(tmp_path):
    # Reuse the independent existing Git fixture without trusting caller JSON.
    from tests.test_community_review_git_scope import revisions as trusted_git_fixture

    return trusted_git_fixture.__wrapped__(tmp_path)


BASE = "/integration-candidates/community/open-code-review/evidence"


def _url(base, head):
    return f"{BASE}/{base}/{head}"


def _preview(client, base, head, *, content=None, headers=None):
    return client.post(
        _url(base, head),
        content=raw() if content is None else content,
        headers={**AUTH, "Content-Type": "application/json", **(headers or {})},
    )


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(item))
            for item in (Task, TaskAttempt, AuditEvent, Approval)
        )


def test_owner_can_preview_scoped_finding_without_starting_reviewer_or_task(
    revisions, monkeypatch, client, database
):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    before = _counts(database)
    response = _preview(client, base, head)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema_version"] == "zelo-ocr-operator-preview-v1"
    assert result["changed_file_count"] == 3
    assert result["changed_line_count"] == 5
    assert result["report"]["findings"] == [
        {
            "severity": "high",
            "path": "alpha.py",
            "message": "[bug, lines 2-2] Add an explicit input boundary",
        }
    ]
    evidence = result["evidence"]
    assert evidence["base_sha"] == base
    assert evidence["head_sha"] == head
    assert len(evidence["diff_sha256"]) == 64
    assert len(evidence["review_evidence_sha256"]) == 64
    assert evidence["source_only"] is True
    assert evidence["git_write_authority"] is False
    assert evidence["approval_authority"] is False
    assert evidence["comment_publication_authority"] is False
    for name in (
        "supplied_review_trusted",
        "reviewer_worker_registered",
        "review_approval_authority",
        "git_write_authority",
        "tool_authority",
        "result_admitted_to_mind",
    ):
        assert result[name] is False
    assert "review_patch" not in json.dumps(result)
    assert "=== alpha.py" not in json.dumps(result)
    assert before == _counts(database)


def test_review_does_not_adopt_mutable_working_tree_as_changed_line_authority(
    revisions, monkeypatch, client
):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    original = _preview(client, base, head)
    assert original.status_code == 200
    (repo / "alpha.py").write_text("completely unrelated live text\n")
    (repo / "untracked-secret.txt").write_text("private not to review\n")
    changed = _preview(client, base, head)
    assert changed.status_code == 200, changed.text
    assert changed.json()["evidence"] == original.json()["evidence"]
    assert changed.json()["report"] == original.json()["report"]


def test_wrong_identity_or_stale_head_never_returns_evidence(revisions, monkeypatch, client):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    invalid = _preview(client, "0" * 40, head)
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "ocr_review_git_scope_denied"
    malformed = _preview(client, "abc", head)
    assert malformed.status_code == 422
    assert _preview(client, base, "F" * 40).status_code == 422

    (repo / "alpha.py").write_text("later\n")
    git(repo, "add", "--all")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "advance")
    stale = _preview(client, base, head)
    assert stale.status_code == 409
    assert stale.json()["detail"] == "ocr_review_head_stale"


@pytest.mark.parametrize(
    "content",
    [
        raw(line=3),  # context line was never changed
        raw(path="removed.py"),  # deleted file is not an approved added line
        json.dumps(
            {
                "schema_version": "zelo-ocr-line-review-input-v1",
                "summary": "x",
                "comments": [],
                "extra": "untrusted repo switch",
            }
        ).encode(),
        b'{"schema_version":"zelo-ocr-line-review-input-v1","summary":"a","summary":"b","comments":[]}',
        b'{"schema_version":"zelo-ocr-line-review-input-v1","summary":"bad","comments":[{"path":"../../secrets","start_line":1,"end_line":1,"category":"security","severity":"critical","content":"escape"}]}',
    ],
)
def test_invalid_or_unanchored_external_findings_fail_closed(
    revisions, monkeypatch, client, content
):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    result = _preview(client, base, head, content=content)
    assert result.status_code == 422
    assert result.json()["detail"] == "ocr_review_invalid_or_untrusted"


def test_preview_refuses_unrelated_history_even_with_identical_base_bytes(
    revisions, monkeypatch, client
):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    base_tree = git(repo, "rev-parse", f"{base}^{{tree}}")
    unrelated_base = git(repo, "commit-tree", base_tree, "-m", "unrelated root")
    assert unrelated_base != base
    assert git(repo, "rev-parse", "HEAD") == head
    response = _preview(client, unrelated_base, head)
    assert response.status_code == 422
    assert response.json()["detail"] == "ocr_review_git_scope_denied"


def test_operator_auth_body_size_and_media_type_gate_before_git_use(revisions, monkeypatch, client):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    url = _url(base, head)
    unauthorized = client.post(
        url,
        content=raw(),
        headers={"Authorization": "Bearer invalid", "Content-Type": "application/json"},
    )
    assert unauthorized.status_code == 401
    assert client.get(url, headers=AUTH).status_code == 405
    assert _preview(client, base, head, headers={"Content-Type": "text/plain"}).status_code == 415
    empty = _preview(client, base, head, content=b"")
    assert empty.status_code == 413 or empty.status_code == 422
    too_big = _preview(client, base, head, content=b"x" * (MAX_BYTES + 1))
    assert too_big.status_code == 413
    assert too_big.json()["detail"] == "ocr_review_size_denied"


def test_concurrent_preview_admission_refuses_before_git_process(revisions, monkeypatch, client):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    invoked = []

    def forbid_git(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("Capacity-denied request must not run Git")

    monkeypatch.setattr(module, "bind_ocr_review_from_git", forbid_git)
    assert module._PREVIEW_ADMISSION.acquire(blocking=False)
    try:
        response = _preview(client, base, head)
        assert response.status_code == 429
        assert response.json()["detail"] == "ocr_review_capacity_exhausted"
        assert invoked == []
    finally:
        module._PREVIEW_ADMISSION.release()


def test_unbounded_stream_without_content_length_refuses_before_git_use(
    revisions, monkeypatch, client
):
    from app import community_review_api as module

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)
    calls = []

    def forbid_git(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Oversized streamed review must not run Git")

    monkeypatch.setattr(module, "bind_ocr_review_from_git", forbid_git)

    def raw_chunks():
        yield raw()
        yield b"x" * (MAX_BYTES + 1)

    response = client.post(
        _url(base, head),
        content=raw_chunks(),
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "ocr_review_size_denied"
    assert calls == []


def test_missing_pinned_community_sources_never_promotes_review(revisions, monkeypatch, client):
    from app import community_review
    from app import community_review_api as module
    from app.community_sources import CommunitySourceRefusal

    repo, base, head = revisions
    monkeypatch.setattr(module, "ROOT", repo)

    def reject_sources():
        raise CommunitySourceRefusal("source_not_verified")

    monkeypatch.setattr(community_review, "verify_community_sources", reject_sources)
    result = _preview(client, base, head)
    assert result.status_code == 503
    assert result.json()["detail"] == "ocr_review_source_unavailable"
