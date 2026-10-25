#!/usr/bin/env python3
"""One-use exact cleanup of eight retained Reader diagnostic images.

Execution requires a separate operator decision. This never edits proof files,
clears admission, grants an attempt, prunes Docker, or removes unknown images.
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
from qualification import media_reader_sixth_ledger as ledger
from scripts import cleanup_media_native_reader_fifth_image as previous
from scripts import reconcile_media_native_reader as historical
from scripts import reconcile_media_native_reader_fifth as recon

BASE = "media-native-reader-residual-image-cleanup"
BUNDLE = "0f53fb8375237850a0f3aacb850159ae9453e346f31c936ce85bbd6de2031e04"
LABEL = "org.zelo.media-reader.lease"
BUNDLE_LABEL = "org.zelo.media-reader.bundle"
# (lease, SHA256 of exact inspected id/labels/env/rootfs/size/tags/digests)
IMAGE_PINS = {
    "sha256:510d4f3baf0c7037d037d5d3a73186864c0f96fd68c885f7f2d99a72a9379dfa": (
        "76f03cc17107202247fd73568b0cd811ccfd86c9638e18e1551b77c1ada83f88",
        "31b97fc4aba8793478c81a25a3b8bb9a92f3c87ae49627c6c47087d89959d2f4",
    ),
    "sha256:6492565471037b3cf351fefe0c3190f095bf8429677912338d1803ab5ca1aa77": (
        "f3f65ffe56cc903f6e9a7f7276a37890295f5c08f3627381762bba24dd1888e5",
        "16cbedaa1c1018cc51277f1336aeb9f670ed577f56f08cdfd5cabbdf8f437bff",
    ),
    "sha256:719a3c669fce88110d8fc08064395c1248c2a61231fd665dd483d9e2bdd980df": (
        "a5207385a863053f724dfdb49991f2364579a03760f3952fa7e09ed744468ed8",
        "4ed9cde88fc03c2862f5fc461d84ec5756b201b4f12cf8ab3c769f3f44e6d0ca",
    ),
    "sha256:82ca69835e8b2127a988eb52f92570d34ce4c936ccb59b48467687aef7c5ce9c": (
        "441221eaed4c16830ad161b025bb97b4bcb4f47cb544b43f48db1ec4fa14a0e3",
        "4c1e35b00c7fc286544eb56f1b793c5d2f4f1b4571549d09c2a3a208a8f75a5e",
    ),
    "sha256:927f8fb6849acbc6ad260cab1e4df0510cbf57fcfdf40ace96f845b166d07576": (
        "c18fa397bd4104aa9dae03e84fed6d52896559209f04697b647ed897dc68a1fd",
        "35619c119b3a27ae09feaed1fc34436f486bf3e628a2f95a97b5ddb7c850936c",
    ),
    "sha256:ad3aa4238326b45540ffc0ccc129c07f12a6e3509718510775115b290d9f3bf3": (
        "08f9fe24b7ef9116145f386b4a4c417091ec8a9363e3664a590635026fc52b73",
        "ab03d70a3211de8461cb7d958f2915ec5c1fb3266a32cc62e353ed457539db82",
    ),
    "sha256:c4b221bcea3b46b410a1c8450daffb976ea9974f902013bca49a6249fba3b9ff": (
        "62f23e424d7f76d8b48786744cceb4ddebaf8edd238d0d82ffbf25abf6f8f8e7",
        "5fcb0b666e60359c615786bf24ff26a887fc41f278bb1759fdc4c951596a18b6",
    ),
    "sha256:f2b35cb8b3f5d4376b2b41f0af66ebb6e599642493d55a6287442568e5ec6f4c": (
        "53a0b76fc1a2aa8c82726e708eb294b5e7238a6ba7f85f537571a7cdc7443b74",
        "3c603e44bf8bd66261690b52b5981981fa252cf9dd77ed3f856610f9d204cede",
    ),
}
IDS = tuple(sorted(IMAGE_PINS))
DOCKER = historical.DOCKER
HOST = "--host=unix:///var/run/docker.sock"


class ResidualCleanupRefused(ValueError):
    """An exact image, shared fence, or one-use receipt was not preserved."""


def _refuse(reason: str) -> None:
    raise ResidualCleanupRefused("reader_residual_cleanup_" + reason) from None


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _command(config: Path, *args: str) -> bytes:
    return historical._command((DOCKER, HOST, *args), config)


def _ids(
    config: Path, kind: str, *, label: bool = True, ancestor: str | None = None
) -> tuple[str, ...]:
    if kind not in ("image", "container") or (ancestor is not None and kind != "container"):
        _refuse("query")
    args = [kind, "ls", "--all", "--no-trunc"]
    if label:
        args += ["--filter", f"label={LABEL}"]
    if ancestor is not None:
        args += ["--filter", f"ancestor={ancestor}"]
    args += ["--format", "{{.ID}}"]
    try:
        rows = _command(config, *args).decode("ascii", "strict").splitlines()
    except UnicodeError:
        _refuse("inventory_encoding")
    pattern = r"sha256:[0-9a-f]{64}" if kind == "image" else r"[0-9a-f]{64}"
    if len(rows) > 4096 or any(re.fullmatch(pattern, row) is None for row in rows):
        _refuse("inventory_shape")
    if label and len(set(rows)) != len(rows):
        _refuse("inventory_duplicate")
    return tuple(sorted(set(rows)))


def _inspect(config: Path, image_id: str) -> dict:
    try:
        rows = json.loads(_command(config, "image", "inspect", image_id))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _refuse("inspect_json")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        _refuse("inspect_shape")
    return rows[0]


def _verify(image_id: str, value: dict) -> None:
    lease, pinned = IMAGE_PINS[image_id]
    config = value.get("Config")
    if type(config) is not dict:
        _refuse("config")
    rootfs = value.get("RootFS")
    labels = config.get("Labels")
    subset = {
        "id": value.get("Id"),
        "labels": labels,
        "env": config.get("Env"),
        "rootfs": rootfs,
        "size": value.get("Size"),
        "repo_tags": value.get("RepoTags"),
        "repo_digests": value.get("RepoDigests"),
    }
    raw = json.dumps(subset, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if (
        value.get("Id") != image_id
        or value.get("Architecture") != "amd64"
        or value.get("Os") != "linux"
        or labels != {LABEL: lease, BUNDLE_LABEL: BUNDLE}
        or value.get("RepoTags") != []
        or value.get("RepoDigests") != []
        or config.get("Env")
        not in (
            ["PATH=/nonexistent"],
            ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
        )
        or type(value.get("Size")) is not int
        or not 1_000_000 < value["Size"] < 2_000_000
        or type(rootfs) is not dict
        or rootfs.get("Type") != "layers"
        or type(rootfs.get("Layers")) is not list
        or len(rootfs["Layers"]) != 8
        or _digest(raw) != pinned
    ):
        _refuse("image_binding")


def _snapshot(config: Path, remaining: tuple[str, ...]) -> None:
    if _ids(config, "container") or _ids(config, "image") != remaining:
        _refuse("resource_inventory")
    all_images = set(_ids(config, "image", label=False))
    if any(image_id in all_images for image_id in IDS if image_id not in remaining):
        _refuse("removed_image_reappeared")
    for image_id in remaining:
        if image_id not in all_images or _ids(config, "container", label=False, ancestor=image_id):
            _refuse("dependent_or_missing")
        _verify(image_id, _inspect(config, image_id))
    if list(config.iterdir()):
        _refuse("docker_config")


def _write(path: Path, name: str, value: dict) -> bytes:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(raw) > 65536:
        _refuse("receipt_bound")
    fd = os.open(
        path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                _refuse("receipt_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    recon._sync_dir(path)
    recon._sync_dir(path.parent)
    return raw


def _evidence(root: Path) -> Path:
    base = root / BASE
    base.mkdir(mode=0o700)
    recon._sync_dir(root)
    path = Path(tempfile.mkdtemp(prefix="cleanup-", dir=base))
    os.chmod(path, 0o700)
    recon._sync_dir(base)
    return path


def _cleanup(
    root: Path,
    *,
    snapshot: Callable[[Path, tuple[str, ...]], None] = _snapshot,
    remove: Callable[[Path, str], bytes] | None = None,
    quiet_window: Callable[[Path], tuple[str, str]] = historical._quiet_window,
) -> dict[str, object]:
    """One process owns the lock and attempt; interruption leaves durable evidence."""
    if remove is None:

        def remove(config: Path, image_id: str) -> bytes:
            return _command(config, "image", "rm", "--no-prune", image_id)

    root = scope._canonical_root(root)
    with recon._mutable_lock(root) as (_, marker_fd):
        ledger._audit_held(root, ledger.FILE_PINS)
        if historical._read_fd(marker_fd, 4096) != b"" or (root / BASE).exists():
            _refuse("fence_or_prior_consumption")
        with tempfile.TemporaryDirectory(prefix="reader-residual-cleanup-", dir=root) as raw:
            config = Path(raw)
            os.chmod(config, 0o700)
            snapshot(config, IDS)
            quiet_start, quiet_end = quiet_window(config)
            snapshot(config, IDS)
            ledger._audit_held(root, ledger.FILE_PINS)
            if historical._read_fd(marker_fd, 4096) != b"":
                _refuse("marker_drift")
            evidence = _evidence(root)
            pending = {
                "schema": "zelo-media-reader-residual-cleanup-v1",
                "status": "pending-exact-eight-image-removal",
                "runtime_root": str(root),
                "images": [
                    {
                        "id": image_id,
                        "lease": IMAGE_PINS[image_id][0],
                        "inspect_sha256": IMAGE_PINS[image_id][1],
                    }
                    for image_id in IDS
                ],
                "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
                "fifth_reconciliation_sha256": ledger.RECONCILIATION_SHA256,
                "quiet_start": quiet_start,
                "quiet_end": quiet_end,
                "quiet_seconds": historical.QUIET_SECONDS,
                "attempts_used": 5,
                "another_attempt_authorized": False,
                "runtime_authority": False,
            }
            pending_sha = _digest(_write(evidence, "pending.json", pending))
            remaining = IDS
            steps: list[str] = []
            for index, image_id in enumerate(IDS, start=1):
                ledger._audit_held(root, ledger.FILE_PINS)
                if historical._read_fd(marker_fd, 4096) != b"":
                    _refuse("marker_drift")
                snapshot(config, remaining)
                output = remove(config, image_id)
                remaining = tuple(item for item in remaining if item != image_id)
                snapshot(config, remaining)
                receipt = {
                    "schema": "zelo-media-reader-residual-cleanup-step-v1",
                    "pending_sha256": pending_sha,
                    "index": index,
                    "image_id": image_id,
                    "docker_remove_stdout_sha256": _digest(output),
                    "remaining": list(remaining),
                    "another_attempt_authorized": False,
                    "runtime_authority": False,
                }
                steps.append(_digest(_write(evidence, f"removed-{index:02}.json", receipt)))
            ledger._audit_held(root, ledger.FILE_PINS)
            if historical._read_fd(marker_fd, 4096) != b"":
                _refuse("marker_drift")
            snapshot(config, ())
            completion = {
                "schema": "zelo-media-reader-residual-cleanup-completion-v1",
                "status": "exact-eight-images-removed",
                "runtime_root": str(root),
                "pending_sha256": pending_sha,
                "step_sha256": steps,
                "images_removed": list(IDS),
                "cleanup_confirmed": True,
                "marker_cleared_before_and_after": True,
                "another_attempt_authorized": False,
                "runtime_authority": False,
            }
            completed_sha = _digest(_write(evidence, "completed.json", completion))
    return {
        "status": "exact-residual-images-cleaned",
        "completion": str(evidence / "completed.json"),
        "completion_sha256": completed_sha,
        "images_removed": len(IDS),
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def cleanup() -> dict[str, object]:
    """Fixed-root operator action; a separate exact-image decision is required."""
    return _cleanup(ledger.RETAINED_ROOT)


def inventory() -> dict[str, object]:
    """Read-only fixed-root Docker and ledger check; no removal or grant."""
    root = ledger.RETAINED_ROOT
    with fifth._locked(root):
        ledger._audit_held(root, ledger.FILE_PINS)
        with tempfile.TemporaryDirectory(prefix="reader-residual-inventory-", dir=root) as raw:
            config = Path(raw)
            os.chmod(config, 0o700)
            _snapshot(config, IDS)
        ledger._audit_held(root, ledger.FILE_PINS)
    return {
        "status": "exact-eight-residual-images-verified",
        "image_count": len(IDS),
        "image_ids": list(IDS),
        "docker_resources_checked": True,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _completion_held(root: Path) -> str:
    """Validate the one-use durable chain; caller owns the shared lock."""
    ledger._audit_held(root, ledger.FILE_PINS)
    base = root / BASE
    try:
        names = {item.name for item in base.iterdir()}
        if len(names) != 1:
            _refuse("completion_inventory")
        name = names.pop()
        if re.fullmatch(r"cleanup-[a-z0-9_]+", name) is None:
            _refuse("completion_inventory")
        fifth._children(base, {name})
        evidence = base / name
        fifth._children(
            evidence,
            {"pending.json", "completed.json"}
            | {f"removed-{index:02}.json" for index in range(1, len(IDS) + 1)},
        )
        pending, pending_raw = previous._record(evidence / "pending.json")
        complete, complete_raw = previous._record(evidence / "completed.json")
        steps = [
            previous._record(evidence / f"removed-{index:02}.json")
            for index in range(1, len(IDS) + 1)
        ]
    except (OSError, ValueError):
        _refuse("completion_files")
    expected_pending = {
        "schema": "zelo-media-reader-residual-cleanup-v1",
        "status": "pending-exact-eight-image-removal",
        "runtime_root": str(root),
        "images": [
            {
                "id": image_id,
                "lease": IMAGE_PINS[image_id][0],
                "inspect_sha256": IMAGE_PINS[image_id][1],
            }
            for image_id in IDS
        ],
        "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
        "fifth_reconciliation_sha256": ledger.RECONCILIATION_SHA256,
        "quiet_start": pending.get("quiet_start"),
        "quiet_end": pending.get("quiet_end"),
        "quiet_seconds": historical.QUIET_SECONDS,
        "attempts_used": 5,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    if type(pending) is not dict or pending != expected_pending:
        _refuse("completion_pending")
    try:
        start = datetime.strptime(pending["quiet_start"], "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(pending["quiet_end"], "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        _refuse("completion_quiet")
    if end - start != timedelta(seconds=historical.QUIET_SECONDS):
        _refuse("completion_quiet")
    pending_sha = _digest(pending_raw)
    step_hashes = []
    for index, (step, raw) in enumerate(steps, start=1):
        stdout_sha = step.get("docker_remove_stdout_sha256") if type(step) is dict else None
        expected_step = {
            "schema": "zelo-media-reader-residual-cleanup-step-v1",
            "pending_sha256": pending_sha,
            "index": index,
            "image_id": IDS[index - 1],
            "docker_remove_stdout_sha256": stdout_sha,
            "remaining": list(IDS[index:]),
            "another_attempt_authorized": False,
            "runtime_authority": False,
        }
        if (
            type(stdout_sha) is not str
            or re.fullmatch(r"[0-9a-f]{64}", stdout_sha) is None
            or step != expected_step
        ):
            _refuse("completion_step")
        step_hashes.append(_digest(raw))
    expected_complete = {
        "schema": "zelo-media-reader-residual-cleanup-completion-v1",
        "status": "exact-eight-images-removed",
        "runtime_root": str(root),
        "pending_sha256": pending_sha,
        "step_sha256": step_hashes,
        "images_removed": list(IDS),
        "cleanup_confirmed": True,
        "marker_cleared_before_and_after": True,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    if complete != expected_complete:
        _refuse("completion_binding")
    return _digest(complete_raw)


def completed_cleanup(root: Path) -> str:
    """Read-only receipt verification; Docker must be checked separately."""
    if Path(root) != ledger.RETAINED_ROOT:
        _refuse("root")
    root = scope._canonical_root(root)
    with fifth._locked(root):
        return _completion_held(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--inventory", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        result = inventory() if args.inventory else cleanup()
    except Exception:
        print("reader_residual_cleanup_refused", file=os.sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
