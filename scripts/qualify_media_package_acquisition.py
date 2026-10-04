#!/usr/bin/env python3
"""Acquire eight fixed archives as inert bytes; no extraction, installation or Core route."""

import argparse
import hashlib
import json
import os
import resource
import stat
import sys
import tempfile
import time
from pathlib import Path


def _operator_limits():
    for kind, bound in (
        (resource.RLIMIT_AS, 536870912),
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_FSIZE, 4194304),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if any(value != resource.RLIM_INFINITY and value < bound for value in (soft, hard)):
            raise ValueError("acquisition_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("acquisition_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qualification import media_package_acquisition as m  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402
from scripts.qualify_media_elf_version_metadata import (  # noqa: E402
    _helper,
    _open,
    _read_fd,
    _release,
    _State,
    _Store,
)

ADMISSION = "runtime/media-elf-metadata-admission"
PROOF_BASE = "runtime/media-package-acquisition"
PREFIX = "acquisition-proof-"
LOCK_PATH = "qualification/fixtures/media-package-acquisition-lock.json"
WHOLE_SECONDS = 300
ATTEMPT_BYTES = 16777216
ATTEMPT_ENTRIES = 16
JSON_BYTES = 2097152
_QUARANTINED = []


def policy():
    return {
        "schema": "zelo-media-fixed-package-acquisition-policy-v1",
        "lock_sha256": m.LOCK_SHA,
        "index_proof_sha256": m.INDEX_PROOF_SHA,
        "baseline_sources": m.BASELINE,
        "ca_path": m.CA_PATH,
        "ca_bytes": m.CA_BYTES,
        "host": m.HOST,
        "port": m.PORT,
        "request_headers": m.HEADERS,
        "method": "GET",
        "tls": "explicit-verified-client-context;immutable-cadata;no-keylog",
        "address_policy": "one-first-IPv4-result;at-most-16-resolver-records",
        "header_bytes": m.HEADER_BYTES,
        "header_line_bytes": m.HEADER_LINE_BYTES,
        "header_lines": m.HEADER_LINES,
        "read_bytes": m.CHUNK_BYTES,
        "framing": "exact-Content-Length-body;unused-wire-trailing-bytes-not-certified",
        "whole_seconds": WHOLE_SECONDS,
        "socket_seconds": 10,
        "deadline": "cooperative;DNS-TLS-buffered-headers-filesystem-cleanup-not-hard",
        "archive_bytes": m.ARCHIVE_BYTES,
        "archive_aggregate_bytes": m.TOTAL_ARCHIVE_BYTES,
        "retained_bytes_per_attempt": ATTEMPT_BYTES,
        "retained_entries_per_attempt": ATTEMPT_ENTRIES,
        "ledger_files": 11,
        "ledger_bytes": m.TOTAL_ARCHIVE_BYTES + 3 * JSON_BYTES,
        "attempts": 4,
        "proof_scope": PROOF_BASE + "/" + PREFIX + "*",
        "support_reservation_bytes": 67108864,
        "readback_copy_reservation_bytes": 2 * m.ARCHIVE_BYTES,
        "reservation_excludes": "interpreter-baseline",
        "host_assumption": "trusted-host-baseline-and-ancestors;no-concurrent-mutator",
        "processes_created": 0,
        "aggregate_cgroup_verified": False,
        "package_member_origin_verified": False,
        "platform_complete": False,
        "abi_qualified": False,
        "freshness_verified": False,
        "current_key_validity_verified": False,
        "runtime_authority": False,
        "admission_release_error": "receipt-refused-marker-may-already-be-empty",
    }


def _proof_directory(base):
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    with os.scandir(base) as attempts:
        for count, entry in enumerate(attempts, 1):
            if count >= 4:
                raise m.Refused("acquisition_attempt_limit")
            path = base / entry.name
            shared._private_path(path, directory=True)
            if not entry.name.startswith(PREFIX) or stat.S_IMODE(path.lstat().st_mode) != 0o700:
                raise m.Refused("acquisition_attempt_identity")
            total = 0
            with os.scandir(path) as files:
                for number, file in enumerate(files, 1):
                    if number > ATTEMPT_ENTRIES:
                        raise m.Refused("acquisition_old_entries")
                    info = file.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_nlink != 1
                        or not 0 <= info.st_size <= m.ARCHIVE_BYTES
                    ):
                        raise m.Refused("acquisition_old_file")
                    total += info.st_size
                    if total > ATTEMPT_BYTES:
                        raise m.Refused("acquisition_old_bytes")
    return Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))


