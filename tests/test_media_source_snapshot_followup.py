"""Offline Stage S2 decision and spent-proof checks; no snapshot request."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qualification import media_source_snapshot_decision as decision
from qualification import media_source_snapshot_ledger as evidence
from scripts import qualify_media_source_snapshot as operator


def test_followup_policy_has_new_namespace_and_original_policy_remains_strict():
    original = operator.policy()
    followup = operator.policy(followup=True)
    assert original["proof_scope"] == str(operator.PROOF_BASE / "snapshot-proof-*")
    assert "duplicate_metadata_headers" not in original
    assert followup["schema"] == "zelo-media-source-snapshot-stage-s2-policy-v1"
    assert followup["proof_scope"] == str(operator.FOLLOWUP_PROOF_BASE / "snapshot-proof-*")
    assert followup["decision_path"] == str(decision.FOLLOWUP_DECISION_PATH)
    assert followup["prior_stage_h_hashes"] == operator.PRIOR_HEADER_HASHES
    assert followup["decision_minimum_remaining_seconds"] == 120
    assert operator.PROOF_BASE != operator.FOLLOWUP_PROOF_BASE
    assert operator.HEADER_PROOF_BASE != operator.FOLLOWUP_PROOF_BASE


def test_followup_decision_is_exact_short_lived_and_private(tmp_path: Path):
    path = tmp_path / "decision.json"
    now = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
    urls = (
        "https://snapshot.ubuntu.com/ubuntu/fixed/InRelease",
        "https://snapshot.ubuntu.com/ubuntu/fixed/Sources.xz",
    )
    caps = (524288, 4194304)
    value = {
        "schema": "zelo-media-source-snapshot-stage-s2-decision-v1",
        "decision": "authorize-one-stage-s2-source-snapshot-input",
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "policy_digest": "c" * 64,
        "host_root": "/home/zelo/zelo",
        "urls": list(urls),
        "body_caps": list(caps),
        "additional_attempts": 1,
        "runtime_authority": False,
        "issued_utc": "2026-10-31T08:59:00Z",
        "expires_utc": "2026-10-31T09:04:00Z",
    }
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    with (
        patch.object(decision, "FOLLOWUP_DECISION_PATH", path),
        patch.object(decision, "source_identity", return_value=("a" * 40, "b" * 40)),
    ):

        def validate(clock=now, *, digest=None):
            return decision.validate_followup(
                path=path,
                expected_sha256=digest or hashlib.sha256(raw).hexdigest(),
                root=tmp_path,
                policy_digest="c" * 64,
                urls=urls,
                caps=caps,
                now=clock,
            )

        assert validate() == raw
        with pytest.raises(decision.DecisionRefused, match="expired"):
            validate(datetime(2026, 10, 31, 9, 2, 1, tzinfo=UTC))
        with pytest.raises(decision.DecisionRefused, match="digest"):
            validate(digest="d" * 64)
        for key, changed in (
            ("schema", "other"),
            ("source_tree", "d" * 40),
            ("urls", ["https://other.example.test/a", urls[1]]),
            ("body_caps", [1, caps[1]]),
            ("runtime_authority", True),
            ("additional_attempts", 2),
        ):
            altered = dict(value, **{key: changed})
            altered_raw = (json.dumps(altered, sort_keys=True) + "\n").encode()
            path.write_bytes(altered_raw)
            with pytest.raises(decision.DecisionRefused, match="binding"):
                validate(digest=hashlib.sha256(altered_raw).hexdigest())
        path.write_bytes(raw)
        path.chmod(0o644)
        with pytest.raises(decision.DecisionRefused, match="file"):
            validate()


def test_followup_prior_header_proof_is_pinned_and_tamper_refuses(tmp_path: Path, monkeypatch):
    root = tmp_path / "header-proof"
    proof = root / "snapshot-proof-fixed"
    admission = tmp_path / "header-admission"
    root.mkdir(mode=0o700)
    proof.mkdir(mode=0o700)
    admission.mkdir(mode=0o700)
    marker = admission / "active.marker"
    marker.write_bytes(b"")
    marker.chmod(0o600)
    hashes = {}
    for name in operator.PRIOR_HEADER_HASHES:
        path = proof / name
        raw = name.encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(operator, "PRIOR_HEADER_PROOF", proof)
    monkeypatch.setattr(operator, "HEADER_ADMISSION", admission)
    monkeypatch.setattr(operator, "PRIOR_HEADER_HASHES", hashes)
    operator._verify_spent_stage_h()
    (proof / "cases.json").write_bytes(b"changed")
    with pytest.raises(operator.SnapshotRefused, match="prior_header_drift"):
        operator._verify_spent_stage_h()


def test_followup_refuses_before_network_when_prior_evidence_drifts(tmp_path: Path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external boundary reached")

    monkeypatch.setattr(operator.os, "getresuid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator.os, "getresgid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator, "_ISOLATED_RUNTIME", True)
    monkeypatch.setattr(operator.decision, "validate_followup", lambda **kwargs: b"decision\n")
    monkeypatch.setattr(operator, "_verify_spent_stage_s", lambda: None)
    monkeypatch.setattr(operator, "_verify_spent_stage_h", forbidden)
    monkeypatch.setattr(operator, "FOLLOWUP_ADMISSION", tmp_path / "admission")
    monkeypatch.setattr(operator.transport, "NetworkOwner", forbidden)
    with pytest.raises(AssertionError, match="external boundary reached"):
        operator._qualify("a" * 64, followup=True)
    assert not (tmp_path / "admission").exists()


def test_followup_consumes_only_its_own_proof_before_offline_baseline(tmp_path: Path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network reached")

    def stop(*args, **kwargs):
        raise ValueError("offline stop")

    monkeypatch.setattr(operator.os, "getresuid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator.os, "getresgid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator, "_ISOLATED_RUNTIME", True)
    monkeypatch.setattr(operator, "HOST_ROOT", tmp_path)
    monkeypatch.setattr(operator, "FOLLOWUP_ADMISSION", tmp_path / "admission")
    monkeypatch.setattr(operator, "FOLLOWUP_PROOF_BASE", tmp_path / "proofs")
    monkeypatch.setattr(operator.decision, "validate_followup", lambda **kwargs: b"decision\n")
    monkeypatch.setattr(operator, "_verify_spent_stage_s", lambda: None)
    monkeypatch.setattr(operator, "_verify_spent_stage_h", lambda: None)
    monkeypatch.setattr(operator.package_operator, "_baseline", stop)
    monkeypatch.setattr(operator.transport, "NetworkOwner", forbidden)
    with pytest.raises(operator.SnapshotRefused, match="stage_s_refused"):
        operator._qualify("a" * 64, followup=True)
    attempts = list((tmp_path / "proofs").iterdir())
    assert len(attempts) == 1
    assert (attempts[0] / "decision.json").read_bytes() == b"decision\n"
    assert (attempts[0] / "failure.json").is_file()
    with pytest.raises(evidence.LedgerRefused, match="attempt_consumed"):
        evidence.allocate_attempt(tmp_path / "proofs")


def test_followup_input_accepts_metadata_duplicate_only(tmp_path: Path, monkeypatch):
    body = b"signed fixture bytes"
    reads = []

    class FakeOwner:
        def __init__(self, state, context, deadline, *, host):
            assert host == operator.transport.SNAPSHOT_HOST
            self.response = type(
                "Response",
                (),
                {
                    "status": 200,
                    "version": 11,
                    "chunked": False,
                    "length": len(body),
                    "headers": type(
                        "Headers",
                        (),
                        {
                            "defects": (),
                            "items": lambda self: [
                                ("Content-Length", str(len(body))),
                                ("Via", "edge-a"),
                                ("via", "edge-b"),
                            ],
                        },
                    )(),
                },
            )()

        def request(self, path):
            assert path == operator.stage.RELEASE_PATH
            return self.response

        def read1(self, count):
            reads.append(count)
            self.response.length = 0
            return body

        def cleanup(self):
            return True

    monkeypatch.setattr(operator.transport, "NetworkOwner", FakeOwner)
    for followup in (False, True):
        state = operator._State()
        proof = evidence.StageSLedger(evidence.allocate_attempt(tmp_path / str(followup)))
        inputs = operator._Inputs(state, proof, object(), float("inf"), {}, followup=followup)
        if followup:
            assert inputs.fetch(operator.stage.RELEASE_PATH, evidence.RELEASE_CAP) == body
            assert (proof.path / "InRelease").read_bytes() == body
        else:
            with pytest.raises(operator.transport.Refused, match="http_headers"):
                inputs.fetch(operator.stage.RELEASE_PATH, evidence.RELEASE_CAP)
            assert (proof.path / "InRelease").read_bytes() == b""
        state.close_all()
    assert reads == [len(body)]
