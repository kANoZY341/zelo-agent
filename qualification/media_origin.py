"""Pure fixed Ubuntu Release evidence validation; no crypto or execution authority."""

import base64
import binascii
import hashlib
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

FINGERPRINT = "F6ECB3762474EDA9D21B7022871920D1991BC93C"
KEY_ID = FINGERPRINT[-16:]
RELEASE_BYTES = 524288
STATUS_BYTES = 16384
SUITES = ("noble", "noble-updates", "noble-security")
INDEX_PATHS = (
    "main/binary-amd64/Packages",
    "main/binary-amd64/Packages.xz",
    "main/binary-amd64/Packages.gz",
)
COMPRESSED_BYTES = 33554432
INDEX_BYTES = 134217728
DECLARATION_SIZE_MAX = 999999999999
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
PATH_COMPONENT_PATTERN = r"[A-Za-z0-9_+@.-]{1,128}\Z"
_PATH_PART = re.compile(PATH_COMPONENT_PATTERN)
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,11})\Z")


class OriginError(ValueError):
    """Stable refusal code, never native tool text."""


def _refuse(code):
    raise OriginError("media_origin_" + code)


def _lines(raw, *, bound, count, width, code):
    if type(raw) is not bytes or not 0 < len(raw) <= bound:
        _refuse(code + "_bytes")
    if not raw.endswith(b"\n") or b"\r" in raw:
        _refuse(code + "_newlines")
    # Bound the scan and allocation before splitting the full bounded input.
    if raw.count(b"\n") > count:
        _refuse(code + "_lines")
    if any(byte != 10 and not 32 <= byte <= 126 for byte in raw):
        _refuse(code + "_ascii")
    lines = raw[:-1].decode("ascii").split("\n")
    if any(len(line) > width for line in lines):
        _refuse(code + "_width")
    return lines


def _number(value, limit, code):
    if not _DECIMAL.fullmatch(value):
        _refuse(code)
    number = int(value)
    if number > limit:
        _refuse(code)
    return number


def _date(value, code):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        _refuse(code)
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        _refuse(code)


def _stamp(value, date, code):
    number = _number(value, 253402300799, code)
    try:
        actual = datetime.fromtimestamp(number, UTC).date()
    except (ValueError, OverflowError, OSError):
        _refuse(code)
    if actual != _date(date, code):
        _refuse(code)
    return number


