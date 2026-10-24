#!/usr/bin/env python3
"""One-use exact fifth-image cleanup; leaves admission fenced and grants no proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from qualification import media_reader_fifth_reconcile as fifth
from qualification import media_reader_scope as scope
from scripts import reconcile_media_native_reader as historical
from scripts import reconcile_media_native_reader_fifth as recon

BASE = "media-native-reader-fifth-image-cleanup"
IMAGE_ID = "sha256:00ebf6496fa9f45cb2b1a452c48dff5f6a002db38d4289fac885855957800ff4"
BUNDLE = "0f53fb8375237850a0f3aacb850159ae9453e346f31c936ce85bbd6de2031e04"
DEFAULT_ENV = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]


def _refuse(code: str) -> None:
    raise fifth.FifthReconciliationRefused("reader_fifth_cleanup_" + code)


def _dependents(config: Path) -> tuple[str, ...]:
    raw = historical._command(
        (
            recon.DOCKER,
            "--host=unix:///var/run/docker.sock",
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"ancestor={IMAGE_ID}",
            "--format",
            "{{.ID}}",
        ),
        config,
    )
    try:
        rows = raw.decode("ascii", "strict").splitlines()
    except UnicodeError:
        _refuse("dependent_inventory")
    if len(rows) > 4096 or any(re.fullmatch(r"[0-9a-f]{64}", row) is None for row in rows):
        _refuse("dependent_inventory")
    return tuple(rows)


def _inspect(config: Path) -> dict:
    raw = historical._command(
        (recon.DOCKER, "--host=unix:///var/run/docker.sock", "image", "inspect", IMAGE_ID), config
    )
    try:
        rows = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError, TypeError):
        _refuse("inspect")
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        _refuse("inspect")
    return rows[0]


def _verify_image(value: dict) -> str:
    config = value.get("Config")
    if type(config) is not dict:
        _refuse("image_shape")
    labels = config.get("Labels")
    expected = {recon.LABEL: fifth.LEASE, "org.zelo.media-reader.bundle": BUNDLE}
    rootfs = value.get("RootFS")
    if (
        value.get("Id") != IMAGE_ID
        or value.get("Architecture") != "amd64"
        or value.get("Os") != "linux"
        or value.get("Size") != 1735813
        or value.get("RepoTags") != []
        or value.get("RepoDigests") != []
        or labels != expected
        or config.get("Env") != DEFAULT_ENV
        or config.get("Entrypoint") not in (None, [])
        or config.get("Cmd") not in (None, [])
        or config.get("User") not in (None, "")
        or config.get("WorkingDir") not in (None, "")
        or type(rootfs) is not dict
        or rootfs.get("Type") != "layers"
        or type(rootfs.get("Layers")) is not list
        or len(rootfs["Layers"]) != 8
    ):
        _refuse("image_binding")
    subset = {
        "id": IMAGE_ID,
        "labels": labels,
        "env": config["Env"],
        "rootfs": rootfs,
        "size": value["Size"],
        "repo_tags": [],
        "repo_digests": [],
    }
    return hashlib.sha256(
        json.dumps(subset, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _remove(config: Path) -> bytes:
    return historical._command(
        (recon.DOCKER, "--host=unix:///var/run/docker.sock", "image", "rm", IMAGE_ID), config
    )


def _evidence_dir(root: Path) -> Path:
    base = root / BASE
    try:
        base.mkdir(mode=0o700)
    except FileExistsError:
        pass
    fifth._children(base, set())
    recon._sync_dir(root)
    path = Path(tempfile.mkdtemp(prefix="cleanup-", dir=base))
    os.chmod(path, 0o700)
    recon._sync_dir(base)
    recon._sync_dir(root)
    return path


def _write_json(path: Path, name: str, value: dict) -> bytes:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(raw) > 65536:
        _refuse("evidence_bound")
    fd = os.open(
        path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                _refuse("evidence_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    recon._sync_dir(path)
    recon._sync_dir(path.parent)
    recon._sync_dir(path.parent.parent)
    return raw


def _cleanup(
    root: Path,
    pins: dict[str, tuple[str, int, int]],
    *,
    discover: Callable = recon._discover,
    image_exists: Callable = recon._image_exists,
    dependents: Callable = _dependents,
    inspect: Callable = _inspect,
    remove: Callable = _remove,
    quiet_window: Callable = historical._quiet_window,
) -> dict[str, object]:
    root = scope._canonical_root(root)
    with recon._mutable_lock(root) as (_, marker_fd):
        fifth._audit_held(root, pins)
        marker_raw = historical._read_fd(marker_fd, 4096)
        if (
            hashlib.sha256(marker_raw).hexdigest()
            != pins["media-elf-metadata-admission/active.marker"][0]
        ):
            _refuse("marker_pin")
        if (root / BASE).exists():
            fifth._children(root / BASE, set())
        image_raw = fifth._read_pinned(
            root / f"media-native-reader/{fifth.FIFTH}/image-id",
            *pins[f"media-native-reader/{fifth.FIFTH}/image-id"],
        )
        if image_raw.decode("ascii", "strict").strip() != IMAGE_ID:
            _refuse("image_id")
        with tempfile.TemporaryDirectory(
            prefix="reader-fifth-image-cleanup-", dir=root
        ) as raw_config:
            config = Path(raw_config)
            os.chmod(config, 0o700)
            if discover("container", config) or discover("image", config) != (IMAGE_ID,):
                _refuse("resource_inventory")
            if dependents(config):
                _refuse("dependent_container")
            if image_exists(IMAGE_ID, config) is not True:
                _refuse("image_missing")
            inspect_sha = _verify_image(inspect(config))
            quiet_start, quiet_end = quiet_window(config)
            if discover("container", config) or discover("image", config) != (IMAGE_ID,):
                _refuse("resource_drift")
            if dependents(config) or image_exists(IMAGE_ID, config) is not True:
                _refuse("resource_drift")
            if _verify_image(inspect(config)) != inspect_sha or list(os.scandir(config)):
                _refuse("image_drift")
            fifth._audit_held(root, pins)
            evidence = _evidence_dir(root)
            pending = {
                "schema": "zelo-media-reader-fifth-image-cleanup-v1",
                "status": "pending-owned-image-removal",
                "runtime_root": str(root),
                "attempt": fifth.FIFTH,
                "lease": fifth.LEASE,
                "source_head": fifth.SOURCE_HEAD,
                "source_tree": fifth.SOURCE_TREE,
                "image_id": IMAGE_ID,
                "image_inspect_sha256": inspect_sha,
                "marker_sha256": pins["media-elf-metadata-admission/active.marker"][0],
                "proof_sha256": pins[f"media-native-reader/{fifth.FIFTH}/cases.json"][0],
                "grant_sha256": pins["media-native-reader-extra-attempt-v3/grant.json"][0],
                "quiet_start": quiet_start,
                "quiet_end": quiet_end,
                "quiet_seconds": historical.QUIET_SECONDS,
                "dependent_container_count": 0,
                "image_has_tags_or_digests": False,
                "marker_clear_authorized": False,
                "another_attempt_authorized": False,
                "runtime_authority": False,
            }
            pending_raw = _write_json(evidence, "pending.json", pending)
            if historical._read_fd(marker_fd, 4096) != marker_raw:
                _refuse("marker_drift")
            if discover("container", config) or discover("image", config) != (IMAGE_ID,):
                _refuse("resource_drift")
            if dependents(config) or image_exists(IMAGE_ID, config) is not True:
                _refuse("resource_drift")
            if _verify_image(inspect(config)) != inspect_sha:
                _refuse("image_drift")
            removed = remove(config)
            if (
                discover("container", config)
                or discover("image", config)
                or image_exists(IMAGE_ID, config)
            ):
                _refuse("cleanup_unconfirmed")
            if dependents(config) or list(os.scandir(config)):
                _refuse("cleanup_unconfirmed")
            fifth._audit_held(root, pins)
            if historical._read_fd(marker_fd, 4096) != marker_raw:
                _refuse("marker_drift")
            complete = {
                "schema": "zelo-media-reader-fifth-image-cleanup-completion-v1",
                "status": "owned-image-removed",
                "runtime_root": str(root),
                "pending_sha256": hashlib.sha256(pending_raw).hexdigest(),
                "image_id": IMAGE_ID,
                "docker_remove_stdout_sha256": hashlib.sha256(removed).hexdigest(),
                "cleanup_confirmed": True,
                "marker_fenced": True,
                "another_attempt_authorized": False,
                "runtime_authority": False,
            }
            complete_raw = _write_json(evidence, "completed.json", complete)
        recon._sync_dir(root)
        if historical._read_fd(marker_fd, 4096) != marker_raw:
            _refuse("marker_drift")
    return {
        "status": "owned-fifth-image-cleaned-marker-fenced",
        "evidence": str(evidence / "completed.json"),
        "evidence_sha256": hashlib.sha256(complete_raw).hexdigest(),
        "image_id": IMAGE_ID,
        "admission_marker_cleared": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def _record(path: Path) -> tuple[dict, bytes]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or before.st_nlink != 1
            or not 0 < before.st_size <= 65536
        ):
            _refuse("completion_file")
        chunks = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _refuse("completion_drift")
        raw = b"".join(chunks)
        if len(raw) != before.st_size:
            _refuse("completion_bound")

        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    _refuse("completion_duplicate_key")
                result[key] = value
            return result

        try:
            data = json.loads(raw, object_pairs_hook=pairs)
        except (UnicodeError, ValueError, RecursionError, TypeError):
            _refuse("completion_json")
        if type(data) is not dict:
            _refuse("completion_shape")
        return data, raw
    finally:
        os.close(fd)


def completed_cleanup(root: Path, pins: dict[str, tuple[str, int, int]], image_id: str) -> str:
    """Validate the sole durable exact-image removal receipt while admission is locked."""
    if image_id != IMAGE_ID:
        _refuse("completion_image_id")
    base = root / BASE
    info = base.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
    ):
        _refuse("completion_directory")
    names = {entry.name for entry in base.iterdir()}
    if len(names) != 1:
        _refuse("completion_inventory")
    name = names.pop()
    if re.fullmatch(r"cleanup-[a-z0-9_]+", name) is None:
        _refuse("completion_inventory")
    fifth._children(base, {name})
    record_dir = base / name
    fifth._children(record_dir, {"pending.json", "completed.json"})
    pending, raw = _record(record_dir / "pending.json")
    complete, complete_raw = _record(record_dir / "completed.json")
    expected_pending = {
        "schema": "zelo-media-reader-fifth-image-cleanup-v1",
        "status": "pending-owned-image-removal",
        "runtime_root": str(root),
        "attempt": fifth.FIFTH,
        "lease": fifth.LEASE,
        "source_head": fifth.SOURCE_HEAD,
        "source_tree": fifth.SOURCE_TREE,
        "image_id": IMAGE_ID,
        "marker_sha256": pins["media-elf-metadata-admission/active.marker"][0],
        "proof_sha256": pins[f"media-native-reader/{fifth.FIFTH}/cases.json"][0],
        "grant_sha256": pins["media-native-reader-extra-attempt-v3/grant.json"][0],
        "dependent_container_count": 0,
        "image_has_tags_or_digests": False,
        "marker_clear_authorized": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    expected_complete = {
        "schema": "zelo-media-reader-fifth-image-cleanup-completion-v1",
        "status": "owned-image-removed",
        "runtime_root": str(root),
        "pending_sha256": hashlib.sha256(raw).hexdigest(),
        "image_id": IMAGE_ID,
        "cleanup_confirmed": True,
        "marker_fenced": True,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    if any(
        type(pending.get(k)) is not type(v) or pending[k] != v for k, v in expected_pending.items()
    ) or any(
        type(complete.get(k)) is not type(v) or complete[k] != v
        for k, v in expected_complete.items()
    ):
        _refuse("completion_binding")
    for _, value in (
        ("image_inspect_sha256", pending.get("image_inspect_sha256")),
        ("docker_remove_stdout_sha256", complete.get("docker_remove_stdout_sha256")),
    ):
        if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            _refuse("completion_digest")
    if (
        pending.get("quiet_seconds") != historical.QUIET_SECONDS
        or type(pending.get("quiet_start")) is not str
        or type(pending.get("quiet_end")) is not str
    ):
        _refuse("completion_quiet_window")
    return hashlib.sha256(complete_raw).hexdigest()


def cleanup() -> dict[str, object]:
    """Fixed-root operator action; requires a separate reviewed cleanup decision."""
    return _cleanup(fifth.RETAINED_ROOT, fifth.FILE_PINS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        result = cleanup()
    except Exception:
        print("reader_fifth_cleanup_refused", file=os.sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
