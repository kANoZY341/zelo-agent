"""One-use validation contract for native-reader attempt-1 fence reconciliation.

Pure validation only. No Docker, filesystem mutation, admission clearing or runtime
authority on import.
"""

import hashlib
import json
import re

REFUSAL_HEAD = "c6127669cda7923c851febe1b4f8530402f58ef8"
REFUSAL_TREE = "d6eaa8a1fa029cd049384f66d27de5218ba7c8cd"
REFUSAL_SOURCE_SHA256 = "524509d4afdd2895350dd93155ce6a7b87658a76c12c1aea63febd41cc26b527"
ATTEMPT_NAME = "native-reader-proof-uvjet91z"
LEASE_IDENTITY = "9dd76284f54fe91361c4646fafc97a78c98f5179437345d54f9c815b8ea8bd0f"
POLICY_DIGEST = "d111c5d7a7e6ef4f2cece7579f29367a37bbeaea2317f60a10e5676a2e81e999"
POLICY_SHA256 = "a566a81238d144163e82ce91d728067c8afe36011fea404594f03f200ac868c3"
CASES_SHA256 = "027e7165fc21001eafb19cdb0a9c89bb0315012931c0a862ad742e5883655378"
MARKER_SHA256 = "f88573e617e66156815491696f8a51fed078e07df85658800cc1cb259bb34472"
FIXTURE_POLICY = "qualification/fixtures/media-native-reader-attempt1-policy.json"
FIXTURE_CASES = "qualification/fixtures/media-native-reader-attempt1-cases.json"
FIXTURE_MARKER = "qualification/fixtures/media-native-reader-attempt1-marker.json"


class Refused(ValueError):
    """Fixed reconciliation refusal with no host diagnostic payload."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused("reader_reconcile_" + code)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(rows):
    value = {}
    for key, item in rows:
        _require(type(key) is str and key not in value, "json")
        value[key] = item
    return value


def _constant(value):
    raise Refused("reader_reconcile_json")


def _json(raw: bytes, bound: int) -> dict:
    _require(type(raw) is bytes and 0 < len(raw) <= bound, "json")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise Refused("reader_reconcile_json") from None
    _require(type(value) is dict, "json")
    return value


def validate_marker(raw: bytes) -> dict:
    _require(_digest(raw) == MARKER_SHA256, "marker_pin")
    value = _json(raw, 4096)
    _require(set(value) == {"version", "lease", "policy"}, "marker_shape")
    _require(type(value["version"]) is int and value["version"] == 1, "marker_version")
    _require(
        type(value["lease"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["lease"]) is not None
        and value["lease"] == LEASE_IDENTITY,
        "marker_lease",
    )
    _require(value["policy"] == POLICY_DIGEST, "marker_policy")
    return value


def validate_policy(raw: bytes) -> dict:
    _require(_digest(raw) == POLICY_SHA256, "policy_pin")
    value = _json(raw, 65536)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _require(hashlib.sha256(canonical).hexdigest() == POLICY_DIGEST, "policy_digest")
    _require(value.get("schema") == "zelo-media-native-reader-synthetic-policy-v1", "policy")
    _require(value.get("attempt_limit") == 2, "policy")
    _require(
        value.get("proof_scope") == "runtime/media-native-reader/native-reader-proof-*",
        "policy",
    )
    _require(value.get("actual_package_provenance") is False, "policy")
    _require(value.get("image_decoder_authority") is False, "policy")
    _require(value.get("runtime_authority") is False, "policy")
    return value


def validate_cases(raw: bytes) -> dict:
    _require(_digest(raw) == CASES_SHA256, "cases_pin")
    value = _json(raw, 65536)
    _require(
        value
        == {
            "runtime_authority": False,
            "stage": "lease-preflight",
            "status": "refused",
            "support_release_confirmed": False,
        },
        "cases",
    )
    return value


def validate_fixture_bindings(marker: bytes, policy: bytes, cases: bytes) -> dict:
    validate_marker(marker)
    validate_policy(policy)
    validate_cases(cases)
    return {
        "schema": "zelo-media-native-reader-reconciliation-contract-v1",
        "attempt": ATTEMPT_NAME,
        "lease_identity": LEASE_IDENTITY,
        "policy_digest": POLICY_DIGEST,
        "marker_sha256": MARKER_SHA256,
        "policy_sha256": POLICY_SHA256,
        "cases_sha256": CASES_SHA256,
        "attempt_consumed": True,
        "marker_clear_authorized": False,
        "runtime_authority": False,
    }