class Ledger:
    def __init__(self, path, state):
        self.path = path
        self.state = state
        self.json_store = _Store(path, state)  # JSON-only subledger, not archive accounting.
        self.names = set()
        self.reserved = 0
        self.archive_reserved = 0
        self.written = {}

    def reserve(self, name, size):
        if (
            name in self.names
            or len(self.names) >= ATTEMPT_ENTRIES
            or self.reserved + size > ATTEMPT_BYTES
        ):
            raise m.Refused("acquisition_retained_budget")
        self.names.add(name)
        self.reserved += size

    def archive(self, row):
        size, name = row["bytes"], row["output_name"]
        if not 0 < size <= m.ARCHIVE_BYTES or self.archive_reserved + size > m.TOTAL_ARCHIVE_BYTES:
            raise m.Refused("acquisition_archive_budget")
        self.reserve(name, size)  # Charge BEFORE creation, including partial failure.
        self.archive_reserved += size
        self.written[name] = 0
        fd = self.state.hold(
            os.open(
                self.path / name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                0o600,
            )
        )
        _output_info(fd, 0)
        return fd

    def write(self, row, fd, raw, deadline):
        if type(raw) is not bytes or len(raw) > m.CHUNK_BYTES:
            raise m.Refused("acquisition_write_chunk")
        name = row["output_name"]
        if self.written[name] + len(raw) > row["bytes"]:
            raise m.Refused("acquisition_write_budget")
        self.written[name] += len(raw)  # Charge BEFORE writes, even when only partly written.
        view = memoryview(raw)
        while view:
            m.check_deadline(deadline)
            count = os.write(fd, view)
            if count <= 0 or count > len(view):
                raise m.Refused("acquisition_short_write")
            view = view[count:]
            m.check_deadline(deadline)

    def json(self, name, value):
        if name not in ("policy.json", "manifest.json", "cases.json"):
            raise m.Refused("acquisition_json_name")
        self.reserve(name, JSON_BYTES)
        return self.json_store.json(name, value)


def _root_info(fd, bound, size=None):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_nlink != 1
        or not 0 < info.st_size <= bound
        or (size is not None and info.st_size != size)
    ):
        raise m.Refused("acquisition_root_source")
    return info


def _root_bytes(state, source, bound, digest=None, size=None):
    path = Path(source)
    if path.resolve(strict=True) != path:
        raise m.Refused("acquisition_root_path")
    fd = _helper(state, _open, state, path)
    before = _root_info(fd, bound, size)
    raw = _read_fd(fd, bound, digest, before.st_size)
    _root_info(fd, bound, before.st_size)
    return raw


def _baseline(state, deadline):
    modules = (m.http.client, m.ssl, m.socket)
    for module, (source, size, digest) in zip(modules, m.BASELINE, strict=True):
        if Path(module.__file__).resolve(strict=True) != Path(source):
            raise m.Refused("acquisition_import_baseline")
        _root_bytes(state, source, 65536, digest, size)
        m.check_deadline(deadline)


def _output_info(fd, size):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size != size
    ):
        raise m.Refused("acquisition_output_identity")


def _fetch(state, ledger, row, context, deadline):
    fd = _helper(state, ledger.archive, row)
    owner = m.NetworkOwner(state, context, deadline)
    try:
        response = owner.request(row["request_path"])
        m.validate_headers(response, row["bytes"])
        size = 0
        digest = hashlib.sha256()
        while size < row["bytes"]:
            m.check_deadline(deadline)
            chunk = owner.read1(min(m.CHUNK_BYTES, row["bytes"] - size))
            if type(chunk) is not bytes or not chunk or len(chunk) > row["bytes"] - size:
                raise m.Refused("acquisition_body_size")
            ledger.write(row, fd, chunk, deadline)
            digest.update(chunk)
            size += len(chunk)
        if response.length != 0 or digest.hexdigest() != row["sha256"]:
            raise m.Refused("acquisition_archive_hash")
        os.fsync(fd)
        m.check_deadline(deadline)
    finally:
        error_type = sys.exc_info()[0]
        if error_type is not None and not issubclass(error_type, Exception):
            owner.unknown()
        elif not owner.cleanup():
            raise m.Refused("acquisition_network_cleanup")
    return fd


