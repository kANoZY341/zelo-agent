"""Bounded prepared-bundle change detection; never execution authorization."""

import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass

from qualification.runtime_contracts import validate_digest

_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@dataclass(frozen=True)
class ArtifactPolicy:
    max_files: int = 4096
    max_depth: int = 16
    max_file_bytes: int = 512 * 1024**2
    max_total_bytes: int = 1024**3
    max_entries: int = 8192  # Includes directories, so empty trees are bounded too.

    def __post_init__(self) -> None:
        for value, ceiling in zip(
            asdict(self).values(), (4096, 16, 512 * 1024**2, 1024**3, 8192), strict=True
        ):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("invalid_artifact_bound")


@dataclass(frozen=True)
class ArtifactFile:
    path: str
    size: int
    mode: int
    sha256: str


@dataclass(frozen=True)
class ArtifactManifest:
    version: str
    policy: ArtifactPolicy
    roles: tuple[tuple[str, str], ...]
    files: tuple[ArtifactFile, ...]
    directories: tuple[tuple[str, int], ...]  # Includes root and empty directories.

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()


def _path(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or "\\" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
        or any(ord(c) < 32 or ord(c) > 126 for c in value)
    ):
        raise ValueError("invalid_artifact_path")


def _roles(required: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    if type(required) is not tuple or not 1 <= len(required) <= 32:
        raise ValueError("invalid_artifact_roles")
    names = set()
    for pair in required:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("invalid_artifact_role")
        name, path = pair
        if (
            type(name) is not str
            or not 1 <= len(name) <= 64
            or name[0] not in "abcdefghijklmnopqrstuvwxyz"
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name)
            or name in names
        ):
            raise ValueError("invalid_artifact_role")
        names.add(name)
        _path(path)
    return tuple(sorted(required))


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mode,
        info.st_nlink,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _safe(info: os.stat_result) -> None:
    if (
        not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        or info.st_mode & 0o022
        or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
    ):
        raise ValueError("unsafe_artifact")


def _scan(
    root_fd: int, policy: ArtifactPolicy, hash_content: bool
) -> tuple[tuple[ArtifactFile, ...], tuple[tuple[str, int], ...], dict[str, tuple[int, ...]]]:
    files: list[ArtifactFile] = []
    directories: list[tuple[str, int]] = []
    stamps: dict[str, tuple[int, ...]] = {}
    total = count = entries = 0

    def walk(fd: int, path: str, depth: int) -> None:
        nonlocal total, count, entries
        before = os.fstat(fd)
        _safe(before)
        stamps[path] = _stamp(before)
        directories.append((path, stat.S_IMODE(before.st_mode)))
        names = []
        with os.scandir(fd) as iterator:
            for entry in iterator:
                entries += 1
                if entries > policy.max_entries:
                    raise ValueError("artifact_entry_limit")
                _path(entry.name)
                names.append(entry.name)
        for name in sorted(names):
            relative = f"{path}/{name}" if path else name
            _path(relative)
            if depth + 1 > policy.max_depth:
                raise ValueError("artifact_depth_limit")
            initial = os.stat(name, dir_fd=fd, follow_symlinks=False)
            _safe(initial)  # Reject FIFO/device/socket BEFORE opening or reading.
            directory = stat.S_ISDIR(initial.st_mode)
            child = os.open(name, _FLAGS | (os.O_DIRECTORY if directory else 0), dir_fd=fd)
            try:
                opened = os.fstat(child)
                if _stamp(initial) != _stamp(opened):
                    raise ValueError("artifact_changed")
                if directory:
                    walk(child, relative, depth + 1)
                else:
                    count += 1
                    total += opened.st_size
                    if (
                        count > policy.max_files
                        or opened.st_size > policy.max_file_bytes
                        or total > policy.max_total_bytes
                    ):
                        raise ValueError("artifact_size_limit")
                    content = hashlib.sha256()
                    size = 0
                    if hash_content:
                        while block := os.read(child, min(65536, opened.st_size - size + 1)):
                            size += len(block)
                            if size > opened.st_size:
                                raise ValueError("artifact_changed")
                            content.update(block)
                        if size != opened.st_size:
                            raise ValueError("artifact_changed")
                    stamps[relative] = _stamp(opened)
                    files.append(
                        ArtifactFile(
                            relative,
                            opened.st_size,
                            stat.S_IMODE(opened.st_mode),
                            content.hexdigest(),
                        )
                    )
                if _stamp(opened) != _stamp(os.fstat(child)) or _stamp(opened) != _stamp(
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                ):
                    raise ValueError("artifact_changed")
            finally:
                os.close(child)
        if _stamp(before) != _stamp(os.fstat(fd)):
            raise ValueError("artifact_changed")

    walk(root_fd, "", 0)
    return tuple(sorted(files, key=lambda f: f.path)), tuple(sorted(directories)), stamps


def capture_manifest(
    root: str | os.PathLike[str],
    required: tuple[tuple[str, str], ...],
    policy: ArtifactPolicy,
) -> ArtifactManifest:
    """Record a candidate from trusted operator inputs; no content becomes approved.

    Linux/local filesystem semantics and trusted ancestors/operator are assumed.
    Two metadata sweeps detect changes, but cannot freeze a tree or defeat an admin.
    """
    if type(policy) is not ArtifactPolicy:
        raise ValueError("invalid_artifact_policy")
    roles = _roles(required)
    root = os.fspath(root)
    # Reject trailing slashes/dot components that could hide a symlink final component.
    if type(root) is not str or not root or (root != "/" and root.endswith("/")):
        raise ValueError("invalid_artifact_root")
    if any(p in (".", "..") for p in root.split("/")):
        raise ValueError("invalid_artifact_root")
    try:
        initial = os.stat(root, follow_symlinks=False)
        _safe(initial)
        fd = os.open(root, _FLAGS | os.O_DIRECTORY)
        try:
            if _stamp(initial) != _stamp(os.fstat(fd)):
                raise ValueError("artifact_changed")
            files, directories, stamps = _scan(fd, policy, True)
            # Revisit early files and directories after hashing the entire bundle.
            if stamps != _scan(fd, policy, False)[2]:
                raise ValueError("artifact_changed")
            if _stamp(initial) != _stamp(os.stat(root, follow_symlinks=False)):
                raise ValueError("artifact_changed")
        finally:
            os.close(fd)
    except OSError:
        raise ValueError("artifact_unavailable") from None
    inventory = {file.path: file for file in files}
    for _, path in roles:
        if path not in inventory or not inventory[path].mode & 0o111:
            raise ValueError("required_executable_missing")
    return ArtifactManifest("zelo-browser-artifacts-v1", policy, roles, files, directories)


def verify_manifest(
    root: str | os.PathLike[str],
    required: tuple[tuple[str, str], ...],
    policy: ArtifactPolicy,
    expected: ArtifactManifest,
    expected_digest: str,
) -> ArtifactManifest:
    """Rescan against an independently trusted digest AND the exact expected manifest.

    The caller must obtain expected_digest through a separate trusted channel.
    This API cannot establish that provenance or authorize a later execution.
    """
    validate_digest(expected_digest)
    if type(expected) is not ArtifactManifest or expected.digest != expected_digest:
        raise ValueError("artifact_manifest_mismatch")
    actual = capture_manifest(root, required, policy)
    if actual != expected or actual.digest != expected_digest:
        raise ValueError("artifact_manifest_mismatch")
    return actual
