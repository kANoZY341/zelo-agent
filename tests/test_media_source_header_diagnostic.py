"""Offline header-only diagnostic contract; no DNS, socket, or snapshot request."""

import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qualification import media_source_snapshot_decision as decision
from qualification import media_source_snapshot_ledger as evidence
from scripts import qualify_media_source_snapshot as operator


def test_header_summary_reveals_structure_without_peer_values():
    secret = "https://other.example.test/private?token=secret"
    response = type(
        "Response",
        (),
        {
            "status": 302,
            "version": 11,
            "chunked": True,
            "length": None,
            "headers": type(
                "Headers",
                (),
                {
                    "defects": (),
                    "items": lambda self: [
                        ("Transfer-Encoding", "chunked"),
                        ("Location", secret),
                        ("Set-Cookie", "credential=secret"),
                    ],
                },
            )(),
        },
    )()
    summary = operator._header_summary(response, 123, 4)
    assert summary == {
        "status": 302,
        "http_version": 11,
        "header_bytes": 123,
        "header_lines": 4,
        "header_count": 3,
        "header_defects": 0,
        "duplicate_header_names": 0,
        "invalid_header_names": 0,
        "invalid_header_values": 0,
        "content_length_count": 0,
        "content_length": None,
        "content_length_syntax_valid": False,
        "content_length_within_cap": False,
        "response_length": None,
        "transfer_encoding_count": 1,
        "chunked": True,
        "content_encoding_count": 0,
        "identity_encoding": False,
        "acceptable_content_encoding": True,
        "location_count": 1,
    }
    assert "secret" not in json.dumps(summary)


def test_header_summary_counts_duplicate_and_invalid_fields_without_values():
    secret = "sensitive-value\x01"
    response = type(
        "Response",
        (),
        {
            "status": 200,
            "version": 11,
            "chunked": False,
            "length": 524289,
            "headers": type(
                "Headers",
                (),
                {
                    "defects": (),
                    "items": lambda self: [
                        ("Content-Length", "524289"),
                        ("X-Trace", "ordinary"),
                        ("x-trace", secret),
                        ("Bad Name", "other"),
                        ("Content-Encoding", "Identity"),
                    ],
                },
            )(),
        },
    )()
    summary = operator._header_summary(response, 110, 5)
    assert summary["duplicate_header_names"] == 1
    assert summary["invalid_header_names"] == 1
    assert summary["invalid_header_values"] == 1
    assert summary["content_length_syntax_valid"] is True
    assert summary["content_length_within_cap"] is False
    assert summary["response_length"] == 524289
    assert summary["identity_encoding"] is False
    assert summary["acceptable_content_encoding"] is False
    assert secret not in json.dumps(summary)


def test_header_response_accepts_non_200_without_reading_body():
    raw = io.BytesIO(
        b"HTTP/1.1 302 Found\r\nLocation: https://other.example.test/private\r\n"
        b"Content-Length: 7\r\n\r\nsecret!"
    )

    class Socket:
        def makefile(self, mode):
            assert mode == "rb"
            return raw

    response = operator._HeaderResponse(Socket())
    response.fp = operator.transport.HeaderStream(response.fp, float("inf"))
    response.begin()
    assert response.status == 302
    assert raw.read() == b"secret!"
    response.close()


def test_header_decision_binds_private_file_and_single_url(tmp_path: Path):
    path = tmp_path / "decision.json"
    now = datetime(2026, 10, 31, 7, 0, tzinfo=UTC)
    value = {
        "schema": "zelo-media-source-header-diagnostic-decision-v1",
        "decision": "authorize-one-header-only-snapshot-diagnostic",
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "policy_digest": "c" * 64,
        "host_root": "/home/zelo/zelo",
        "url": "https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/dists/noble-updates/InRelease",
        "additional_attempts": 1,
        "body_bytes": 0,
        "runtime_authority": False,
        "issued_utc": "2026-10-31T06:59:00Z",
        "expires_utc": "2026-10-31T07:09:00Z",
    }
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    with (
        patch.object(decision, "HEADER_DECISION_PATH", path),
        patch.object(decision, "source_identity", return_value=("a" * 40, "b" * 40)),
    ):
        assert (
            decision.validate_header_diagnostic(
                path=path,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                root=tmp_path,
                policy_digest="c" * 64,
                url=value["url"],
                now=now,
            )
            == raw
        )
        with pytest.raises(decision.DecisionRefused, match="expired"):
            decision.validate_header_diagnostic(
                path=path,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
                root=tmp_path,
                policy_digest="c" * 64,
                url=value["url"],
                now=datetime(2026, 10, 31, 7, 8, tzinfo=UTC),
            )
        value["body_bytes"] = 1
        changed = (json.dumps(value, sort_keys=True) + "\n").encode()
        path.write_bytes(changed)
        with pytest.raises(decision.DecisionRefused):
            decision.validate_header_diagnostic(
                path=path,
                expected_sha256=hashlib.sha256(changed).hexdigest(),
                root=tmp_path,
                policy_digest="c" * 64,
                url=value["url"],
                now=now,
            )


