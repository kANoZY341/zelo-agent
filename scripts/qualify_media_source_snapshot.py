#!/usr/bin/env python3
"""One-use Stage S snapshot acquisition; inert source bytes, no Media runtime grant."""

import argparse
import asyncio
import hashlib
import http.client
import json
import os
import re
import resource
import stat
import sys
import time
from pathlib import Path

_ISOLATED_RUNTIME = False


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
    if not sys.flags.isolated:
        raise SystemExit("media_snapshot_isolated_python_required")
    cache_prefix = f"/home/zelo/zelo/.stage-s-pycache-{os.urandom(16).hex()}"
    if os.path.lexists(cache_prefix):
        raise SystemExit("media_snapshot_cache_prefix_occupied")
    sys.pycache_prefix = cache_prefix
    sys.dont_write_bytecode = True
    _ISOLATED_RUNTIME = True
    _operator_limits()
if not __debug__:
    raise SystemExit("media_snapshot_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.developer_process import NativeExecutionObservation, run_process  # noqa: E402
from qualification import media_package_acquisition as transport  # noqa: E402
from qualification import media_source_snapshot as snapshot  # noqa: E402
from qualification import media_source_snapshot_acquisition as stage  # noqa: E402
from qualification import media_source_snapshot_decision as decision  # noqa: E402
from qualification import media_source_snapshot_guest as guest  # noqa: E402
from qualification import media_source_snapshot_ledger as evidence  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from qualification.media_origin_guest import GUEST_BOOTSTRAP, KEYRING, TOOL  # noqa: E402
from scripts import qualify_media_origin as origin_operator  # noqa: E402
from scripts import qualify_media_package_acquisition as package_operator  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_elf_version_metadata import _helper, _release, _State  # noqa: E402

HOST_ROOT = Path("/home/zelo/zelo")
ADMISSION = HOST_ROOT / "media-source-snapshot-stage-s-admission"
PROOF_BASE = HOST_ROOT / "media-source-snapshot-stage-s-proof"
HEADER_ADMISSION = HOST_ROOT / "media-source-snapshot-stage-h-admission"
HEADER_PROOF_BASE = HOST_ROOT / "media-source-snapshot-stage-h-proof"
FOLLOWUP_ADMISSION = HOST_ROOT / "media-source-snapshot-stage-s2-admission"
FOLLOWUP_PROOF_BASE = HOST_ROOT / "media-source-snapshot-stage-s2-proof"
PRIOR_PROOF = PROOF_BASE / "snapshot-proof-10cwy2xm"
PRIOR_ADMISSION = ADMISSION
PRIOR_HASHES = {
    "decision.json": "513454c0026f5aad71301f875de31813f30dd0b67ff00f5bbd65946be1f0d777",
    "policy.json": "f0f9cdbb9f41550ca1b60ed2fa6f19f34279aabc15181acdaaf09a67b103eaee",
    "failure.json": "0df5f5cca68caad1ebdf2a78eea80aaa7b3df19d4ebe5045521fac1b0b07dcec",
    "request.json": "e63fafb182433f97caa901c7b35c94747f0ae76414381059fd87d2bb7afadbbd",
}
WHOLE_SECONDS = 120
CASE_SECONDS = 15
_QUARANTINED = []
PRIOR_HEADER_PROOF = HEADER_PROOF_BASE / "snapshot-proof-m_c_seem"
PRIOR_HEADER_HASHES = {
    "cases.json": "003cbad44e0f1fb9ed94dd1efb63f03a13193282ed8e767f09cbf51564c622e3",
    "decision.json": "09b2cbe5e95bccd1807214f89c9ed04b9969139559c9f51e41c824c4d2db1ffb",
    "manifest.json": "3fc81e48088d73b32d402a7a301a615f08a25c227b41a9694d88770d8c48db6a",
    "policy.json": "1b8080b414c4b1ada810e053523eaca2ac958fe854076e2c4cba3d12dd190bab",
    "request.json": "aad639f73270574e495f5609a91da61a02cc7465ff030270a6ad31db13c1b1fd",
}


class SnapshotRefused(ValueError):
    """Stable operator refusal; no network or GPG diagnostics in its text."""


def policy(*, followup: bool = False):
    specification = {
        "schema": (
            "zelo-media-source-snapshot-stage-s2-policy-v1"
            if followup
            else "zelo-media-source-snapshot-stage-s-policy-v1"
        ),
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
        "proof_scope": str((FOLLOWUP_PROOF_BASE if followup else PROOF_BASE) / "snapshot-proof-*"),
        "admission_scope": str(FOLLOWUP_ADMISSION if followup else ADMISSION),
        "decision_path": str(
            decision.FOLLOWUP_DECISION_PATH if followup else decision.DECISION_PATH
        ),
        "decision_max_bytes": decision.MAX_BYTES,
        "decision_lifetime_seconds": 600,
        "host_assumption": "trusted-ancestors-and-no-concurrent-mutator",
        "uploader_identity_verified": False,
        "installed_binary_provenance_verified": False,
        "decoder_qualified": False,
        "runtime_authority": False,
    }
    if followup:
        specification.update(
            {
                "duplicate_metadata_headers": "unused-duplicates-validated;critical-singular",
                "prior_stage_s_proof": str(PRIOR_PROOF),
                "prior_stage_s_hashes": PRIOR_HASHES,
                "prior_stage_h_proof": str(PRIOR_HEADER_PROOF),
                "prior_stage_h_hashes": PRIOR_HEADER_HASHES,
                "prior_stage_h_admission": str(HEADER_ADMISSION),
                "decision_minimum_remaining_seconds": WHOLE_SECONDS,
            }
        )
    return specification


def _journal(state: _State, ledger: evidence.StageSLedger, name: str, value) -> bytes:
    raw = (
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode()
    _helper(state, ledger.save_journal, name, raw)
    return raw


def _private_host_admission() -> None:
    if ADMISSION.parent != HOST_ROOT or PROOF_BASE.parent != HOST_ROOT:
        raise SnapshotRefused("media_snapshot_host_root")
    parent = HOST_ROOT.lstat()
    target = ADMISSION.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o022
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != os.geteuid()
        or target.st_gid != os.getegid()
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise SnapshotRefused("media_snapshot_host_admission")


def _private_header_admission() -> None:
    if HEADER_ADMISSION.parent != HOST_ROOT or HEADER_PROOF_BASE.parent != HOST_ROOT:
        raise SnapshotRefused("media_header_host_root")
    parent = HOST_ROOT.lstat()
    target = HEADER_ADMISSION.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o022
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != os.geteuid()
        or target.st_gid != os.getegid()
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise SnapshotRefused("media_header_host_admission")


def _verify_spent_stage_s() -> None:
    """Pin the terminal first attempt before a separate header-only input."""
    root = PRIOR_PROOF.parent
    for folder in (root, PRIOR_PROOF, PRIOR_ADMISSION):
        info = folder.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise SnapshotRefused("media_header_prior_directory")
    if set(os.listdir(root)) != {PRIOR_PROOF.name} or set(os.listdir(PRIOR_PROOF)) != (
        set(PRIOR_HASHES) | {"InRelease"}
    ):
        raise SnapshotRefused("media_header_prior_entries")
    for path, expected in (
        *((PRIOR_PROOF / name, digest) for name, digest in PRIOR_HASHES.items()),
        (PRIOR_PROOF / "InRelease", hashlib.sha256(b"").hexdigest()),
        (PRIOR_ADMISSION / "active.marker", hashlib.sha256(b"").hexdigest()),
    ):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or not 0 <= before.st_size <= 1048576
            ):
                raise SnapshotRefused("media_header_prior_file")
            raw = os.read(fd, before.st_size + 1)
            after = os.fstat(fd)
            stable = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if (
                len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != expected
                or any(getattr(before, key) != getattr(after, key) for key in stable)
            ):
                raise SnapshotRefused("media_header_prior_drift")
        finally:
            os.close(fd)


def _verify_spent_stage_h() -> None:
    """Pin the consumed diagnostic and its empty lease before a new source input."""
    root = PRIOR_HEADER_PROOF.parent
    for folder in (root, PRIOR_HEADER_PROOF, HEADER_ADMISSION):
        info = folder.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise SnapshotRefused("media_snapshot_prior_header_directory")
    if set(os.listdir(root)) != {PRIOR_HEADER_PROOF.name} or set(
        os.listdir(PRIOR_HEADER_PROOF)
    ) != set(PRIOR_HEADER_HASHES):
        raise SnapshotRefused("media_snapshot_prior_header_entries")
    for path, expected in (
        *((PRIOR_HEADER_PROOF / name, digest) for name, digest in PRIOR_HEADER_HASHES.items()),
        (HEADER_ADMISSION / "active.marker", hashlib.sha256(b"").hexdigest()),
    ):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or not 0 <= before.st_size <= 1048576
            ):
                raise SnapshotRefused("media_snapshot_prior_header_file")
            raw = os.read(fd, before.st_size + 1)
            after = os.fstat(fd)
            stable = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if (
                len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != expected
                or any(getattr(before, key) != getattr(after, key) for key in stable)
            ):
                raise SnapshotRefused("media_snapshot_prior_header_drift")
        finally:
            os.close(fd)


