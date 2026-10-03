"""Operator-only normalized copies; no package import or execution authority."""

import hashlib
import io
import json
import os
import stat
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from qualification.artifacts import (
    ArtifactFile,
    ArtifactManifest,
    ArtifactPolicy,
    capture_manifest,
)
from qualification.media_wheel import (
    BUNDLED_LIBRARIES,
    ENTRY_LIMIT,
    FILE_BYTES,
    NATIVE_EXTENSIONS,
    TOTAL_BYTES,
    MediaArtifactError,
    PillowWheelInventory,
    inspect_pillow_wheel,
)

_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIRECTORY = _READ | os.O_DIRECTORY
_WRITE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
ATTEMPT_LIMIT = 4
NATIVE_PATHS = tuple(sorted(NATIVE_EXTENSIONS + BUNDLED_LIBRARIES))
ROLES = tuple((f"native_{index:02d}", path) for index, path in enumerate(NATIVE_PATHS))
SCAN_POLICY = ArtifactPolicy(ENTRY_LIMIT, 8, FILE_BYTES, TOTAL_BYTES, ENTRY_LIMIT)


def preparation_policy() -> dict:
    return {
        "schema": "zelo-media-file-preparation-policy-v1",
        "files": {"data": 0o400, "fixed_native": 0o500},
        "directories": 0o500,
        "roles": ROLES,
        "scan_policy": asdict(SCAN_POLICY),
        "initial_directory_mode": 0o700,
        "initial_file_mode": 0o600,
        "attempt_limit": ATTEMPT_LIMIT,
        "read_buffer_bytes": 65536,
        "publisher_attribution": "unverified",
        "instruction_trust": "none",
        "runtime_authority": False,
    }


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


PREPARATION_POLICY_DIGEST = _digest(preparation_policy())


@dataclass(frozen=True, init=False)
class PreparedPillowFiles:
    """Scanner-derived diagnostic receipt, never an executable selection."""

    archive_sha256: str
    inventory_digest: str
    manifest: ArtifactManifest
    relative_payload: str
    root_identity: tuple[int, int]
    schema: str = field(default="zelo-media-prepared-files-v1", init=False)
    policy_digest: str = field(default=PREPARATION_POLICY_DIGEST, init=False)
    publisher_attribution: str = field(default="unverified", init=False)
    instruction_trust: str = field(default="none", init=False)

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("media_preparation_scanner_required")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _owned(info: os.stat_result, directory: bool, mode: int) -> None:
    if (
        info.st_uid != os.getuid()
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or stat.S_IMODE(info.st_mode) != mode
        or (not directory and info.st_nlink != 1)
    ):
        raise MediaArtifactError("media_preparation_unsafe_file")


@contextmanager
def _descriptor(fd: int):
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _directory(root: int, path: str):
    # Every component is opened relative to a verified directory, never a ZIP path.
    with ExitStack() as stack:
        fd = stack.enter_context(_descriptor(os.dup(root)))
        for component in path.split("/") if path else ():
            initial = os.stat(component, dir_fd=fd, follow_symlinks=False)
            child = stack.enter_context(_descriptor(os.open(component, _DIRECTORY, dir_fd=fd)))
            opened = os.fstat(child)
            if _identity(initial) != _identity(opened):
                raise MediaArtifactError("media_preparation_changed")
            _owned(opened, True, stat.S_IMODE(opened.st_mode))
            if stat.S_IMODE(opened.st_mode) not in (0o700, 0o500):
                raise MediaArtifactError("media_preparation_unsafe_file")
            fd = child
        yield fd


def _expected(inventory: PillowWheelInventory) -> ArtifactManifest:
    directories = {""}
    for path, _ in inventory.directories:
        directories.add(path)
    for item in inventory.files:
        parts = item.path.split("/")[:-1]
        directories.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    if len(inventory.files) + len(directories) - 1 > ENTRY_LIMIT:
        raise MediaArtifactError("media_preparation_entry_limit")
    return ArtifactManifest(
        "zelo-browser-artifacts-v1",
        SCAN_POLICY,
        ROLES,
        tuple(
            ArtifactFile(
                item.path, item.size, 0o500 if item.path in NATIVE_PATHS else 0o400, item.sha256
            )
            for item in inventory.files
        ),
        tuple((path, 0o500) for path in sorted(directories)),
    )


def _write_all(fd: int, block: bytes) -> None:
    remaining = memoryview(block)
    while remaining:
        count = os.write(fd, remaining)
        if type(count) is not int or not 1 <= count <= len(remaining):
            raise MediaArtifactError("media_preparation_write_stalled")
        remaining = remaining[count:]


