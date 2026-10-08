"""Offline operator snapshots only: no analyzer, Core authority, or runtime imports."""

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
INBOX = ROOT / "workspaces" / "rea-target-inbox"
STAGING = ROOT / "workspaces" / "rea-target-snapshots"
LIMITS = {
    "files": 128,
    "file_bytes": 512 * 1024,
    "total_bytes": 4 * 1024 * 1024,
    "depth": 5,
    "entries": 256,
}
EXTENSIONS = frozenset({".js", ".mjs", ".cjs", ".json", ".html", ".css", ".map"})
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
IDENTITY = re.compile(r"target-[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
MAX_RECEIPT_BYTES = 96 * 1024


class AdmissionRefusal(ValueError):
    """Bounded error codes only; never include source names or contents."""


def _require(condition: bool, reason: str = "unsafe_or_changed_input") -> None:
    if not condition:
        raise AdmissionRefusal(reason)


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _check_link(parent: int, name: str, expected: tuple[int, int]) -> None:
    _require(
        _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected,
        "directory_replaced",
    )


@contextmanager
def _directory(parent: int, name: str, *, private: bool = False) -> Iterator[int]:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _require(stat.S_ISDIR(before.st_mode))
    fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    try:
        info = os.fstat(fd)
        _require(_stamp(before) == _stamp(info))
        _require(not info.st_mode & 0o022)
        if private:
            _require(info.st_uid == os.geteuid() and not info.st_mode & 0o077)
        yield fd
        _require(
            _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == _identity(info),
            "directory_replaced",
        )
    finally:
        os.close(fd)


@contextmanager
def _fixed_root(path: Path) -> Iterator[int]:
    """Anchor every ancestor; never resolve through a symlink or writable parent."""
    _require(path.is_absolute() and ".." not in path.parts)
    with ExitStack() as stack:
        fd = os.open("/", DIR_FLAGS)
        stack.callback(os.close, fd)
        system_uid = os.fstat(fd).st_uid
        for part in path.parts[1:]:
            # A system-owned sticky ancestor (e.g. /tmp in tests) protects the
            # operator-owned child. It is never allowed as inbox/staging itself.
            info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            _require(info.st_uid in {system_uid, os.geteuid()})
            if info.st_mode & 0o022:
                _require(
                    stat.S_ISDIR(info.st_mode)
                    and info.st_mode & stat.S_ISVTX
                    and info.st_uid == system_uid
                    and part != path.name
                )
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                stack.callback(os.close, child)
                _require(_stamp(info) == _stamp(os.fstat(child)))
                stack.callback(_check_link, fd, part, _identity(info))
                fd = child
            else:
                fd = stack.enter_context(_directory(fd, part))
        info = os.fstat(fd)
        _require(
            info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "private_root_required",
        )
        yield fd


def _read(parent: int, name: str, maximum: int, mode: int | None = None) -> bytes:
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
    _require(before.st_uid == os.geteuid() and not before.st_mode & 0o022)
    _require(before.st_size <= maximum, "size_limit")
    if mode is not None:
        _require(stat.S_IMODE(before.st_mode) == mode)
    fd = os.open(name, READ_FLAGS, dir_fd=parent)
    try:
        _require(_stamp(before) == _stamp(os.fstat(fd)))
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        _require(len(data) == before.st_size and len(data) <= maximum, "changed_read")
        _require(_stamp(before) == _stamp(os.fstat(fd)), "changed_read")
        _require(
            _stamp(before) == _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)),
            "changed_read",
        )
        return bytes(data)
    finally:
        os.close(fd)


def _text(data: bytes) -> None:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise AdmissionRefusal("text_only") from None
    _require(
        not any(value < 32 and value not in (9, 10, 13) or value == 127 for value in data),
        "text_only",
    )


