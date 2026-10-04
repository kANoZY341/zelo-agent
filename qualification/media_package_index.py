"""Pure fixed package metadata selection; no input authentication or I/O authority."""

import hashlib
import re
from collections.abc import Iterable

SUITES = ("noble", "noble-updates", "noble-security")
TARGETS = (
    ("python3.12-minimal", "3.12.3-1ubuntu0.17"),
    ("libpython3.12-minimal", "3.12.3-1ubuntu0.17"),
    ("libpython3.12-stdlib", "3.12.3-1ubuntu0.17"),
    ("libc6", "2.39-0ubuntu8.9"),
    ("libffi8", "3.4.6-1build1"),
    ("libseccomp2", "2.5.5-1ubuntu3.1"),
    ("zlib1g", "1:1.3.dfsg-3.1ubuntu2.2"),
    ("libexpat1", "2.6.1-2ubuntu0.6"),
)
INDEX_BYTES = 134217728
CHUNK_BYTES = 65536
STANZA_BYTES = 65536
STANZAS = 100000
FIELDS = 256
PACKAGE_BYTES = 33554432
INDEX_SCHEMA = "zelo-media-fixed-package-index-metadata-v1"
SELECTION_SCHEMA = "zelo-media-fixed-package-selection-v1"
_EXPECTED = dict(TARGETS)
_CRITICAL = frozenset(
    ("package", "version", "architecture", "filename", "size", "sha256", "source")
)
_FIELD = re.compile(r"[!-9;-~]{1,64}\Z")
_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]{1,127}\Z")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.+:~-]{0,127}\Z")
_ARCH = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9_+@.-]{1,128}\Z")
_SIZE = re.compile(r"[1-9][0-9]{0,7}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"([a-z0-9][a-z0-9+.-]{1,127})(?: \(([0-9][A-Za-z0-9.+:~-]{0,127})\))?\Z")
_RECORD_KEYS = frozenset(
    ("package", "version", "architecture", "filename", "bytes", "sha256", "source")
)
_INDEX_KEYS = frozenset(
    (
        "schema",
        "suite",
        "bytes",
        "sha256",
        "packages",
        "metadata_only",
        "package_member_origin_verified",
        "runtime_authority",
    )
)
_SOURCE_KEYS = frozenset(("name", "version"))


class PackageIndexError(ValueError):
    """Stable refusal; supplied metadata never appears in the error message."""


def _refuse(code: str) -> None:
    raise PackageIndexError("media_package_index_" + code)


def _lex(value: object, pattern: re.Pattern, maximum: int, code: str) -> str:
    if type(value) is not str or not 0 < len(value) <= maximum or not pattern.fullmatch(value):
        _refuse(code)
    return value


def _keys(value: object, keys: frozenset[str], code: str) -> None:
    if (
        type(value) is not dict
        or len(value) != len(keys)
        or any(type(key) is not str for key in value)
        or set(value) != keys
    ):
        _refuse(code)


def _positive(value: object, maximum: int, code: str) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        _refuse(code)
    return value


def _suite(value: object) -> str:
    if type(value) is not str or value not in SUITES:
        _refuse("suite")
    return value


def _filename(value: object) -> str:
    if type(value) is not str or not 0 < len(value) <= 512:
        _refuse("filename")
    parts = value.split("/")
    if (
        not value.startswith("pool/main/")
        or not value.endswith(".deb")
        or len(parts) > 8
        or any(part in (".", "..") or not _COMPONENT.fullmatch(part) for part in parts)
    ):
        _refuse("filename")
    return value


def _source(value: str | None, package: str, version: str) -> dict:
    if value is None:
        return {"name": package, "version": version}
    match = _SOURCE.fullmatch(value) if len(value) <= 259 else None
    if match is None:
        _refuse("source")
    return {"name": match[1], "version": match[2] or version}


def _selected(values: dict[str, str]) -> dict | None:
    package = _lex(values.get("package"), _NAME, 128, "package")
    version = _lex(values.get("version"), _VERSION, 128, "version")
    architecture = _lex(values.get("architecture"), _ARCH, 32, "architecture")
    if _EXPECTED.get(package) != version or architecture != "amd64":
        return None
    if not {"filename", "size", "sha256"} <= values.keys():
        _refuse("selected_fields")
    size = _lex(values["size"], _SIZE, 8, "size")
    return {
        "package": package,
        "version": version,
        "architecture": architecture,
        "filename": _filename(values["filename"]),
        "bytes": _positive(int(size), PACKAGE_BYTES, "size"),
        "sha256": _lex(values["sha256"], _SHA, 64, "sha256"),
        "source": _source(values.get("source"), package, version),
    }


def _validate_record(value: object) -> dict:
    _keys(value, _RECORD_KEYS, "record_shape")
    package = _lex(value["package"], _NAME, 128, "package")
    version = _lex(value["version"], _VERSION, 128, "version")
    architecture = _lex(value["architecture"], _ARCH, 32, "architecture")
    if _EXPECTED.get(package) != version or architecture != "amd64":
        _refuse("membership")
    _keys(value["source"], _SOURCE_KEYS, "source_shape")
    return {
        "package": package,
        "version": version,
        "architecture": architecture,
        "filename": _filename(value["filename"]),
        "bytes": _positive(value["bytes"], PACKAGE_BYTES, "size"),
        "sha256": _lex(value["sha256"], _SHA, 64, "sha256"),
        "source": {
            "name": _lex(value["source"]["name"], _NAME, 128, "source"),
            "version": _lex(value["source"]["version"], _VERSION, 128, "source"),
        },
    }


def _flags() -> dict:
    return {
        "metadata_only": True,
        "package_member_origin_verified": False,
        "runtime_authority": False,
    }


class _Parser:
    def __init__(self) -> None:
        self.total = 0
        self.stanzas = 0
        self.stanza_bytes = 0
        self.active = False
        self.partial = bytearray()
        self.seen: set[str] = set()
        self.current: str | None = None
        self.values: dict[str, str] = {}
        self.rows: dict[str, dict] = {}
        self.hasher = hashlib.sha256()

    def _charge(self, count: int) -> None:
        if self.stanza_bytes + count > STANZA_BYTES:
            _refuse("stanza_bytes")
        self.stanza_bytes += count

    def _finish_stanza(self) -> None:
        row = _selected(self.values)
        if row is not None:
            name = row["package"]
            if name in self.rows:
                _refuse("duplicate_package")
            self.rows[name] = row
        self.active = False
        self.stanza_bytes = 0
        self.seen.clear()
        self.current = None
        self.values.clear()

    def _line(self, raw: bytearray) -> None:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            _refuse("utf8")
        if any((ord(char) < 32 and char != "\t") or 127 <= ord(char) <= 159 for char in line):
            _refuse("control")
        if not line.strip(" \t"):
            _refuse("whitespace_line")
        if line[0] in " \t":
            if self.current is None:
                _refuse("orphan_continuation")
            if self.current in _CRITICAL:
                _refuse("simple_continuation")
            return
        name, colon, value = line.partition(":")
        if not colon or not _FIELD.fullmatch(name) or name[0] in "#-":
            _refuse("field")
        key = name.lower()
        if key in self.seen:
            _refuse("duplicate_field")
        if len(self.seen) >= FIELDS:
            _refuse("fields")
        self.seen.add(key)
        self.current = key
        if key in _CRITICAL:
            value = value.strip(" \t")
            if not value:
                _refuse("empty_simple")
            self.values[key] = value

    def feed(self, chunk: bytes) -> None:
        if type(chunk) is not bytes or not 0 < len(chunk) <= CHUNK_BYTES:
            _refuse("chunk")
        if self.total + len(chunk) > INDEX_BYTES:
            _refuse("index_bytes")
        self.total += len(chunk)
        self.hasher.update(chunk)
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            stop = len(chunk) if newline < 0 else newline + 1
            count = stop - start
            if newline == start and not self.partial:
                if self.active:
                    self._charge(1)
                    self._finish_stanza()
                start = stop
                continue
            if not self.active:
                if self.stanzas >= STANZAS:
                    _refuse("stanzas")
                self.stanzas += 1
                self.active = True
            # Charge partial bytes and framing before allocating a slice or extending.
            self._charge(count)
            self.partial.extend(chunk[start : stop if newline < 0 else newline])
            if newline >= 0:
                self._line(self.partial)
                self.partial.clear()
            start = stop

    def finish(self, suite: str) -> dict:
        if not self.total or not self.stanzas:
            _refuse("empty")
        if self.partial:
            _refuse("final_lf")
        if self.active:
            self._finish_stanza()
        return {
            "schema": INDEX_SCHEMA,
            "suite": suite,
            "bytes": self.total,
            "sha256": self.hasher.hexdigest(),
            "packages": [self.rows[name] for name, _ in TARGETS if name in self.rows],
            **_flags(),
        }


def parse_index(chunks: Iterable[bytes], suite: str) -> dict:
    """Select metadata only after complete input; callers authenticate bytes first.

    The iterable is trusted caller code, not an I/O or hard timing boundary.
    Iterator failure propagates without returning any partially accepted result.
    """
    _suite(suite)
    parser = _Parser()
    for chunk in chunks:
        parser.feed(chunk)
    return parser.finish(suite)


def merge_fixed_indexes(results: list | tuple) -> dict:
    """Validate three metadata structures and require all eight fixed selections."""
    if type(results) not in (list, tuple) or len(results) != 3:
        _refuse("index_set")
    indexes: dict[str, dict] = {}
    selected: dict[str, dict] = {}
    memberships: dict[str, set[str]] = {}
    for result in results:
        _keys(result, _INDEX_KEYS, "index_shape")
        if type(result["schema"]) is not str or result["schema"] != INDEX_SCHEMA:
            _refuse("schema")
        if any(result[key] is not expected for key, expected in _flags().items()):
            _refuse("authority_flags")
        suite = _suite(result["suite"])
        if suite in indexes:
            _refuse("duplicate_suite")
        indexes[suite] = {
            "suite": suite,
            "bytes": _positive(result["bytes"], INDEX_BYTES, "index_bytes"),
            "sha256": _lex(result["sha256"], _SHA, 64, "index_sha256"),
        }
        rows = result["packages"]
        if type(rows) is not list or len(rows) > len(TARGETS):
            _refuse("records")
        seen = set()
        for value in rows:
            row = _validate_record(value)
            name = row["package"]
            if name in seen:
                _refuse("duplicate_package")
            seen.add(name)
            if name in selected and row != selected[name]:
                _refuse("conflict")
            selected[name] = row
            memberships.setdefault(name, set()).add(suite)
    if set(indexes) != set(SUITES):
        _refuse("index_set")
    if set(selected) != set(_EXPECTED):
        _refuse("missing_packages")
    return {
        "schema": SELECTION_SCHEMA,
        "indexes": [indexes[suite] for suite in SUITES],
        "packages": [
            {**selected[name], "suites": [suite for suite in SUITES if suite in memberships[name]]}
            for name, _ in TARGETS
        ],
        **_flags(),
    }