def _qualify():
    if os.getresuid() != (1000,) * 3 or os.getresgid() != (1000,) * 3:
        raise m.Refused("acquisition_operator_ids")
    deadline = time.monotonic() + WHOLE_SECONDS
    admission_path = ROOT / ADMISSION
    admission_path.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission_path, directory=True)
    specification = policy()
    digest = shared._digest(specification)
    state = _State()
    lease = AdmissionController(admission_path).acquire(digest)
    ledger = None
    report_attempted = False
    try:
        ledger = Ledger(_helper(state, _proof_directory, ROOT / PROOF_BASE), state)
        ledger.json("policy.json", specification)
        lock_fd = _helper(state, _open, state, ROOT / LOCK_PATH)
        lock = m.load_lock(_read_fd(lock_fd, 8192, m.LOCK_SHA))
        _baseline(state, deadline)
        ca = _root_bytes(state, m.CA_PATH, m.CA_BYTES)
        m.check_deadline(deadline)
        context = _helper(state, m.tls_context, ca)
        m.check_deadline(deadline)
        archives = []
        for row in lock["packages"]:
            archives.append((_fetch(state, ledger, row, context, deadline), row))
        for fd, row in archives:
            _output_info(fd, row["bytes"])
            _read_fd(fd, m.ARCHIVE_BYTES, row["sha256"], row["bytes"])
            _output_info(fd, row["bytes"])
            m.check_deadline(deadline)
        manifest = {
            "schema": "zelo-media-fixed-package-acquisition-manifest-v1",
            "policy_digest": digest,
            "lock_sha256": m.LOCK_SHA,
            "index_proof_sha256": m.INDEX_PROOF_SHA,
            "ca_baseline": {
                "source": m.CA_PATH,
                "bytes": len(ca),
                "sha256": hashlib.sha256(ca).hexdigest(),
            },
            "packages": lock["packages"],
            "archive_hash_verified": True,
            "package_member_origin_verified": False,
            "runtime_authority": False,
        }
        manifest_raw = ledger.json("manifest.json", manifest)
        report = {
            "schema": "zelo-media-fixed-package-acquisition-proof-v1",
            "policy_digest": digest,
            "lock_sha256": m.LOCK_SHA,
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "packages": 8,
            "archive_bytes": m.TOTAL_ARCHIVE_BYTES,
            "archive_hash_verified": True,
            "support_release_confirmed": False,
            **{
                key: specification[key]
                for key in (
                    "processes_created",
                    "aggregate_cgroup_verified",
                    "package_member_origin_verified",
                    "platform_complete",
                    "abi_qualified",
                    "freshness_verified",
                    "current_key_validity_verified",
                    "runtime_authority",
                )
            },
        }
        m.check_deadline(deadline)
        report_attempted = True
        proof_raw = ledger.json("cases.json", report)
        proof_sha = hashlib.sha256(proof_raw).hexdigest()
        proof_path = str(ledger.path / "cases.json")
        m.check_deadline(deadline)
    except Exception as exc:
        if ledger is not None and not report_attempted:
            report_attempted = True
            ledger.json("cases.json", {"status": "refused", "support_release_confirmed": False})
        raise m.Refused("acquisition_refused") from exc
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
    if released is not True:
        raise m.Refused("acquisition_support_cleanup")
    m.check_deadline(deadline)
    return {
        "status": "qualified-fixed-historical-archive-bytes",
        "proof": proof_path,
        "proof_sha256": proof_sha,
        "policy_digest": digest,
        "support_release_confirmed": True,
        "runtime_authority": False,
    }, deadline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        receipt, deadline = _qualify()
        rendered = json.dumps(receipt, sort_keys=True)
        m.check_deadline(deadline)
    except Exception:
        print("acquisition_refused", file=sys.stderr)
        return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
