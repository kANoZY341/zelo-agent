"""Offline tests for the append-only one-extra-attempt native reader gate."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from qualification import media_reader_extra_attempt as extra
from qualification import media_reader_scope as scope
from scripts import grant_media_native_reader_extra_attempt as grant_cli
from scripts import qualify_media_native_reader as reader
from tests.test_media_native_reader_scope import make_runtime


def _file(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)


def two_attempt_runtime(tmp_path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime"
    make_runtime(runtime)
    scope.bind_runtime_scope(runtime)
    monkeypatch.setattr(
        extra,
        "SCOPE_SHA256",
        hashlib.sha256((runtime / scope.SCOPE_FILENAME).read_bytes()).hexdigest(),
    )
    second = runtime / scope.PROOF_BASE / extra.SECOND
    second.mkdir(mode=0o700)
    policy = {
        "schema": "zelo-media-native-reader-synthetic-policy-v1",
        "attempt_limit": 2,
        "network": "none",
        "runtime_authority": False,
    }
    proof = {
        "status": "refused",
        "stage": "build",
        "support_release_confirmed": False,
        "runtime_authority": False,
    }
    policy_raw = json.dumps(policy, sort_keys=True).encode()
    proof_raw = json.dumps(proof, sort_keys=True).encode()
    _file(second / "policy.json", policy_raw)
    _file(second / "cases.json", proof_raw)
    monkeypatch.setattr(extra, "SECOND_POLICY_SHA256", hashlib.sha256(policy_raw).hexdigest())
    monkeypatch.setattr(extra, "SECOND_PROOF_SHA256", hashlib.sha256(proof_raw).hexdigest())
    monkeypatch.setattr(extra, "_source_identity", lambda: ("a" * 40, "b" * 40))
    return runtime


def test_two_consumed_attempts_refuse_before_readiness_without_grant(tmp_path, monkeypatch):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        reader,
        "_docker_readiness",
        lambda root: pytest.fail("ungranted proof reached Docker readiness"),
    )
    with pytest.raises(ValueError, match="reader_native_extra_grant_missing"):
        asyncio.run(reader._qualify(runtime))
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 2


def test_valid_grant_reaches_readiness_but_not_admission_when_readiness_refuses(
    tmp_path, monkeypatch
):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    extra.grant_once(runtime, lambda root: None)

    def refused(root):
        raise ValueError("reader_native_readiness_identity")

    monkeypatch.setattr(reader, "_docker_readiness", refused)
    monkeypatch.setattr(
        reader,
        "AdmissionController",
        lambda root: pytest.fail("failed readiness reached admission"),
    )
    with pytest.raises(ValueError, match="reader_native_readiness_identity"):
        asyncio.run(reader._qualify(runtime))
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 2


def test_grant_is_append_only_and_allows_exactly_one_new_directory(tmp_path, monkeypatch):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    old = runtime / scope.PROOF_BASE
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for attempt in old.iterdir()
        for path in attempt.glob("*.json")
    }
    observed = []
    grant = extra.grant_once(runtime, lambda root: observed.append(root))
    assert observed == [runtime]
    assert grant["attempts_used_before"] == 2
    assert grant["attempt_limit_after"] == 3
    assert grant["additional_attempts"] == 1
    assert grant["attempt_reset"] is False
    assert extra.validate(runtime) == grant
    assert before == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in before}
    with pytest.raises(extra.RecoveryRefused, match="already_granted"):
        extra.grant_once(runtime, lambda root: pytest.fail("repeated Docker check"))
    third = reader._attempt_directory(runtime, extra_grant=True)
    assert third.is_dir()
    assert len(list(old.iterdir())) == 3
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(runtime, extra_grant=True)


@pytest.mark.parametrize(
    "corruption",
    ["limit", "reset", "source", "scope", "second_proof", "extra_inventory"],
)
def test_tampered_grant_or_history_refuses_without_allocating(tmp_path, monkeypatch, corruption):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    extra.grant_once(runtime, lambda root: None)
    target = runtime / extra.BASE / extra.GRANT
    if corruption in {"limit", "reset", "source"}:
        record = json.loads(target.read_text())
        if corruption == "limit":
            record["attempt_limit_after"] = 4
        elif corruption == "reset":
            record["attempt_reset"] = True
        else:
            record["source_head"] = "c" * 40
        _file(target, json.dumps(record, sort_keys=True).encode())
    elif corruption == "scope":
        _file(runtime / scope.SCOPE_FILENAME, b"{}")
    elif corruption == "second_proof":
        _file(runtime / scope.PROOF_BASE / extra.SECOND / "cases.json", b"{}")
    else:
        _file(runtime / extra.BASE / "unexpected.json", b"{}")
    with pytest.raises(ValueError, match="reader_native_(extra|scope)_"):
        extra.validate(runtime)
    with pytest.raises(ValueError, match="reader_native_(extra|scope)_"):
        reader._attempt_directory(runtime, extra_grant=True)
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 2


def test_observer_failure_leaves_no_grant_and_no_new_attempt(tmp_path, monkeypatch):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)

    def refused(root):
        raise ValueError("reader_native_extra_resources")

    with pytest.raises(ValueError, match="reader_native_extra_resources"):
        extra.grant_once(runtime, refused)
    assert not (runtime / extra.BASE).exists()
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 2


def test_marker_change_during_observation_refuses_before_grant(tmp_path, monkeypatch):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    marker = runtime / scope.ADMISSION_BASE / "active.marker"

    def drift(root):
        _file(marker, b"active")

    with pytest.raises(extra.RecoveryRefused, match="reader_native_extra_marker"):
        extra.grant_once(runtime, drift)
    assert not (runtime / extra.BASE).exists()
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 2


def test_observer_uses_only_bounded_read_only_docker_commands(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    calls = []

    def docker(argv, environment, timeout=20, *, stdout_limit=131072):
        calls.append(tuple(argv))
        assert stdout_limit == 0
        assert environment["DOCKER_CONFIG"].startswith(str(runtime))
        assert "--filter" in argv
        assert "label=org.zelo.media-reader.lease" in argv
        assert "--no-trunc" in argv
        return b""

    monkeypatch.setattr(grant_cli.reader, "_docker", docker)
    monkeypatch.setattr(
        grant_cli.reader,
        "_docker_readiness",
        lambda root: calls.append(("readiness", str(root))),
    )
    grant_cli._observe(runtime)
    assert len(calls) == 5
    assert calls[2] == ("readiness", str(runtime))
    assert not list(runtime.iterdir())


def test_observer_refuses_any_existing_labeled_resource(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(grant_cli.reader, "_docker", lambda *a, **k: b"owned\n")
    monkeypatch.setattr(
        grant_cli.reader,
        "_docker_readiness",
        lambda root: pytest.fail("resource collision reached quiet check"),
    )
    with pytest.raises(ValueError, match="reader_native_extra_resources"):
        grant_cli._observe(runtime)
    assert not list(runtime.iterdir())


def test_consumed_build_attempt_retains_sanitized_failure(tmp_path, monkeypatch):
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    extra.grant_once(runtime, lambda root: None)
    old = set((runtime / scope.PROOF_BASE).iterdir())
    monkeypatch.setattr(reader, "_docker_readiness", lambda root: {"synthetic": True})
    monkeypatch.setattr(reader, "_require_empty_lease", lambda *args: None)
    monkeypatch.setattr(reader, "_cleanup_owned", lambda *args: True)

    def failed_build(*args):
        raise reader.browser_setup._CommandFailure(
            1,
            b"",
            b"token=private-value\nunknown flag: chmod",
            (reader.setup.DOCKER, "--host=unix:///var/run/docker.sock", "build"),
        )

    monkeypatch.setattr(reader, "_build_and_attest", failed_build)
    with pytest.raises(reader.browser_setup._CommandFailure, match="setup_command_failed"):
        asyncio.run(reader._qualify(runtime))

    new = set((runtime / scope.PROOF_BASE).iterdir()) - old
    assert len(new) == 1
    proof = json.loads((new.pop() / "cases.json").read_text())
    assert proof["status"] == "refused"
    assert proof["stage"] == "build"
    assert proof["runtime_authority"] is False
    assert proof["build_failure"] == {
        "kind": "docker_command_exit",
        "operation": "build",
        "exit_code": 1,
        "output_category": "copy_chmod_unsupported",
        "stdout_bytes": 0,
        "stdout_sha256": hashlib.sha256(b"").hexdigest(),
        "stderr_bytes": len(b"token=private-value\nunknown flag: chmod"),
        "stderr_sha256": hashlib.sha256(b"token=private-value\nunknown flag: chmod").hexdigest(),
        "overflowed_streams": [],
        "error_excerpt": "unknown flag: chmod",
    }
    assert "private-value" not in json.dumps(proof)
