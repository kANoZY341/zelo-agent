"""Restricted USTAR inventory adapter; no extraction, native launch or authority.

Only fixed-size TarInfo header decoding is reused. Unsupported extensions fail
before their bodies are read. Real package use still requires a qualified owner.
"""

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from qualification import media_member_contract as contract

Refused = contract.Refused
BLOCK_BYTES = 512
READ_BYTES = 65536
MAX_STREAM_BYTES = contract.MAX_EXPANDED_BYTES
MAX_READ_CALLS = 262144
MAX_MEMBERS = contract.MAX_MEMBERS
MAX_JSON_BYTES = contract.MAX_JSON_BYTES
WHOLE_SECONDS = 60
_MAGIC = b"ustar\x0000"
_TYPES = {b"0": "regular", b"1": "hardlink", b"2": "symlink", b"5": "directory"}
_STRINGS = ((0, 100), (157, 257), (265, 297), (297, 329), (345, 500))
_NUMBERS = (
    (100, 108),
    (108, 116),
    (116, 124),
    (124, 136),
    (136, 148),
    (148, 156),
    (329, 337),
    (337, 345),
)
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class ByteSource(Protocol):
    def read(self, count: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class Header:
    name: str
    kind: str
    mode: int
    uid: int
    gid: int
    size: int
    link_target: str | None


@dataclass(frozen=True, slots=True)
class ParsedTar:
    """Observed uncompressed stream only; deliberately not an OwnerOutcome."""

    manifest_raw: bytes
    expanded_bytes: int
    tar_sha256: str
    member_count: int
    runtime_authority: bool = field(default=False, init=False)


def preflight_header(block: bytes) -> None:
    """Reject unsupported wire forms before even fixed-size library decoding."""
    if type(block) is not bytes or len(block) != BLOCK_BYTES:
        raise Refused("tar_header_width")
    if block[257:265] != _MAGIC:
        raise Refused("tar_format")
    if block[156:157] not in _TYPES:
        raise Refused("tar_type")
    if any(block[500:512]):
        raise Refused("tar_reserved")
    for start, end in _STRINGS:
        _, separator, tail = block[start:end].partition(b"\0")
        if separator and any(tail):
            raise Refused("tar_string_padding")
    for start, end in _NUMBERS:
        value = block[start:end]
        if any(c not in b"01234567 \0" for c in value):
            raise Refused("tar_number_encoding")
        _, separator, tail = value.partition(b"\0")
        if separator and any(c not in b" \0" for c in tail):
            raise Refused("tar_number_padding")


def decode_header(block: bytes) -> Header:
    """Reuse stdlib checksum/numeric/string decoding, never TarFile traversal."""
    preflight_header(block)
    try:
        # Lazy import: importing this adapter alone does not activate tarfile.
        import tarfile

        info = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
        if info.chksum != tarfile.calc_chksums(block)[0]:
            raise Refused("tar_checksum_unsigned")
        name = tarfile.nts(block[:100], "utf-8", "strict")
        prefix = tarfile.nts(block[345:500], "utf-8", "strict")
        if prefix:
            name = prefix + "/" + name
        normalized = name.rstrip("/") if info.type == b"5" else name
        if info.type != block[156:157] or info.name != normalized:
            raise Refused("tar_header_normalization")
        if info.devmajor != 0 or info.devminor != 0:
            raise Refused("tar_device_metadata")
        if type(info.mtime) is not int or not 0 <= info.mtime <= 2**63 - 1:
            raise Refused("tar_mtime")
        kind = _TYPES[info.type]
        if kind not in ("symlink", "hardlink") and info.linkname:
            raise Refused("tar_unexpected_link")
        return Header(
            name,
            kind,
            info.mode,
            info.uid,
            info.gid,
            info.size,
            info.linkname if kind in ("symlink", "hardlink") else None,
        )
    except Refused:
        raise
    except Exception:
        raise Refused("tar_header_invalid") from None


def _not_cancelled() -> bool:
    return False


class _Reader:
    """Bounded reads; caller owns source lifetime and external process timeout."""

    def __init__(
        self, source: ByteSource, cancelled: Callable[[], bool], clock: Callable[[], float]
    ):
        self.source = source
        self.cancelled = cancelled
        self.clock = clock
        self.deadline = self._now() + WHOLE_SECONDS
        self.total = 0
        self.calls = 0
        self.digest = hashlib.sha256()

    def _now(self) -> float:
        try:
            now = self.clock()
        except Exception:
            raise Refused("tar_clock") from None
        if type(now) not in (int, float) or not math.isfinite(now):
            raise Refused("tar_clock")
        return now

    def check(self) -> None:
        try:
            cancelled = self.cancelled()
        except Exception:
            raise Refused("tar_cancel_check") from None
        if type(cancelled) is not bool:
            raise Refused("tar_cancel_check")
        if cancelled:
            raise Refused("tar_cancelled")
        if self._now() >= self.deadline:
            raise Refused("tar_deadline")

    def read(self, count: int) -> bytes:
        self.check()
        if not 0 < count <= READ_BYTES:
            raise Refused("tar_read_bound")
        if self.calls >= MAX_READ_CALLS:
            raise Refused("tar_read_calls")
        # One extra byte can detect overflow at the exact cap, never be accepted.
        count = min(count, MAX_STREAM_BYTES - self.total + 1)
        self.calls += 1
        try:
            raw = self.source.read(count)
        except Exception:
            raise Refused("tar_read") from None
        self.check()
        if type(raw) is not bytes or len(raw) > count:
            raise Refused("tar_read_result")
        if self.total + len(raw) > MAX_STREAM_BYTES:
            raise Refused("tar_stream_bound")
        self.total += len(raw)
        self.digest.update(raw)
        return raw

    def exact(self, count: int) -> bytes:
        if not 0 <= count <= READ_BYTES:
            raise Refused("tar_read_bound")
        raw = bytearray()
        while len(raw) < count:
            chunk = self.read(count - len(raw))
            if not chunk:
                raise Refused("tar_truncated")
            raw.extend(chunk)
        return bytes(raw)

    def payload(self, size: int) -> str:
        # Reserve payload, padding and both terminal blocks before reading data.
        extent = ((size + BLOCK_BYTES - 1) // BLOCK_BYTES) * BLOCK_BYTES
        if extent + self.total + 2 * BLOCK_BYTES > MAX_STREAM_BYTES:
            raise Refused("tar_payload_extent")
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            chunk = self.exact(min(READ_BYTES, remaining))
            digest.update(chunk)
            remaining -= len(chunk)
        if any(self.exact(extent - size)):
            raise Refused("tar_payload_padding")
        return digest.hexdigest()

    def termination(self) -> None:
        # The first zero block has already been read.
        if any(self.exact(BLOCK_BYTES)):
            raise Refused("tar_terminator")
        while True:
            padding = self.read(READ_BYTES)
            if not padding:
                break
            if any(padding):
                raise Refused("tar_trailing_data")
        if self.total % BLOCK_BYTES:
            raise Refused("tar_trailing_alignment")
        self.check()


def _row(header: Header, ordinal: int) -> dict:
    if type(header) is not Header:
        raise Refused("tar_decoder_result")
    return {
        "ordinal": ordinal,
        "name": header.name,
        "kind": header.kind,
        "mode": header.mode,
        "uid": header.uid,
        "gid": header.gid,
        "size": header.size,
        "sha256": (_EMPTY_SHA if header.size == 0 else "0" * 64)
        if header.kind == "regular"
        else None,
        "link_target": header.link_target,
    }


def _json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _prefix(binding: dict, expanded: int) -> bytes:
    return (
        b'{"schema":'
        + _json(contract.SCHEMA)
        + b',"binding":'
        + _json(binding)
        + b',"expanded_bytes":'
        + str(expanded).encode("ascii")
        + b',"members":['
    )


def scan_tar(
    source: ByteSource,
    expected: contract.ExpectedBinding,
    *,
    cancelled: Callable[[], bool] = _not_cancelled,
    clock: Callable[[], float] = time.monotonic,
    header_decoder: Callable[[bytes], Header] = decode_header,
) -> ParsedTar:
    """Inventory restricted uncompressed USTAR without following or creating files.

    Source, decoder, clock and cancellation callback are trusted caller objects,
    not capabilities supplied by the input bytes. Deadlines are cooperative;
    this does not sandbox or preempt a blocking callback/read. An actual package
    pipeline still needs independently established native/process outcomes.
    """
    binding = contract._binding(expected)
    reader = _Reader(source, cancelled, clock)
    seen: dict[str, str] = {}
    needed_directories: set[str] = set()
    rows: list[bytes] = []
    reserved = len(_prefix(binding, MAX_STREAM_BYTES)) + 2
    if reserved > MAX_JSON_BYTES:
        raise Refused("tar_json_bound")
    while True:
        block = reader.exact(BLOCK_BYTES)
        if not any(block):
            reader.termination()
            break
        if len(rows) >= MAX_MEMBERS:
            raise Refused("tar_member_count")
        preflight_header(block)  # Always before the injected/library decoder.
        try:
            header = header_decoder(block)
        except Refused:
            raise
        except Exception:
            raise Refused("tar_header_invalid") from None
        reader.check()
        row = _row(header, len(rows))
        if row["kind"] != _TYPES[block[156:157]]:
            raise Refused("tar_decoder_kind")
        member = contract._member(row, len(rows))
        name = member.canonical_name
        if name in seen:
            raise Refused("member_duplicate_name")
        if member.kind != "directory" and name in needed_directories:
            raise Refused("member_ancestor")
        parts = name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in seen and seen[parent] != "directory":
                raise Refused("member_ancestor")
            needed_directories.add(parent)
        preview = _json(row)
        charge = len(preview) + bool(rows)
        if reserved + charge > MAX_JSON_BYTES:
            raise Refused("tar_json_bound")
        reserved += charge  # Charge before payload reads or retaining a new row.
        if member.kind == "regular":
            row["sha256"] = reader.payload(member.size)
        rendered = _json(row)
        if len(rendered) != len(preview):
            raise Refused("tar_json_accounting")
        rows.append(rendered)
        seen[name] = member.kind
        reader.check()
    if not rows:
        raise Refused("tar_empty_inventory")
    prefix = _prefix(binding, reader.total)
    actual = len(prefix) + sum(map(len, rows)) + len(rows) - 1 + 2
    if actual > reserved or actual > MAX_JSON_BYTES:
        raise Refused("tar_json_bound")
    result = prefix + b",".join(rows) + b"]}"
    reader.check()
    return ParsedTar(result, reader.total, reader.digest.hexdigest(), len(rows))
