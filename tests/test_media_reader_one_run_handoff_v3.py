"""Disposable allocation handoff; no native Docker or retained runtime."""

import fcntl
import os

import pytest

from qualification import media_reader_approval_capture_v3 as capture
from tests.test_media_reader_approval_capture_v3 import (
    COMMENT_ID,
    RECHECKED,
)
from tests.test_media_reader_preattempt_recheck_v3 import _session


def test_allocation_callback_runs_once_with_fresh_evidence_and_held_lock(tmp_path, monkeypatch):
    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    calls = []

    def allocate(runtime, gate):
        assert type(gate) is object
        assert runtime == root
        assert (root / capture.BASE / capture.ATTEMPT_RECHECK).is_file()
        fd = os.open(root / "media-elf-metadata-admission" / "admission.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        calls.append("allocated")
        return "disposable-attempt"

    try:
        evidence, value = session.recheck_before_attempt(
            lease, allocate=allocate, clock=lambda: RECHECKED
        )
        assert evidence["comment_id"] == COMMENT_ID
        assert value == "disposable-attempt"
        assert calls == ["allocated"]
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_used"):
            session.recheck_before_attempt(lease, allocate=allocate, clock=lambda: RECHECKED)
    finally:
        lease.finish(cleanup_confirmed=True)
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_changed_comment_never_reaches_allocation(tmp_path, monkeypatch):
    root, fetched, session, lease, _ = _session(tmp_path, monkeypatch)
    endpoint = f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"
    fetched[endpoint]["body"] = fetched[endpoint]["body"].replace("19:00:00Z", "18:30:00Z")
    try:
        with pytest.raises(capture.approval.ApprovalRefused, match="comment_drift"):
            session.recheck_before_attempt(
                lease,
                allocate=lambda _, check: pytest.fail("allocation reached"),
                clock=lambda: RECHECKED,
            )
    finally:
        lease.finish(cleanup_confirmed=True)
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_expiry_before_allocation_consumes_recheck_without_attempt(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    times = iter((RECHECKED, datetime(2026, 10, 20, 19, 0, tzinfo=UTC)))
    try:
        with pytest.raises(capture.CaptureRefused, match="attempt_expired"):
            session.recheck_before_attempt(
                lease,
                allocate=lambda _, check: pytest.fail("allocation reached"),
                clock=times.__next__,
            )
        assert (root / capture.BASE / capture.ATTEMPT_RECHECK).is_file()
        assert len(list((root / "media-native-reader").iterdir())) == 4
        with pytest.raises(capture.CaptureRefused, match="attempt_recheck_used"):
            session.recheck_before_attempt(lease, clock=lambda: RECHECKED)
    finally:
        lease.finish(cleanup_confirmed=True)


def test_fifth_directory_requires_live_recheck_and_final_clock(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(root, extra_grant=True, recovery_version=3)
    try:
        _, attempt = session.recheck_before_attempt(
            lease,
            allocate=lambda runtime, check: reader._attempt_directory(
                runtime, extra_grant=True, recovery_version=3, v3_before_mkdir=check
            ),
            clock=lambda: RECHECKED,
        )
        assert attempt.parent == root / "media-native-reader"
        assert attempt.name.startswith(reader.PREFIX)
        assert len(list(attempt.parent.iterdir())) == 5
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            reader._attempt_directory(root, extra_grant=True, recovery_version=3)
    finally:
        lease.finish(cleanup_confirmed=False)


def test_saved_grant_and_caller_callback_cannot_allocate_fifth(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, _, lease, _ = _session(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            reader._attempt_directory(
                root, extra_grant=True, recovery_version=3, v3_before_mkdir=lambda: None
            )
        assert len(list((root / "media-native-reader").iterdir())) == 4
    finally:
        lease.finish(cleanup_confirmed=True)


def test_gate_cannot_be_saved_for_later_allocation(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    saved = []
    try:
        session.recheck_before_attempt(
            lease, allocate=lambda runtime, gate: saved.append(gate), clock=lambda: RECHECKED
        )
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            reader._attempt_directory(
                root, extra_grant=True, recovery_version=3, v3_before_mkdir=saved[0]
            )
        assert len(list((root / "media-native-reader").iterdir())) == 4
    finally:
        lease.finish(cleanup_confirmed=True)


def test_expiry_during_attempt_validation_refuses_before_mkdir(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)
    times = iter((RECHECKED, RECHECKED, datetime(2026, 10, 20, 19, 0, tzinfo=UTC)))
    try:
        with pytest.raises(capture.CaptureRefused, match="attempt_expired"):
            session.recheck_before_attempt(
                lease,
                allocate=lambda runtime, gate: reader._attempt_directory(
                    runtime, extra_grant=True, recovery_version=3, v3_before_mkdir=gate
                ),
                clock=times.__next__,
            )
        assert len(list((root / "media-native-reader").iterdir())) == 4
        assert not capture._LIVE_ATTEMPT_GATES
    finally:
        lease.finish(cleanup_confirmed=True)


def test_qualifier_v3_single_process_handoff_allocates_once_without_docker(tmp_path, monkeypatch):
    import asyncio

    from qualification import media_reader_extra_attempt_v3 as v3
    from scripts import qualify_media_native_reader as reader
    from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime
    from tests.test_media_reader_approval_capture_v3 import CHECKED, ISSUED, _github_fixture

    root = four_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.preflight, "observe", lambda _: None)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: "ready-without-Docker")

    def stop_after_allocation(_):
        raise ValueError("test_stop_after_allocation")

    monkeypatch.setattr(reader, "_docker_environment", stop_after_allocation)
    existing = {attempt.name for attempt in (root / "media-native-reader").iterdir()}
    times = iter((ISSUED, CHECKED, CHECKED, CHECKED, RECHECKED, RECHECKED, RECHECKED))
    with pytest.raises(ValueError, match="test_stop_after_allocation"):
        asyncio.run(
            reader._qualify(
                root,
                v3_request_comment_id=lambda challenge, history: COMMENT_ID,
                v3_clock=times.__next__,
            )
        )
    attempts = list((root / "media-native-reader").iterdir())
    assert len(attempts) == 5
    fifth = next(path for path in attempts if path.name not in existing)
    assert fifth.name.startswith(reader.PREFIX)
    assert (fifth / "cases.json").is_file()


def test_forked_child_cannot_consume_parent_gate(tmp_path, monkeypatch):
    from scripts import qualify_media_native_reader as reader

    root, _, session, lease, _ = _session(tmp_path, monkeypatch)

    def allocate(runtime, gate):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                capture._consume_attempt_gate(runtime, gate)
            except ValueError:
                os.write(write_fd, b"refused")
            else:
                os.write(write_fd, b"accepted")
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        verdict = os.read(read_fd, 16)
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        assert status == 0
        assert verdict == b"refused"
        return reader._attempt_directory(
            runtime, extra_grant=True, recovery_version=3, v3_before_mkdir=gate
        )

    try:
        _, attempt = session.recheck_before_attempt(
            lease, allocate=allocate, clock=lambda: RECHECKED
        )
        assert attempt.is_dir()
        assert len(list((root / "media-native-reader").iterdir())) == 5
    finally:
        lease.finish(cleanup_confirmed=False)


def test_qualifier_v3_saved_grant_refuses_before_docker(tmp_path, monkeypatch):
    import asyncio

    from scripts import qualify_media_native_reader as reader

    root, _, _, lease, _ = _session(tmp_path, monkeypatch)
    lease.finish(cleanup_confirmed=True)
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: pytest.fail("Docker reached"))
    with pytest.raises(ValueError, match="reader_native_v3_prior_consumption"):
        asyncio.run(
            reader._qualify(root, v3_request_comment_id=lambda challenge, history: COMMENT_ID)
        )
    assert len(list((root / "media-native-reader").iterdir())) == 4


def test_qualifier_v3_comment_drift_refuses_without_attempt(tmp_path, monkeypatch):
    import asyncio

    from qualification import media_reader_extra_attempt_v3 as v3
    from scripts import qualify_media_native_reader as reader
    from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime
    from tests.test_media_reader_approval_capture_v3 import CHECKED, ISSUED, _github_fixture

    root = four_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.preflight, "observe", lambda _: None)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    comment_endpoint = f"/repos/kANoZY341/zelo-agent/issues/comments/{COMMENT_ID}"
    reads = [0]

    def get(endpoint):
        if endpoint == comment_endpoint:
            reads[0] += 1
            if reads[0] == 3:
                fetched[endpoint]["body"] = fetched[endpoint]["body"].replace(
                    "19:00:00Z", "18:30:00Z"
                )
        return fetched[endpoint]

    monkeypatch.setattr(capture.approval, "gh_api_get", get)
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: "ready-without-Docker")
    monkeypatch.setattr(reader, "_docker_environment", lambda _: pytest.fail("Docker reached"))
    times = iter((ISSUED, CHECKED, CHECKED, CHECKED, RECHECKED))
    with pytest.raises(capture.approval.ApprovalRefused, match="comment_drift"):
        asyncio.run(
            reader._qualify(
                root,
                v3_request_comment_id=lambda challenge, history: COMMENT_ID,
                v3_clock=times.__next__,
            )
        )
    assert reads[0] == 3
    assert len(list((root / "media-native-reader").iterdir())) == 4
    assert (root / "media-elf-metadata-admission" / "active.marker").read_bytes() == b""


def test_operator_cli_explicit_v3_flag_routes_to_live_callback(tmp_path, monkeypatch, capsys):
    import sys
    import time

    from scripts import qualify_media_native_reader as reader

    seen = []

    async def fake_qualify(root, **kwargs):
        seen.append((root, kwargs))
        return {"status": "test-only"}, time.monotonic() + 30

    monkeypatch.setattr(reader, "_qualify", fake_qualify)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_media_native_reader.py",
            "--run",
            "--runtime-root",
            str(tmp_path),
            "--one-run-v3",
        ],
    )
    assert reader.main() == 0
    assert seen[0][0] == tmp_path
    assert callable(seen[0][1]["v3_request_comment_id"])
    assert "test-only" in capsys.readouterr().out


