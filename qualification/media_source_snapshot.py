"""Offline source snapshot metadata selection; no input or runtime authority."""

import hashlib
import lzma
import re
from datetime import UTC
from email.utils import parsedate_to_datetime

from qualification import media_origin as origin

COMPRESSED_BYTES = 4194304
INDEX_BYTES = 16777216
STANZA_BYTES = 65536
TARGET = "dpkg"
VERSION = "1.22.6ubuntu6.6"
INDEX_PATH = "main/source/Sources.xz"
DIRECTORY = "pool/main/d/dpkg"
TARBALL = "dpkg_1.22.6ubuntu6.6.tar.xz"
TARBALL_BYTES = 5548716
TARBALL_SHA = "7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196"
_NAME = re.compile(rb"[A-Za-z][A-Za-z0-9-]{0,63}\Z")
_HASH = re.compile(rb"[0-9a-f]{64}\Z")
_NUMBER = re.compile(rb"[1-9][0-9]{0,11}\Z")
_FILE = re.compile(rb"[A-Za-z0-9_+.-]{1,128}\Z")


class SourceSnapshotError(ValueError):
    """Stable refusal without untrusted metadata in its text."""


def _refuse(code: str) -> None:
    raise SourceSnapshotError("media_source_snapshot_" + code)


def _date(value: str) -> int:
    try:
        parsed = parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError, OverflowError):
        _refuse("release_date")
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        _refuse("release_date")
    return int(parsed.astimezone(UTC).timestamp())


def select_source_index(plaintext: bytes) -> dict:
    """Parse a Release payload only; caller must independently verify signature binding."""
    try:
        lines = origin._lines(
            plaintext, bound=origin.RELEASE_BYTES, count=8192, width=4096, code="release"
        )
        fields = origin._fields(lines)
    except origin.OriginError:
        _refuse("release_format")
    for name, expected in (
        ("Origin", "Ubuntu"),
        ("Label", "Ubuntu"),
        ("Suite", "noble-updates"),
        ("Codename", "noble"),
    ):
        if fields.get(name) != [expected]:
            _refuse("release_identity")
    components = fields.get("Components", [""])[0].split(" ")
    if "main" not in components or len(components) != len(set(components)):
        _refuse("release_components")
    dates = fields.get("Date", [])
    validity = fields.get("Valid-Until", [])
    if len(dates) != 1 or len(validity) > 1:
        _refuse("release_dates")
    release_time = _date(dates[0])
    if validity and release_time >= _date(validity[0]):
        _refuse("release_dates")
    rows = fields.get("SHA256", [])
    if len(rows) < 2 or rows[0] != "":
        _refuse("release_hashes")
    selected = None
    seen = set()
    for row in rows[1:]:
        values = row.split()
        if len(values) != 3 or not re.fullmatch(r"[0-9a-f]{64}", values[0]):
            _refuse("release_hashes")
        sha, size, path = values
        try:
            origin._path(path)
            length = origin._number(size, origin.DECLARATION_SIZE_MAX, "size")
        except origin.OriginError:
            _refuse("release_hashes")
        if path in seen:
            _refuse("release_duplicate_path")
        seen.add(path)
        if path == INDEX_PATH:
            if not 0 < length <= COMPRESSED_BYTES:
                _refuse("release_index_size")
            selected = {"path": INDEX_PATH, "bytes": length, "sha256": sha}
    if selected is None:
        _refuse("release_index_missing")
    return selected


def _stanza(raw: bytes) -> dict[str, list[bytes]]:
    values: dict[str, list[bytes]] = {}
    current = ""
    for line in raw.split(b"\n"):
        if not line or len(line) > 8192 or b"\r" in line or b"\x00" in line:
            _refuse("stanza_line")
        if line.startswith((b" ", b"\t")):
            if not current or current in ("package", "version", "directory"):
                _refuse("stanza_continuation")
            values[current].append(line[1:])
            continue
        key, sep, value = line.partition(b":")
        if not sep or not _NAME.fullmatch(key) or (value and not value.startswith(b" ")):
            _refuse("stanza_field")
        current = key.lower().decode("ascii")
        if current in values or len(values) >= 256:
            _refuse("stanza_duplicate_field")
        if current in ("package", "version", "directory") and (
            not value[1:] or value[1:] != value[1:].strip(b" \t")
        ):
            _refuse("stanza_identity_whitespace")
        values[current] = [value[1:] if value else b""]
    return values


