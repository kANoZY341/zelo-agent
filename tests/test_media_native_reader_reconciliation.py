"""Offline tests for explicit native-reader admission reconciliation."""

import fcntl
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from qualification import media_reader_reconcile as contract
from scripts import reconcile_media_native_reader as r


def test_docker_inventory_uses_bounded_go_concurrency_under_operator_limits(tmp_path, monkeypatch):
    """The qualifier's 1 GiB address-space cap must not strand Docker's Go CLI."""
    spawned = []
    original = r.subprocess.Popen

    def launch(args, **kwargs):
        spawned.append((args, kwargs["env"].copy()))
        return original([sys.executable, "-c", "print('ready')"], **kwargs)

    monkeypatch.setattr(r.subprocess, "Popen", launch)
    monkeypatch.setenv("GOMAXPROCS", "128")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.example:2375")
    assert r._command((r.DOCKER, "version"), tmp_path.resolve()) == b"ready\n"
    assert spawned[0][0] == (r.DOCKER, "version")
    assert spawned[0][1] == {
        "DOCKER_CONFIG": str(tmp_path.resolve()),
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "GOMAXPROCS": "2",
    }


def test_committed_attempt1_refusal_fixtures_match_contract():
    root = Path(__file__).resolve().parents[1]
    marker = (root / contract.FIXTURE_MARKER).read_bytes()
    policy = (root / contract.FIXTURE_POLICY).read_bytes()
    cases = (root / contract.FIXTURE_CASES).read_bytes()
    value = contract.validate_fixture_bindings(marker, policy, cases)
    assert value["attempt"] == contract.ATTEMPT_NAME
    assert value["lease_identity"] == contract.LEASE_IDENTITY
    assert value["attempt_consumed"] is True
    assert value["marker_clear_authorized"] is False
    assert value["runtime_authority"] is False


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@pytest.fixture
def fenced(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    runtime = root / "runtime"
    runtime.mkdir(parents=True, mode=0o700)
    admission = runtime / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    proof_root = runtime / "media-native-reader"
    proof_root.mkdir(mode=0o700)
    attempt = proof_root / "native-reader-proof-old"
    attempt.mkdir(mode=0o700)
    config = attempt / "docker-config"
    config.mkdir(mode=0o700)

    policy = {
        "schema": "zelo-media-native-reader-synthetic-policy-v1",
        "attempt_limit": 2,
        "runtime_authority": False,
    }
    policy_raw = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode()
    policy_digest = hashlib.sha256(_canonical(policy)).hexdigest()
    proof = {
        "runtime_authority": False,
        "stage": "lease-preflight",
        "status": "refused",
        "support_release_confirmed": False,
    }
    proof_raw = (json.dumps(proof, sort_keys=True, indent=2) + "\n").encode()
    lease = "a" * 64
    marker_value = {"version": 1, "lease": lease, "policy": policy_digest}
    marker_raw = json.dumps(marker_value).encode()

    for path, raw in (
        (attempt / "policy.json", policy_raw),
        (attempt / "cases.json", proof_raw),
        (admission / "active.marker", marker_raw),
        (admission / "admission.lock", b""),
    ):
        path.write_bytes(raw)
        path.chmod(0o600)

    monkeypatch.setattr(r, "ROOT", root)
    monkeypatch.setattr(r, "ADMISSION", admission)
    monkeypatch.setattr(r, "FAILED_ATTEMPT", attempt)
    monkeypatch.setattr(r, "RECON_BASE", runtime / "media-native-reader-reconciliation")
    monkeypatch.setattr(r, "FAILED_LEASE", lease)
    monkeypatch.setattr(r, "FAILED_POLICY_DIGEST", policy_digest)
    monkeypatch.setattr(r, "FAILED_POLICY_SHA256", hashlib.sha256(policy_raw).hexdigest())
    monkeypatch.setattr(r, "FAILED_PROOF_SHA256", hashlib.sha256(proof_raw).hexdigest())
    monkeypatch.setattr(r, "FAILED_MARKER_SHA256", hashlib.sha256(marker_raw).hexdigest())
    monkeypatch.setattr(r, "FAILED_SOURCE_HEAD", "b" * 40)
    monkeypatch.setattr(r, "FAILED_SOURCE_TREE", "c" * 40)
    return {
        "root": root,
        "runtime": runtime,
        "admission": admission,
        "attempt": attempt,
        "policy_raw": policy_raw,
        "proof_raw": proof_raw,
        "marker_raw": marker_raw,
        "lease": lease,
        "policy_digest": policy_digest,
    }


def zero_discovery(kind, config):
    assert kind in ("container", "image")
    assert Path(config).name.startswith("reader-reconcile-docker-")
    return ()


def quiet(config):
    assert Path(config).name.startswith("reader-reconcile-docker-")
    return ("2026-10-05T00:00:00Z", "2026-10-05T00:00:02Z")


def test_success_clears_only_marker_and_preserves_failed_attempt(fenced):
    before_policy = (fenced["attempt"] / "policy.json").read_bytes()
    before_proof = (fenced["attempt"] / "cases.json").read_bytes()
    retained_config = fenced["attempt"] / "docker-config"
    before_config = retained_config.stat()
    result = r.reconcile(docker_discover=zero_discovery, quiet_window=quiet)
    assert result["status"] == "reconciled-native-reader-admission"
    assert result["attempts_used"] == 1 and result["attempt_limit"] == 2
    assert result["runtime_authority"] is False
    assert (fenced["admission"] / "active.marker").read_bytes() == b""
    assert (fenced["attempt"] / "policy.json").read_bytes() == before_policy
    assert (fenced["attempt"] / "cases.json").read_bytes() == before_proof
    assert retained_config.is_dir() and not list(retained_config.iterdir())
    after_config = retained_config.stat()
    assert (after_config.st_dev, after_config.st_ino, after_config.st_mtime_ns) == (
        before_config.st_dev,
        before_config.st_ino,
        before_config.st_mtime_ns,
    )
    assert not list(fenced["runtime"].glob("reader-reconcile-docker-*"))
    evidence = Path(result["evidence"])
    assert evidence.exists() and evidence.stat().st_mode & 0o777 == 0o600
    value = json.loads(evidence.read_bytes())
    assert value["proof_rewritten"] is False
    assert value["proof_deleted"] is False
    assert value["attempt_reset"] is False
    assert value["attempts_used"] == 1
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == result["evidence_sha256"]


def test_pending_evidence_is_durable_before_marker_truncate(fenced, monkeypatch):
    events = []
    original_write = r._write_pending
    original_truncate = r.os.ftruncate

    def write_pending(path, value):
        result = original_write(path, value)
        events.append("pending")
        return result

    def truncate(fd, length):
        assert length == 0
        assert events == ["pending"]
        events.append("truncate")
        return original_truncate(fd, length)

    monkeypatch.setattr(r, "_write_pending", write_pending)
    monkeypatch.setattr(r.os, "ftruncate", truncate)
    result = r.reconcile(docker_discover=zero_discovery, quiet_window=quiet)
    assert result["admission_marker_cleared"] is True
    assert events == ["pending", "truncate"]
    assert (fenced["admission"] / "active.marker").read_bytes() == b""


def test_live_lock_refuses_without_marker_change(fenced):
    path = fenced["admission"] / "admission.lock"
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="reader_reconcile_live_lock"):
            r.reconcile(docker_discover=zero_discovery, quiet_window=quiet)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