def validate_envelope(raw):
    """Syntax-only framing; signed content comes exclusively from settled gpgv."""
    lines = _lines(raw, bound=RELEASE_BYTES, count=8192, width=4096, code="armor")
    first = "-----BEGIN PGP SIGNED MESSAGE-----"
    begin = "-----BEGIN PGP SIGNATURE-----"
    end = "-----END PGP SIGNATURE-----"
    if (
        len(lines) < 8
        or lines[0] != first
        or lines[1] not in ("Hash: SHA256", "Hash: SHA384", "Hash: SHA512")
        or lines[2] != ""
        or lines[-1] != end
        or lines.count(first) != 1
        or lines.count(begin) != 1
        or lines.count(end) != 1
    ):
        _refuse("armor_frame")
    start = lines.index(begin)
    if start < 4 or lines[start + 1] != "":
        _refuse("armor_signature")
    payload = lines[start + 2 : -1]
    if len(payload) < 2 or not re.fullmatch(r"=[A-Za-z0-9+/]{4}", payload[-1]):
        _refuse("armor_crc")
    if any(not re.fullmatch(r"[A-Za-z0-9+/]{1,76}={0,2}", line) for line in payload[:-1]):
        _refuse("armor_base64")
    try:
        signature = base64.b64decode("".join(payload[:-1]), validate=True)
    except (ValueError, binascii.Error):
        _refuse("armor_base64")
    if not 1 <= len(signature) <= 8192:
        _refuse("armor_signature_bytes")
    # Reject extra armor containers and malformed clear-sign dash escaping.
    for line in lines[3:start]:
        if "-----BEGIN PGP " in line or "-----END PGP " in line:
            _refuse("armor_nested")
        if line.startswith("-") and not line.startswith("- "):
            _refuse("armor_dash")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def validate_status(raw):
    """Strict fixed GnuPG 2.4.4 subset; this function does not verify a signature."""
    lines = _lines(raw, bound=STATUS_BYTES, count=16, width=2048, code="status")
    phase = 0
    keys = 0
    plaintext = False
    sig_id = None
    valid = None
    for line in lines:
        if not line.startswith("[GNUPG:] "):
            _refuse("status_prefix")
        value = line[9:]
        name, _, args = value.partition(" ")
        if name == "PLAINTEXT":
            if phase or plaintext or args != "74 0 ":
                _refuse("status_plaintext")
            plaintext = True
        elif name == "NEWSIG":
            # Fixed archive signatures contain no signer-UID subpacket.
            if phase or not plaintext or args:
                _refuse("status_newsig")
            phase = 1
        elif name == "KEY_CONSIDERED":
            if phase not in (1, 2, 3) or args != FINGERPRINT + " 0" or keys >= 8:
                _refuse("status_key")
            keys += 1
        elif name == "SIG_ID":
            fields = args.split(" ")
            if phase != 1 or len(fields) != 3 or not keys:
                _refuse("status_sig_id")
            ident, date, stamp = fields
            if not re.fullmatch(r"[A-Za-z0-9+/]{27}", ident):
                _refuse("status_sig_id")
            sig_id = (date, _stamp(stamp, date, "status_sig_id"))
            phase = 2
        elif name == "GOODSIG":
            key, sep, uid = args.partition(" ")
            # UID is diagnostic text only; not a trust root.
            if phase != 2 or key != KEY_ID or not sep or not 1 <= len(uid) <= 1024:
                _refuse("status_goodsig")
            if re.search(r"%(?![0-9A-Fa-f]{2})", uid):
                _refuse("status_uid_escape")
            phase = 3
        elif name == "VALIDSIG":
            fields = args.split(" ")
            if phase != 3 or len(fields) != 10:
                _refuse("status_validsig")
            signer, date, stamp, expiry, version, reserved, algorithm, digest, cls, primary = fields
            if (
                signer != FINGERPRINT
                or primary != FINGERPRINT
                or version != "4"
                or reserved != "0"
                or algorithm != "1"
                or digest not in ("8", "9", "10")
                or cls != "01"
            ):
                _refuse("status_identity")
            created = _stamp(stamp, date, "status_time")
            expires = _number(expiry, 253402300799, "status_time")
            if (date, created) != sig_id or (expires and expires <= created):
                _refuse("status_time")
            valid = {
                "signer": signer,
                "primary": primary,
                "created": created,
                "expires": expires,
                "digest_algorithm": int(digest),
                "signature_class": cls,
            }
            phase = 4
        else:
            _refuse("status_unknown")
    if phase != 4 or not keys or valid is None:
        _refuse("status_incomplete")
    return valid


def validate_bad_signature_status(raw):
    """Exact negative transcript, not arbitrary failure or a substring witness."""
    lines = _lines(raw, bound=STATUS_BYTES, count=16, width=2048, code="negative_status")
    if len(lines) < 4 or lines[:2] != ["[GNUPG:] PLAINTEXT 74 0 ", "[GNUPG:] NEWSIG"]:
        _refuse("negative_status_order")
    keys = lines[2:-1]
    if not 1 <= len(keys) <= 8 or any(
        line != "[GNUPG:] KEY_CONSIDERED " + FINGERPRINT + " 0" for line in keys
    ):
        _refuse("negative_status_key")
    prefix = "[GNUPG:] BADSIG " + KEY_ID + " "
    if not lines[-1].startswith(prefix):
        _refuse("negative_status_badsig")
    uid = lines[-1][len(prefix) :]
    if not 1 <= len(uid) <= 1024 or re.search(r"%(?![0-9A-Fa-f]{2})", uid):
        _refuse("negative_status_uid")
    return {"status": "pinned-key-bad-signature", "primary": FINGERPRINT}


