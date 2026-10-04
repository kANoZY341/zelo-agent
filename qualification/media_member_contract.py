"""Pure member metadata validation; no archive parsing, IO, execution or authority."""

import hashlib
import json
from dataclasses import dataclass, field

SCHEMA = "zelo-media-member-inventory-v1"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 8192
MAX_NAME_BYTES = 1024
MAX_DEPTH = 64
BATCH_SIZE = 8
MAX_BATCH_JSON_BYTES = 32 * 1024 * 1024
MAX_BATCH_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_BATCH_MEMBERS = 16384
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_HEX = frozenset("0123456789abcdef")
_MEMBER_KEYS = frozenset(
    ("ordinal", "name", "kind", "mode", "uid", "gid", "size", "sha256", "link_target")
)
_MANIFEST_KEYS = frozenset(("schema", "binding", "expanded_bytes", "members"))
_BINDING_KEYS = frozenset(
    (
        "archive_sha256",
        "archive_bytes",
        "source_proof_sha256",
        "reader_policy_sha256",
        "run_id",
        "action",
    )
)


class Refused(ValueError):
    """Only fixed diagnostic codes, never untrusted metadata or raw exceptions."""


@dataclass(frozen=True, slots=True)
class ExpectedBinding:
    archive_sha256: str
    archive_bytes: int
    source_proof_sha256: str
    reader_policy_sha256: str
    run_id: str
    action: str = "fsys-tar"


@dataclass(frozen=True, slots=True)
class OwnerOutcome:
    """Caller-owned observations, not fields accepted from the child manifest."""

    exit_code: int | None
    eof: bool
    parser_complete: bool
    cleanup_confirmed: bool
    cancelled: bool
    timed_out: bool
    stdout_saturated: bool
    stderr_saturated: bool
    expanded_bytes: int


@dataclass(frozen=True, slots=True)
class Member:
    ordinal: int
    name: str
    canonical_name: str
    kind: str
    mode: int
    uid: int
    gid: int
    size: int
    sha256: str | None
    link_target: str | None


@dataclass(frozen=True, slots=True)
class MemberInventory:
    """Consistent asserted metadata only, not authenticated member evidence."""

    binding: ExpectedBinding
    expanded_bytes: int
    members: tuple[Member, ...]
    raw_sha256: str
    runtime_authority: bool = field(default=False, init=False)


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise Refused(code)
    return value


def _hex(value: object, length: int, code: str) -> str:
    if type(value) is not str or len(value) != length or any(c not in _HEX for c in value):
        raise Refused(code)
    return value


def _text(value: object) -> str:
    if type(value) is not str or not 0 < len(value) <= MAX_NAME_BYTES or not value.isprintable():
        raise Refused("member_text")
    try:
        if len(value.encode("utf-8", "strict")) > MAX_NAME_BYTES:
            raise Refused("member_text")
    except UnicodeError:
        raise Refused("member_text") from None
    return value


def _shape(value: object, keys: frozenset[str], code: str) -> dict:
    if type(value) is not dict or len(value) != len(keys) or value.keys() != keys:
        raise Refused(code)
    return value


def _binding(expected: ExpectedBinding) -> dict:
    if type(expected) is not ExpectedBinding:
        raise Refused("member_expected_binding")
    if type(expected.action) is not str or expected.action != "fsys-tar":
        raise Refused("member_expected_binding")
    return {
        "archive_sha256": _hex(expected.archive_sha256, 64, "member_expected_binding"),
        "archive_bytes": _integer(
            expected.archive_bytes, 1, MAX_ARCHIVE_BYTES, "member_expected_binding"
        ),
        "source_proof_sha256": _hex(expected.source_proof_sha256, 64, "member_expected_binding"),
        "reader_policy_sha256": _hex(expected.reader_policy_sha256, 64, "member_expected_binding"),
        "run_id": _hex(expected.run_id, 32, "member_expected_binding"),
        "action": expected.action,
    }


def _owner(outcome: OwnerOutcome) -> None:
    if type(outcome) is not OwnerOutcome:
        raise Refused("member_owner_outcome")
    for value, required in (
        (outcome.eof, True),
        (outcome.parser_complete, True),
        (outcome.cleanup_confirmed, True),
        (outcome.cancelled, False),
        (outcome.timed_out, False),
        (outcome.stdout_saturated, False),
        (outcome.stderr_saturated, False),
    ):
        if type(value) is not bool or value is not required:
            raise Refused("member_owner_outcome")
    if type(outcome.exit_code) is not int or outcome.exit_code != 0:
        raise Refused("member_owner_outcome")
    size = _integer(outcome.expanded_bytes, 1024, MAX_EXPANDED_BYTES, "member_owner_extent")
    if size % 512:
        raise Refused("member_owner_extent")


def _raw_bound(raw: bytes) -> None:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_JSON_BYTES:
        raise Refused("member_json_bound")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    if len(pairs) > len(_MEMBER_KEYS):
        raise Refused("member_json_object")
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("member_json_duplicate")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise Refused("member_json_constant")


def _decode(raw: bytes) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except Refused:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("member_json_invalid") from None
    return _shape(value, _MANIFEST_KEYS, "member_manifest_shape")


