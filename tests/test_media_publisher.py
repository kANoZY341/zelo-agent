"""Offline envelope/verified-payload fixtures; fake calls are not crypto proofs."""

import base64
import copy
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from qualification import media_publisher as m

RAW = (
    Path(__file__).resolve().parents[1] / "qualification/fixtures/pillow-publisher-provenance.json"
).read_bytes()


def encoded(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def fixture():
    return json.loads(RAW)


def attestation(value):
    return value["attestation_bundles"][0]["attestations"][0]


def log(value):
    return attestation(value)["verification_material"]["transparency_entries"][0]


def fake_verified(value):
    # Structural/authority binding only; never counts as real crypto acceptance.
    return m.DSSE_TYPE, base64.b64decode(value["envelope"]["statement"], validate=True)


def test_public_evidence_digest_and_structural_receipt_only():
    assert (
        hashlib.sha256(RAW).hexdigest()
        == "1a4086109fcaa3747795acbc5f7a1dd63cbe5b4dcdc684f18b39d3d844fba32a"
    )
    a, statement = m._envelope(RAW)
    assert len(statement) == 308
    value = fixture()
    entry = log(value)
    assert entry["logIndex"] != entry["inclusionProof"]["logIndex"]
    assert "\u2014" in entry["inclusionProof"]["checkpoint"]["envelope"]
    receipt = m._verify(RAW, fake_verified)
    assert receipt.provenance_sha256 == hashlib.sha256(RAW).hexdigest()
    assert receipt.verified_statement_sha256 == hashlib.sha256(statement).hexdigest()
    assert receipt.archive_sha256 == m.WHEEL_SHA256 and receipt.filename == m.WHEEL_FILENAME
    assert (
        receipt.root_sha256 == m.ROOT_SHA256
        and receipt.tool_manifest_digest == m.TOOL_MANIFEST_DIGEST
    )
    assert (
        receipt.instruction_trust == "none"
        and receipt.trust == "operator-pinned-production-root-snapshot"
    )
    assert not any(
        hasattr(receipt, name)
        for name in (
            "approved",
            "success",
            "cleanup_confirmed",
            "confinement_qualified",
            "current_tuf_freshness",
        )
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.provenance_sha256 = "other"
    for kwargs in ({}, dataclasses.asdict(receipt), {"approved": True}):
        with pytest.raises(TypeError, match="verifier_required"):
            m.PublisherAttribution(**kwargs)
    assert receipt.digest == "08906bdf36e76385f4fe9dc690221ed31300f4d0f019a9679713a4eedec2bbb8"


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        {},
        [],
        "json",
        b"",
        bytearray(RAW),
        memoryview(RAW),
        type("Bytes", (bytes,), {})(RAW),
        b"x" * (m.PROVENANCE_BYTES + 1),
    ],
)
def test_strict_bytes_before_any_mature_or_file_activity(value):
    with pytest.raises(m.PublisherError):
        m._verify(value, lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":1,"version":1}',
        b'{"nested":{"x":1,"x":2}}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":"\xff"}',
        b"[]",
        b"null",
        b'"json"',
        b"{",
        b'{"x":' + b"[" * 1001 + b"0" + b"]" * 1001 + b"}",
    ],
)
def test_json_rejections_before_mature(raw):
    with pytest.raises(m.PublisherError):
        m._verify(raw, lambda *a: pytest.fail("mature reached"))


def test_json_depth_limit():
    nested = {}
    for _ in range(33):
        nested = {"x": nested}
    with pytest.raises(m.PublisherError, match="depth"):
        m._json(encoded(nested), m.PROVENANCE_BYTES)


