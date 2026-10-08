"""Offline qualification of the exact, inert REA v6 source-vendor snapshot.

No REA child processes, package installs, MCP registration, network or
native analysis are authorized by this inventory verifier.
"""

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = ROOT / "third_party" / "rea"
MANIFEST = ROOT / "integrations" / "rea" / "source-v6.0.0.manifest.json"

EXPECTED_TAG = "rea-agents-6.0.0"
EXPECTED_COMMIT = "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
EXPECTED_GIT_TREE = "1f5c0ba7ab679c44a31243346417c3bd9baa2921"
EXPECTED_MANIFEST_SHA256 = "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
EXPECTED_TRACKED_FILES = 2082
EXPECTED_GITLINKS = {
    "third_party/binwalk": "4fdab3d464d97b68e0af9088df3f9e2e1545b21c",
    "third_party/ghidra-nativeaot": "effeb734fc570c32650f88b159608979dc7b423e",
    "third_party/jadx-headless-mcp": "5844800a486d2248fa949b74c9896285f5b54de2",
    "third_party/unblob": "1fcc7a0a584a70a96c31f5a276c20944d199a089",
    "third_party/wakaru": "6070266d24b32951fa2fe1d2dad5b4313bd5c81b",
}
REQUIRED_NOTICES = (
    "LICENSE",
    "third_party/evmole/LICENSE",
    "third_party/pwndbg/LICENSE.md",
)
_SHA1 = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_ALLOWED_MODES = frozenset({"100644", "100755"})
_MAX_TOTAL_BYTES = 30 * 1024 * 1024
_MAX_FILE_BYTES = 8 * 1024 * 1024


class ReaVendorRefusal(ValueError):
    """The local REA source tree does not match its separately pinned release."""


@dataclass(frozen=True, slots=True)
class ReaSourceVendorReceipt:
    source_tag: str
    upstream_commit: str
    upstream_tree: str
    verified_files: int
    total_bytes: int
    optional_engine_gitlinks: int
    execution_enabled: bool = False


def _safe_relative_path(value: str) -> Path:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > 1024
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
    ):
        raise ReaVendorRefusal("rea_vendor_path_invalid")
    components = value.split("/")
    if any(part in ("", ".", "..", ".git") for part in components):
        raise ReaVendorRefusal("rea_vendor_path_invalid")
    return Path(*components)


