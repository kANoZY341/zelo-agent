"""Closed optional operator attribution under a pinned root; no Core authority."""

import base64
import binascii
import hashlib
import json
import os
import stat
import sys
from dataclasses import asdict, dataclass, field

from qualification.artifacts import ArtifactPolicy, capture_manifest
from qualification.media_wheel import WHEEL_FILENAME, WHEEL_SHA256

PROVENANCE_BYTES = 1024**2
ROOT_BYTES = 6787
ROOT_SHA256 = "6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66"
ROOT_FILE = "/opt/zelo-publisher-root.json"
TOOL_ROOT = "/opt/zelo-publisher-tools"
TOOL_ROLES = (("verifier_native", "cryptography/hazmat/bindings/_rust.abi3.so"),)
TOOL_SCAN_POLICY = ArtifactPolicy(4096, 16, 16 * 1024**2, 256 * 1024**2, 8192)
GUEST_PATHS = (
    TOOL_ROOT,
    "/opt/zelo-publisher-source",
    "/usr/lib/python312.zip",
    "/usr/lib/python3.12",
    "/usr/lib/python3.12/lib-dynload",
)
TOOL_LOCK_SHA256 = "429dcbdd42195f0710503edf00b2ced171705efc6c6aac949fb244e1b3e6df63"
TOOL_MANIFEST_DIGEST = "414ddc6170a0eed9699dd799637c72fe7d1058c94b6e49c02cb41a0bbeaff829"
ISSUER = "https://token.actions.githubusercontent.com"
REPOSITORY = "https://github.com/python-pillow/Pillow"
COMMIT = "bb1d8e8ab8d29048624d96e3ee53cecf7c13d13d"
WORKFLOW = REPOSITORY + "/.github/workflows/wheels.yml@refs/tags/12.3.0"
DSSE_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://docs.pypi.org/attestations/publish/v1"


class PublisherError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def publisher_policy() -> dict:
    return {
        "schema": "zelo-media-publisher-policy-v1",
        "subject": {"filename": WHEEL_FILENAME, "sha256": WHEEL_SHA256},
        "signer": {
            "issuer": ISSUER,
            "repository": REPOSITORY,
            "commit": COMMIT,
            "workflow": WORKFLOW,
        },
        "dsse_type": DSSE_TYPE,
        "statement_type": STATEMENT_TYPE,
        "predicate_type": PREDICATE_TYPE,
        "predicate": None,
        "root_sha256": ROOT_SHA256,
        "root_bytes": ROOT_BYTES,
        "root_trust": "operator-pinned-production-root-snapshot",
        "tool_lock_sha256": TOOL_LOCK_SHA256,
        "tool_manifest_digest": TOOL_MANIFEST_DIGEST,
        "tool_roles": TOOL_ROLES,
        "tool_scan_policy": asdict(TOOL_SCAN_POLICY),
        "guest_paths": GUEST_PATHS,
        "interpreter_flags": ("-I", "-S", "-B"),
        "tools": {"pypi-attestations": "0.0.30", "sigstore": "4.5.0"},
        "provenance_bytes": PROVENANCE_BYTES,
        "json_depth": 32,
        "bundle_attestation_transparency_counts": (1, 1, 1),
        "decoded_bounds": {
            "certificate": 65536,
            "statement": 65536,
            "signature": 1024,
            "log_body": 131072,
        },
        "log_profile": "dsse-0.0.1-promise-time-proof-checkpoint",
        "current_tuf_freshness": False,
        "instruction_trust": "none",
        "execution_authority": False,
    }


PUBLISHER_POLICY_DIGEST = _digest(publisher_policy())


@dataclass(frozen=True, init=False)
class PublisherAttribution:
    """Crypto evidence only; native confinement and current tree checks are separate."""

    provenance_sha256: str
    verified_statement_sha256: str
    archive_sha256: str = field(default=WHEEL_SHA256, init=False)
    filename: str = field(default=WHEEL_FILENAME, init=False)
    root_sha256: str = field(default=ROOT_SHA256, init=False)
    tool_lock_sha256: str = field(default=TOOL_LOCK_SHA256, init=False)
    tool_manifest_digest: str = field(default=TOOL_MANIFEST_DIGEST, init=False)
    policy_digest: str = field(default=PUBLISHER_POLICY_DIGEST, init=False)
    schema: str = field(default="zelo-media-publisher-attribution-v1", init=False)
    trust: str = field(default="operator-pinned-production-root-snapshot", init=False)
    instruction_trust: str = field(default="none", init=False)

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("media_publisher_verifier_required")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