@pytest.mark.parametrize(
    "location",
    [
        "root",
        "bundle",
        "attestation",
        "material",
        "envelope",
        "log",
        "logId",
        "promise",
        "proof",
        "checkpoint",
    ],
)
@pytest.mark.parametrize("operation", ["extra", "missing", "wrong_type"])
def test_every_closed_envelope_object(location, operation):
    value = fixture()
    locations = {
        "root": value,
        "bundle": value["attestation_bundles"][0],
        "attestation": attestation(value),
        "material": attestation(value)["verification_material"],
        "envelope": attestation(value)["envelope"],
        "log": log(value),
        "logId": log(value)["logId"],
        "promise": log(value)["inclusionPromise"],
        "proof": log(value)["inclusionProof"],
        "checkpoint": log(value)["inclusionProof"]["checkpoint"],
    }
    target = locations[location]
    if operation == "extra":
        target["unreviewed"] = "extra"
    elif operation == "missing":
        target.pop(next(iter(target)))
    else:
        target.clear()
        target["unexpected"] = []
    with pytest.raises(m.PublisherError):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize("location", ["bundles", "attestations", "transparency"])
@pytest.mark.parametrize("count", [0, 2])
def test_exact_one_cardinalities(location, count):
    value = fixture()
    target = {
        "bundles": value["attestation_bundles"],
        "attestations": value["attestation_bundles"][0]["attestations"],
        "transparency": attestation(value)["verification_material"]["transparency_entries"],
    }[location]
    original = copy.deepcopy(target[0])
    target[:] = [copy.deepcopy(original) for _ in range(count)]
    with pytest.raises(m.PublisherError, match="count"):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize("location", ["root", "attestation"])
@pytest.mark.parametrize("version", [True, False, 0, 2, "1", 1.0, None])
def test_versions_are_exact_integers(location, version):
    value = fixture()
    target = value if location == "root" else attestation(value)
    target["version"] = version
    with pytest.raises(m.PublisherError, match="version"):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize("field", ["kind", "repository", "workflow", "environment"])
def test_unsigned_reported_publisher_is_only_fixed_consistency(field):
    value = fixture()
    value["attestation_bundles"][0]["publisher"][field] = "other"
    with pytest.raises(m.PublisherError, match="reported_identity"):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize(
    "value", [None, True, 1, "", "AA", "AAAA\n", "!!!!", "\u0100", "AB==", "".join(["A"] * 10000)]
)
def test_canonical_bounded_base64(value):
    with pytest.raises(m.PublisherError):
        m._base64(value, 1024)


@pytest.mark.parametrize(
    "value", [None, True, 1, "", "-1", "+1", "01", "1.0", "\u0661", "9223372036854775808", "1" * 20]
)
def test_canonical_uint_strings(value):
    with pytest.raises(m.PublisherError):
        m._uint(value)


@pytest.mark.parametrize("field", ["integratedTime", "logIndex", "proofIndex", "treeSize"])
def test_log_uint_field_types(field):
    value = fixture()
    entry = log(value)
    target, key = (
        (entry, field)
        if field in ("integratedTime", "logIndex")
        else (entry["inclusionProof"], "logIndex" if field == "proofIndex" else "treeSize")
    )
    target[key] = True
    with pytest.raises(m.PublisherError):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize(
    "kind",
    [
        "version",
        "tree_bounds",
        "zero_hashes",
        "many_hashes",
        "short_hash",
        "empty_checkpoint",
        "large_checkpoint",
        "surrogate_checkpoint",
    ],
)
def test_log_profile_and_sizes(kind):
    value = fixture()
    entry = log(value)
    proof = entry["inclusionProof"]
    if kind == "version":
        entry["kindVersion"]["version"] = "0.0.2"
    elif kind == "tree_bounds":
        proof["treeSize"] = proof["logIndex"]
    elif kind == "zero_hashes":
        proof["hashes"] = []
    elif kind == "many_hashes":
        proof["hashes"] *= 5
    elif kind == "short_hash":
        proof["hashes"][0] = base64.b64encode(b"x" * 31).decode()
    elif kind == "empty_checkpoint":
        proof["checkpoint"]["envelope"] = ""
    elif kind == "large_checkpoint":
        proof["checkpoint"]["envelope"] = "x" * 8193
    else:
        proof["checkpoint"]["envelope"] = "\ud800"
    with pytest.raises(m.PublisherError):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize(
    "field,limit",
    [("certificate", 65536), ("statement", 65536), ("signature", 1024), ("body", 131072)],
)
def test_decoded_member_bounds(field, limit):
    value = fixture()
    a = attestation(value)
    if field == "certificate":
        target, key = a["verification_material"], field
    elif field == "body":
        target, key = log(value), "canonicalizedBody"
    else:
        target, key = a["envelope"], field
    target[key] = base64.b64encode(b"x" * (limit + 1)).decode()
    with pytest.raises(m.PublisherError):
        m._verify(encoded(value), lambda *a: pytest.fail("mature reached"))


