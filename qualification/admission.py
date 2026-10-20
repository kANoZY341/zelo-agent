"""Linux host admission for trusted qualification callers only; fixed capacity ONE.

The operator must supply one canonical persistent 0700 directory on a trusted local
filesystem. Ancestors/mounts and the same-UID operator are trusted: this is not a
boundary against a hostile host owner. Production must not choose a directory per
request. flock is advisory and does not stop orphan children. Unknown markers require
future explicit operator reconciliation; no PID inference, TTL, or recovery here.
"""

import fcntl
import json
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path

from qualification.runtime_contracts import validate_digest


class AdmissionDenied(ValueError):
    """Busy, fenced, or unsafe admission state."""


def _private(fd: int, *, directory: bool = False) -> None:
    info = os.fstat(fd)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or (not directory and info.st_nlink != 1)
    ):
        raise AdmissionDenied("unsafe_runtime_state")


def _file(directory: int, name: str) -> int:
    fd = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        0o600,
        dir_fd=directory,
    )
    try:
        _private(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


class Lease:
    """Opaque ownership token. Only trusted cleanup confirmation may clear its marker."""

    def __init__(self, directory: int, lock: int, marker: int, identity: str, digest: str) -> None:
        self._identity = identity
        self._policy_digest = digest
        self._fds: tuple[int, int, int] | None = (directory, lock, marker)

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def policy_digest(self) -> str:
        return self._policy_digest

    def finish(self, *, cleanup_confirmed: bool) -> None:
        if self._fds is None:
            return
        directory, lock, marker = self._fds
        try:
            if cleanup_confirmed is True:
                _private(marker)
                os.ftruncate(marker, 0)
                os.fsync(marker)
                os.fsync(directory)
        finally:
            self._fds = None
            # Never unlink either file: all contenders must lock the same inode.
            os.close(marker)
            os.close(lock)
            os.close(directory)


class AdmissionController:
    def __init__(self, runtime_directory: Path) -> None:
        self._path = runtime_directory

    def acquire(self, policy_digest: str) -> Lease:
        return self._acquire(policy_digest, before_marker=None)

    def acquire_after(self, policy_digest: str, before_marker: Callable[[], None]) -> Lease:
        """Run a trusted, synchronous writer under the admission lock before lease marking.

        The writer must fence its own uncertain durable side effects; this API never
        rolls them back. It must return normally only after its work is durable.
        """
        if not callable(before_marker):
            raise AdmissionDenied("admission_writer_missing")
        return self._acquire(policy_digest, before_marker=before_marker)

    def _acquire(self, policy_digest: str, *, before_marker: Callable[[], None] | None) -> Lease:
        validate_digest(policy_digest)
        descriptors: list[int] = []
        try:
            directory = os.open(
                self._path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            descriptors.append(directory)
            _private(directory, directory=True)
            lock = _file(directory, "admission.lock")
            descriptors.append(lock)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            marker = _file(directory, "active.marker")
            descriptors.append(marker)
            if os.fstat(marker).st_size != 0:
                raise AdmissionDenied("runtime_fenced")
            if before_marker is not None:
                before_marker()
                if os.fstat(marker).st_size != 0:
                    raise AdmissionDenied("runtime_fenced")
            identity = secrets.token_hex(32)
            payload = json.dumps(
                {"version": 1, "lease": identity, "policy": policy_digest}
            ).encode()
            if os.write(marker, payload) != len(payload):
                raise AdmissionDenied("marker_write_failed")
            os.fsync(marker)
            os.fsync(directory)
            lease = Lease(directory, lock, marker, identity, policy_digest)
            descriptors.clear()
            return lease
        except OSError as exc:
            raise AdmissionDenied("runtime_unavailable") from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