def _json(raw: bytes, limit: int) -> dict:
    if type(raw) is not bytes or not 1 <= len(raw) <= limit:
        raise PublisherError("media_publisher_bytes")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PublisherError("media_publisher_duplicate")
            result[key] = value
        return result

    def nonfinite(value):
        raise PublisherError("media_publisher_nonfinite")

    try:
        result = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=nonfinite
        )
    except (ValueError, UnicodeError, RecursionError):
        raise PublisherError("media_publisher_json") from None
    if type(result) is not dict:
        raise PublisherError("media_publisher_shape")
    stack = [(result, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 32:
            raise PublisherError("media_publisher_depth")
        if type(value) is dict:
            stack.extend((v, depth + 1) for v in value.values())
        elif type(value) is list:
            stack.extend((v, depth + 1) for v in value)
    return result


def _keys(value, expected: set[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise PublisherError("media_publisher_shape")


def _one(value):
    if type(value) is not list or len(value) != 1:
        raise PublisherError("media_publisher_count")
    return value[0]


def _base64(value, limit: int, *, exact: int | None = None) -> bytes:
    if type(value) is not str or not 1 <= len(value) <= 4 * ((limit + 2) // 3):
        raise PublisherError("media_publisher_base64")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise PublisherError("media_publisher_base64") from None
    if (
        not 1 <= len(result) <= limit
        or (exact is not None and len(result) != exact)
        or base64.b64encode(result).decode() != value
    ):
        raise PublisherError("media_publisher_base64")
    return result


def _uint(value) -> None:
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdigit()
        or not 1 <= len(value) <= 19
        or (len(value) > 1 and value.startswith("0"))
        or int(value) > 2**63 - 1
    ):
        raise PublisherError("media_publisher_log")


def _envelope(raw: bytes) -> tuple[dict, bytes]:
    provenance = _json(raw, PROVENANCE_BYTES)
    _keys(provenance, {"version", "attestation_bundles"})
    if type(provenance["version"]) is not int or provenance["version"] != 1:
        raise PublisherError("media_publisher_version")
    bundle = _one(provenance["attestation_bundles"])
    _keys(bundle, {"publisher", "attestations"})
    # Consistency only: these unsigned fields NEVER construct verification policy.
    if bundle["publisher"] != {
        "kind": "GitHub",
        "repository": "python-pillow/Pillow",
        "workflow": "wheels.yml",
        "environment": "release-pypi",
    }:
        raise PublisherError("media_publisher_reported_identity")
    attestation = _one(bundle["attestations"])
    _keys(attestation, {"version", "verification_material", "envelope"})
    if type(attestation["version"]) is not int or attestation["version"] != 1:
        raise PublisherError("media_publisher_version")
    material, envelope = attestation["verification_material"], attestation["envelope"]
    _keys(material, {"certificate", "transparency_entries"})
    _keys(envelope, {"signature", "statement"})
    _base64(material["certificate"], 65536)
    _base64(envelope["signature"], 1024)
    statement = _base64(envelope["statement"], 65536)
    log = _one(material["transparency_entries"])
    _keys(
        log,
        {
            "canonicalizedBody",
            "inclusionPromise",
            "inclusionProof",
            "integratedTime",
            "kindVersion",
            "logId",
            "logIndex",
        },
    )
    if log["kindVersion"] != {"kind": "dsse", "version": "0.0.1"}:
        raise PublisherError("media_publisher_log")
    _base64(log["canonicalizedBody"], 131072)
    _uint(log["integratedTime"])
    _uint(log["logIndex"])
    _keys(log["logId"], {"keyId"})
    _base64(log["logId"]["keyId"], 32, exact=32)
    _keys(log["inclusionPromise"], {"signedEntryTimestamp"})
    _base64(log["inclusionPromise"]["signedEntryTimestamp"], 1024)
    proof = log["inclusionProof"]
    _keys(proof, {"checkpoint", "hashes", "logIndex", "rootHash", "treeSize"})
    _uint(proof["logIndex"])
    _uint(proof["treeSize"])
    # Entry SET and Merkle proof authenticate their respective indices separately.
    if int(proof["treeSize"]) <= int(proof["logIndex"]):
        raise PublisherError("media_publisher_log")
    _base64(proof["rootHash"], 32, exact=32)
    if type(proof["hashes"]) is not list or not 1 <= len(proof["hashes"]) <= 64:
        raise PublisherError("media_publisher_log")
    for value in proof["hashes"]:
        _base64(value, 32, exact=32)
    _keys(proof["checkpoint"], {"envelope"})
    checkpoint = proof["checkpoint"]["envelope"]
    if type(checkpoint) is not str:
        raise PublisherError("media_publisher_log")
    try:
        checkpoint_bytes = checkpoint.encode("utf-8", "strict")
    except UnicodeError:
        raise PublisherError("media_publisher_log") from None
    if not 1 <= len(checkpoint_bytes) <= 8192:
        raise PublisherError("media_publisher_log")
    return attestation, statement


def _statement(raw: bytes) -> None:
    value = _json(raw, 65536)
    _keys(value, {"_type", "subject", "predicateType", "predicate"})
    if (
        value["_type"] != STATEMENT_TYPE
        or value["predicateType"] != PREDICATE_TYPE
        or value["predicate"] is not None
    ):
        raise PublisherError("media_publisher_statement")
    subject = _one(value["subject"])
    _keys(subject, {"name", "digest"})
    _keys(subject["digest"], {"sha256"})
    if subject["name"] != WHEEL_FILENAME or subject["digest"]["sha256"] != WHEEL_SHA256:
        raise PublisherError("media_publisher_subject")


def _identity_values(case: str) -> tuple[str, str, str, str]:
    # Closed operator negative controls; public verification always uses positive.
    values = (ISSUER, REPOSITORY, COMMIT, WORKFLOW)
    controls = {
        "wrong_issuer": (0, "https://invalid.example/issuer"),
        "wrong_repository": (1, "https://github.com/invalid/repository"),
        "wrong_commit": (2, "0" * 40),
        "wrong_workflow": (3, REPOSITORY + "/.github/workflows/invalid.yml@refs/tags/12.3.0"),
    }
    if case == "positive":
        return values
    if case not in controls:
        raise PublisherError("media_publisher_control")
    index, value = controls[case]
    return tuple(value if i == index else original for i, original in enumerate(values))


def _mature_verify(attestation: dict, *, _case: str = "positive") -> tuple[str, bytes]:
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.dont_write_bytecode
        or any(type(path) is not str or path not in GUEST_PATHS for path in sys.path)
        or TOOL_ROOT not in sys.path
    ):
        raise PublisherError("media_publisher_guest_required")
    manifest = capture_manifest(TOOL_ROOT, TOOL_ROLES, TOOL_SCAN_POLICY)
    if manifest.digest != TOOL_MANIFEST_DIGEST:
        raise PublisherError("media_publisher_tool_manifest")
    # Optional packages are imported only after actual tool-source binding in guest.
    from pypi_attestations import Attestation
    from sigstore.models import TrustedRoot
    from sigstore.verify import Verifier, policy

    fd = os.open(ROOT_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != ROOT_BYTES
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise PublisherError("media_publisher_root")
        data = bytearray()
        while block := os.read(fd, min(65536, ROOT_BYTES - len(data) + 1)):
            data.extend(block)
            if len(data) > ROOT_BYTES:
                raise PublisherError("media_publisher_root")
        if len(data) != ROOT_BYTES or hashlib.sha256(data).hexdigest() != ROOT_SHA256:
            raise PublisherError("media_publisher_root")
        os.lseek(fd, 0, os.SEEK_SET)
        root = TrustedRoot.from_file(f"/proc/self/fd/{fd}")
        verifier = Verifier(trusted_root=root)
        issuer, repository, commit, workflow = _identity_values(_case)
        identity = policy.AllOf(
            [
                policy.OIDCIssuerV2(issuer),
                policy.OIDCSourceRepositoryURI(repository),
                policy.OIDCSourceRepositoryDigest(commit),
                policy.OIDCBuildConfigURI(workflow),
            ]
        )
        result = verifier.verify_dsse(Attestation.model_validate(attestation).to_bundle(), identity)
        after = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise PublisherError("media_publisher_root_changed")
    finally:
        os.close(fd)
    return result


def _verify(raw: bytes, mature) -> PublisherAttribution:
    attestation, original_statement = _envelope(raw)
    try:
        kind, verified = mature(attestation)
    except Exception:
        raise PublisherError("media_publisher_crypto_refused") from None
    if (
        type(kind) is not str
        or kind != DSSE_TYPE
        or type(verified) is not bytes
        or verified != original_statement
    ):
        raise PublisherError("media_publisher_verified_binding")
    _statement(verified)  # Only successfully authenticated bytes are interpreted.
    result = object.__new__(PublisherAttribution)
    object.__setattr__(result, "provenance_sha256", hashlib.sha256(raw).hexdigest())
    object.__setattr__(result, "verified_statement_sha256", hashlib.sha256(verified).hexdigest())
    return result


def verify_pillow_publisher(raw: bytes) -> PublisherAttribution:
    """Crypto attribution under fixed root/tools; no confinement or execution claim."""
    return _verify(raw, _mature_verify)