def test_operator_comment_template_matches_strict_body(tmp_path, monkeypatch, capsys):
    import json

    from qualification import media_reader_run_approval_v3 as approval
    from scripts import qualify_media_native_reader as reader
    from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime

    root = four_attempt_runtime(tmp_path, monkeypatch)
    _, record = reader.extra_v3._history(root)
    monkeypatch.setattr("builtins.input", lambda prompt: "123456")
    comment_id = reader._request_v3_comment_id(root, "c" * 64, record["proof_history"])
    lines = capsys.readouterr().out.splitlines()
    body = json.loads(lines[-1])
    assert comment_id == 123456
    assert body["schema"] == approval.SCHEMA
    assert body["decision"] == approval.DECISION
    assert body["challenge"] == "c" * 64
    assert body["runtime_root"] == str(root)
    assert body["proof_history"] == record["proof_history"]
    assert body["proof_history_sha256"] == approval._history_digest(record["proof_history"])
    assert body["source_head"] == "e" * 40
    assert body["source_tree"] == "f" * 40
    assert len(lines[-1].encode()) <= approval.MAX_BODY_BYTES


def test_second_readiness_refusal_clears_lease_before_any_attempt(tmp_path, monkeypatch):
    import asyncio

    from qualification import media_reader_extra_attempt_v3 as v3
    from scripts import qualify_media_native_reader as reader
    from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime
    from tests.test_media_reader_approval_capture_v3 import CHECKED, ISSUED, _github_fixture

    root = four_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.preflight, "observe", lambda _: None)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    readiness_calls = [0]

    def readiness(_):
        readiness_calls[0] += 1
        if readiness_calls[0] == 2:
            raise ValueError("reader_native_docker_global_event")
        return "ready-without-Docker"

    monkeypatch.setattr(reader, "_docker_readiness", readiness)
    times = iter((ISSUED, CHECKED, CHECKED, CHECKED))
    with pytest.raises(ValueError, match="reader_native_docker_global_event"):
        asyncio.run(
            reader._qualify(
                root,
                v3_request_comment_id=lambda challenge, history: COMMENT_ID,
                v3_clock=times.__next__,
            )
        )
    assert readiness_calls[0] == 2
    assert len(list((root / "media-native-reader").iterdir())) == 4
    assert (root / "media-elf-metadata-admission" / "active.marker").read_bytes() == b""
    assert (root / capture.BASE / capture.RECEIPT).is_file()