def test_header_input_starts_one_get_and_retains_no_body(tmp_path: Path, monkeypatch):
    state = operator._State()
    proof = evidence.StageSLedger(evidence.allocate_attempt(tmp_path / "proofs"))
    paths = []

    class FakeOwner:
        def __init__(self, state, context, deadline, *, host):
            assert host == operator.transport.SNAPSHOT_HOST
            self.stream = type("Stream", (), {"header_bytes": 87, "header_lines": 3})()

        def request(self, path):
            paths.append(path)
            return type(
                "Response",
                (),
                {
                    "status": 200,
                    "version": 11,
                    "chunked": True,
                    "length": None,
                    "headers": type(
                        "Headers",
                        (),
                        {
                            "defects": (),
                            "items": lambda self: [("Transfer-Encoding", "chunked")],
                        },
                    )(),
                },
            )()

        def read1(self, count):
            raise AssertionError("body read forbidden")

        def cleanup(self):
            return True

    monkeypatch.setattr(operator, "_HeaderOwner", FakeOwner)
    inputs = operator._HeaderInput(state, proof, object(), float("inf"))
    summary = inputs.observe()
    assert summary["chunked"] is True
    assert paths == [operator.stage.RELEASE_PATH]
    assert not (proof.path / "InRelease").exists()
    assert not (proof.path / "Sources.xz").exists()
    with pytest.raises(operator.SnapshotRefused):
        inputs.observe()
    state.close_all()


def test_header_mode_without_decision_refuses_before_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external boundary reached")

    monkeypatch.setattr(operator, "AdmissionController", forbidden)
    monkeypatch.setattr(operator, "_HeaderOwner", forbidden, raising=False)
    with pytest.raises(operator.SnapshotRefused, match="decision_missing"):
        operator._qualify_header_diagnostic(None)


def test_header_cli_without_decision_refuses_before_admission():
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            str(operator.ROOT / "scripts/qualify_media_source_snapshot.py"),
            "--run",
            "--headers-diagnostic",
        ],
        capture_output=True,
        timeout=10,
    )
    assert run.returncode == 1
    assert run.stderr == b"media_header_diagnostic_refused\n"


def test_header_decision_retained_before_network_and_one_use(tmp_path: Path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network reached")

    def stop_at_baseline(*args, **kwargs):
        raise ValueError("offline stop")

    monkeypatch.setattr(operator.os, "getresuid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator.os, "getresgid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(operator, "_ISOLATED_RUNTIME", True)
    monkeypatch.setattr(operator, "HOST_ROOT", tmp_path)
    monkeypatch.setattr(operator, "HEADER_ADMISSION", tmp_path / "header-admission")
    monkeypatch.setattr(operator, "HEADER_PROOF_BASE", tmp_path / "header-proofs")
    monkeypatch.setattr(decision, "validate_header_diagnostic", lambda **kwargs: b"decision\n")
    monkeypatch.setattr(operator.package_operator, "_baseline", stop_at_baseline)
    monkeypatch.setattr(operator, "_HeaderOwner", forbidden)
    with pytest.raises(operator.SnapshotRefused, match="diagnostic_refused"):
        operator._qualify_header_diagnostic("a" * 64)
    attempts = list((tmp_path / "header-proofs").iterdir())
    assert len(attempts) == 1
    assert (attempts[0] / "decision.json").read_bytes() == b"decision\n"
    assert (attempts[0] / "failure.json").is_file()
    assert not (attempts[0] / "request.json").exists()
    with pytest.raises(evidence.LedgerRefused, match="attempt_consumed"):
        evidence.allocate_attempt(tmp_path / "header-proofs")


def test_header_request_failure_retains_fixed_code_without_response(tmp_path: Path, monkeypatch):
    state = operator._State()
    proof = evidence.StageSLedger(evidence.allocate_attempt(tmp_path / "proofs"))

    class FakeOwner:
        def __init__(self, state, context, deadline, *, host):
            assert host == operator.transport.SNAPSHOT_HOST

        def request(self, path):
            assert path == operator.stage.RELEASE_PATH
            raise operator.transport.Refused("acquisition_header_bytes")

        def cleanup(self):
            return True

    monkeypatch.setattr(operator, "_HeaderOwner", FakeOwner)
    inputs = operator._HeaderInput(state, proof, object(), float("inf"))
    with pytest.raises(operator.transport.Refused, match="acquisition_header_bytes"):
        inputs.observe()
    request = json.loads((proof.path / "request.json").read_text())
    failure = json.loads((proof.path / "failure.json").read_text())
    assert request["requests_started"] == 1
    assert request["body_bytes"] == 0
    assert failure["refusal_code"] == "acquisition_header_bytes"
    assert not (proof.path / "manifest.json").exists()
    assert not (proof.path / "InRelease").exists()
    state.close_all()


def test_header_preflight_requires_intact_spent_stage_s_evidence(tmp_path: Path, monkeypatch):
    root = tmp_path / "spent"
    proof = root / "snapshot-proof-10cwy2xm"
    proof.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    admission = tmp_path / "spent-admission"
    admission.mkdir(mode=0o700)
    (admission / "active.marker").write_bytes(b"")
    os.chmod(admission / "active.marker", 0o600)
    files = {
        "request.json": b'{"status":"partial-refused","requests_started":1,"bytes":0}\n',
        "failure.json": b'{"status":"refused","type":"Refused"}\n',
        "decision.json": b"spent-decision\n",
        "policy.json": b"spent-policy\n",
        "InRelease": b"",
    }
    for name, content in files.items():
        path = proof / name
        path.write_bytes(content)
        os.chmod(path, 0o600)
    monkeypatch.setattr(operator, "PRIOR_PROOF", proof, raising=False)
    monkeypatch.setattr(operator, "PRIOR_ADMISSION", admission, raising=False)
    monkeypatch.setattr(
        operator,
        "PRIOR_HASHES",
        {
            name: hashlib.sha256(content).hexdigest()
            for name, content in files.items()
            if name != "InRelease"
        },
        raising=False,
    )
    operator._verify_spent_stage_s()
    (proof / "request.json").write_bytes(b"changed")
    with pytest.raises(operator.SnapshotRefused):
        operator._verify_spent_stage_s()
