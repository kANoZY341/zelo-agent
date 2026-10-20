"""Offline checks for the inert proposed Media fifth-attempt ledger."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from qualification import media_reader_extra_attempt_v2 as v2
from qualification import media_reader_extra_attempt_v3 as v3
from qualification import media_reader_scope as scope
from scripts import grant_media_native_reader_extra_attempt_v3 as grant_cli
from scripts import qualify_media_native_reader as reader
from tests.test_media_native_reader_extra_attempt_v2 import three_attempt_runtime


def _write(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def four_attempt_runtime(tmp_path, monkeypatch) -> Path:
    root = three_attempt_runtime(tmp_path, monkeypatch)
    v2.grant_once(root, lambda _: None, expected_source=("c" * 40, "d" * 40))
    prior_raw = (root / v2.BASE / v2.GRANT).read_bytes()
    monkeypatch.setattr(v3, "V2_GRANT_SHA256", hashlib.sha256(prior_raw).hexdigest())
    monkeypatch.setattr(v3, "FOURTH_SOURCE_HEAD", "c" * 40)
    monkeypatch.setattr(v3, "FOURTH_SOURCE_TREE", "d" * 40)
    fourth = root / scope.PROOF_BASE / v3.FOURTH
    fourth.mkdir(mode=0o700)
    policy = {
        "schema": "zelo-media-native-reader-synthetic-policy-v1",
        "attempt_limit": 4,
        "network": "none",
        "runtime_authority": False,
    }
    proof = {
        "status": "refused",
        "stage": "build",
        "runtime_authority": False,
        "support_release_confirmed": False,
        "build_failure": {
            "operation": "build",
            "kind": "docker_command_exit",
            "exit_code": 2,
            "overflowed_streams": ["err"],
            "output_category": "unclassified",
        },
    }
    monkeypatch.setattr(v3, "FOURTH_POLICY_SHA256", _write(fourth / "policy.json", policy))
    monkeypatch.setattr(v3, "FOURTH_PROOF_SHA256", _write(fourth / "cases.json", proof))
    monkeypatch.setattr(v3, "SCOPE_SHA256", v2.SCOPE_SHA256)
    monkeypatch.setattr(v3, "RECONCILIATION_SHA256", v2.RECONCILIATION_SHA256)
    monkeypatch.setattr(v2, "_source_identity", lambda: ("e" * 40, "f" * 40))
    return root


def test_four_exact_refusals_and_two_prior_grants_bind_one_inert_record(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    base = root / scope.PROOF_BASE
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for attempt in base.iterdir()
        for path in attempt.glob("*.json")
    }
    observed = []
    record = v3.grant_once(
        root, lambda path: observed.append(path), expected_source=("e" * 40, "f" * 40)
    )
    assert observed == [root]
    assert record["attempts_used_before"] == 4
    assert record["attempt_limit_after"] == 5
    assert record["additional_attempts"] == 1
    assert [entry["attempt"] for entry in record["proof_history"]] == [
        "native-reader-proof-uvjet91z",
        "native-reader-proof-672ubxw7",
        v2.THIRD,
        v3.FOURTH,
    ]
    assert record["existing_grant_v2_sha256"] == v3.V2_GRANT_SHA256
    assert record["source_head"] == "e" * 40
    assert record["source_tree"] == "f" * 40
    assert record["runtime_authority"] is False
    assert record["attempt_reset"] is False
    assert record["network"] == "none"
    assert record["pull"] is False
    assert v3.validate(root) == record
    assert before == {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before}
    # This proposal does not change the current qualifier's attempt ceiling.
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(root, extra_grant=True, recovery_version=3)
    assert len(list(base.iterdir())) == 4


def test_current_qualifier_refuses_four_even_with_valid_proposed_record(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    v3.grant_once(root, lambda _: None, expected_source=("e" * 40, "f" * 40))
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: pytest.fail("reached Docker"))
    with pytest.raises(
        ValueError, match="reader_native_precondition_scope_reader_native_scope_attempt_count"
    ):
        asyncio.run(reader._qualify(root))
    assert len(list((root / scope.PROOF_BASE).iterdir())) == 4


@pytest.mark.parametrize(
    "corruption", ["fourth_proof", "v2_grant", "limit", "source", "seal", "extra_attempt"]
)
def test_v3_refuses_changed_history_or_record(tmp_path, monkeypatch, corruption):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    v3.grant_once(root, lambda _: None, expected_source=("e" * 40, "f" * 40))
    target = root / v3.BASE / v3.GRANT
    if corruption == "fourth_proof":
        (root / scope.PROOF_BASE / v3.FOURTH / "cases.json").write_bytes(b"{}")
    elif corruption == "v2_grant":
        (root / v2.BASE / v2.GRANT).write_bytes(b"{}")
    elif corruption == "seal":
        _write(root / v3.BASE / v3.SEAL, {"schema": "wrong"})
    elif corruption == "extra_attempt":
        (root / scope.PROOF_BASE / "native-reader-proof-unapproved").mkdir(mode=0o700)
    else:
        data = json.loads(target.read_bytes())
        data["attempt_limit_after" if corruption == "limit" else "source_head"] = (
            6 if corruption == "limit" else "a" * 40
        )
        _write(target, data)
    with pytest.raises(ValueError):
        v3.validate(root)
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(root, extra_grant=True, recovery_version=3)


def test_v3_source_mismatch_and_repeat_record_refuse_before_observation(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    with pytest.raises(v3.RecoveryRefused, match="source_approval"):
        v3.grant_once(
            root,
            lambda _: pytest.fail("observed before source binding"),
            expected_source=("a" * 40, "f" * 40),
        )
    assert not (root / v3.BASE).exists()
    v3.grant_once(root, lambda _: None, expected_source=("e" * 40, "f" * 40))
    with pytest.raises(v3.RecoveryRefused, match="already_granted"):
        v3.grant_once(
            root,
            lambda _: pytest.fail("repeat observation"),
            expected_source=("e" * 40, "f" * 40),
        )


def test_v3_observation_drift_refuses_without_record(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)

    def corrupt(_: Path) -> None:
        _write(root / scope.PROOF_BASE / v3.FOURTH / "cases.json", {})

    with pytest.raises(v3.RecoveryRefused, match="fourth_pin"):
        v3.grant_once(root, corrupt, expected_source=("e" * 40, "f" * 40))
    assert not (root / v3.BASE).exists()


@pytest.mark.parametrize("failed_fsync_call", [1, 4, 5])
def test_v3_sync_failure_cannot_allocate_or_retry_as_fresh(
    tmp_path, monkeypatch, failed_fsync_call
):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    original_fsync = v3.os.fsync
    calls = 0

    def fail_one(fd):
        nonlocal calls
        calls += 1
        if calls == failed_fsync_call:
            raise OSError("injected sync failure")
        return original_fsync(fd)

    monkeypatch.setattr(v3.os, "fsync", fail_one)
    with pytest.raises(OSError, match="injected sync failure"):
        v3.grant_once(root, lambda _: None, expected_source=("e" * 40, "f" * 40))
    assert (root / v3.BASE / v3.GRANT).exists()
    assert not (root / v3.BASE / v3.SEAL).exists()
    with pytest.raises(v3.RecoveryRefused, match="grant_inventory"):
        v3.validate(root)
    with pytest.raises(v3.RecoveryRefused, match="already_granted"):
        v3.grant_once(
            root, lambda _: pytest.fail("retry observation"), expected_source=("e" * 40, "f" * 40)
        )
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(root, extra_grant=True, recovery_version=3)


def test_v3_observer_uses_exact_docker_env_and_only_owned_resource_queries(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    calls = []

    def docker(argv, environment, timeout=20, *, stdout_limit=131072):
        calls.append(tuple(argv))
        assert stdout_limit == 0
        assert environment == {
            "DOCKER_CONFIG": environment["DOCKER_CONFIG"],
            "HOME": "/nonexistent",
            "LANG": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        }
        assert environment["DOCKER_CONFIG"].startswith(str(root))
        assert "label=org.zelo.media-reader.lease" in argv
        return b""

    monkeypatch.setattr(grant_cli.reader, "_docker", docker)
    monkeypatch.setattr(
        grant_cli.reader, "_docker_readiness", lambda path: calls.append(("readiness", str(path)))
    )
    grant_cli._observe(root)
    assert len(calls) == 5
    assert calls[2] == ("readiness", str(root))
    assert not list(root.iterdir())


def test_v3_observer_refuses_owned_resources_before_readiness(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setattr(grant_cli.reader, "_docker", lambda *args, **kwargs: b"owned\n")
    monkeypatch.setattr(
        grant_cli.reader, "_docker_readiness", lambda _: pytest.fail("reached readiness")
    )
    with pytest.raises(ValueError, match="reader_native_extra_v3_resources"):
        grant_cli._observe(root)
    assert not list(root.iterdir())


def test_v3_cli_requires_confirmation_before_observation(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grant_media_native_reader_extra_attempt_v3.py",
            "--grant-once",
            "--runtime-root",
            str(root),
            "--source-head",
            "a" * 40,
            "--source-tree",
            "b" * 40,
            "--operator-confirmation",
            "wrong",
        ],
    )
    monkeypatch.setattr(
        grant_cli.extra, "grant_once", lambda *a, **k: pytest.fail("grant attempted")
    )
    assert grant_cli.main() == 1
    assert not list(root.iterdir())


def test_uncertain_seal_cleanup_remains_inert_before_docker(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    original_fsync = v3.os.fsync
    original_unlink = v3.os.unlink
    calls = 0

    def fail_seal_directory_sync(fd):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("injected seal directory sync failure")
        return original_fsync(fd)

    def fail_seal_cleanup(path, *args, **kwargs):
        if Path(path) == root / v3.BASE / v3.SEAL:
            raise OSError("injected seal cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(v3.os, "fsync", fail_seal_directory_sync)
    monkeypatch.setattr(v3.os, "unlink", fail_seal_cleanup)
    with pytest.raises(OSError, match="seal directory sync failure"):
        v3.grant_once(root, lambda _: None, expected_source=("e" * 40, "f" * 40))
    assert (root / v3.BASE / v3.SEAL).exists()
    # Readable ledger bytes are not evidence that the grant returned successfully.
    assert v3.validate(root)["attempts_used_before"] == 4
    monkeypatch.setattr(reader, "_docker_readiness", lambda _: pytest.fail("reached Docker"))
    with pytest.raises(
        ValueError, match="reader_native_precondition_scope_reader_native_scope_attempt_count"
    ):
        asyncio.run(reader._qualify(root))
    assert len(list((root / scope.PROOF_BASE).iterdir())) == 4