@pytest.mark.parametrize("kind", ["container", "image"])
def test_owned_resource_refuses_without_marker_clear(fenced, kind):
    def discover(which, config):
        if which == kind:
            return ("d" * 64,) if which == "container" else ("sha256:" + "e" * 64,)
        return ()

    with pytest.raises(ValueError, match="reader_reconcile_resources_present"):
        r.reconcile(docker_discover=discover, quiet_window=quiet)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


def test_daemon_activity_refuses_without_marker_clear(fenced):
    def busy(config):
        raise ValueError("reader_reconcile_daemon_busy")

    with pytest.raises(ValueError, match="reader_reconcile_daemon_busy"):
        r.reconcile(docker_discover=zero_discovery, quiet_window=busy)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


def test_marker_drift_after_quiet_refuses(fenced):
    def mutate(config):
        marker = fenced["admission"] / "active.marker"
        marker.write_bytes(b'{"changed":true}')
        marker.chmod(0o600)
        return quiet(config)

    with pytest.raises(ValueError, match="reader_reconcile_marker_drift"):
        r.reconcile(docker_discover=zero_discovery, quiet_window=mutate)
    assert (fenced["admission"] / "active.marker").read_bytes() != b""


def test_proof_drift_after_quiet_refuses_before_marker_clear(fenced):
    def mutate(config):
        path = fenced["attempt"] / "cases.json"
        value = json.loads(path.read_bytes())
        value["stage"] = "changed"
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)
        return quiet(config)

    with pytest.raises(ValueError):
        r.reconcile(docker_discover=zero_discovery, quiet_window=mutate)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