def _git_blob_digest(data: bytes) -> str:
    return hashlib.sha1(  # noqa: S324  Git's content-addressed blob format, not authentication.
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def _regular_file(root: Path, relative: Path) -> Path:
    path = root
    for part in relative.parts:
        path /= part
        try:
            mode = path.lstat().st_mode
        except OSError:
            raise ReaVendorRefusal("rea_vendor_file_missing") from None
        if stat.S_ISLNK(mode):
            raise ReaVendorRefusal("rea_vendor_symlink_forbidden")
    if not stat.S_ISREG(mode):
        raise ReaVendorRefusal("rea_vendor_nonregular_file")
    return path


def _read_bounded_regular_file(path: Path, remaining_bytes: int) -> tuple[bytes, int]:
    """Open without following final symlinks, and bound bytes BEFORE reading.

    These are source-integrity checks, never permission to execute the contents.
    """

    if remaining_bytes < 0:
        raise ReaVendorRefusal("rea_vendor_byte_limit")
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ReaVendorRefusal("rea_vendor_nonregular_file")
            if before.st_size > min(remaining_bytes, _MAX_FILE_BYTES):
                raise ReaVendorRefusal("rea_vendor_byte_limit")
            # Even if the file grows after fstat, only this many bytes can be allocated.
            data = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if len(data) != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ReaVendorRefusal("rea_vendor_file_changed")
            return data, before.st_mode
    except OSError:
        raise ReaVendorRefusal("rea_vendor_file_unavailable") from None


def verify_vendored_rea_source(
    *,
    vendor_root: Path = VENDOR_ROOT,
    manifest_path: Path = MANIFEST,
) -> ReaSourceVendorReceipt:
    """Verify all 2082 copied Git blobs, explicit notices and optional gitlink pins."""

    try:
        raw = manifest_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != EXPECTED_MANIFEST_SHA256:
            raise ReaVendorRefusal("rea_vendor_manifest_changed")
        manifest = json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, ReaVendorRefusal):
            raise
        raise ReaVendorRefusal("rea_vendor_manifest_unavailable") from None

    expected_header = {
        "schema": "zelo-vendored-rea-source-v1",
        "upstream": "https://github.com/morluto/rea",
        "tag": EXPECTED_TAG,
        "version": "6.0.0",
        "upstream_git_commit": EXPECTED_COMMIT,
        "upstream_git_tree": EXPECTED_GIT_TREE,
        "license": "MIT",
        "execution_enabled": False,
    }
    if (
        set(manifest) != {*expected_header, "files", "optional_external_gitlinks"}
        or any(manifest.get(k) != v for k, v in expected_header.items())
        or not isinstance(manifest.get("files"), list)
        or len(manifest["files"]) != EXPECTED_TRACKED_FILES
    ):
        raise ReaVendorRefusal("rea_vendor_manifest_identity_invalid")

    expected_links = EXPECTED_GITLINKS
    links = manifest["optional_external_gitlinks"]
    if (
        not isinstance(links, list)
        or len(links) != len(expected_links)
        or any(
            type(entry) is not dict
            or set(entry) != {"path", "git_commit"}
            or entry["path"] not in expected_links
            or entry["git_commit"] != expected_links[entry["path"]]
            for entry in links
        )
        or len({entry["path"] for entry in links}) != len(expected_links)
    ):
        raise ReaVendorRefusal("rea_vendor_external_engine_identity_invalid")

    if not vendor_root.is_dir() or vendor_root.is_symlink():
        raise ReaVendorRefusal("rea_vendor_root_unavailable")
    observed: set[str] = set()
    expected: set[str] = set()
    total_bytes = 0
    previous = ""
    for record in manifest["files"]:
        if type(record) is not dict or set(record) != {"path", "mode", "git_blob"}:
            raise ReaVendorRefusal("rea_vendor_file_record_invalid")
        rel = _safe_relative_path(record["path"])
        posix_path = rel.as_posix()
        if posix_path <= previous:
            raise ReaVendorRefusal("rea_vendor_inventory_order_invalid")
        previous = posix_path
        if record["mode"] not in _ALLOWED_MODES or not _SHA1.fullmatch(record["git_blob"]):
            raise ReaVendorRefusal("rea_vendor_file_identity_invalid")
        path = _regular_file(vendor_root, rel)
        data, actual_mode = _read_bounded_regular_file(path, _MAX_TOTAL_BYTES - total_bytes)
        if bool(actual_mode & stat.S_IXUSR) != (record["mode"] == "100755"):
            raise ReaVendorRefusal("rea_vendor_file_mode_mismatch")
        total_bytes += len(data)
        if _git_blob_digest(data) != record["git_blob"]:
            raise ReaVendorRefusal("rea_vendor_file_modified")
        expected.add(posix_path)

    # Also refuse FIFOs, sockets, devices and symlinks: not just extra regular files.
    for path in vendor_root.rglob("*"):
        rel = path.relative_to(vendor_root).as_posix()
        _safe_relative_path(rel)
        try:
            mode = path.lstat().st_mode
        except OSError:
            raise ReaVendorRefusal("rea_vendor_inventory_unavailable") from None
        if stat.S_ISDIR(mode):
            continue
        if stat.S_ISLNK(mode):
            raise ReaVendorRefusal("rea_vendor_symlink_forbidden")
        if not stat.S_ISREG(mode):
            raise ReaVendorRefusal("rea_vendor_special_entry_forbidden")
        observed.add(rel)
    if observed != expected:
        raise ReaVendorRefusal("rea_vendor_file_inventory_mismatch")
    for submodule in expected_links:
        if (vendor_root / submodule).exists() and any((vendor_root / submodule).iterdir()):
            raise ReaVendorRefusal("rea_vendor_external_engine_not_qualified")
    if not set(REQUIRED_NOTICES).issubset(expected):
        raise ReaVendorRefusal("rea_vendor_notice_missing")
    root_notice = (vendor_root / "LICENSE").read_text(encoding="utf-8")
    if "MIT License" not in root_notice or "Copyright (c) 2026 morluto" not in root_notice:
        raise ReaVendorRefusal("rea_vendor_notice_invalid")
    return ReaSourceVendorReceipt(
        source_tag=EXPECTED_TAG,
        upstream_commit=EXPECTED_COMMIT,
        upstream_tree=EXPECTED_GIT_TREE,
        verified_files=len(expected),
        total_bytes=total_bytes,
        optional_engine_gitlinks=len(expected_links),
    )