def _private_followup_admission() -> None:
    if FOLLOWUP_ADMISSION.parent != HOST_ROOT or FOLLOWUP_PROOF_BASE.parent != HOST_ROOT:
        raise SnapshotRefused("media_snapshot_followup_host_root")
    parent = HOST_ROOT.lstat()
    target = FOLLOWUP_ADMISSION.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o022
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != os.geteuid()
        or target.st_gid != os.getegid()
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise SnapshotRefused("media_snapshot_followup_host_admission")


def header_policy():
    return {
        "schema": "zelo-media-source-header-diagnostic-policy-v1",
        "url": f"https://{transport.SNAPSHOT_HOST}{stage.RELEASE_PATH}",
        "method": "GET",
        "requests": 1,
        "body_bytes": 0,
        "no_redirect_proxy_retry_resume": True,
        "headers": dict(transport.HEADERS, Host=transport.SNAPSHOT_HOST),
        "header_bytes": transport.HEADER_BYTES,
        "header_lines": transport.HEADER_LINES,
        "socket_seconds": 10,
        "whole_seconds": WHOLE_SECONDS,
        "deadline": "cooperative;DNS-TLS-buffered-I/O-filesystem-not-hard",
        "transport_baseline": transport.BASELINE,
        "ca_path": transport.CA_PATH,
        "ca_bytes": transport.CA_BYTES,
        "proof_scope": str(HEADER_PROOF_BASE / "snapshot-proof-*"),
        "admission_scope": str(HEADER_ADMISSION),
        "decision_path": str(decision.HEADER_DECISION_PATH),
        "decision_max_bytes": decision.MAX_BYTES,
        "decision_lifetime_seconds": 600,
        "decision_minimum_remaining_seconds": decision.HEADER_MINIMUM_SECONDS,
        "prior_stage_s_proof": str(PRIOR_PROOF),
        "prior_stage_s_hashes": PRIOR_HASHES,
        "prior_stage_s_admission": str(PRIOR_ADMISSION),
        "retained_journal_bytes": evidence.JOURNAL_BYTES,
        "host_assumption": "trusted-ancestors-and-no-concurrent-mutator",
        "source_provenance_verified": False,
        "runtime_authority": False,
    }


