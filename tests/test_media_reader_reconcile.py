"""Pure attempt-1 reconciliation contract tests; no Docker or admission mutation."""

import json
from pathlib import Path

import pytest

from qualification import media_reader_reconcile as r

ROOT = Path(__file__).resolve().parents[1]


def fixtures():
    return (
        (ROOT / r.FIXTURE_MARKER).read_bytes(),
        (ROOT / r.FIXTURE_POLICY).read_bytes(),
        (ROOT / r.FIXTURE_CASES).read_bytes(),
    )


def test_tracked_refusal_fixtures_match_exact_contract():
    marker, policy, cases = fixtures()
    value = r.validate_fixture_bindings(marker, policy, cases)
    assert value == {
        "schema": "zelo-media-native-reader-reconciliation-contract-v1",
        "attempt": r.ATTEMPT_NAME,
        "lease_identity": r.LEASE_IDENTITY,
        "policy_digest": r.POLICY_DIGEST,
        "marker_sha256": r.MARKER_SHA256,
        "policy_sha256": r.POLICY_SHA256,
        "cases_sha256": r.CASES_SHA256,
        "attempt_consumed": True,
        "marker_clear_authorized": False,
        "runtime_authority": False,
    }


@pytest.mark.parametrize("which", ["marker", "policy", "cases"])
def test_each_fixture_pin_refuses_byte_drift(which):
    marker, policy, cases = fixtures()
    values = {"marker": marker, "policy": policy, "cases": cases}
    raw = bytearray(values[which])
    raw[-1] ^= 1
    values[which] = bytes(raw)
    with pytest.raises(r.Refused, match=f"reader_reconcile_{which}_pin"):
        r.validate_fixture_bindings(values["marker"], values["policy"], values["cases"])


def test_marker_requires_exact_lease_and_policy_even_if_valid_json(monkeypatch):
    marker, _, _ = fixtures()
    value = json.loads(marker)
    value["lease"] = "0" * 64
    raw = json.dumps(value).encode()
    monkeypatch.setattr(r, "MARKER_SHA256", r._digest(raw))
    with pytest.raises(r.Refused, match="reader_reconcile_marker_lease"):
        r.validate_marker(raw)


def test_policy_requires_non_authoritative_attempt_scope(monkeypatch):
    _, policy, _ = fixtures()
    value = json.loads(policy)
    value["runtime_authority"] = True
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    monkeypatch.setattr(r, "POLICY_SHA256", r._digest(raw))
    monkeypatch.setattr(r, "POLICY_DIGEST", r._digest(canonical))
    with pytest.raises(r.Refused, match="reader_reconcile_policy"):
        r.validate_policy(raw)


def test_cases_require_exact_prebuild_refusal(monkeypatch):
    _, _, cases = fixtures()
    value = json.loads(cases)
    value["stage"] = "build"
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    monkeypatch.setattr(r, "CASES_SHA256", r._digest(raw))
    with pytest.raises(r.Refused, match="reader_reconcile_cases"):
        r.validate_cases(raw)


@pytest.mark.parametrize("raw", [b"NaN", b'{"a":1,"a":2}', b"\xff"])
def test_json_parser_refuses_nonfinite_duplicate_or_non_utf8(raw):
    with pytest.raises(r.Refused, match="reader_reconcile_json"):
        r._json(raw, 4096)