def _scan(
    root: int, *, sealed: bool = False
) -> tuple[dict[str, bytes], dict[str, tuple[int, ...]]]:
    files: dict[str, bytes] = {}
    stamps: dict[str, tuple[int, ...]] = {}
    entries = 0
    total = 0

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal entries, total
        before = os.fstat(fd)
        _require(before.st_uid == os.geteuid())
        if sealed:
            _require(stat.S_IMODE(before.st_mode) == 0o500)
        stamps[prefix] = _stamp(before)
        # scandir streams the directory so an enormous inbox cannot allocate an
        # unbounded list before the entry limit is checked.
        names = []
        with os.scandir(fd) as iterator:
            for entry in iterator:
                entries += 1
                _require(entries <= LIMITS["entries"], "entry_limit")
                _require(
                    NAME.fullmatch(entry.name) is not None and depth <= LIMITS["depth"],
                    "unsafe_name_or_depth",
                )
                names.append(entry.name)
        _require(bool(names), "empty_directory")
        for name in sorted(names):
            relative = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            stamps[relative] = _stamp(info)
            if stat.S_ISDIR(info.st_mode):
                with _directory(fd, name) as child:
                    walk(child, relative + "/", depth + 1)
            else:
                _require(Path(name).suffix in EXTENSIONS, "unsupported_extension")
                _require(len(files) < LIMITS["files"], "file_limit")
                data = _read(fd, name, LIMITS["file_bytes"], 0o400 if sealed else None)
                _text(data)
                total += len(data)
                _require(total <= LIMITS["total_bytes"], "total_limit")
                files[relative] = data
        _require(_stamp(before) == _stamp(os.fstat(fd)), "changed_directory")

    walk(root, "", 1)
    return files, stamps


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _receipt(identity: str, files: dict[str, bytes]) -> dict:
    records = [
        {"path": path, "size": len(data), "sha256": _sha(data)}
        for path, data in sorted(files.items())
    ]
    return {
        "schema": "zelo-rea-inert-target-v1",
        "snapshot_id": identity,
        "provenance": {"selection": "operator-fixed-inbox", "operator_uid": os.geteuid()},
        "limits": LIMITS,
        "files": records,
        "file_count": len(records),
        "total_bytes": sum(len(data) for data in files.values()),
        "content_sha256": _sha(_json(records)),
        "execution_enabled": False,
        "worker_registered": False,
        "grants": [],
        "core_approval": None,
    }


def _summary(receipt: dict, digest: str) -> dict:
    # No local names, per-file paths or target bytes leave this operator utility.
    return {
        key: receipt[key]
        for key in (
            "snapshot_id",
            "file_count",
            "total_bytes",
            "content_sha256",
            "execution_enabled",
            "worker_registered",
            "grants",
            "core_approval",
        )
    } | {"receipt_sha256": digest}


def _write(parent: int, name: str, data: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent,
    )
    try:
        remaining = memoryview(data)
        while remaining:
            count = os.write(fd, remaining)
            _require(count > 0, "write_failed")
            remaining = remaining[count:]
        os.fchmod(fd, 0o400)
        os.fsync(fd)
    finally:
        os.close(fd)


def _populate(fd: int, files: dict[str, bytes]) -> None:
    for path, data in sorted(files.items()):
        with ExitStack() as stack:
            parent = fd
            parts = path.split("/")
            for part in parts[:-1]:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                parent = stack.enter_context(_directory(parent, part, private=True))
            _write(parent, parts[-1], data)


def _seal(fd: int) -> None:
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with _directory(fd, name, private=True) as child:
                _seal(child)
    os.fchmod(fd, 0o500)
    os.fsync(fd)


def _cleanup(fd: int) -> None:
    """Only the exclusive private scratch tree; never follow a replacement link."""
    os.fchmod(fd, 0o700)
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with _directory(fd, name, private=True) as child:
                _cleanup(child)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


