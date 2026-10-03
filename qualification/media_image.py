"""Managed sealed package bytes; no package import or execution authority."""

import fcntl
import hashlib
import io
import json
import os
import resource
import stat
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from qualification.artifacts import ArtifactFile, ArtifactManifest
from qualification.media_preparation import PREPARATION_POLICY_DIGEST, _expected, _write_all
from qualification.media_wheel import (
    ARCHIVE_BYTES,
    FILE_BYTES,
    TOTAL_BYTES,
    WHEEL_FILENAME,
    WHEEL_POLICY_DIGEST,
    MediaArtifactError,
    PillowWheelInventory,
    inspect_pillow_wheel,
)

SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
FILE_COUNT = 140
DIRECTORY_COUNT = 6
FIXED_ARCHIVE_BYTES = 6940830
FIXED_EXPANDED_BYTES = 19667368
NORMALIZED_MANIFEST_DIGEST = "47adde6932ce7c0c40cf7f781fe142057465771bc2895b00e80f9fdc411a5dc7"
SUPERVISOR_FDS = 256
RESERVED_FDS = 16
BUFFER_BYTES = 65536


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def image_policy() -> dict:
    return {
        "schema": "zelo-media-sealed-file-image-policy-v1",
        "archive_bytes": ARCHIVE_BYTES,
        "file_bytes": FILE_BYTES,
        "total_bytes": TOTAL_BYTES,
        "fixed_archive_bytes": FIXED_ARCHIVE_BYTES,
        "fixed_files": FILE_COUNT,
        "fixed_directories": DIRECTORY_COUNT,
        "fixed_expanded_bytes": FIXED_EXPANDED_BYTES,
        "normalized_manifest_digest": NORMALIZED_MANIFEST_DIGEST,
        "preparation_policy_digest": PREPARATION_POLICY_DIGEST,
        "seals": ("WRITE", "GROW", "SHRINK", "SEAL"),
        "supervisor_fds": SUPERVISOR_FDS,
        "reserved_fds": RESERVED_FDS,
        "read_buffer_bytes": BUFFER_BYTES,
        "one_live_image": True,
        "one_consumer": True,
        "unknown_native_cleanup": "retain-image-descriptors-and-block-new-owner",
        "source": "same-inspected-immutable-wheel-bytes",
        "permission_metadata_immutable": False,
        "offsets_immutable": False,
        "hard_io_deadline": False,
        "aggregate_host_memory_enforced": False,
        "publisher_attribution": "separate",
        "instruction_trust": "none",
        "execution_authority": False,
    }


IMAGE_POLICY_DIGEST = _digest(image_policy())


@dataclass(frozen=True, init=False)
class SealedImageIdentity:
    """Historical construction identity; carries no descriptor or live permission."""

    archive_sha256: str
    inventory_digest: str
    manifest: ArtifactManifest
    filename: str = field(default=WHEEL_FILENAME, init=False)
    wheel_policy_digest: str = field(default=WHEEL_POLICY_DIGEST, init=False)
    policy_digest: str = field(default=IMAGE_POLICY_DIGEST, init=False)
    schema: str = field(default="zelo-media-sealed-file-image-v1", init=False)
    instruction_trust: str = field(default="none", init=False)
    publisher_attribution: str = field(default="separate", init=False)

    def __init__(self, *args, **kwargs):
        raise TypeError("media_image_factory_required")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


_REGISTRY_LOCK = threading.Lock()
_LIVE_IMAGES: set = set()