def test_uncertain_fifth_directory_keeps_shared_admission_fenced(tmp_path, monkeypatch):
    import asyncio

    from qualification import media_reader_extra_attempt_v3 as v3
    from scripts import qualify_media_native_reader as reader
    from tests.test_media_native_reader_extra_attempt_v3 import four_attempt_runtime
    from tests.test_media_reader_approval_capture_v3 import CHECKED, ISSUED, _github_fixture

    root = four_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.preflight, "observe", lambda _: None)
    _, record = v3._history(root)
    fetched = _github_fixture(root, record["proof_history"], "c" * 64)
    monkeypatch.setattr(capture.secrets, "token_hex", lambda n: "c" * 64)
    monkeypatch.setattr(capture.approval, "gh_api_get", lambda endpoint: fetched[endpoint])
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: "ready-without-Docker")

    def uncertain_mkdir(*, prefix, dir):
        (root / "media-native-reader" / (prefix + "uncertain")).mkdir(mode=0o700)
        raise OSError("simulated uncertain fifth allocation")

    monkeypatch.setattr(reader.tempfile, "mkdtemp", uncertain_mkdir)
    times = iter((ISSUED, CHECKED, CHECKED, CHECKED, RECHECKED, RECHECKED, RECHECKED))
    with pytest.raises(OSError, match="uncertain fifth allocation"):
        asyncio.run(
            reader._qualify(
                root,
                v3_request_comment_id=lambda challenge, history: COMMENT_ID,
                v3_clock=times.__next__,
            )
        )
    assert len(list((root / "media-native-reader").iterdir())) == 5
    assert (root / "media-elf-metadata-admission" / "active.marker").read_bytes()