class _HeaderResponse(http.client.HTTPResponse):
    def begin(self):
        super().begin()
        self.fp.header_mode = False


class _HeaderOwner(transport.NetworkOwner):
    def response_factory(self, sock, *args, **kwargs):
        response = _HeaderResponse(sock, *args, **kwargs)
        self.response = response
        self.stream = transport.HeaderStream(response.fp, self.deadline)
        response.fp = self.stream
        return response


def _header_summary(response, header_bytes: int, header_lines: int) -> dict:
    if (
        type(response.status) is not int
        or not 100 <= response.status <= 599
        or type(response.version) is not int
        or not 0 <= header_bytes <= transport.HEADER_BYTES
        or not 0 <= header_lines <= transport.HEADER_LINES
    ):
        raise SnapshotRefused("media_header_response_shape")
    rows = response.headers.items()
    if len(rows) > transport.HEADER_LINES or len(response.headers.defects) > transport.HEADER_LINES:
        raise SnapshotRefused("media_header_count")
    fields = {
        name: [] for name in ("content-length", "transfer-encoding", "content-encoding", "location")
    }
    seen = set()
    duplicate_names = 0
    invalid_names = 0
    invalid_values = 0
    for name, value in rows:
        if type(name) is not str or type(value) is not str:
            raise SnapshotRefused("media_header_field_shape")
        lowered = name.lower()
        if lowered in seen:
            duplicate_names += 1
        seen.add(lowered)
        if not transport._TOKEN.fullmatch(name):
            invalid_names += 1
        if len(value) > transport.HEADER_LINE_BYTES or any(
            ord(char) < 32 or ord(char) > 126 for char in value
        ):
            invalid_values += 1
        if lowered in fields:
            fields[lowered].append(value)
    lengths = fields["content-length"]
    length = (
        int(lengths[0]) if len(lengths) == 1 and re.fullmatch(r"[0-9]{1,9}", lengths[0]) else None
    )
    length_syntax_valid = len(lengths) == 1 and bool(re.fullmatch(r"[1-9][0-9]{0,6}", lengths[0]))
    response_length = response.length
    if type(response_length) is not int or not 0 <= response_length <= 999999999:
        response_length = None
    identity_encoding = (
        len(fields["content-encoding"]) == 1 and fields["content-encoding"][0] == "identity"
    )
    return {
        "status": response.status,
        "http_version": response.version,
        "header_bytes": header_bytes,
        "header_lines": header_lines,
        "header_count": len(rows),
        "header_defects": len(response.headers.defects),
        "duplicate_header_names": duplicate_names,
        "invalid_header_names": invalid_names,
        "invalid_header_values": invalid_values,
        "content_length_count": len(lengths),
        "content_length": length,
        "content_length_syntax_valid": length_syntax_valid,
        "content_length_within_cap": length_syntax_valid and length <= evidence.RELEASE_CAP,
        "response_length": response_length,
        "transfer_encoding_count": len(fields["transfer-encoding"]),
        "chunked": response.chunked is True,
        "content_encoding_count": len(fields["content-encoding"]),
        "identity_encoding": identity_encoding,
        "acceptable_content_encoding": not fields["content-encoding"] or identity_encoding,
        "location_count": len(fields["location"]),
    }


