import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from qualification import media_reader_reconcile as reconcile
from qualification import media_reader_scope as scope
from scripts import qualify_media_native_reader as operator

REPO = Path(__file__).resolve().parents[1]


def make_runtime(runtime_root: Path) -> dict:
    runtime_root.mkdir(parents=True, mode=0o755)
    proof = runtime_root / scope.PROOF_BASE
    proof.mkdir(mode=0o700)
    attempt = proof / reconcile.ATTEMPT_NAME
    attempt.mkdir(mode=0o700)
    config = attempt / "docker-config"
    config.mkdir(mode=0o700)
    for fixture, name in (
        (reconcile.FIXTURE_POLICY, "policy.json"),
        (reconcile.FIXTURE_CASES, "cases.json"),
    ):
        target = attempt / name
        target.write_bytes((REPO / fixture).read_bytes())
        target.chmod(0o600)

    recon = runtime_root / scope.RECON_BASE
    recon.mkdir(mode=0o700)
    evidence_dir = recon / scope.RECONCILIATION_DIRECTORY
    evidence_dir.mkdir(mode=0o700)
    evidence = {
        "schema": "zelo-media-native-reader-reconciliation-v1",
        "status": "pending-marker-clear",
        "failed_source_head": reconcile.REFUSAL_HEAD,
        "failed_source_tree": reconcile.REFUSAL_TREE,
        "failed_attempt": reconcile.ATTEMPT_NAME,
        "failed_lease": reconcile.LEASE_IDENTITY,
        "policy_sha256": reconcile.POLICY_SHA256,
        "proof_sha256": reconcile.CASES_SHA256,
        "marker_sha256": reconcile.MARKER_SHA256,
        "docker_container_count_before": 0,
        "docker_image_count_before": 0,
        "docker_container_count_after": 0,
        "docker_image_count_after": 0,
        "quiet_start": "2026-10-05T22:23:49Z",
        "quiet_end": "2026-10-05T22:23:51Z",
        "quiet_seconds": 2,
        "attempts_used": 1,
        "attempt_limit": 2,
        "proof_rewritten": False,
        "proof_deleted": False,
        "attempt_reset": False,
        "runtime_authority": False,
    }
    pending = evidence_dir / "pending.json"
    pending_raw = (json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    assert hashlib.sha256(pending_raw).hexdigest() == scope.RECONCILIATION_SHA256
    pending.write_bytes(pending_raw)
    pending.chmod(0o600)

    admission = runtime_root / scope.ADMISSION_BASE
    admission.mkdir(mode=0o700)
    for name in ("admission.lock", "active.marker"):
        item = admission / name
        item.write_bytes(b"")
        item.chmod(0o600)
    return evidence


def test_one_explicit_runtime_scope_is_shared_across_old_and_new_worktrees(tmp_path):
    old_checkout = tmp_path / "old-checkout"
    new_checkout = tmp_path / "new-checkout"
    old_checkout.mkdir()
    new_checkout.mkdir()
    runtime_root = old_checkout / "runtime"
    make_runtime(runtime_root)

    record = scope.bind_runtime_scope(runtime_root)
    assert record["runtime_root"] == str(runtime_root.resolve())
    assert scope.validate_runtime_scope(runtime_root) == record
    assert not (new_checkout / "runtime").exists()

    original_policy = runtime_root / scope.PROOF_BASE / reconcile.ATTEMPT_NAME / "policy.json"
    original_proof = runtime_root / scope.PROOF_BASE / reconcile.ATTEMPT_NAME / "cases.json"
    hashes_before = (
        hashlib.sha256(original_policy.read_bytes()).hexdigest(),
        hashlib.sha256(original_proof.read_bytes()).hexdigest(),
    )

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(operator, "ROOT", old_checkout)
        second_attempt = operator._attempt_directory(runtime_root)
        assert second_attempt.parent == runtime_root / scope.PROOF_BASE
        monkeypatch.setattr(operator, "ROOT", new_checkout)
        with pytest.raises(ValueError, match="reader_native_attempt_limit"):
            operator._attempt_directory(runtime_root)
    finally:
        monkeypatch.undo()

    assert hashes_before == (
        hashlib.sha256(original_policy.read_bytes()).hexdigest(),
        hashlib.sha256(original_proof.read_bytes()).hexdigest(),
    )


def test_missing_history_refuses_before_readiness_or_attempt_creation(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("missing scope reached readiness or admission")

    monkeypatch.setattr(operator.scope, "validate_runtime_scope", scope.validate_runtime_scope)
    monkeypatch.setattr(operator, "_docker_readiness", forbidden)
    monkeypatch.setattr(operator, "_attempt_directory", forbidden)
    with pytest.raises(ValueError, match="reader_native_scope_"):
        asyncio.run(operator._qualify(runtime_root))
    assert not (runtime_root / scope.PROOF_BASE).exists()
    assert not (runtime_root / scope.ADMISSION_BASE).exists()


def test_binding_refuses_missing_history_without_creating_scope_file(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    with pytest.raises(ValueError, match="reader_native_scope_"):
        scope.bind_runtime_scope(runtime_root)
    assert not (runtime_root / scope.SCOPE_FILENAME).exists()


@pytest.mark.parametrize(
    "corruption",
    [
        "proof",
        "reconciliation",
        "missing_reconciliation",
        "duplicate_reconciliation",
        "typed_attempt_count",
        "marker",
    ],
)
def test_scope_refuses_mismatched_history_before_attempt_creation(
    tmp_path, monkeypatch, corruption
):
    runtime_root = tmp_path / "runtime"
    make_runtime(runtime_root)
    scope.bind_runtime_scope(runtime_root)
    if corruption == "proof":
        proof = runtime_root / scope.PROOF_BASE / reconcile.ATTEMPT_NAME / "cases.json"
        proof.write_bytes(b"{}")
        proof.chmod(0o600)
    elif corruption == "reconciliation":
        pending = next((runtime_root / scope.RECON_BASE).glob("*/pending.json"))
        value = json.loads(pending.read_text())
        value["attempt_reset"] = True
        pending.write_text(json.dumps(value, sort_keys=True))
        pending.chmod(0o600)
    elif corruption == "missing_reconciliation":
        next((runtime_root / scope.RECON_BASE).glob("*/pending.json")).unlink()
    elif corruption == "duplicate_reconciliation":
        pending = next((runtime_root / scope.RECON_BASE).glob("*/pending.json"))
        pending.write_bytes(pending.read_bytes()[:-1] + b', "attempt_reset": false}')
        pending.chmod(0o600)
    elif corruption == "typed_attempt_count":
        pending = next((runtime_root / scope.RECON_BASE).glob("*/pending.json"))
        value = json.loads(pending.read_text())
        value["attempts_used"] = True
        pending.write_text(json.dumps(value, sort_keys=True))
        pending.chmod(0o600)
    else:
        marker = runtime_root / scope.ADMISSION_BASE / "active.marker"
        marker.write_text("{}")
        marker.chmod(0o600)

    def forbidden(*args, **kwargs):
        pytest.fail("mismatched scope reached readiness or attempt creation")

    monkeypatch.setattr(operator, "_docker_readiness", forbidden)
    monkeypatch.setattr(operator, "_attempt_directory", forbidden)
    with pytest.raises(ValueError, match="reader_native_scope_"):
        asyncio.run(operator._qualify(runtime_root))


def test_scope_binding_rejects_copy_to_a_different_worktree_path(tmp_path):
    runtime_root = tmp_path / "old-checkout" / "runtime"
    make_runtime(runtime_root)
    scope.bind_runtime_scope(runtime_root)
    copied = tmp_path / "new-checkout" / "runtime"
    shutil.copytree(runtime_root, copied)
    with pytest.raises(ValueError, match="reader_native_scope_binding_mismatch"):
        scope.validate_runtime_scope(copied)


def test_scope_binding_is_one_shot_and_does_not_overwrite(tmp_path):
    runtime_root = tmp_path / "runtime"
    make_runtime(runtime_root)
    record = scope.bind_runtime_scope(runtime_root)
    path = runtime_root / scope.SCOPE_FILENAME
    before = path.read_bytes()
    with pytest.raises(ValueError, match="reader_native_scope_already_bound"):
        scope.bind_runtime_scope(runtime_root)
    assert path.read_bytes() == before
    assert scope.validate_runtime_scope(runtime_root) == record


def test_second_attempt_record_blocks_replay_before_operator_readiness(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    make_runtime(runtime_root)
    scope.bind_runtime_scope(runtime_root)
    second = runtime_root / scope.PROOF_BASE / "native-reader-proof-second"
    second.mkdir(mode=0o700)

    def forbidden(*args, **kwargs):
        pytest.fail("replayed scope reached readiness")

    monkeypatch.setattr(operator, "_docker_readiness", forbidden)
    with pytest.raises(ValueError, match="reader_native_scope_attempt_count"):
        asyncio.run(operator._qualify(runtime_root))
    assert second.is_dir()
    assert len(list((runtime_root / scope.PROOF_BASE).iterdir())) == 2


def test_scope_refuses_a_held_admission_lock(tmp_path):
    import fcntl
    import os

    runtime_root = tmp_path / "runtime"
    make_runtime(runtime_root)
    scope.bind_runtime_scope(runtime_root)
    lock = os.open(runtime_root / scope.ADMISSION_BASE / "admission.lock", os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="reader_native_scope_live_lock"):
            scope.validate_runtime_scope(runtime_root)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def test_attempt_directory_does_not_create_missing_history(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    missing = runtime_root / scope.PROOF_BASE
    with pytest.raises(ValueError, match="reader_native_scope_path"):
        operator._attempt_directory(runtime_root)
    assert not missing.exists()


def test_scope_json_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="reader_native_scope_json"):
        scope._json(b'{"attempts_used":1,"attempts_used":1}')


def test_operator_requires_explicit_runtime_root_before_readiness(monkeypatch):
    monkeypatch.setattr(
        operator, "_docker_readiness", lambda *args: pytest.fail("readiness ran without scope")
    )
    with pytest.raises(ValueError, match="reader_native_scope_required"):
        asyncio.run(operator._qualify())


def test_scope_binding_rejects_symlink_runtime_root(tmp_path):
    runtime_root = tmp_path / "real-runtime"
    make_runtime(runtime_root)
    alias = tmp_path / "runtime-alias"
    alias.symlink_to(runtime_root, target_is_directory=True)

    with pytest.raises(ValueError, match="reader_native_scope_path"):
        scope.bind_runtime_scope(alias)
    assert not (runtime_root / scope.SCOPE_FILENAME).exists()


def test_scope_rejects_symlinked_original_proof(tmp_path):
    runtime_root = tmp_path / "runtime"
    make_runtime(runtime_root)
    scope.bind_runtime_scope(runtime_root)

    attempt = runtime_root / scope.PROOF_BASE / reconcile.ATTEMPT_NAME
    proof = attempt / "cases.json"
    external = tmp_path / "external-proof.json"
    proof.rename(external)
    proof.symlink_to(external)

    with pytest.raises(ValueError, match="reader_native_scope_file"):
        scope.validate_runtime_scope(runtime_root)


def test_scope_rejects_symlinked_binding_manifest(tmp_path):
    runtime_root = tmp_path / "runtime"
    make_runtime(runtime_root)
    scope.bind_runtime_scope(runtime_root)

    manifest = runtime_root / scope.SCOPE_FILENAME
    external = tmp_path / "external-scope.json"
    external.write_bytes(manifest.read_bytes())
    external.chmod(0o600)
    manifest.unlink()
    manifest.symlink_to(external)

    with pytest.raises(ValueError, match="reader_native_scope_file"):
        scope.validate_runtime_scope(runtime_root)
