"""Fixed inert metadata-tool source copies; never import or execute pyelftools."""

import hashlib
import io
import json
import os
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from qualification.artifacts import ArtifactFile, ArtifactManifest, ArtifactPolicy, capture_manifest
from qualification.media_preparation import (
    _DIRECTORY,
    _WRITE,
    _attempt,
    _descriptor,
    _directory,
    _identity,
    _match_root,
    _owned,
    _write_all,
)

ROOT = Path(__file__).resolve().parents[1]
WHEEL_FILENAME = "pyelftools-0.33-py3-none-any.whl"
WHEEL_SHA256 = "f215ad5f47d3f1373a21496a6c9e0707c622840d0622f23ff7ce08678b020036"
WHEEL_BYTES = 201178
LOCK_SHA256 = "588f4dc28ca39f03bbf8846b4ccb625cf916158065a7693d365c09188c89141a"
ANCHOR = b"# Fixed inert Zelo metadata-tool inventory role; never executed.\n"
ROLES = (("inventory_anchor", "inventory-anchor"),)
POLICY = ArtifactPolicy(96, 8, 524288, 1048576, 96)


class ElfToolError(ValueError):
    pass


def _read_pin(path: Path, limit: int, digest: str) -> bytes:
    # Fixed repository source ancestors and absence of a concurrent host mutator
    # are trusted. Byte bounds do not establish a filesystem deadline.
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if not raw or len(raw) > limit or hashlib.sha256(raw).hexdigest() != digest:
        raise ElfToolError("media_elf_tool_source_pin")
    return raw


def _source_files(raw: bytes) -> tuple[tuple[str, bytes, int], ...]:
    if type(raw) is not bytes or len(raw) != WHEEL_BYTES:
        raise ElfToolError("media_elf_tool_archive_bytes")
    if hashlib.sha256(raw).hexdigest() != WHEEL_SHA256:
        raise ElfToolError("media_elf_tool_archive_pin")
    lock = json.loads(
        _read_pin(ROOT / "qualification/fixtures/media-elf-tool-lock.json", 16384, LOCK_SHA256)
    )
    expected = {item["path"]: item for item in lock["files"]}
    result = [("inventory-anchor", ANCHOR, 0o500)]
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or len({item.filename for item in members}) != len(
            members
        ):
            raise ElfToolError("media_elf_tool_inventory")
        for info in members:
            item = expected.get(info.filename)
            mode = info.external_attr >> 16
            if (
                item is None
                or info.is_dir()
                or info.flag_bits & 1
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or (stat.S_IFMT(mode) and not stat.S_ISREG(mode))
                or info.file_size != item["bytes"]
                or not 0 <= info.file_size <= POLICY.max_file_bytes
            ):
                raise ElfToolError("media_elf_tool_inventory")
            total += info.file_size
            if total > POLICY.max_total_bytes:
                raise ElfToolError("media_elf_tool_size")
            with archive.open(info) as member:
                content = member.read(info.file_size + 1)
            if (
                len(content) != info.file_size
                or hashlib.sha256(content).hexdigest() != item["sha256"]
            ):
                raise ElfToolError("media_elf_tool_member_pin")
            if info.filename.startswith("elftools/") and info.filename.endswith(".py"):
                result.append(("lib/" + info.filename, content, 0o400))
    if total != lock["expanded_bytes"] or len(result) != 53:
        raise ElfToolError("media_elf_tool_inventory")
    for notice in lock["notices"]:
        name = notice["path"].rsplit("/", 1)[1]
        content = _read_pin(ROOT / notice["path"], 65536, notice["sha256"])
        result.append(("notices/" + name, content, 0o400))
    return tuple(sorted(result))


def _expected(files: tuple[tuple[str, bytes, int], ...]) -> ArtifactManifest:
    directories = {""}
    for path, _, _ in files:
        parts = path.split("/")[:-1]
        directories.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return ArtifactManifest(
        "zelo-browser-artifacts-v1",
        POLICY,
        ROLES,
        tuple(
            ArtifactFile(path, len(data), mode, hashlib.sha256(data).hexdigest())
            for path, data, mode in files
        ),
        tuple((path, 0o500) for path in sorted(directories)),
    )