def _copy_file(archive: zipfile.ZipFile, item: ArtifactFile, root: int) -> None:
    parent, _, name = item.path.rpartition("/")
    with _directory(root, parent) as directory:
        with _descriptor(os.open(name, _WRITE, 0o600, dir_fd=directory)) as fd:
            opened = os.fstat(fd)
            _owned(opened, False, 0o600)
            size = 0
            digest = hashlib.sha256()
            with archive.open(item.path) as source:
                while block := source.read(min(65536, item.size - size + 1)):
                    size += len(block)
                    if size > item.size or size > FILE_BYTES:
                        raise MediaArtifactError("media_preparation_size")
                    digest.update(block)
                    _write_all(fd, block)
            if size != item.size or digest.hexdigest() != item.sha256:
                raise MediaArtifactError("media_preparation_content")
            written = os.fstat(fd)
            _owned(written, False, 0o600)
            if _identity(written) != _identity(opened) or written.st_size != size:
                raise MediaArtifactError("media_preparation_changed")
            os.fsync(fd)
            mode = 0o500 if item.path in NATIVE_PATHS else 0o400
            os.fchmod(fd, mode)
            os.fsync(fd)
            final = os.fstat(fd)
            _owned(final, False, mode)
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                _identity(final) != _identity(opened)
                or _identity(named) != _identity(final)
                or final.st_size != size
                or named.st_mode != final.st_mode
            ):
                raise MediaArtifactError("media_preparation_changed")


def _match_root(root: int, path: Path) -> tuple[int, int]:
    opened = os.fstat(root)
    _owned(opened, True, 0o500)
    named = os.stat(path, follow_symlinks=False)
    if _identity(named) != _identity(opened) or named.st_mode != opened.st_mode:
        raise MediaArtifactError("media_preparation_changed")
    return _identity(opened)


def _attempt(base: int) -> str:
    count = 0
    with os.scandir(base) as entries:
        for entry in entries:
            count += 1
            if count >= ATTEMPT_LIMIT:
                raise MediaArtifactError("media_preparation_attempt_limit")
            if not entry.name.startswith("attempt-"):
                raise MediaArtifactError("media_preparation_unknown_evidence")
            _owned(os.stat(entry.name, dir_fd=base, follow_symlinks=False), True, 0o700)
    name = "attempt-" + uuid.uuid4().hex
    os.mkdir(name, 0o700, dir_fd=base)
    os.fsync(base)
    return name


def _prepare_files(data: bytes, inventory: PillowWheelInventory, base: Path) -> PreparedPillowFiles:
    """Private synthetic-fixture seam; public entry always uses the closed inspector."""
    expected = _expected(inventory)
    try:
        try:
            os.mkdir(base, 0o700)
        except FileExistsError:
            pass
        initial = os.stat(base, follow_symlinks=False)
        _owned(initial, True, 0o700)
        with _descriptor(os.open(base, _DIRECTORY)) as base_fd:
            if _identity(initial) != _identity(os.fstat(base_fd)):
                raise MediaArtifactError("media_preparation_changed")
            attempt = _attempt(base_fd)
            with _descriptor(os.open(attempt, _DIRECTORY, dir_fd=base_fd)) as attempt_fd:
                _owned(os.fstat(attempt_fd), True, 0o700)
                os.mkdir("payload", 0o700, dir_fd=attempt_fd)
                with _descriptor(os.open("payload", _DIRECTORY, dir_fd=attempt_fd)) as root:
                    _owned(os.fstat(root), True, 0o700)
                    created_identity = _identity(os.fstat(root))
                    for path, _ in sorted(
                        expected.directories, key=lambda p: (p[0].count("/"), p[0])
                    ):
                        if not path:
                            continue
                        parent, _, name = path.rpartition("/")
                        with _directory(root, parent) as directory:
                            os.mkdir(name, 0o700, dir_fd=directory)
                            os.fsync(directory)
                    total = 0
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        for item in inventory.files:
                            total += item.size
                            if total > TOTAL_BYTES:
                                raise MediaArtifactError("media_preparation_size")
                            _copy_file(archive, item, root)
                    for path, _ in sorted(
                        expected.directories, key=lambda p: (p[0].count("/"), p[0]), reverse=True
                    ):
                        with _directory(root, path) as directory:
                            os.fchmod(directory, 0o500)
                            _owned(os.fstat(directory), True, 0o500)
                            os.fsync(directory)
                    payload = base / attempt / "payload"
                    if _match_root(root, payload) != created_identity:
                        raise MediaArtifactError("media_preparation_changed")
                    actual = capture_manifest(payload, ROLES, SCAN_POLICY)
                    if actual != expected or actual.digest != expected.digest:
                        raise MediaArtifactError("media_preparation_manifest")
                    if _match_root(root, payload) != created_identity:
                        raise MediaArtifactError("media_preparation_changed")
                    os.fsync(attempt_fd)
                    os.fsync(base_fd)
            # All descriptor closes must succeed before returning a receipt.
        receipt = object.__new__(PreparedPillowFiles)
        for key, value in {
            "archive_sha256": inventory.archive_sha256,
            "inventory_digest": inventory.digest,
            "manifest": actual,
            "relative_payload": f"{attempt}/payload",
            "root_identity": created_identity,
        }.items():
            object.__setattr__(receipt, key, value)
        return receipt
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        raise MediaArtifactError("media_preparation_failed") from None


def prepare_pillow_files(data: bytes) -> PreparedPillowFiles:
    """Prepare one private diagnostic copy; never install/import/execute its code."""
    inventory = inspect_pillow_wheel(data)  # Validation precedes every filesystem write.
    base = Path(__file__).resolve().parents[1] / "runtime" / "media-file-preparation"
    return _prepare_files(data, inventory, base)
