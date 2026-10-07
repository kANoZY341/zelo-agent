"""Offline tests for the exact one-use v2 native-reader continuation."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from qualification import media_reader_extra_attempt as v1
from qualification import media_reader_extra_attempt_v2 as extra_v2
from qualification import media_reader_scope as scope
from scripts import grant_media_native_reader_extra_attempt_v2 as grant_cli
from scripts import qualify_media_native_reader as reader
from tests.test_media_native_reader_extra_attempt import two_attempt_runtime


def _write(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)


def three_attempt_runtime(tmp_path, monkeypatch) -> Path:
    runtime = two_attempt_runtime(tmp_path, monkeypatch)
    third = runtime / scope.PROOF_BASE / extra_v2.THIRD
    third.mkdir(mode=0o700)
    policy = {
        "schema": "zelo-media-native-reader-synthetic-policy-v1",
        "attempt_limit": 3,
        "network": "none",
        "read_only_rootfs": True,
        "runtime_authority": False,
    }
    proof = {
        "runtime_authority": False,
        "stage": "build",
        "status": "refused",
        "support_release_confirmed": False,
    }
    policy_raw = json.dumps(policy, sort_keys=True).encode()
    proof_raw = json.dumps(proof, sort_keys=True).encode()
    _write(third / "policy.json", policy_raw)
    _write(third / "cases.json", proof_raw)
    monkeypatch.setattr(extra_v2, "THIRD_POLICY_SHA256", hashlib.sha256(policy_raw).hexdigest())
    monkeypatch.setattr(extra_v2, "THIRD_PROOF_SHA256", hashlib.sha256(proof_raw).hexdigest())

    v1 = {
        "schema": "zelo-media-native-reader-extra-attempt-v1",
        "attempts_used_before": 2,
        "attempt_limit_after": 3,
        "additional_attempts": 1,
        "attempt_reset": False,
        "proof_rewritten": False,
        "proof_deleted": False,
        "runtime_authority": False,
        "source_head": extra_v2.THIRD_SOURCE_HEAD,
        "source_tree": extra_v2.THIRD_SOURCE_TREE,
    }
    v1_raw = (json.dumps(v1, sort_keys=True, indent=2) + "\n").encode()
    v1_dir = runtime / extra_v2.V1_BASE
    v1_dir.mkdir(mode=0o700)
    _write(v1_dir / "grant.json", v1_raw)
    monkeypatch.setattr(extra_v2, "V1_GRANT_SHA256", hashlib.sha256(v1_raw).hexdigest())

    scope_raw = (runtime / scope.SCOPE_FILENAME).read_bytes()
    monkeypatch.setattr(extra_v2, "SCOPE_SHA256", hashlib.sha256(scope_raw).hexdigest())
    _, pending_raw, _ = scope._pending_evidence(runtime)
    monkeypatch.setattr(extra_v2, "RECONCILIATION_SHA256", hashlib.sha256(pending_raw).hexdigest())
    monkeypatch.setattr(extra_v2, "_source_identity", lambda: ("c" * 40, "d" * 40))
    return runtime


def test_v2_grant_binds_three_refusals_v1_grant_and_exactly_one_additional_attempt(
    tmp_path, monkeypatch
):
    runtime = three_attempt_runtime(tmp_path, monkeypatch)
    proof_base = runtime / scope.PROOF_BASE
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for attempt in proof_base.iterdir()
        for path in attempt.glob("*.json")
    }
    observed = []
    grant = extra_v2.grant_once(
        runtime, lambda root: observed.append(root), expected_source=("c" * 40, "d" * 40)
    )

    assert observed == [runtime]
    assert grant["attempts_used_before"] == 3
    assert grant["attempt_limit_after"] == 4
    assert grant["additional_attempts"] == 1
    assert grant["proof_history"] == [
        {
            "attempt": "native-reader-proof-uvjet91z",
            "policy_sha256": "a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3",
            "proof_sha256": "027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378",
            "source_head": "c6127669cda7923c851febe1b4f8530402f58ef8",
            "source_tree": "d6eaa8a1fa029cd049384f66d27de5218ba7c8cd",
        },
        {
            "attempt": "native-reader-proof-672ubxw7",
            "policy_sha256": v1.SECOND_POLICY_SHA256,
            "proof_sha256": v1.SECOND_PROOF_SHA256,
            "source_head": extra_v2.SECOND_SOURCE_HEAD,
            "source_tree": extra_v2.SECOND_SOURCE_TREE,
        },
        {
            "attempt": extra_v2.THIRD,
            "policy_sha256": extra_v2.THIRD_POLICY_SHA256,
            "proof_sha256": extra_v2.THIRD_PROOF_SHA256,
            "source_head": extra_v2.THIRD_SOURCE_HEAD,
            "source_tree": extra_v2.THIRD_SOURCE_TREE,
        },
    ]
    assert grant["existing_grant_sha256"] == extra_v2.V1_GRANT_SHA256
    assert grant["source_head"] == "c" * 40
    assert grant["source_tree"] == "d" * 40
    assert grant["attempt_reset"] is False
    assert grant["proof_rewritten"] is False
    assert grant["proof_deleted"] is False
    assert grant["runtime_authority"] is False
    assert grant["network"] == "none"
    assert grant["pull"] is False
    assert grant["whole_seconds"] == 240
    assert grant["case_seconds"] == 20
    assert grant["build_seconds"] == 90
    assert grant["read_only_rootfs"] is True
    assert grant["cleanup_scope"] == "exact-new-lease-labeled-resources-only"
    assert grant["global_prune"] is False
    assert extra_v2.validate(runtime) == grant
    assert before == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in before}

    fourth = reader._attempt_directory(runtime, extra_grant=True, recovery_version=2)
    assert fourth.is_dir()
    assert len(list(proof_base.iterdir())) == 4
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(runtime, extra_grant=True, recovery_version=2)


def test_three_refusals_do_not_allocate_without_v2_grant(tmp_path, monkeypatch):
    runtime = three_attempt_runtime(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reader_native_attempt_limit"):
        reader._attempt_directory(runtime)
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 3
    assert not (runtime / extra_v2.BASE).exists()


@pytest.mark.parametrize("corruption", ["limit", "reset", "source", "third_proof", "v1_grant"])
def test_v2_tampering_refuses_without_allocating_fourth_attempt(tmp_path, monkeypatch, corruption):
    runtime = three_attempt_runtime(tmp_path, monkeypatch)
    extra_v2.grant_once(runtime, lambda root: None, expected_source=("c" * 40, "d" * 40))
    target = runtime / extra_v2.BASE / extra_v2.GRANT
    if corruption in {"limit", "reset", "source"}:
        record = json.loads(target.read_text())
        if corruption == "limit":
            record["attempt_limit_after"] = 5
        elif corruption == "reset":
            record["attempt_reset"] = True
        else:
            record["source_head"] = "e" * 40
        _write(target, json.dumps(record, sort_keys=True).encode())
    elif corruption == "third_proof":
        _write(
            runtime / scope.PROOF_BASE / extra_v2.THIRD / "cases.json",
            b"{}",
        )
    else:
        _write(runtime / extra_v2.V1_BASE / "grant.json", b"{}")

    with pytest.raises(ValueError, match="reader_native_extra_v2_"):
        extra_v2.validate(runtime)
    with pytest.raises(ValueError, match="reader_native_extra_v2_"):
        reader._attempt_directory(runtime, extra_grant=True, recovery_version=2)
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 3


def test_v2_qualification_gate_stops_at_readiness_before_admission_or_attempt(
    tmp_path, monkeypatch
):
    runtime = three_attempt_runtime(tmp_path, monkeypatch)
    extra_v2.grant_once(runtime, lambda root: None, expected_source=("c" * 40, "d" * 40))
    reached = []

    def readiness(root):
        reached.append(root)
        raise ValueError("reader_native_readiness_identity")

    class ForbiddenAdmission:
        def __init__(self, *args, **kwargs):
            pytest.fail("v2 readiness refusal reached admission")

    monkeypatch.setattr(reader, "_docker_readiness", readiness)
    monkeypatch.setattr(reader, "AdmissionController", ForbiddenAdmission)

    with pytest.raises(
        ValueError,
        match="reader_native_precondition_docker_readiness_reader_native_readiness_identity",
    ):
        asyncio.run(reader._qualify(runtime))

    assert reached == [runtime]
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 3
    assert not list((runtime / extra_v2.BASE).glob("native-reader-proof-*"))


def test_v2_grant_requires_explicit_exact_source_binding(tmp_path, monkeypatch):
    runtime = three_attempt_runtime(tmp_path, monkeypatch)
    with pytest.raises(extra_v2.RecoveryRefused, match="reader_native_extra_v2_source_approval"):
        extra_v2.grant_once(
            runtime,
            lambda root: pytest.fail("source mismatch reached resource observation"),
            expected_source=("e" * 40, "d" * 40),
        )
    assert not (runtime / extra_v2.BASE).exists()
    assert len(list((runtime / scope.PROOF_BASE).iterdir())) == 3


def test_v2_grant_observer_uses_bounded_read_only_owned_resource_queries(tmp_path, monkeypatch):
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


def test_v2_grant_observer_refuses_existing_owned_resources(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(grant_cli.reader, "_docker", lambda *a, **k: b"owned\n")
    monkeypatch.setattr(
        grant_cli.reader,
        "_docker_readiness",
        lambda root: pytest.fail("resource collision reached readiness"),
    )

    with pytest.raises(ValueError, match="reader_native_extra_v2_resources"):
        grant_cli._observe(runtime)
    assert not list(runtime.iterdir())