class ManagedPillowImage:
    """One-shot owner. Only trusted qualification code borrows private handles."""

    def __init__(self, *args, **kwargs):
        raise TypeError("media_image_factory_required")

    @property
    def identity(self) -> SealedImageIdentity:
        with self._lock:
            if self._identity is None:
                raise MediaArtifactError("media_image_incomplete")
            return self._identity

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def cleanup_confirmed(self) -> bool:
        with self._lock:
            return self._state == "closed" and self._cleanup_confirmed

    def _verify_locked(self) -> None:
        for item, fd, identity in self._files:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or (info.st_dev, info.st_ino) != identity
                or info.st_uid != os.getuid()
                or info.st_nlink != 0
                or info.st_size != item.size
                or stat.S_IMODE(info.st_mode) != item.mode
                or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & SEALS != SEALS
            ):
                raise MediaArtifactError("media_image_descriptor")
            os.lseek(fd, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            total = 0
            while block := os.read(fd, min(BUFFER_BYTES, item.size - total + 1)):
                total += len(block)
                if total > item.size:
                    raise MediaArtifactError("media_image_size")
                digest.update(block)
            if total != item.size or digest.hexdigest() != item.sha256:
                raise MediaArtifactError("media_image_hash")
            after = os.fstat(fd)
            if (info.st_dev, info.st_ino, info.st_size, info.st_mode) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mode,
            ):
                raise MediaArtifactError("media_image_descriptor")
            os.lseek(fd, 0, os.SEEK_SET)

    def _begin_consumer(self) -> tuple[tuple[ArtifactFile, int], ...]:
        with self._lock:
            if self._state != "ready" or self._close_requested:
                raise MediaArtifactError("media_image_not_ready")
            self._state = "consuming"
            try:
                self._verify_locked()
            except BaseException:
                self._state = "failed"
                raise
            return tuple((item, fd) for item, fd, _ in self._files)

    def _end_consumer(self, cleanup_confirmed: bool) -> None:
        with self._lock:
            if self._state != "consuming":
                raise MediaArtifactError("media_image_not_consuming")
            confirmed = type(cleanup_confirmed) is bool and cleanup_confirmed
            self._state = "ready" if confirmed else "failed"
            self._consumer_cleanup_unknown = not confirmed
            close_requested = self._close_requested
        if close_requested:
            self._close()
        if not confirmed:
            raise MediaArtifactError("media_image_consumer_cleanup_unknown")

    def _settle_failed_consumer(self, cleanup_confirmed: bool) -> None:
        # Trusted owner continuation only; never re-enable a failed image.
        with self._lock:
            if not self._consumer_cleanup_unknown or self._state != "failed":
                raise MediaArtifactError("media_image_not_pending")
            if type(cleanup_confirmed) is not bool or not cleanup_confirmed:
                raise MediaArtifactError("media_image_consumer_cleanup_unknown")
            self._consumer_cleanup_unknown = False
            close_requested = self._close_requested
        if close_requested:
            self._close()

    def _close(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            if self._state == "closed_failed":
                raise MediaArtifactError("media_image_close_unknown")
            if self._state == "consuming" or self._consumer_cleanup_unknown:
                self._close_requested = True
                raise MediaArtifactError(
                    "media_image_consumer_cleanup_unknown"
                    if self._consumer_cleanup_unknown
                    else "media_image_consumer_pending"
                )
            self._state = "closed_failed"
            failed = self._unregistered_close_failed
            for _, fd, _ in self._files:
                try:
                    os.close(fd)
                except BaseException:
                    failed = True
            self._files.clear()
            if failed:
                # Never retry descriptor numbers; uncertainty blocks another owner.
                raise MediaArtifactError("media_image_close_unknown")
            self._state = "closed"
            self._cleanup_confirmed = True
        with _REGISTRY_LOCK:
            _LIVE_IMAGES.remove(self)


def _new_owner() -> ManagedPillowImage:
    with _REGISTRY_LOCK:
        if _LIVE_IMAGES:
            raise MediaArtifactError("media_image_live_owner")
        owner = object.__new__(ManagedPillowImage)
        owner._lock = threading.Lock()
        owner._files = []
        owner._identity = None
        owner._state = "constructing"
        owner._cleanup_confirmed = False
        owner._close_requested = False
        owner._unregistered_close_failed = False
        owner._consumer_cleanup_unknown = False
        _LIVE_IMAGES.add(owner)
        return owner


def _capacity(files: int) -> None:
    count = 0
    with os.scandir("/proc/self/fd") as entries:
        for _ in entries:
            count += 1
            if count + files + RESERVED_FDS > SUPERVISOR_FDS:
                raise MediaArtifactError("media_image_descriptor_capacity")
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and soft < count + files + RESERVED_FDS:
        raise MediaArtifactError("media_image_descriptor_capacity")


def _copy_member(archive: zipfile.ZipFile, item: ArtifactFile, fd: int) -> None:
    total = 0
    digest = hashlib.sha256()
    with archive.open(item.path) as source:
        while block := source.read(min(BUFFER_BYTES, item.size - total + 1)):
            total += len(block)
            if total > item.size or total > FILE_BYTES:
                raise MediaArtifactError("media_image_size")
            digest.update(block)
            _write_all(fd, block)
    if total != item.size or digest.hexdigest() != item.sha256:
        raise MediaArtifactError("media_image_hash")
    os.fchmod(fd, item.mode)
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, SEALS)


@contextmanager
def _seal_image(data: bytes, inventory: PillowWheelInventory, *, _fixed: bool = False):
    if (
        type(data) is not bytes
        or type(inventory) is not PillowWheelInventory
        or len(data) != inventory.archive_bytes
        or hashlib.sha256(data).hexdigest() != inventory.archive_sha256
    ):
        raise MediaArtifactError("media_image_source")
    inventory.__post_init__()
    manifest = _expected(inventory)
    if len(manifest.files) > FILE_COUNT:
        raise MediaArtifactError("media_image_file_count")
    if _fixed and (
        inventory.archive_bytes != FIXED_ARCHIVE_BYTES
        or inventory.uncompressed_bytes != FIXED_EXPANDED_BYTES
        or len(manifest.files) != FILE_COUNT
        or len(manifest.directories) != DIRECTORY_COUNT
        or manifest.digest != NORMALIZED_MANIFEST_DIGEST
    ):
        raise MediaArtifactError("media_image_fixed_closure")
    _capacity(len(manifest.files))
    owner = _new_owner()
    pending_fd = None
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r", allowZip64=False) as archive:
            for item in manifest.files:
                pending_fd = os.memfd_create(
                    "zelo-media-image", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
                )
                fd = pending_fd
                # Retain even if allocating the registration tuple/list fails.
                owner._files.append((item, fd, None))
                pending_fd = None
                info = os.fstat(fd)
                owner._files[-1] = (item, fd, (info.st_dev, info.st_ino))
                _copy_member(archive, item, fd)
        with owner._lock:
            owner._verify_locked()
            identity = object.__new__(SealedImageIdentity)
            object.__setattr__(identity, "archive_sha256", inventory.archive_sha256)
            object.__setattr__(identity, "inventory_digest", inventory.digest)
            object.__setattr__(identity, "manifest", manifest)
            owner._identity = identity
            owner._state = "ready"
        yield owner
    finally:
        try:
            if pending_fd is not None:
                try:
                    os.close(pending_fd)
                except BaseException:
                    owner._unregistered_close_failed = True
        finally:
            owner._close()


@contextmanager
def seal_pillow_image(data: bytes):
    """Inspect fixed bytes before allocation; owner must outlive every consumer."""
    inventory = inspect_pillow_wheel(data)
    with _seal_image(data, inventory, _fixed=True) as image:
        yield image
