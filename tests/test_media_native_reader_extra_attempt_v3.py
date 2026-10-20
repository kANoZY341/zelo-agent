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


def _synthetic_record(root: Path) -> dict:
    """Offline validation fixture; no public grant writer is invoked."""
    _, record = v3._history(root)
    record["source_head"], record["source_tree"] = v3._source_identity()
    record["created_utc"] = "2026-10-20T00:00:00Z"
    base = root / v3.BASE
    base.mkdir(mode=0o700)
    digest = _write(base / v3.GRANT, record)
    _write(
        base / v3.SEAL,
        {
            "schema": "zelo-media-native-reader-extra-attempt-v3-seal-v1",
            "grant_sha256": digest,
            "source_head": record["source_head"],
            "source_tree": record["source_tree"],
        },
    )
    return record


def test_four_refusals_and_two_prior_grants_bind_read_only_record(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    record = _synthetic_record(root)
    assert record["attempts_used_before"] == 4
    assert record["attempt_limit_after"] == 5
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
    assert v3.validate(root) == record
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(root, extra_grant=True, recovery_version=3)
    assert len(list((root / scope.PROOF_BASE).iterdir())) == 4


def test_current_qualifier_refuses_four_even_with_synthetic_record(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    _synthetic_record(root)
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
    _synthetic_record(root)
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
    monkeypatch.setattr(v3, "grant_once", lambda *a, **k: pytest.fail("grant attempted"))
    assert grant_cli.main() == 1
    assert not list(root.iterdir())


def test_standalone_v3_cli_cannot_create_grant_from_fixed_phrase(tmp_path, monkeypatch, capsys):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(grant_cli, "_observe", lambda _: calls.append("observed"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grant_media_native_reader_extra_attempt_v3.py",
            "--grant-once",
            "--runtime-root",
            str(root),
            "--source-head",
            "e" * 40,
            "--source-tree",
            "f" * 40,
            "--operator-confirmation",
            grant_cli.CONFIRMATION,
        ],
    )
    assert grant_cli.main() == 1
    assert "approval_unavailable" in capsys.readouterr().err
    assert calls == []
    assert not (root / v3.BASE).exists()
    assert len(list((root / scope.PROOF_BASE).iterdir())) == 4


def test_direct_v3_grant_refuses_without_approval(tmp_path, monkeypatch):
    root = four_attempt_runtime(tmp_path, monkeypatch)
    with pytest.raises(v3.RecoveryRefused, match="approval_unavailable"):
        v3.grant_once(
            root,
            lambda _: pytest.fail("observed without approval"),
            expected_source=("e" * 40, "f" * 40),
        )
    assert not (root / v3.BASE).exists()
    assert len(list((root / scope.PROOF_BASE).iterdir())) == 4
