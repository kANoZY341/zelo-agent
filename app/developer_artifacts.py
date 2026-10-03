"""Verified executable bytes, sealed independently of the configured pathname."""

import fcntl
import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.developer_contracts import WorkerArtifactIdentity
from app.developer_process import DeveloperError

SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
MAX_BINARY_BYTES = 512 * 1024 * 1024


class ArtifactCleanupObservation:
    """Trusted one-shot settlement evidence; default callers retain their API."""

    def __init__(self) -> None:
        self._claimed = False
        self._terminal = False
        self._confirmed = False

    @property
    def cleanup_confirmed(self) -> bool:
        return self._terminal and self._confirmed

    def _claim(self) -> None:
        if self._claimed:
            raise DeveloperError("artifact_observation_reused")
        self._claimed = True


@dataclass(frozen=True)
class PinnedExecutable:
    source: Path
    digest: str
    fd: int

    def verify(self) -> None:
        if fcntl.fcntl(self.fd, fcntl.F_GET_SEALS) & SEALS != SEALS:
            raise DeveloperError("worker_artifact_unsealed")
        os.lseek(self.fd, 0, os.SEEK_SET)


@contextmanager
def pin_artifact(
    configured: Path,
    *,
    approved_path: str | None = None,
    approved_digest: str | None = None,
    name: str,
    cleanup_observation: ArtifactCleanupObservation | None = None,
):
    """Never execute the source. Runtime requires a match before yielding any bytes.

    Submission has no existing approval: trusted ownership plus a sealed/digested
    snapshot establishes the proposed identity. Runtime supplies that identity.
    """
    if cleanup_observation is not None:
        if type(cleanup_observation) is not ArtifactCleanupObservation:
            raise DeveloperError("artifact_observation_invalid")
        cleanup_observation._claim()
    source_fd = artifact_fd = None
    try:
        try:
            source = configured.resolve(strict=True)
            if approved_path is not None and str(source) != approved_path:
                raise DeveloperError("worker_configuration_changed")
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            before = os.fstat(source_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_mode & 0o022
                or before.st_uid not in {0, os.getuid()}
                or not 0 < before.st_size <= MAX_BINARY_BYTES
            ):
                raise DeveloperError("codex_executable_untrusted")
            artifact_fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            digest = hashlib.sha256()
            total = 0
            while block := os.read(source_fd, 65536):
                total += len(block)
                if total > MAX_BINARY_BYTES:
                    raise DeveloperError("codex_executable_untrusted")
                digest.update(block)
                pending = memoryview(block)
                while pending:
                    pending = pending[os.write(artifact_fd, pending) :]
            after = os.fstat(source_fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise DeveloperError("worker_configuration_changed")
            hexdigest = digest.hexdigest()
            if approved_digest is not None and hexdigest != approved_digest:
                raise DeveloperError("worker_configuration_changed")
            os.fchmod(artifact_fd, 0o500)
            fcntl.fcntl(artifact_fd, fcntl.F_ADD_SEALS, SEALS)
            # Independently verify the sealed copy, not another open of the source.
            os.lseek(artifact_fd, 0, os.SEEK_SET)
            copied = hashlib.sha256()
            while block := os.read(artifact_fd, 65536):
                copied.update(block)
            if copied.hexdigest() != hexdigest:
                raise DeveloperError("worker_artifact_mismatch")
            artifact = PinnedExecutable(source, hexdigest, artifact_fd)
            artifact.verify()
        except OSError:
            raise DeveloperError("codex_executable_unavailable") from None
        yield artifact
    finally:
        confirmed = True
        for descriptor in (source_fd, artifact_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    confirmed = False
        if cleanup_observation is not None:
            cleanup_observation._confirmed = confirmed
            cleanup_observation._terminal = True


@contextmanager
def pin_executable(configured: Path, approved: WorkerArtifactIdentity | None = None):
    """Pin an executable using its approval-bound generic artifact identity."""
    with pin_artifact(
        configured,
        approved_path=approved.path if approved else None,
        approved_digest=approved.sha256 if approved else None,
        name="zelo-approved-worker-executable",
    ) as artifact:
        yield artifact