@pytest.mark.parametrize(
    "kind",
    [
        "_type",
        "predicateType",
        "predicate",
        "extra",
        "subject_count",
        "subject_extra",
        "name",
        "case_name",
        "sha",
        "digest_extra",
        "subject_type",
    ],
)
def test_successfully_returned_bytes_still_require_exact_statement(kind):
    value = fixture()
    a = attestation(value)
    statement = json.loads(base64.b64decode(a["envelope"]["statement"]))
    if kind in ("_type", "predicateType"):
        statement[kind] = "other"
    elif kind == "predicate":
        statement["predicate"] = {}
    elif kind == "extra":
        statement["extra"] = True
    elif kind == "subject_count":
        statement["subject"] *= 2
    elif kind == "subject_extra":
        statement["subject"][0]["extra"] = True
    elif kind == "name":
        statement["subject"][0]["name"] = "other.whl"
    elif kind == "case_name":
        statement["subject"][0]["name"] = m.WHEEL_FILENAME.upper()
    elif kind == "sha":
        statement["subject"][0]["digest"]["sha256"] = "0" * 64
    elif kind == "digest_extra":
        statement["subject"][0]["digest"]["sha512"] = "0" * 128
    else:
        statement["subject"][0] = []
    a["envelope"]["statement"] = base64.b64encode(encoded(statement)).decode()
    with pytest.raises(m.PublisherError):
        m._verify(encoded(value), fake_verified)


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("other", b"x"),
        (None, b"x"),
        (m.DSSE_TYPE, "text"),
        (m.DSSE_TYPE, bytearray(b"x")),
        (m.DSSE_TYPE, b"x"),
    ],
)
def test_verified_type_and_exact_original_bytes_binding(kind, payload):
    with pytest.raises(m.PublisherError, match="verified_binding"):
        m._verify(RAW, lambda *a: (kind, payload))


def test_crypto_error_is_sanitized_and_no_statement_acceptance(monkeypatch):
    monkeypatch.setattr(m, "_statement", lambda *a: pytest.fail("unverified statement interpreted"))

    def refused(*args):
        raise RuntimeError("untrusted diagnostic content")

    with pytest.raises(m.PublisherError, match="crypto_refused") as error:
        m._verify(RAW, refused)
    assert "untrusted" not in str(error.value)


def test_ordinary_host_refused_before_any_optional_import_or_scan(monkeypatch):
    monkeypatch.setattr(m, "capture_manifest", lambda *a: pytest.fail("host scanner reached"))
    with pytest.raises(m.PublisherError, match="crypto_refused"):
        m.verify_pillow_publisher(RAW)


def test_policy_vector_and_uncached_nested_values():
    assert (
        m.PUBLISHER_POLICY_DIGEST
        == "9b8d34f2d2c0b31ecbc59b23f4203daee13477d5061973f106278a247b025aac"
    )
    value = m.publisher_policy()
    value["signer"]["commit"] = "other"
    value["tools"].clear()
    assert m.publisher_policy()["signer"]["commit"] == m.COMMIT
    assert m.publisher_policy()["tools"] == {"pypi-attestations": "0.0.30", "sigstore": "4.5.0"}
    assert (
        m.PUBLISHER_POLICY_DIGEST
        == hashlib.sha256(
            json.dumps(
                m.publisher_policy(), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )


def test_no_application_import_or_optional_module_import_at_collection():
    import sys

    root = Path(__file__).resolve().parents[1]
    for path in (root / "app").rglob("*.py"):
        assert "media_publisher" not in path.read_text()
    assert not any(
        name == "pypi_attestations"
        or name.startswith("pypi_attestations.")
        or name == "sigstore"
        or name.startswith("sigstore.")
        for name in sys.modules
    )