class _HeaderInput:
    def __init__(self, state, ledger, context, deadline):
        self.state = state
        self.ledger = ledger
        self.context = context
        self.deadline = deadline
        self.calls = 0

    def observe(self) -> dict:
        if self.calls:
            raise SnapshotRefused("media_header_attempt_consumed")
        self.calls = 1
        _journal(
            self.state,
            self.ledger,
            "request.json",
            {
                "status": "started",
                "path": stage.RELEASE_PATH,
                "requests_started": 1,
                "body_bytes": 0,
            },
        )
        transport.check_deadline(self.deadline)
        owner = _HeaderOwner(self.state, self.context, self.deadline, host=transport.SNAPSHOT_HOST)
        try:
            response = owner.request(stage.RELEASE_PATH)
            summary = _header_summary(
                response, owner.stream.header_bytes, owner.stream.header_lines
            )
        except BaseException as exc:
            _journal(
                self.state,
                self.ledger,
                "failure.json",
                {
                    "status": "refused",
                    "type": type(exc).__name__,
                    "refusal_code": (
                        str(exc)
                        if type(exc) is transport.Refused
                        and re.fullmatch(r"acquisition_[a-z_]{1,48}", str(exc))
                        else "unclassified"
                    ),
                    "runtime_authority": False,
                },
            )
            raise
        finally:
            error_type = sys.exc_info()[0]
            if error_type is not None and not issubclass(error_type, Exception):
                owner.unknown()
            elif not owner.cleanup():
                self.state.close_ok = False
                raise SnapshotRefused("media_header_network_cleanup")
        transport.check_deadline(self.deadline)
        _journal(
            self.state,
            self.ledger,
            "manifest.json",
            {"schema": "zelo-media-header-observation-v1", "response": summary, "body_bytes": 0},
        )
        return summary