def _file_row(raw: bytes, digest_length: int = 64) -> dict:
    words = raw.split()
    if (
        len(words) != 3
        or len(words[0]) != digest_length
        or not re.fullmatch(rb"[0-9a-f]+", words[0])
        or not _NUMBER.fullmatch(words[1])
    ):
        _refuse("file_row")
    if not _FILE.fullmatch(words[2]):
        _refuse("file_path")
    return {
        "filename": words[2].decode("ascii"),
        "bytes": int(words[1]),
        "sha256": words[0].decode("ascii"),
    }


def _target(values: dict[str, list[bytes]]) -> dict:
    if values.get("directory") != [DIRECTORY.encode()]:
        _refuse("directory")
    rows = values.get("checksums-sha256", [])
    if len(rows) != 3 or rows[0] != b"":
        _refuse("checksums")
    files = [_file_row(row) for row in rows[1:]]
    membership = {(item["filename"], item["bytes"]) for item in files}
    if len(membership) != 2:
        _refuse("files")
    for field, length in (("checksums-sha1", 40), ("files", 32)):
        other = values.get(field, [])
        if len(other) != 3 or other[0] != b"":
            _refuse("file_membership")
        parsed = [_file_row(row, length) for row in other[1:]]
        if (
            len({(item["filename"], item["bytes"]) for item in parsed}) != 2
            or {(item["filename"], item["bytes"]) for item in parsed} != membership
        ):
            _refuse("file_membership")
    descriptor = next((row for row in files if row["filename"] == f"{TARGET}_{VERSION}.dsc"), None)
    tarball = next((row for row in files if row["filename"] == TARBALL), None)
    if (
        descriptor is None
        or tarball is None
        or descriptor["bytes"] > 65536
        or tarball["bytes"] != TARBALL_BYTES
        or tarball["sha256"] != TARBALL_SHA
    ):
        _refuse("files")
    return {
        "source": TARGET,
        "version": VERSION,
        "directory": DIRECTORY,
        "descriptor": descriptor,
        "tarball": tarball,
    }


def parse_source_index(compressed: bytes, declaration: dict) -> dict:
    """Match a caller-supplied declaration and select metadata, never grant source trust."""
    if (
        type(declaration) is not dict
        or set(declaration) != {"path", "bytes", "sha256"}
        or declaration["path"] != INDEX_PATH
        or type(declaration["bytes"]) is not int
        or not 0 < declaration["bytes"] <= COMPRESSED_BYTES
        or type(declaration["sha256"]) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", declaration["sha256"])
    ):
        _refuse("declaration")
    if (
        type(compressed) is not bytes
        or len(compressed) != declaration["bytes"]
        or hashlib.sha256(compressed).hexdigest() != declaration["sha256"]
    ):
        _refuse("compressed_identity")
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=67108864)
        raw = decoder.decompress(compressed, max_length=INDEX_BYTES + 1)
    except lzma.LZMAError:
        _refuse("xz")
    if not decoder.eof or decoder.unused_data or not 0 < len(raw) <= INDEX_BYTES:
        _refuse("xz_frame")
    if not raw.endswith(b"\n") or b"\r" in raw or b"\x00" in raw:
        _refuse("index_frame")
    count = 0
    selected = None
    for part in raw.rstrip(b"\n").split(b"\n\n"):
        if not 0 < len(part) <= STANZA_BYTES or count >= 100000:
            _refuse("stanza_bound")
        count += 1
        values = _stanza(part)
        if values.get("package") == [TARGET.encode()] and values.get("version") == [
            VERSION.encode()
        ]:
            if selected is not None:
                _refuse("duplicate_target")
            selected = _target(values)
    if selected is None:
        _refuse("target_missing")
    return {
        "schema": "zelo-media-source-snapshot-metadata-v1",
        "index": declaration.copy(),
        "expanded_bytes": len(raw),
        "expanded_sha256": hashlib.sha256(raw).hexdigest(),
        **selected,
        "metadata_only": True,
        "signature_verified": False,
        "snapshot_binding_verified": False,
        "historical_time_policy_verified": False,
        "runtime_authority": False,
    }