def _fields(lines):
    fields = {}
    current = None
    seen = set()
    for line in lines:
        if not line:
            _refuse("release_blank")
        if line.startswith(" "):
            if current not in ("MD5Sum", "SHA1", "SHA256", "SHA512"):
                _refuse("release_continuation")
            fields[current].append(line[1:])
            continue
        key, sep, value = line.partition(":")
        if not sep or not _KEY.fullmatch(key) or key.lower() in seen or len(fields) >= 64:
            _refuse("release_field")
        if value and not value.startswith(" "):
            _refuse("release_field")
        seen.add(key.lower())
        fields[key] = [value[1:] if value else ""]
        current = key
    return fields


def _path(value):
    parts = value.split("/")
    if (
        not 1 <= len(value) <= 512
        or len(parts) > 8
        or any(part in (".", "..") or not _PATH_PART.fullmatch(part) for part in parts)
    ):
        _refuse("release_path")
    return value


def parse_release(raw, suite):
    """Bounded Release declaration parser; has no authentication authority."""
    if suite not in SUITES:
        _refuse("release_suite")
    lines = _lines(raw, bound=RELEASE_BYTES, count=8192, width=4096, code="release")
    fields = _fields(lines)
    for name, expected in (
        ("Origin", "Ubuntu"),
        ("Label", "Ubuntu"),
        ("Suite", suite),
        ("Codename", "noble"),
    ):
        if fields.get(name) != [expected]:
            _refuse("release_identity")
    architectures = fields.get("Architectures", [""])[0].split(" ")
    if (
        "amd64" not in architectures
        or len(architectures) != len(set(architectures))
        or any(not re.fullmatch(r"[a-z0-9-]{1,32}", item) for item in architectures)
    ):
        _refuse("release_architectures")
    dates = {}
    for name in ("Date", "Valid-Until"):
        if name not in fields:
            if name == "Date":
                _refuse("release_date")
            continue
        value = fields[name][0]
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError, IndexError, OverflowError):
            _refuse("release_date")
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            _refuse("release_date")
        dates[name] = value
    rows = fields.get("SHA256", [])
    if not 2 <= len(rows) <= 4097 or rows[0] != "":
        _refuse("release_sha256")
    seen = set()
    selected = {}
    for row in rows[1:]:
        words = row.split()
        if len(words) != 3 or not _HEX.fullmatch(words[0]):
            _refuse("release_hash_row")
        sha, size, path = words
        # Unselected lengths are bounded metadata, never allocation or I/O sizes.
        # Only the three selected indexes can leave this parser, under lower caps.
        size = _number(size, DECLARATION_SIZE_MAX, "release_size")
        _path(path)
        if path in seen:
            _refuse("release_duplicate_path")
        seen.add(path)
        if path in INDEX_PATHS:
            maximum = INDEX_BYTES if path == INDEX_PATHS[0] else COMPRESSED_BYTES
            if not 0 < size <= maximum:
                _refuse("release_selected_size")
            selected[path] = {"path": path, "bytes": size, "sha256": sha}
    if INDEX_PATHS[0] not in selected or not any(path in selected for path in INDEX_PATHS[1:]):
        _refuse("release_indexes_missing")
    return {
        "suite": suite,
        "dates": dates,
        "indexes": [selected[path] for path in INDEX_PATHS if path in selected],
    }


def accept_release(raw, plaintext, status, *, suite, exit_code, cleanup_confirmed):
    """Combine one completed invocation; callers own authentic tool/input binding."""
    if type(exit_code) is not int or exit_code != 0 or cleanup_confirmed is not True:
        _refuse("invocation_unsettled")
    envelope = validate_envelope(raw)
    signature = validate_status(status)
    release = parse_release(plaintext, suite)
    return {
        "schema": "zelo-media-fixed-release-declarations-v1",
        "input": envelope,
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "signature": signature,
        "release": release,
        "trust": "conditional-on-fixed-tool-invocation-and-operator-selected-historical-root",
        "current_key_validity_verified": False,
        "freshness_verified": False,
        "package_member_origin_verified": False,
        "platform_complete": False,
        "abi_qualified": False,
        "runtime_authority": False,
        "instruction_trust": "none",
    }