def admit_target(folder: str, *, operator_opt_in: bool = False) -> dict:
    """Select one basename in the fixed inbox; return only bounded opaque metadata."""
    _require(operator_opt_in is True, "explicit_opt_in_required")
    _require(isinstance(folder, str) and NAME.fullmatch(folder) is not None, "unsafe_selection")
    with ExitStack() as recovery:
        scratch_copy: int | None = None
        staging_copy: int | None = None
        reserved = False
        try:
            with _fixed_root(INBOX) as inbox, _fixed_root(STAGING) as staging:
                with _directory(inbox, folder, private=True) as source:
                    files, stamps = _scan(source)
                    identity = "target-" + secrets.token_hex(16)
                    staging_copy = os.dup(staging)
                    recovery.callback(os.close, staging_copy)
                    os.mkdir(identity, 0o700, dir_fd=staging)
                    reserved = True
                    with _directory(staging, identity, private=True) as scratch:
                        # Retain handles through ALL ancestor exit checks so a
                        # late source/root replacement also cleans our scratch.
                        scratch_copy = os.dup(scratch)
                        recovery.callback(os.close, scratch_copy)
                        os.mkdir("files", 0o700, dir_fd=scratch)
                        with _directory(scratch, "files", private=True) as target:
                            _populate(target, files)
                        _require((files, stamps) == _scan(source), "source_changed")
                        receipt = _receipt(identity, files)
                        encoded = _json(receipt)
                        _require(len(encoded) <= MAX_RECEIPT_BYTES, "receipt_limit")
                        _write(scratch, "receipt.json", encoded)
                        _seal(scratch)
            return _summary(receipt, _sha(encoded))
        except BaseException as exc:
            if reserved and staging_copy is not None:
                try:
                    if scratch_copy is not None:
                        _check_link(staging_copy, identity, _identity(os.fstat(scratch_copy)))
                        _cleanup(scratch_copy)
                    # Without a scratch handle, failure occurred before any
                    # content was created. rmdir never follows links/recurses.
                    os.rmdir(identity, dir_fd=staging_copy)
                except OSError:
                    raise AdmissionRefusal("cleanup_failed") from None
            if isinstance(exc, OSError):
                raise AdmissionRefusal("filesystem_refusal") from None
            raise


def verify_target(identity: str, receipt_sha256: str) -> dict:
    """Verify against the independently retained admission digest, never self-trust JSON."""
    _require(
        isinstance(identity, str) and IDENTITY.fullmatch(identity) is not None,
        "invalid_snapshot_identity",
    )
    _require(
        isinstance(receipt_sha256, str) and HEX.fullmatch(receipt_sha256) is not None,
        "invalid_receipt_digest",
    )
    try:
        with _fixed_root(STAGING) as staging:
            with _directory(staging, identity, private=True) as snapshot:
                before = os.fstat(snapshot)
                _require(stat.S_IMODE(before.st_mode) == 0o500)
                names = set()
                with os.scandir(snapshot) as iterator:
                    for entry in iterator:
                        names.add(entry.name)
                        _require(len(names) <= 2, "unexpected_snapshot_entry")
                _require(names == {"files", "receipt.json"})
                encoded = _read(snapshot, "receipt.json", MAX_RECEIPT_BYTES, 0o400)
                _require(_sha(encoded) == receipt_sha256, "receipt_changed")
                with _directory(snapshot, "files", private=True) as target:
                    files, stamps = _scan(target, sealed=True)
                    _require((files, stamps) == _scan(target, sealed=True), "snapshot_changed")
                receipt = _receipt(identity, files)
                _require(encoded == _json(receipt), "snapshot_changed")
                _require(_stamp(before) == _stamp(os.fstat(snapshot)), "snapshot_changed")
                _require(encoded == _read(snapshot, "receipt.json", MAX_RECEIPT_BYTES, 0o400))
                return _summary(receipt, receipt_sha256)
    except OSError:
        raise AdmissionRefusal("filesystem_refusal") from None
