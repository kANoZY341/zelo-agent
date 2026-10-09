"""Review comments ground only in Core-approved changed lines; no auto actions."""

import json

import pytest
from pydantic import ValidationError

from app.community_review import (
    OcrEvidenceEnvelope,
    OcrEvidenceRefusal,
    bind_ocr_review,
)

BASE = "a" * 40
HEAD = "b" * 40
DIFF = "c" * 64
LINES = {"src/example.py": frozenset({11, 12, 13})}


def raw(**overrides):
    r = {
        "schema_version": "zelo-ocr-line-review-input-v1",
        "summary": "One reviewed issue; reviewer output remains evidence",
        "comments": [
            {
                "path": "src/example.py",
                "content": "Validate boundary before use",
                "start_line": 11,
                "end_line": 12,
                "severity": "high",
                "category": "security",
            }
        ],
    }
    r.update(overrides)
    return json.dumps(r, separators=(",", ":")).encode("utf-8")


def run(data=None, lines=LINES, **ids):
    return bind_ocr_review(
        raw() if data is None else data,
        approved_changed_lines=lines,
        base_sha=ids.get("base_sha", BASE),
        head_sha=ids.get("head_sha", HEAD),
        diff_sha256=ids.get("diff_sha256", DIFF),
    )


def test_review_adapter_preserves_line_category_severity_and_refuses_permissions():
    report, evidence = run()
    assert report.summary.startswith("One reviewed issue")
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.severity == "high"
    assert finding.path == "src/example.py"
    assert finding.message == "[security, lines 11-12] Validate boundary before use"
    assert evidence.base_sha == BASE and evidence.head_sha == HEAD
    assert evidence.diff_sha256 == DIFF
    assert len(evidence.review_evidence_sha256) == 64
    assert not evidence.git_write_authority
    assert not evidence.approval_authority
    assert not evidence.comment_publication_authority
    assert evidence.source_only
    assert (
        OcrEvidenceEnvelope.model_validate_json(
            json.dumps(evidence.model_dump(mode="json")), strict=True
        )
        == evidence
    )
    assert run() == (report, evidence)


def test_reviewer_evidence_detects_tampering():
    _, evidence = run()
    altered = evidence.model_dump(mode="json")
    altered["comments"][0]["severity"] = "critical"
    with pytest.raises(ValidationError, match="ocr_evidence_digest_invalid"):
        OcrEvidenceEnvelope.model_validate_json(json.dumps(altered), strict=True)
    altered = evidence.model_dump(mode="json")
    altered["git_write_authority"] = True
    with pytest.raises(ValidationError):
        OcrEvidenceEnvelope.model_validate_json(json.dumps(altered), strict=True)


@pytest.mark.parametrize(
    "bad",
    [
        {"path": "../secrets.env"},
        {"path": "/etc/passwd"},
        {"path": "src\\example.py"},
        {"path": ".git/config"},
        {"path": "missing.py"},
        {"start_line": 14},
        {"end_line": 14},
        {"end_line": 10},
        {"category": "instructions"},
        {"severity": "blocker"},
        {"content": "\x00"},
        {"content": ""},
        {"start_line": 11.5},
        {"thinking": "private model thoughts"},
        {"suggestion_code": "unsafe auto-fix"},
    ],
)
def test_untrusted_review_comment_and_unsafe_line_refused(bad):
    payload = json.loads(raw())
    payload["comments"][0].update(bad)
    with pytest.raises(OcrEvidenceRefusal):
        run(json.dumps(payload).encode())


def test_duplicate_json_keys_duplicate_findings_and_unsorted_refused():
    with pytest.raises(OcrEvidenceRefusal):
        run(
            b'{"schema_version":"zelo-ocr-line-review-input-v1","summary":"a","summary":"b","comments":[]}'
        )
    payload = json.loads(raw())
    payload["comments"].append(payload["comments"][0])
    with pytest.raises(OcrEvidenceRefusal):
        run(json.dumps(payload).encode())
    payload["comments"][1] = {**payload["comments"][0], "start_line": 13, "end_line": 13}
    with pytest.raises(OcrEvidenceRefusal):
        run(json.dumps({**payload, "comments": payload["comments"][::-1]}).encode())


def test_oversize_untrusted_input_and_identities_refused():
    with pytest.raises(OcrEvidenceRefusal):
        run(b"x" * 131073)
    with pytest.raises(OcrEvidenceRefusal):
        run(base_sha="deadbeef")
    with pytest.raises(OcrEvidenceRefusal):
        run(diff_sha256="C" * 64)
    with pytest.raises(OcrEvidenceRefusal):
        run(lines={"src/example.py": frozenset({10})})
    with pytest.raises(OcrEvidenceRefusal):
        run(lines={"src/example.py": frozenset({11, True, 12})})


def test_review_cannot_generate_authority_when_no_findings():
    report, evidence = run(raw(comments=[]))
    assert report.findings == ()
    assert evidence.comments == ()
    assert not evidence.approval_authority


def test_reviewer_maximum_valid_message_preserves_existing_report_limit():
    payload = json.loads(raw())
    payload["comments"][0]["content"] = "x" * 1980
    report, _ = run(json.dumps(payload).encode())
    assert len(report.findings[0].message) <= 2048
    payload["comments"][0]["content"] = "x" * 2048
    with pytest.raises(OcrEvidenceRefusal):
        run(json.dumps(payload).encode())