def _canonical(name: str, kind: str, ordinal: int) -> str:
    _text(name)
    if "\\" in name or ":" in name or name.startswith("/"):
        raise Refused("member_name")
    if name in (".", "./"):
        if kind != "directory" or ordinal != 0:
            raise Refused("member_root")
        return "."
    canonical = name[2:] if name.startswith("./") else name
    if canonical.endswith("/") and kind == "directory":
        canonical = canonical[:-1]
    components = canonical.split("/")
    if len(components) > MAX_DEPTH or any(c in ("", ".", "..") for c in components):
        raise Refused("member_name")
    return canonical


def _member(value: object, ordinal: int) -> Member:
    row = _shape(value, _MEMBER_KEYS, "member_record_shape")
    _integer(row["ordinal"], ordinal, ordinal, "member_ordinal")
    kind = row["kind"]
    if type(kind) is not str or kind not in ("regular", "directory", "symlink", "hardlink"):
        raise Refused("member_kind")
    name = _text(row["name"])
    canonical = _canonical(name, kind, ordinal)
    mode = _integer(row["mode"], 0, 4095, "member_mode")
    uid = _integer(row["uid"], 0, 2**32 - 1, "member_owner_id")
    gid = _integer(row["gid"], 0, 2**32 - 1, "member_owner_id")
    size = _integer(row["size"], 0, MAX_EXPANDED_BYTES, "member_size")
    digest, target = row["sha256"], row["link_target"]
    if kind == "regular":
        _hex(digest, 64, "member_digest")
        if target is not None or (size == 0 and digest != _EMPTY_SHA256):
            raise Refused("member_regular")
    else:
        if size != 0 or digest is not None:
            raise Refused("member_nonfile")
        if kind in ("symlink", "hardlink"):
            _text(target)  # Preserve as inert text. NEVER normalize, follow or create it.
        elif target is not None:
            raise Refused("member_directory")
    return Member(ordinal, name, canonical, kind, mode, uid, gid, size, digest, target)


def validate_manifest(
    raw: bytes, expected: ExpectedBinding, outcome: OwnerOutcome
) -> MemberInventory:
    """Check bounded child assertions against caller-owned identity and outcomes.

    A successful return establishes metadata consistency only. The future owner
    must independently authenticate its reader, archive and execution evidence.
    """
    wanted = _binding(expected)
    _owner(outcome)
    _raw_bound(raw)  # Before hashing, decoding or allocating a JSON object graph.
    document = _decode(raw)
    if type(document["schema"]) is not str or document["schema"] != SCHEMA:
        raise Refused("member_schema")
    binding = _shape(document["binding"], _BINDING_KEYS, "member_binding")
    if any(type(binding[k]) is not type(v) or binding[k] != v for k, v in wanted.items()):
        raise Refused("member_binding")
    expanded = _integer(document["expanded_bytes"], 1024, MAX_EXPANDED_BYTES, "member_extent")
    if expanded != outcome.expanded_bytes:
        raise Refused("member_extent")
    rows = document["members"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_MEMBERS:
        raise Refused("member_count")
    seen: dict[str, str] = {}
    required_directories: set[str] = set()
    members = []
    minimum_extent = 1024
    for ordinal, row in enumerate(rows):
        member = _member(row, ordinal)
        name = member.canonical_name
        if name in seen:
            raise Refused("member_duplicate_name")
        if member.kind != "directory" and name in required_directories:
            raise Refused("member_ancestor")
        components = name.split("/")
        for count in range(1, len(components)):
            parent = "/".join(components[:count])
            if parent in seen and seen[parent] != "directory":
                raise Refused("member_ancestor")
            required_directories.add(parent)
        minimum_extent += 512 + ((member.size + 511) // 512) * 512
        if minimum_extent > expanded:
            raise Refused("member_extent")
        seen[name] = member.kind
        members.append(member)
    return MemberInventory(expected, expanded, tuple(members), hashlib.sha256(raw).hexdigest())


def validate_batch(
    manifests: tuple[bytes, ...],
    expected: tuple[ExpectedBinding, ...],
    outcomes: tuple[OwnerOutcome, ...],
) -> tuple[MemberInventory, ...]:
    """Validate a fixed batch atomically; never expose a partial accepted batch."""
    for value in (manifests, expected, outcomes):
        if type(value) is not tuple or len(value) != BATCH_SIZE:
            raise Refused("member_batch_shape")
    raw_bytes = 0
    expanded_bytes = 0
    archive_ids: set[str] = set()
    shared_identity = None
    # Validate every caller outcome and input bound before any JSON decode.
    for raw, binding, outcome in zip(manifests, expected, outcomes, strict=True):
        _binding(binding)
        _owner(outcome)
        _raw_bound(raw)
        identity = (binding.run_id, binding.source_proof_sha256, binding.reader_policy_sha256)
        if shared_identity is None:
            shared_identity = identity
        if identity != shared_identity or binding.archive_sha256 in archive_ids:
            raise Refused("member_batch_binding")
        archive_ids.add(binding.archive_sha256)
        raw_bytes += len(raw)
        expanded_bytes += outcome.expanded_bytes
        if raw_bytes > MAX_BATCH_JSON_BYTES or expanded_bytes > MAX_BATCH_EXPANDED_BYTES:
            raise Refused("member_batch_budget")
    result = []
    member_count = 0
    for raw, binding, outcome in zip(manifests, expected, outcomes, strict=True):
        inventory = validate_manifest(raw, binding, outcome)
        member_count += len(inventory.members)
        if member_count > MAX_BATCH_MEMBERS:
            raise Refused("member_batch_budget")
        result.append(inventory)
    return tuple(result)
