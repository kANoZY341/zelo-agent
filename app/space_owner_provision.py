"""Create a new bounded Space directory from explicitly supplied operator text.

This helper is not a Tool or Worker. The authenticated service must first
validate the active Agent, Project, allowed Space and remaining resource quota.
Existing roots and files are never replaced, followed, adopted or deleted.
"""

import ctypes
import errno
import os
import secrets
import stat
from pathlib import Path

from app.agent_space_intents import _ID, _UUID
from app.policy import DomainError

MAX_SEED_FILES = 16
MAX_SEED_FILE_BYTES = 65536
MAX_SEED_TOTAL_BYTES = 262144


def encode_seed_files(files: tuple[tuple[str, str], ...]) -> tuple[tuple[str, bytes], ...]:
    from app.space_file_gateway import _path_components

    if type(files) is not tuple or len(files) > MAX_SEED_FILES:
        raise DomainError("Space seed file count invalid", 422)
    encoded: list[tuple[str, bytes]] = []
    names: set[str] = set()
    total = 0
    for item in files:
        if type(item) is not tuple or len(item) != 2:
            raise DomainError("Space seed file invalid", 422)
        name, text = item
        if len(_path_components(name)) != 1 or name in names or type(text) is not str:
            raise DomainError("Space seed filenames must be unique and flat", 422)
        try:
            content = text.encode("utf-8")
        except UnicodeError:
            raise DomainError("Space seed content must be UTF-8", 422) from None
        total += len(content)
        if len(content) > MAX_SEED_FILE_BYTES or total > MAX_SEED_TOTAL_BYTES:
            raise DomainError("Space seed content exceeds bounded text limits", 422)
        names.add(name)
        encoded.append((name, content))
    return tuple(sorted(encoded))


def _publish_noreplace(parent_fd: int, prepared: str, destination: str) -> None:
    """Linux atomic no-clobber rename; never fall back to replacing rename().

    Contract: Linux rename(2), RENAME_NOREPLACE. A missing symbol or unsupported
    filesystem refuses this operation and preserves the private staging data.
    """
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise OSError(errno.ENOTSUP, "atomic_space_publish_unavailable") from None
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent_fd, os.fsencode(prepared), parent_fd, os.fsencode(destination), 1) != 0:
        raise OSError(ctypes.get_errno(), "atomic_space_publish_refused")


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise OSError("short_space_file_write")
        offset += written
    os.fsync(fd)


def _file_stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _verify_seed_set(
    root_fd: int,
    seeds: list[tuple[str, bytes, int, os.stat_result]],
) -> None:
    """Verify every bounded file, not just the most recently written one."""
    before = os.fstat(root_fd)
    names = []
    with os.scandir(root_fd) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_SEED_FILES:
                raise OSError("unexpected_space_seed_count")
    if set(names) != {name for name, _, _, _ in seeds}:
        raise OSError("space_seed_inventory_changed")
    for name, expected, fd, original in seeds:
        current = os.fstat(fd)
        visible = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.getuid()
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) & 0o077
            or _file_stamp(current) != _file_stamp(original)
            or _file_stamp(visible) != _file_stamp(original)
        ):
            raise OSError("space_seed_identity_changed")
        os.lseek(fd, 0, os.SEEK_SET)
        data = bytearray()
        while len(data) <= len(expected):
            chunk = os.read(fd, min(8192, len(expected) + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if bytes(data) != expected or _file_stamp(os.fstat(fd)) != _file_stamp(original):
            raise OSError("space_seed_content_changed")
    if _file_stamp(os.fstat(root_fd)) != _file_stamp(before):
        raise OSError("space_seed_directory_changed")


def provision_seeded_root(
    runtime_dir: Path,
    *,
    project_id: str,
    space_id: str,
    resource_id: str,
    files: tuple[tuple[str, bytes], ...],
) -> tuple[int, int]:
    """Prepare privately, verify complete seeds, then atomically publish NEW root.

    A persistent exclusive reservation prevents automatic retry after partial
    failure. All staged/final files are preserved on error for owner recovery.
    Filesystem publication and the caller's SQLite commit are not atomic.
    """
    if (
        not _UUID.fullmatch(project_id)
        or not _ID.fullmatch(space_id)
        or not _ID.fullmatch(resource_id)
    ):
        raise DomainError("Space provisioning identity is not canonical", 422)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    opened: list[int] = []
    chain: list[tuple[int, str, int]] = []

    def private_directory(fd: int) -> os.stat_result:
        info = os.fstat(fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise OSError("directory_not_owned_or_private")
        return info

    def reattest() -> None:
        visible = os.stat(runtime_dir, follow_symlinks=False)
        pinned = private_directory(opened[0])
        if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != (
            pinned.st_dev,
            pinned.st_ino,
        ):
            raise OSError("runtime_directory_changed")
        for parent, name, fd in chain:
            visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
            pinned = private_directory(fd)
            if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != (
                pinned.st_dev,
                pinned.st_ino,
            ):
                raise OSError("space_directory_changed")

    try:
        if not runtime_dir.is_absolute() or runtime_dir.resolve(strict=True) != runtime_dir:
            raise OSError("runtime_alias")
        parent = os.open(runtime_dir, flags)
        opened.append(parent)
        private_directory(parent)
        for name in ("spaces", project_id, space_id):
            reattest()
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            child = os.open(name, flags, dir_fd=parent)
            opened.append(child)
            chain.append((parent, name, child))
            private_directory(child)
            parent = child
        reattest()
        try:
            os.stat(resource_id, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DomainError(
                "Space resource root already exists; explicit registration required", 409
            )
        prepared = f".provision-{resource_id}-{secrets.token_hex(16)}"
        # The reservation is intentionally retained on success and failure.
        # It is owner-readable recovery metadata, not an Agent-access grant.
        reservation = os.open(
            f".provision-{resource_id}",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        opened.append(reservation)
        _write_all(reservation, prepared.encode("ascii") + b"\n")
        os.fsync(parent)
        reattest()
        os.mkdir(prepared, mode=0o700, dir_fd=parent)
        created = os.stat(prepared, dir_fd=parent, follow_symlinks=False)
        root_fd = os.open(prepared, flags, dir_fd=parent)
        opened.append(root_fd)
        root = private_directory(root_fd)
        if (root.st_dev, root.st_ino) != (created.st_dev, created.st_ino):
            raise OSError("prepared_space_root_changed")
        chain.append((parent, prepared, root_fd))
        seeds: list[tuple[str, bytes, int, os.stat_result]] = []
        _verify_seed_set(root_fd, seeds)
        for name, content in files:
            reattest()
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
            opened.append(fd)
            _write_all(fd, content)
            seeds.append((name, content, fd, os.fstat(fd)))
        os.fsync(root_fd)
        reattest()
        _verify_seed_set(root_fd, seeds)
        _publish_noreplace(parent, prepared, resource_id)
        chain[-1] = (parent, resource_id, root_fd)
        os.fsync(parent)
        reattest()
        _verify_seed_set(root_fd, seeds)
        return root.st_dev, root.st_ino
    except DomainError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise DomainError(
            "Space provisioning unsafe or incomplete; created data preserved", 409
        ) from None
    finally:
        for fd in reversed(opened):
            os.close(fd)