@dataclass(frozen=True, init=False)
class PreparedElfTool:
    """Historical mutable-tree diagnostic; no optional execution permission."""

    manifest: ArtifactManifest
    relative_payload: str
    root_identity: tuple[int, int]
    archive_sha256: str = field(default=WHEEL_SHA256, init=False)
    source_lock_sha256: str = field(default=LOCK_SHA256, init=False)
    optional_tool_executed: bool = field(default=False, init=False)
    immutable_delivery: bool = field(default=False, init=False)
    platform_complete: bool = field(default=False, init=False)
    runtime_authority: bool = field(default=False, init=False)
    instruction_trust: str = field(default="none", init=False)

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("media_elf_tool_preparation_required")


def _prepare(files: tuple[tuple[str, bytes, int], ...], base: Path) -> PreparedElfTool:
    expected = _expected(files)
    base.mkdir(mode=0o700, exist_ok=True)
    initial = base.lstat()
    _owned(initial, True, 0o700)
    with _descriptor(os.open(base, _DIRECTORY)) as base_fd:
        if _identity(initial) != _identity(os.fstat(base_fd)):
            raise ElfToolError("media_elf_tool_changed")
        attempt = _attempt(base_fd)  # Existing capacity-four retained-attempt gate.
        with _descriptor(os.open(attempt, _DIRECTORY, dir_fd=base_fd)) as attempt_fd:
            _owned(os.fstat(attempt_fd), True, 0o700)
            os.mkdir("payload", 0o700, dir_fd=attempt_fd)
            with _descriptor(os.open("payload", _DIRECTORY, dir_fd=attempt_fd)) as root:
                created = _identity(os.fstat(root))
                for path, _ in sorted(
                    expected.directories, key=lambda item: (item[0].count("/"), item[0])
                ):
                    if path:
                        parent, _, name = path.rpartition("/")
                        with _directory(root, parent) as directory:
                            os.mkdir(name, 0o700, dir_fd=directory)
                            os.fsync(directory)
                for path, data, mode in files:
                    parent, _, name = path.rpartition("/")
                    with _directory(root, parent) as directory:
                        with _descriptor(os.open(name, _WRITE, 0o600, dir_fd=directory)) as fd:
                            _owned(os.fstat(fd), False, 0o600)
                            _write_all(fd, data)
                            os.fchmod(fd, mode)
                            os.fsync(fd)
                for path, _ in sorted(
                    expected.directories,
                    key=lambda item: (item[0].count("/"), item[0]),
                    reverse=True,
                ):
                    with _directory(root, path) as directory:
                        os.fchmod(directory, 0o500)
                        os.fsync(directory)
                payload = base / attempt / "payload"
                if _match_root(root, payload) != created:
                    raise ElfToolError("media_elf_tool_changed")
                actual = capture_manifest(payload, ROLES, POLICY)
                if actual != expected or actual.digest != expected.digest:
                    raise ElfToolError("media_elf_tool_manifest")
                if _match_root(root, payload) != created:
                    raise ElfToolError("media_elf_tool_changed")
                os.fsync(attempt_fd)
                os.fsync(base_fd)
    # Return only after every descriptor close has succeeded. No failed copy deleted.
    receipt = object.__new__(PreparedElfTool)
    for key, value in {
        "manifest": actual,
        "relative_payload": f"{attempt}/payload",
        "root_identity": created,
    }.items():
        object.__setattr__(receipt, key, value)
    return receipt


def prepare_elf_tool(raw: bytes) -> PreparedElfTool:
    """Copy fixed authenticated sources/notices only; no install or import."""
    try:
        files = _source_files(raw)  # All authentication before any allocation on disk.
        return _prepare(files, ROOT / "runtime/media-elf-tool-preparation")
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        raise ElfToolError("media_elf_tool_preparation_failed") from None
