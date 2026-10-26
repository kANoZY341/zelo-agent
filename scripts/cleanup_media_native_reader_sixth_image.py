#!/usr/bin/env python3
"""One-use exact sixth-image cleanup; preserves all six refused proofs.

Running this requires a separate operator decision. Inventory is read-only.
Neither action grants another attempt or changes the admission marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from qualification import media_reader_sixth_ledger as history
from scripts import cleanup_media_native_reader_fifth_image as records
from scripts import cleanup_media_native_reader_residual_images as docker
from scripts import reconcile_media_native_reader as reconcile
from scripts import reconcile_media_native_reader_fifth as fence

ROOT = history.RETAINED_ROOT
BASE = "media-native-reader-sixth-image-cleanup"
ATTEMPT = "native-reader-proof-60_k7n6n"
IMAGE_ID = "sha256:8276938065123be889a9f34653240166589dd6463c695ad078d066deea1c0660"
LEASE = "d14819f8a26b4c04140345560d4a230b0765096359b7dfc9adca1e1c96201999"
SOURCE_HEAD = "1e5b871724bce036deee173ad1a883ef7fc100fd"
SOURCE_TREE = "53c8af68e602f9b90fbdac737caeead8732064c5"
IMAGE_INSPECT_SHA256 = "26ab301eb1cf2f9495463d529938c2f93cf89fbf599dd334b9c54222739db30a"
OLD_RESIDUAL_RECEIPT = "cleanup-7hk_lak9"
PINS = {
    f"media-native-reader/{ATTEMPT}/cases.json": (
        "96a54922cac16c3aedc3f2af280aa316275956990fba73c0d58c2f6daed07e10"
    ),
    f"media-native-reader/{ATTEMPT}/policy.json": (
        "a4b44bfc87c1cdb2e4991b1655a4c93f756df90ce74192471550cc09348413c3"
    ),
    "media-native-reader-run-approval-v4/receipt.json": (
        "b481465d548bbfcaabeb8eae32d193a3520ecb99ea5f15cfa055a7b8a3caeeeb"
    ),
    "media-native-reader-run-approval-v4/recheck.json": (
        "cef5e7c91263106e6b47f380c15b7ebd2a682d139a1f425227637ae7bd43c8db"
    ),
    "media-native-reader-run-approval-v4/attempt-recheck.json": (
        "5f570d5cdeba63a2f881c9bbca3dbafdcd8b78978824dfe58fdf73b82c499795"
    ),
    "media-native-reader-extra-attempt-v4/grant.json": (
        "887c9a142723e494bf088cb9fdcad538f265a17967663b93a55401a2f2c06568"
    ),
    "media-native-reader-extra-attempt-v4/seal.json": (
        "1119b1e0b6d357c6bd82c3c9090a1e1e562a825f311324cb2ae9803337e43e8f"
    ),
}


class SixthImageCleanupRefused(ValueError):
    """The exact sixth refusal, image or one-use fence changed."""


def _refuse(code: str) -> None:
    raise SixthImageCleanupRefused("reader_sixth_image_cleanup_" + code) from None


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _audit(root: Path) -> dict[str, str]:
    """Read the pinned six-refusal history while the caller owns the shared lock."""
    if root != ROOT or scope._canonical_root(root) != root:
        _refuse("root")
    history._marker(root / "media-elf-metadata-admission/active.marker", b"")
    fifth._children(
        root / "media-native-reader",
        {
            history.first.ATTEMPT_NAME,
            history.v1.SECOND,
            history.v2.THIRD,
            history.v3.FOURTH,
            fifth.FIFTH,
            ATTEMPT,
        },
    )
    fifth._children(root / "media-native-reader" / ATTEMPT, {"cases.json", "policy.json"})
    fifth._children(
        root / "media-native-reader-run-approval-v4",
        {"receipt.json", "recheck.json", "attempt-recheck.json"},
    )
    fifth._children(root / "media-native-reader-extra-attempt-v4", {"grant.json", "seal.json"})
    for relative, pin in history.FILE_PINS.items():
        fifth._read_pinned(root / relative, *pin)
    values = {
        relative: fifth._read_pinned(root / relative, digest, 0o600, 65536)
        for relative, digest in PINS.items()
    }
    case = scope._json(values[f"media-native-reader/{ATTEMPT}/cases.json"])
    policy = scope._json(values[f"media-native-reader/{ATTEMPT}/policy.json"])
    grant = scope._json(values["media-native-reader-extra-attempt-v4/grant.json"])
    seal = scope._json(values["media-native-reader-extra-attempt-v4/seal.json"])
    if (
        case
        != {
            "status": "refused",
            "stage": "matrix",
            "support_release_confirmed": False,
            "runtime_authority": False,
        }
        or policy.get("attempt_limit") != 6
        or policy.get("runtime_authority") is not False
        or grant.get("status") != "one-additional-attempt-granted"
        or grant.get("approval_comment_id") != 6418185792
        or grant.get("attempts_used_before") != 5
        or grant.get("attempt_limit_after") != 6
        or grant.get("source_head") != SOURCE_HEAD
        or grant.get("source_tree") != SOURCE_TREE
        or grant.get("runtime_authority") is not False
        or seal.get("grant_sha256") != PINS["media-native-reader-extra-attempt-v4/grant.json"]
    ):
        _refuse("sixth_binding")
    residual = root / docker.BASE
    fifth._children(residual, {OLD_RESIDUAL_RECEIPT})
    evidence = residual / OLD_RESIDUAL_RECEIPT
    fifth._children(
        evidence,
        {"pending.json", "completed.json"} | {f"removed-{index:02}.json" for index in range(1, 9)},
    )
    completion, completion_raw = records._record(evidence / "completed.json")
    pending, pending_raw = records._record(evidence / "pending.json")
    steps = [records._record(evidence / f"removed-{index:02}.json") for index in range(1, 9)]
    if (
        _digest(completion_raw)
        != "8795cf71ffa8b117e5c5902ba7f5d6470c0b6bfd2c696a7b59015efa2b8b382c"
        or completion.get("pending_sha256") != _digest(pending_raw)
        or completion.get("step_sha256") != [_digest(raw) for _, raw in steps]
        or completion.get("images_removed") != list(docker.IDS)
        or completion.get("cleanup_confirmed") is not True
        or pending.get("attempts_used") != 5
    ):
        _refuse("prior_cleanup")
    return {
        "proof_sha256": PINS[f"media-native-reader/{ATTEMPT}/cases.json"],
        "grant_sha256": PINS["media-native-reader-extra-attempt-v4/grant.json"],
    }


def _snapshot(config: Path, *, image_present: bool) -> None:
    expected = (IMAGE_ID,) if image_present else ()
    if docker._ids(config, "container") or docker._ids(config, "image") != expected:
        _refuse("resource_inventory")
    all_images = set(docker._ids(config, "image", label=False))
    if any(image_id in all_images for image_id in docker.IDS):
        _refuse("prior_image_reappeared")
    if image_present:
        if IMAGE_ID not in all_images or docker._ids(
            config, "container", label=False, ancestor=IMAGE_ID
        ):
            _refuse("dependent_or_missing")
        value = docker._inspect(config, IMAGE_ID)
        image_config = value.get("Config")
        if type(image_config) is not dict:
            _refuse("image_shape")
        rootfs = value.get("RootFS")
        subset = {
            "id": value.get("Id"),
            "labels": image_config.get("Labels"),
            "env": image_config.get("Env"),
            "rootfs": rootfs,
            "size": value.get("Size"),
            "repo_tags": value.get("RepoTags"),
            "repo_digests": value.get("RepoDigests"),
        }
        if (
            value.get("Id") != IMAGE_ID
            or value.get("Architecture") != "amd64"
            or value.get("Os") != "linux"
            or value.get("Size") != 1735854
            or image_config.get("Labels")
            != {docker.LABEL: LEASE, docker.BUNDLE_LABEL: docker.BUNDLE}
            or image_config.get("Env") != ["PATH=/nonexistent"]
            or value.get("RepoTags") != []
            or value.get("RepoDigests") != []
            or type(rootfs) is not dict
            or rootfs.get("Type") != "layers"
            or type(rootfs.get("Layers")) is not list
            or len(rootfs["Layers"]) != 8
            or _digest(
                json.dumps(subset, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            )
            != IMAGE_INSPECT_SHA256
        ):
            _refuse("image_binding")
    elif IMAGE_ID in all_images:
        _refuse("image_remaining")
    if list(config.iterdir()):
        _refuse("docker_config")


def inventory() -> dict[str, object]:
    root = ROOT
    with fifth._locked(root):
        _audit(root)
        with tempfile.TemporaryDirectory(prefix="reader-sixth-image-inventory-", dir=root) as raw:
            config = Path(raw)
            os.chmod(config, 0o700)
            _snapshot(config, image_present=True)
        _audit(root)
    return {
        "status": "exact-sixth-image-verified",
        "image_id": IMAGE_ID,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _pending(root: Path, pins: dict[str, str], start: str, end: str) -> dict[str, object]:
    return {
        "schema": "zelo-media-reader-sixth-image-cleanup-v1",
        "status": "pending-exact-image-removal",
        "runtime_root": str(root),
        "attempt": ATTEMPT,
        "source_head": SOURCE_HEAD,
        "source_tree": SOURCE_TREE,
        "image_id": IMAGE_ID,
        "lease": LEASE,
        "inspect_sha256": IMAGE_INSPECT_SHA256,
        **pins,
        "quiet_start": start,
        "quiet_end": end,
        "quiet_seconds": reconcile.QUIET_SECONDS,
        "dependent_containers": 0,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _completed(root: Path, pending_sha: str, stdout_sha: str) -> dict[str, object]:
    return {
        "schema": "zelo-media-reader-sixth-image-cleanup-completion-v1",
        "status": "exact-sixth-image-removed",
        "runtime_root": str(root),
        "pending_sha256": pending_sha,
        "image_id": IMAGE_ID,
        "docker_remove_stdout_sha256": stdout_sha,
        "cleanup_confirmed": True,
        "marker_empty": True,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _completion_held(root: Path) -> str:
    """Caller owns the admission lock; verify exactly one complete receipt."""
    pins = _audit(root)
    base = root / BASE
    names = [path.name for path in base.iterdir()]
    if len(names) != 1 or re.fullmatch(r"cleanup-[a-z0-9_]+", names[0]) is None:
        _refuse("completion_inventory")
    fifth._children(base, {names[0]})
    evidence = base / names[0]
    fifth._children(evidence, {"pending.json", "completed.json"})
    pending, raw = records._record(evidence / "pending.json")
    complete, completion_raw = records._record(evidence / "completed.json")
    if type(pending) is not dict or type(complete) is not dict:
        _refuse("completion_shape")
    start, end = pending.get("quiet_start"), pending.get("quiet_end")
    try:
        if datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(
            start, "%Y-%m-%dT%H:%M:%SZ"
        ) != timedelta(seconds=reconcile.QUIET_SECONDS):
            _refuse("completion_quiet")
    except (TypeError, ValueError):
        _refuse("completion_quiet")
    stdout_sha = complete.get("docker_remove_stdout_sha256")
    if (
        type(stdout_sha) is not str
        or re.fullmatch(r"[0-9a-f]{64}", stdout_sha) is None
        or pending != _pending(root, pins, start, end)
        or complete != _completed(root, _digest(raw), stdout_sha)
    ):
        _refuse("completion_binding")
    return _digest(completion_raw)


def verify_completed() -> dict[str, object]:
    """Read-only attestation of the receipt, six refusals and absent image."""
    root = ROOT
    with fifth._locked(root):
        completion_sha = _completion_held(root)
        with tempfile.TemporaryDirectory(
            prefix="reader-sixth-image-verification-", dir=root
        ) as raw:
            config = Path(raw)
            os.chmod(config, 0o700)
            _snapshot(config, image_present=False)
        if _completion_held(root) != completion_sha:
            _refuse("completion_drift")
    return {
        "status": "exact-sixth-image-removal-verified",
        "completion_sha256": completion_sha,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _cleanup(
    root: Path,
    *,
    snapshot: Callable[[Path, bool], None] | None = None,
    remove: Callable[[Path], bytes] | None = None,
    quiet_window: Callable[[Path], tuple[str, str]] = reconcile._quiet_window,
) -> dict[str, object]:
    if snapshot is None:

        def snapshot(config: Path, present: bool) -> None:
            _snapshot(config, image_present=present)

    if remove is None:

        def remove(config: Path) -> bytes:
            return docker._command(config, "image", "rm", "--no-prune", IMAGE_ID)

    root = scope._canonical_root(root)
    with fence._mutable_lock(root) as (_, marker_fd):
        pins = _audit(root)
        if reconcile._read_fd(marker_fd, 4096) != b"" or (root / BASE).exists():
            _refuse("marker_or_prior_consumption")
        with tempfile.TemporaryDirectory(prefix="reader-sixth-image-cleanup-", dir=root) as raw:
            config = Path(raw)
            os.chmod(config, 0o700)
            snapshot(config, True)
            quiet_start, quiet_end = quiet_window(config)
            snapshot(config, True)
            if _audit(root) != pins or reconcile._read_fd(marker_fd, 4096) != b"":
                _refuse("drift")
            base = root / BASE
            base.mkdir(mode=0o700)
            fence._sync_dir(root)
            evidence = Path(tempfile.mkdtemp(prefix="cleanup-", dir=base))
            os.chmod(evidence, 0o700)
            fence._sync_dir(base)
            pending = _pending(root, pins, quiet_start, quiet_end)
            pending_sha = _digest(docker._write(evidence, "pending.json", pending))
            snapshot(config, True)
            if _audit(root) != pins or reconcile._read_fd(marker_fd, 4096) != b"":
                _refuse("drift")
            output = remove(config)
            snapshot(config, False)
            if _audit(root) != pins or reconcile._read_fd(marker_fd, 4096) != b"":
                _refuse("cleanup_unconfirmed")
            complete = _completed(root, pending_sha, _digest(output))
            completion_sha = _digest(docker._write(evidence, "completed.json", complete))
    return {
        "status": "exact-sixth-image-cleaned",
        "completion": str(evidence / "completed.json"),
        "completion_sha256": completion_sha,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def cleanup() -> dict[str, object]:
    """Fixed-root destructive action; requires an explicit exact-image decision."""
    return _cleanup(ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--inventory", action="store_true")
    choice.add_argument("--run", action="store_true")
    choice.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = inventory() if args.inventory else verify_completed() if args.verify else cleanup()
    except Exception:
        print("reader_sixth_image_cleanup_refused", file=os.sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
