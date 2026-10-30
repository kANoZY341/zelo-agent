#!/usr/bin/env python3
"""One-use Stage S snapshot acquisition; inert source bytes, no Media runtime grant."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import resource
import sys
import time
from pathlib import Path


def _operator_limits():
    for kind, bound in (
        (resource.RLIMIT_AS, 536870912),
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_NOFILE, 256),
        (resource.RLIMIT_FSIZE, 8388608),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if any(value != resource.RLIM_INFINITY and value < bound for value in (soft, hard)):
            raise ValueError("media_snapshot_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("media_snapshot_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import NativeExecutionObservation, run_process  # noqa: E402
from qualification import media_package_acquisition as transport  # noqa: E402
from qualification import media_source_snapshot as snapshot  # noqa: E402
from qualification import media_source_snapshot_acquisition as stage  # noqa: E402
from qualification import media_source_snapshot_guest as guest  # noqa: E402
from qualification import media_source_snapshot_ledger as evidence  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from qualification.media_origin_guest import GUEST_BOOTSTRAP, KEYRING, TOOL  # noqa: E402
from scripts import qualify_media_origin as origin_operator  # noqa: E402
from scripts import qualify_media_package_acquisition as package_operator  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_elf_version_metadata import _helper, _release, _State  # noqa: E402

ADMISSION = "runtime/media-elf-metadata-admission"
PROOF_BASE = "runtime/media-source-snapshot-stage-s"
WHOLE_SECONDS = 120
CASE_SECONDS = 15
# Filled only by a separately reviewed one-use input decision at an exact tree.
APPROVED_DECISION_SHA = None
_QUARANTINED = []


class SnapshotRefused(ValueError):
    """Stable operator refusal; no network or GPG diagnostics in its text."""


def policy():
    return {
        "schema": "zelo-media-source-snapshot-stage-s-policy-v1",
        "snapshot_id": snapshot.SNAPSHOT_ID,
        "host": transport.SNAPSHOT_HOST,
        "release_path": stage.RELEASE_PATH,
        "sources_path": stage.SOURCES_PATH,
        "release_cap": evidence.RELEASE_CAP,
        "sources_cap": evidence.SOURCES_CAP,
        "journal_cap": evidence.JOURNAL_BYTES,
        "body_cap_total": evidence.RELEASE_CAP + evidence.SOURCES_CAP,
        "one_attempt": True,
        "requests": 2,
        "method": "GET",
        "redirect_proxy_retry_resume": False,
        "headers": dict(transport.HEADERS, Host=transport.SNAPSHOT_HOST),
        "header_bytes": transport.HEADER_BYTES,
        "header_lines": transport.HEADER_LINES,
        "socket_seconds": 10,
        "whole_seconds": WHOLE_SECONDS,
        "case_seconds": CASE_SECONDS,
        "deadline": "cooperative;DNS-TLS-buffered-I/O-filesystem-not-hard",
        "transport_baseline": transport.BASELINE,
        "ca_path": transport.CA_PATH,
        "ca_bytes": transport.CA_BYTES,
        "tool": TOOL,
        "keyring": KEYRING,
        "signer": snapshot.origin.FINGERPRINT,
        "guest_input_difference": "one fixed source identity in reviewed origin guest",
        "proof_scope": PROOF_BASE + "/snapshot-proof-*",
        "admission_scope": ADMISSION,
        "host_assumption": "trusted-ancestors-and-no-concurrent-mutator",
        "uploader_identity_verified": False,
        "installed_binary_provenance_verified": False,
        "decoder_qualified": False,
        "runtime_authority": False,
    }


def _journal(state: _State, ledger: evidence.StageSLedger, name: str, value) -> bytes:
    raw = (
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode()
    _helper(state, ledger.save_journal, name, raw)
    return raw


class _Inputs:
    def __init__(self, state, ledger, context, deadline, pins):
        self.state = state
        self.ledger = ledger
        self.context = context
        self.deadline = deadline
        self.pins = pins
        self.calls = 0

    def fetch(self, path: str, maximum: int, expected: dict | None = None) -> bytes:
        order = (
            (stage.RELEASE_PATH, evidence.RELEASE_CAP, "InRelease"),
            (stage.SOURCES_PATH, evidence.SOURCES_CAP, "Sources.xz"),
        )
        if self.calls >= len(order) or (path, maximum) != order[self.calls][:2]:
            raise SnapshotRefused("media_snapshot_request_sequence")
        name = order[self.calls][2]
        if (self.calls == 0 and expected is not None) or (
            self.calls == 1
            and (
                type(expected) is not dict
                or set(expected) != {"bytes", "sha256"}
                or type(expected["bytes"]) is not int
                or not 0 < expected["bytes"] <= maximum
                or type(expected["sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"])
            )
        ):
            raise SnapshotRefused("media_snapshot_expected_identity")
        self.calls += 1  # Consume before allocation or network, never retry this URL.
        transport.check_deadline(self.deadline)
        fd = _helper(self.state, self.ledger.open_body, name, maximum)
        self.state.hold(fd)
        if name == "InRelease":
            self.pins["input"] = fd
        owner = transport.NetworkOwner(
            self.state, self.context, self.deadline, host=transport.SNAPSHOT_HOST
        )
        request_raw = None
        try:
            response = owner.request(path)
            size = transport.bounded_content_length(response, maximum)
            if expected is not None and size != expected["bytes"]:
                raise SnapshotRefused("media_snapshot_declared_size")
            received = 0
            while received < size:
                transport.check_deadline(self.deadline)
                chunk = owner.read1(min(transport.CHUNK_BYTES, size - received))
                if type(chunk) is not bytes or not chunk or len(chunk) > size - received:
                    raise SnapshotRefused("media_snapshot_body_size")
                self.ledger.write_body(name, fd, chunk)
                received += len(chunk)
            if response.length != 0:
                raise SnapshotRefused("media_snapshot_http_length")
            request_raw = self.ledger.finish_body(
                name, fd, size, expected["sha256"] if expected is not None else None
            )
        except Exception:
            partial = _helper(self.state, self.ledger.persist_partial, name, fd)
            _journal(
                self.state,
                self.ledger,
                "request.json",
                {
                    "status": "partial-refused",
                    "path": path,
                    "requests_started": self.calls,
                    **partial,
                },
            )
            raise
        finally:
            error_type = sys.exc_info()[0]
            if error_type is not None and not issubclass(error_type, Exception):
                owner.unknown()
            elif not owner.cleanup():
                raise SnapshotRefused("media_snapshot_network_cleanup")
        transport.check_deadline(self.deadline)
        return request_raw

    def verify(self, raw: bytes) -> tuple[bytes, bytes, int, bool]:
        if self.calls != 1 or self.pins.get("input") is None:
            raise SnapshotRefused("media_snapshot_verify_sequence")
        return asyncio.run(self._verify_native(raw))

    async def _verify_native(self, raw: bytes) -> tuple[bytes, bytes, int, bool]:
        if not self.state.processes_settled:
            raise SnapshotRefused("media_snapshot_previous_process")
        fds = self.pins
        selected = origin_operator._prepare_fds(fds, "input")
        argv = origin_operator._argv(
            fds,
            "input",
            self.ledger.path / "host-only-sentinel",
            os.readlink("/proc/self/ns/net"),
            0,
            None,
        )
        if argv.count(GUEST_BOOTSTRAP) != 1:
            raise SnapshotRefused("media_snapshot_guest_argv")
        code = guest.bootstrap(len(raw), hashlib.sha256(raw).hexdigest())
        argv[argv.index(GUEST_BOOTSTRAP)] = code
        deadline = min(self.deadline, time.monotonic() + CASE_SECONDS)
        if deadline <= time.monotonic():
            raise SnapshotRefused("media_snapshot_verify_deadline")
        observation = NativeExecutionObservation()
        self.state.observations.append(observation)
        operation = asyncio.create_task(
            run_process(
                argv,
                cwd=ROOT,
                timeout=max(0.001, deadline - time.monotonic()),
                deadline=deadline,
                stdout_limit=524288,
                stderr_limit=16384,
                env=origin_operator.ENV,
                pass_fds=selected,
                observation=observation,
            )
        )
        result, error = await shared._own_operation(operation)
        if not observation.cleanup_confirmed or result is None or error is not None:
            raise SnapshotRefused("media_snapshot_verify_unsettled")
        if result.stderr:
            _helper(self.state, self.ledger.save_journal, "status.bin", result.stderr)
        if result.stdout:
            _helper(self.state, self.ledger.save_journal, "plaintext.bin", result.stdout)
        _journal(
            self.state,
            self.ledger,
            "verification.json",
            {
                "guest_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
                "exit_code": result.exit_code,
                "cleanup_confirmed": observation.cleanup_confirmed,
            },
        )
        return result.stdout, result.stderr, result.exit_code, observation.cleanup_confirmed


def _qualify():
    if APPROVED_DECISION_SHA is None:
        raise SnapshotRefused("media_snapshot_decision_missing")
    if os.getresuid() != (1000, 1000, 1000) or os.getresgid() != (1000, 1000, 1000):
        raise SnapshotRefused("media_snapshot_operator_ids")
    deadline = time.monotonic() + WHOLE_SECONDS
    admission_path = ROOT / ADMISSION
    admission_path.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission_path, directory=True)
    specification = policy()
    digest = shared._digest(specification)
    state = _State()
    lease = AdmissionController(admission_path).acquire(digest)
    ledger = None
    proof_raw = None
    try:
        ledger = evidence.StageSLedger(_helper(state, evidence.allocate_attempt, ROOT / PROOF_BASE))
        shared._private_path(ledger.path, directory=True)
        _journal(state, ledger, "policy.json", specification)
        package_operator._baseline(state, deadline)
        ca = package_operator._root_bytes(state, transport.CA_PATH, transport.CA_BYTES)
        context = _helper(state, transport.tls_context, ca)
        pins = {}
        for role, pin in (("tool", TOOL), ("keyring", KEYRING)):
            pins[role], _ = origin_operator._host_file(state, *pin)
        inputs = _Inputs(state, ledger, context, deadline, pins)
        selected = stage.assess_snapshot(inputs.fetch, inputs.verify)
        if inputs.calls != 2 or not state.processes_settled or not state.close_ok:
            raise SnapshotRefused("media_snapshot_incomplete")
        manifest_raw = _journal(
            state,
            ledger,
            "manifest.json",
            {
                "schema": "zelo-media-source-snapshot-stage-s-manifest-v1",
                "policy_digest": digest,
                "ca_sha256": hashlib.sha256(ca).hexdigest(),
                "source": selected,
                "requests": inputs.calls,
                "runtime_authority": False,
            },
        )
        proof_raw = _journal(
            state,
            ledger,
            "cases.json",
            {
                "status": "stage-s-conditional-accepted-pending-support-release",
                "policy_digest": digest,
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "requests": inputs.calls,
                "admission_release_confirmed": False,
                "runtime_authority": False,
            },
        )
        transport.check_deadline(deadline)
    except Exception as exc:
        if ledger is not None and "failure.json" not in ledger.journal_names:
            try:
                _journal(
                    state,
                    ledger,
                    "failure.json",
                    {"status": "refused", "type": type(exc).__name__, "runtime_authority": False},
                )
            except BaseException:
                state.close_ok = False
        raise SnapshotRefused("media_snapshot_stage_s_refused") from exc
    finally:
        error_type = sys.exc_info()[0]
        if error_type is not None and not issubclass(error_type, Exception):
            _QUARANTINED.append((state, lease))
        else:
            try:
                released = _release(state, lease)
            except BaseException:
                _QUARANTINED.append((state, lease))
                raise
    if released is not True or proof_raw is None:
        raise SnapshotRefused("media_snapshot_support_release")
    transport.check_deadline(deadline)
    return {
        "status": "stage-s-conditional-archive-source-evidence",
        "proof": str(ledger.path / "cases.json"),
        "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
        "policy_digest": digest,
        "admission_release_confirmed": True,
        "uploader_identity_verified": False,
        "installed_binary_provenance_verified": False,
        "decoder_qualified": False,
        "runtime_authority": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        result = _qualify()
    except Exception:
        print("media_snapshot_stage_s_refused", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
