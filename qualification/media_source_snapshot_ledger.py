"""One-use private Stage S evidence allocation; never remove a consumed attempt."""

import hashlib
import os
import stat
import tempfile
from pathlib import Path

RELEASE_CAP = 524288
SOURCES_CAP = 4194304
_CAPS = {"InRelease": RELEASE_CAP, "Sources.xz": SOURCES_CAP}
_JOURNALS = frozenset(
    ("policy.json", "manifest.json", "cases.json", "failure.json",
     "verification.json", "request.json", "plaintext.bin", "status.bin")
)
JOURNAL_BYTES = 1048576
_QUARANTINED_FDS: list[int] = []


class LedgerRefused(ValueError):
    """Stable failure without peer-controlled content."""


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def allocate_attempt(base: Path) -> Path:
    """Consume the single allocation even when later work fails."""
    try:
        base.mkdir(mode=0o700)
    except FileExistsError:
        created = False
    else:
        created = True
    info = base.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise LedgerRefused("media_snapshot_proof_root")
    if created:
        _sync_directory(base.parent)
    with os.scandir(base) as entries:
        if next(entries, None) is not None:
            raise LedgerRefused("media_snapshot_attempt_consumed")
    path = Path(tempfile.mkdtemp(prefix="snapshot-proof-", dir=base))
    _sync_directory(base)
    return path


class StageSLedger:
    def __init__(self, path: Path):
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise LedgerRefused("media_snapshot_proof_identity")
        self.path = path
        self.written: dict[str, int] = {}
        self.journal_names: set[str] = set()
        self.journal_bytes = 0

    def save_journal(self, name: str, raw: bytes) -> None:
        if (
            type(name) is not str
            or name not in _JOURNALS
            or name in self.journal_names
            or len(self.journal_names) >= 8
            or type(raw) is not bytes
            or not 0 < len(raw) <= 524288
            or self.journal_bytes + len(raw) > JOURNAL_BYTES
        ):
            raise LedgerRefused("media_snapshot_journal_reservation")
        self.journal_names.add(name)
        self.journal_bytes += len(raw)
        fd = os.open(
            self.path / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                if count <= 0 or count > len(view):
                    raise LedgerRefused("media_snapshot_journal_write")
                view = view[count:]
            os.fsync(fd)
            _sync_directory(self.path)
            os.close(fd)
        except BaseException:
            _QUARANTINED_FDS.append(fd)
            raise

    def open_body(self, name: str, cap: int) -> int:
        if name not in _CAPS or type(cap) is not int or cap != _CAPS[name] or name in self.written:
            raise LedgerRefused("media_snapshot_body_reservation")
        self.written[name] = 0  # Consume before creation; a failed open is never retried.
        fd = os.open(
            self.path / name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            0o600,
        )
        try:
            self._identity(fd, 0)
            _sync_directory(self.path)
        except BaseException:
            _QUARANTINED_FDS.append(fd)  # Do not retry an uncertain descriptor number.
            raise
        return fd

    @staticmethod
    def _identity(fd: int, size: int) -> os.stat_result:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != size
        ):
            raise LedgerRefused("media_snapshot_body_identity")
        return info

    def write_body(self, name: str, fd: int, chunk: bytes) -> None:
        if name not in self.written or type(chunk) is not bytes or not 0 < len(chunk) <= 65536:
            raise LedgerRefused("media_snapshot_body_chunk")
        if self.written[name] + len(chunk) > _CAPS[name]:
            raise LedgerRefused("media_snapshot_body_bound")
        self.written[name] += len(chunk)  # Charge before any partial write.
        view = memoryview(chunk)
        while view:
            count = os.write(fd, view)
            if count <= 0 or count > len(view):
                raise LedgerRefused("media_snapshot_body_write")
            view = view[count:]

    def finish_body(self, name: str, fd: int, size: int, sha256: str | None = None) -> bytes:
        if (
            name not in self.written
            or type(size) is not int
            or not 0 < size <= _CAPS[name]
            or self.written[name] != size
        ):
            raise LedgerRefused("media_snapshot_body_size")
        os.fsync(fd)
        before = self._identity(fd, size)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = bytearray()
        while chunk := os.read(fd, min(65536, size - len(raw) + 1)):
            raw.extend(chunk)
            if len(raw) > size:
                raise LedgerRefused("media_snapshot_body_read")
        after = self._identity(fd, size)
        stable = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            len(raw) != size
            or any(getattr(before, name) != getattr(after, name) for name in stable)
            or (sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256)
        ):
            raise LedgerRefused("media_snapshot_body_drift")
        return bytes(raw)

    def persist_partial(self, name: str, fd: int) -> dict:
        """Durably retain failed response bytes, including a zero-byte allocation."""
        if name not in self.written:
            raise LedgerRefused("media_snapshot_partial_name")
        os.fsync(fd)
        before = os.fstat(fd)
        if not 0 <= before.st_size <= self.written[name] <= _CAPS[name]:
            raise LedgerRefused("media_snapshot_partial_bound")
        self._identity(fd, before.st_size)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = bytearray()
        while chunk := os.read(fd, min(65536, before.st_size - len(raw) + 1)):
            raw.extend(chunk)
            if len(raw) > before.st_size:
                raise LedgerRefused("media_snapshot_partial_read")
        after = self._identity(fd, before.st_size)
        stable = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(raw) != before.st_size or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise LedgerRefused("media_snapshot_partial_drift")
        _sync_directory(self.path)
        return {
            "name": name,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "accepted": False,
        }