class _Inputs:
    def __init__(self, state, ledger, context, deadline, pins, *, followup=False):
        self.state = state
        self.ledger = ledger
        self.context = context
        self.deadline = deadline
        self.pins = pins
        self.followup = followup
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
            size = transport.bounded_content_length(
                response, maximum, allow_duplicate_metadata=self.followup
            )
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
        except BaseException as exc:
            partial = _helper(self.state, self.ledger.persist_partial, name, fd)
            _journal(
                self.state,
                self.ledger,
                "request.json",
                {
                    "status": "partial-refused",
                    "path": path,
                    "requests_started": self.calls,
                    "refusal_code": (
                        str(exc)
                        if type(exc) is transport.Refused
                        and re.fullmatch(r"acquisition_[a-z_]{1,48}", str(exc))
                        else "unclassified"
                    ),
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


def _qualify(decision_sha: str | None = None, *, followup: bool = False):
    if decision_sha is None:
        raise SnapshotRefused("media_snapshot_decision_missing")
    if not _ISOLATED_RUNTIME:
        raise SnapshotRefused("media_snapshot_isolation_missing")
    if os.getresuid() != (1000, 1000, 1000) or os.getresgid() != (1000, 1000, 1000):
        raise SnapshotRefused("media_snapshot_operator_ids")
    deadline = time.monotonic() + WHOLE_SECONDS
    if followup and decision.FOLLOWUP_MINIMUM_SECONDS != WHOLE_SECONDS:
        raise SnapshotRefused("media_snapshot_followup_lifetime")
    specification = policy(followup=followup)
    digest = shared._digest(specification)
    validate = decision.validate_followup if followup else decision.validate
    decision_raw = validate(
        path=decision.FOLLOWUP_DECISION_PATH if followup else decision.DECISION_PATH,
        expected_sha256=decision_sha,
        root=ROOT,
        policy_digest=digest,
        urls=(
            f"https://{transport.SNAPSHOT_HOST}{stage.RELEASE_PATH}",
            f"https://{transport.SNAPSHOT_HOST}{stage.SOURCES_PATH}",
        ),
        caps=(evidence.RELEASE_CAP, evidence.SOURCES_CAP),
    )
    if followup:
        _verify_spent_stage_s()
        _verify_spent_stage_h()
    admission_path = FOLLOWUP_ADMISSION if followup else ADMISSION
    admission_path.mkdir(mode=0o700, exist_ok=True)
    if followup:
        _private_followup_admission()
    else:
        _private_host_admission()
    state = _State()
    lease = AdmissionController(admission_path).acquire(digest)
    ledger = None
    proof_raw = None
    try:
        proof_base = FOLLOWUP_PROOF_BASE if followup else PROOF_BASE
        ledger = evidence.StageSLedger(_helper(state, evidence.allocate_attempt, proof_base))
        _journal(state, ledger, "policy.json", specification)
        _helper(state, ledger.save_journal, "decision.json", decision_raw)
        package_operator._baseline(state, deadline)
        ca = package_operator._root_bytes(state, transport.CA_PATH, transport.CA_BYTES)
        context = _helper(state, transport.tls_context, ca)
        pins = {}
        for role, pin in (("tool", TOOL), ("keyring", KEYRING)):
            pins[role], _ = origin_operator._host_file(state, *pin)
        inputs = _Inputs(state, ledger, context, deadline, pins, followup=followup)
        selected = stage.assess_snapshot(inputs.fetch, inputs.verify)
        if inputs.calls != 2 or not state.processes_settled or not state.close_ok:
            raise SnapshotRefused("media_snapshot_incomplete")
        manifest_raw = _journal(
            state,
            ledger,
            "manifest.json",
            {
                "schema": (
                    "zelo-media-source-snapshot-stage-s2-manifest-v1"
                    if followup
                    else "zelo-media-source-snapshot-stage-s-manifest-v1"
                ),
                "policy_digest": digest,
                "decision_sha256": hashlib.sha256(decision_raw).hexdigest(),
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
                "status": (
                    "stage-s2-conditional-accepted-pending-support-release"
                    if followup
                    else "stage-s-conditional-accepted-pending-support-release"
                ),
                "policy_digest": digest,
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "requests": inputs.calls,
                "admission_release_confirmed": False,
                "runtime_authority": False,
            },
        )
        transport.check_deadline(deadline)
    except BaseException as exc:
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
        if isinstance(exc, Exception):
            raise SnapshotRefused("media_snapshot_stage_s_refused") from exc
        raise
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
        "status": (
            "stage-s2-conditional-archive-source-evidence"
            if followup
            else "stage-s-conditional-archive-source-evidence"
        ),
        "proof": str(ledger.path / "cases.json"),
        "proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
        "policy_digest": digest,
        "decision_sha256": hashlib.sha256(decision_raw).hexdigest(),
        "admission_release_confirmed": True,
        "uploader_identity_verified": False,
        "installed_binary_provenance_verified": False,
        "decoder_qualified": False,
        "runtime_authority": False,
    }


def _qualify_header_diagnostic(decision_sha: str | None = None):
    if decision_sha is None:
        raise SnapshotRefused("media_header_decision_missing")
    if not _ISOLATED_RUNTIME:
        raise SnapshotRefused("media_header_isolation_missing")
    if os.getresuid() != (1000, 1000, 1000) or os.getresgid() != (1000, 1000, 1000):
        raise SnapshotRefused("media_header_operator_ids")
    if decision.HEADER_MINIMUM_SECONDS != WHOLE_SECONDS:
        raise SnapshotRefused("media_header_lifetime")
    deadline = time.monotonic() + WHOLE_SECONDS
    specification = header_policy()
    digest = shared._digest(specification)
    decision_raw = decision.validate_header_diagnostic(
        path=decision.HEADER_DECISION_PATH,
        expected_sha256=decision_sha,
        root=ROOT,
        policy_digest=digest,
        url=specification["url"],
    )
    _verify_spent_stage_s()
    HEADER_ADMISSION.mkdir(mode=0o700, exist_ok=True)
    _private_header_admission()
    state = _State()
    lease = AdmissionController(HEADER_ADMISSION).acquire(digest)
    ledger = None
    cases = None
    try:
        ledger = evidence.StageSLedger(_helper(state, evidence.allocate_attempt, HEADER_PROOF_BASE))
        _journal(state, ledger, "policy.json", specification)
        _helper(state, ledger.save_journal, "decision.json", decision_raw)
        package_operator._baseline(state, deadline)
        ca = package_operator._root_bytes(state, transport.CA_PATH, transport.CA_BYTES)
        context = _helper(state, transport.tls_context, ca)
        observed = _HeaderInput(state, ledger, context, deadline).observe()
        cases = _journal(
            state,
            ledger,
            "cases.json",
            {
                "status": "header-observed-pending-support-release",
                "policy_digest": digest,
                "decision_sha256": hashlib.sha256(decision_raw).hexdigest(),
                "response": observed,
                "body_bytes": 0,
                "admission_release_confirmed": False,
                "source_provenance_verified": False,
                "runtime_authority": False,
            },
        )
        transport.check_deadline(deadline)
    except BaseException as exc:
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
        if isinstance(exc, Exception):
            raise SnapshotRefused("media_header_diagnostic_refused") from exc
        raise
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
    if released is not True or cases is None:
        raise SnapshotRefused("media_header_support_release")
    return {
        "status": "header-diagnostic-observed",
        "proof": str(ledger.path / "cases.json"),
        "proof_sha256": hashlib.sha256(cases).hexdigest(),
        "policy_digest": digest,
        "admission_release_confirmed": True,
        "source_provenance_verified": False,
        "runtime_authority": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--decision-sha256")
    parser.add_argument("--headers-diagnostic", action="store_true")
    parser.add_argument("--followup-source", action="store_true")
    args = parser.parse_args()
    if args.headers_diagnostic and args.followup_source:
        parser.error("choose one input mode")
    try:
        if args.headers_diagnostic:
            result = _qualify_header_diagnostic(args.decision_sha256)
        else:
            result = _qualify(args.decision_sha256, followup=args.followup_source)
    except Exception:
        print(
            "media_header_diagnostic_refused"
            if args.headers_diagnostic
            else "media_snapshot_stage_s_refused",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