def test_nonempty_retained_docker_config_refuses(fenced):
    path = fenced["attempt"] / "docker-config/config.json"
    path.write_text("{}")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="reader_reconcile_config_nonempty"):
        r.reconcile(docker_discover=zero_discovery, quiet_window=quiet)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


def test_proof_pin_drift_refuses_before_marker_touch(fenced):
    path = fenced["attempt"] / "cases.json"
    path.write_bytes(b"{}\n")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="reader_reconcile_proof_pin"):
        r.reconcile(docker_discover=zero_discovery, quiet_window=quiet)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


def test_existing_reconciliation_evidence_refuses_without_clear(fenced):
    base = r.RECON_BASE
    base.mkdir(mode=0o700)
    prior = base / "reconcile-prior"
    prior.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="reader_reconcile_attempt_exists"):
        r.reconcile(docker_discover=zero_discovery, quiet_window=quiet)
    assert (fenced["admission"] / "active.marker").read_bytes() == fenced["marker_raw"]


def test_discover_requires_exact_bounded_ids(monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(r, "_command", lambda *a, **k: b"not-an-id\n")
    with pytest.raises(ValueError, match="reader_reconcile_discovery"):
        r._discover("container", config)
    monkeypatch.setattr(r, "_command", lambda *a, **k: ("sha256:" + "a" * 64 + "\n").encode())
    assert r._discover("image", config) == ("sha256:" + "a" * 64,)


def test_quiet_window_refuses_any_daemon_event_without_label_filter(monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    seen = []

    def command(args, config_arg, timeout=6):
        seen.append(tuple(args))
        return b'{"Type":"container","Actor":{"Attributes":{"project":"other"}}}\n'

    monkeypatch.setattr(r, "_command", command)
    with pytest.raises(ValueError, match="reader_reconcile_daemon_busy"):
        r._quiet_window(config)
    assert "--filter" not in seen[0]
    assert "events" in seen[0]


def test_pending_directory_is_fsynced_before_return(tmp_path, monkeypatch):
    target = tmp_path / "evidence"
    target.mkdir(mode=0o700)
    calls = []
    original = r.os.fsync

    def fsync(fd):
        calls.append(r.os.fstat(fd).st_mode)
        return original(fd)

    monkeypatch.setattr(r.os, "fsync", fsync)
    raw, path = r._write_pending(target, {"schema": "test"})
    assert path.read_bytes() == raw
    assert any(stat.S_ISDIR(mode) for mode in calls)


def test_cli_refusal_is_generic(monkeypatch, capsys):
    monkeypatch.setattr(r, "reconcile", lambda: (_ for _ in ()).throw(ValueError("PRIVATE")))
    monkeypatch.setattr(r.os.sys, "argv", ["reconcile", "--run"])
    assert r.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "reader_reconcile_refused\n"
