"""Offline Stage S operator checks; no socket, GPG process or input request."""

import hashlib
import json
from pathlib import Path

import pytest

from qualification import media_source_snapshot_ledger as evidence
from scripts import qualify_media_source_snapshot as operator


def test_inactive_decision_refuses_before_admission_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("operator reached an external boundary")

    monkeypatch.setattr(operator, "AdmissionController", forbidden)
    monkeypatch.setattr(operator.transport, "NetworkOwner", forbidden)
    with pytest.raises(operator.SnapshotRefused, match="decision_missing"):
        operator._qualify()


def test_one_use_namespace_is_fixed_across_worktrees():
    assert operator.PROOF_BASE == Path("/home/zelo/zelo/media-source-snapshot-stage-s-proof")
    assert operator.ADMISSION == Path("/home/zelo/zelo/media-source-snapshot-stage-s-admission")
    assert operator.PROOF_BASE != operator.ROOT / "runtime/media-source-snapshot-stage-s"


def test_fixed_url_sequence_and_hash_mismatch_retain_body(tmp_path: Path, monkeypatch):
    state = operator._State()
    proof = evidence.StageSLedger(evidence.allocate_attempt(tmp_path / "proofs"))
    body = b"fixed synthetic bytes"
    observed = []

    class FakeResponse:
        status = 200
        version = 11
        chunked = False
        headers = None

        def __init__(self):
            self.length = len(body)
            self.headers = type(
                "Headers",
                (),
                {"defects": (), "items": lambda self: [("Content-Length", str(len(body)))]},
            )()

    class FakeOwner:
        def __init__(self, state, context, deadline, *, host):
            observed.append(("host", host))
            self.response = FakeResponse()

        def request(self, path):
            observed.append(("request", path))
            return self.response

        def read1(self, count):
            self.response.length = 0
            return body

        def cleanup(self):
            observed.append(("cleanup",))
            return True

    monkeypatch.setattr(operator.transport, "NetworkOwner", FakeOwner)
    inputs = operator._Inputs(state, proof, object(), float("inf"), {})
    with pytest.raises(operator.SnapshotRefused, match="request_sequence"):
        inputs.fetch("/unexpected", evidence.RELEASE_CAP)
    assert observed == [] and inputs.calls == 0

    assert inputs.fetch(operator.stage.RELEASE_PATH, evidence.RELEASE_CAP) == body
    assert inputs.calls == 1
    with pytest.raises(operator.SnapshotRefused, match="expected_identity"):
        inputs.fetch(
            operator.stage.SOURCES_PATH,
            evidence.SOURCES_CAP,
            {"bytes": len(body), "sha256": "A" * 64},
        )
    assert inputs.calls == 1
    with pytest.raises(evidence.LedgerRefused, match="body_drift"):
        inputs.fetch(
            operator.stage.SOURCES_PATH,
            evidence.SOURCES_CAP,
            {"bytes": len(body), "sha256": hashlib.sha256(b"changed").hexdigest()},
        )
    assert inputs.calls == 2
    assert (proof.path / "Sources.xz").read_bytes() == body
    partial = json.loads((proof.path / "request.json").read_text())
    assert partial["status"] == "partial-refused"
    assert partial["bytes"] == len(body)
    assert partial["sha256"] == hashlib.sha256(body).hexdigest()
    assert [row[1] for row in observed if row[0] == "request"] == [
        operator.stage.RELEASE_PATH,
        operator.stage.SOURCES_PATH,
    ]
    state.close_all()


def test_catchable_interrupt_retains_partial_body_and_refusal(tmp_path: Path, monkeypatch):
    state = operator._State()
    proof = evidence.StageSLedger(evidence.allocate_attempt(tmp_path / "proofs"))
    observed = []

    class FakeOwner:
        def __init__(self, state, context, deadline, *, host):
            self.state = state
            self.response = type(
                "Response",
                (),
                {
                    "status": 200,
                    "version": 11,
                    "chunked": False,
                    "length": 10,
                    "headers": type(
                        "Headers",
                        (),
                        {"defects": (), "items": lambda self: [("Content-Length", "10")]},
                    )(),
                },
            )()

        def request(self, path):
            return self.response

        def read1(self, count):
            if not observed:
                observed.append("partial")
                return b"first"
            raise KeyboardInterrupt()

        def unknown(self):
            self.state.close_ok = False
            observed.append("quarantined")

    monkeypatch.setattr(operator.transport, "NetworkOwner", FakeOwner)
    inputs = operator._Inputs(state, proof, object(), float("inf"), {})
    with pytest.raises(KeyboardInterrupt):
        inputs.fetch(operator.stage.RELEASE_PATH, evidence.RELEASE_CAP)
    assert inputs.calls == 1
    assert observed == ["partial", "quarantined"]
    assert state.close_ok is False
    assert (proof.path / "InRelease").read_bytes() == b"first"
    record = json.loads((proof.path / "request.json").read_text())
    assert record["bytes"] == 5
    assert record["sha256"] == hashlib.sha256(b"first").hexdigest()
    state.close_all()
