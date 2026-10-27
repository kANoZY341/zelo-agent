"""Disposable seventh handoff and CLI tests; never contacts Docker or GitHub."""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime

import pytest

from qualification import media_reader_approval_capture_v5 as capture
from qualification import media_reader_run_approval_v5 as approval
from qualification import media_reader_seventh_ledger as ledger
from scripts import qualify_media_native_reader as reader
from tests import test_media_reader_approval_capture_v5 as fixtures
from tests.test_media_reader_approval_capture_v5 import COMMENT_ID, _clock

candidate = fixtures.candidate


def test_qualifier_single_process_gate_allocates_once_and_consumes_marker(candidate, monkeypatch):
    root, _, _, request, get, _ = candidate
    base = root / "media-native-reader"
    base.mkdir(mode=0o700)
    for index in range(6):
        (base / f"native-reader-proof-prior-{index}").mkdir(mode=0o700)
    monkeypatch.setattr(
        reader.scope,
        "validate_runtime_scope",
        lambda _: (_ for _ in ()).throw(
            reader.scope.ScopeRefused("reader_native_scope_attempt_count")
        ),
    )
    monkeypatch.setattr(ledger, "audit", lambda _: {"status": "disposable"})
    monkeypatch.setattr(ledger, "_audit_held_live", lambda _, __, before: before)
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: "disposable-ready")
    allocated = []

    def allocate(runtime, *, recovery_version, v5_before_mkdir, **kwargs):
        assert runtime == root and recovery_version == 5 and kwargs["extra_grant"] is True
        capture._consume_attempt_gate(runtime, v5_before_mkdir)
        attempt = base / "native-reader-proof-disposable-seventh"
        attempt.mkdir(mode=0o700)
        allocated.append(attempt)
        return attempt

    monkeypatch.setattr(reader, "_attempt_directory", allocate)
    monkeypatch.setattr(
        reader,
        "_docker_environment",
        lambda _: (_ for _ in ()).throw(ValueError("disposable_stop_after_allocation")),
    )
    with pytest.raises(ValueError, match="disposable_stop_after_allocation"):
        asyncio.run(
            reader._qualify(
                root,
                v5_request_comment_id=request,
                v5_get=get,
                v5_clock=_clock(),
            )
        )
    assert len(allocated) == 1
    assert (root / capture.BASE / "attempt-recheck.json").is_file()
    assert (allocated[0] / "cases.json").is_file()
    assert (root / "media-elf-metadata-admission/active.marker").stat().st_size > 0
    assert not capture._GATES


def test_qualifier_comment_edit_fences_marker_before_allocation(candidate, monkeypatch):
    root, _, _, request, get, responses = candidate
    base = root / "media-native-reader"
    base.mkdir(mode=0o700)
    for index in range(6):
        (base / f"native-reader-proof-prior-{index}").mkdir(mode=0o700)
    monkeypatch.setattr(
        reader.scope,
        "validate_runtime_scope",
        lambda _: (_ for _ in ()).throw(
            reader.scope.ScopeRefused("reader_native_scope_attempt_count")
        ),
    )
    monkeypatch.setattr(ledger, "audit", lambda _: {"status": "disposable"})
    monkeypatch.setattr(ledger, "_audit_held_live", lambda _, __, before: before)
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: "disposable-ready")
    monkeypatch.setattr(reader, "_attempt_directory", lambda *_, **__: pytest.fail("allocated"))
    reads = 0

    def drift(endpoint):
        nonlocal reads
        if endpoint.endswith(f"/{COMMENT_ID}"):
            reads += 1
            if reads == 3:
                responses[endpoint]["body"] += " "
        return get(endpoint)

    with pytest.raises(approval.ApprovalRefused, match="comment_drift"):
        asyncio.run(
            reader._qualify(
                root,
                v5_request_comment_id=request,
                v5_get=drift,
                v5_clock=_clock(),
            )
        )
    assert reads == 3
    assert len(list(base.iterdir())) == 6
    assert (root / "media-elf-metadata-admission/active.marker").stat().st_size > 0


def test_explicit_v5_cli_routes_callback_and_rejects_both_versions(tmp_path, monkeypatch, capsys):
    seen = []

    async def fake_qualify(root, **kwargs):
        seen.append((root, kwargs))
        return {"status": "disposable"}, time.monotonic() + 30

    monkeypatch.setattr(reader, "_qualify", fake_qualify)
    args = ["qualify_media_native_reader.py", "--run", "--runtime-root", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", [*args, "--one-run-v5"])
    assert reader.main() == 0
    assert seen[0][0] == tmp_path
    assert callable(seen[0][1]["v5_request_comment_id"])
    assert "disposable" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", [*args, "--one-run-v4", "--one-run-v5"])
    with pytest.raises(SystemExit, match="2"):
        reader.main()


def test_v5_operator_template_has_exact_strict_binding(candidate, monkeypatch, capsys):
    root, _, _, request, _, _ = candidate
    from qualification import media_reader_run_approval_v3 as prior

    monkeypatch.setattr("builtins.input", lambda _: "123456")
    assert reader._request_v5_comment_id(root, "c" * 64, approval._history()) == 123456
    body = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert body == {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "challenge": "c" * 64,
        "runtime_root": str(root),
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "proof_history": approval._history(),
        "proof_history_sha256": prior._history_digest(approval._history()),
        "sixth_image_cleanup_completion_sha256": ledger.COMPLETION_SHA256,
        "attempts_used_before": 6,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
        "expires_utc": body["expires_utc"],
    }
    assert datetime.fromisoformat(body["expires_utc"].replace("Z", "+00:00")) > datetime.now(UTC)
