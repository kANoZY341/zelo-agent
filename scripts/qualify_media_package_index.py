#!/usr/bin/env python3
"""Fixed historical package-index qualification; explicit operator entry point only."""

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
        (resource.RLIMIT_FSIZE, 8388608),
        (resource.RLIMIT_CORE, 0),
    ):
        soft, hard = resource.getrlimit(kind)
        if any(value != resource.RLIM_INFINITY and value < bound for value in (soft, hard)):
            raise ValueError("media_index_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("media_index_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qualification import media_package_index as selector  # noqa: E402
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

INPUTS = (
    (
        "noble",
        "/var/lib/apt/lists/archive.ubuntu.com_ubuntu_dists_noble_main_binary-amd64_Packages",
        7165069,
        "8f6f71ae839c8cba390a7643fcbbdacddb0bc7d12c1583a2dd80a1f8443a30e5",
    ),
    (
        "noble-updates",
        "/var/lib/apt/lists/archive.ubuntu.com_ubuntu_dists_noble-updates_main_binary-amd64_Packages",
        7375495,
        "1a82e1177b4a1eefab5f4a985c79911fac514e7ad6c5c31ebf780c1983663185",
    ),
    (
        "noble-security",
        "/var/lib/apt/lists/security.ubuntu.com_ubuntu_dists_noble-security_main_binary-amd64_Packages",
        5812460,
        "96ebaa6dcfaa00ad04c5a56f26f7bdc104a6da15d0fdd22ae29c91c299c3f276",
    ),
)
ORIGIN_PROOF_SHA = "faab51dce2a95073a251049a559ceddb027ff01a36f3219e5236ca0c1bd6cd52"
ORIGIN_HEAD = "1d533c35d38d6ce7d155b2de54275573e85061a9"
ADMISSION = "runtime/media-elf-metadata-admission"
PROOF_BASE = "runtime/media-package-index-proofs"
PROOF_PREFIX = "index-proof-"
INPUT_LIMIT = 8388608
AGGREGATE_LIMIT = 33554432
WHOLE_SECONDS = 120
_QUARANTINED = []  # Never retry descriptor numbers after an interrupted cleanup.


class QualificationRefused(ValueError):
    pass


def policy():
    return {
        "schema": "zelo-media-local-package-index-policy-v1",
        "inputs": [
            dict(zip(("suite", "source", "bytes", "sha256"), pin, strict=True)) for pin in INPUTS
        ],
        "origin_proof_sha256": ORIGIN_PROOF_SHA,
        "origin_source_head": ORIGIN_HEAD,
        "source_metadata_preflight": "before-admission-and-proof-allocation",
        "source_uid": 0,
        "source_gid": 0,
        "source_mode": 0o644,
        "source_nlink": 1,
        "input_bytes": INPUT_LIMIT,
        "input_aggregate_bytes": AGGREGATE_LIMIT,
        "whole_seconds": WHOLE_SECONDS,
        "filesystem_deadline": "not-hard",
        "support_reservation_bytes": 67108864,
        "reservation_excludes": "interpreter-baseline",
        "aggregate_cgroup_verified": False,
        "processes_created": 0,
        "attempts": 4,
        "proof_scope": PROOF_BASE + "/" + PROOF_PREFIX + "*",
        "retained_entries_per_attempt": 96,
        "retained_bytes_per_attempt": 8388608,
        "ledger_entries": 3,
        "ledger_bytes": 6291456,
        "host_source_assumption": "trusted-ancestors-and-no-concurrent-mutator",
        "admission_release_error": "receipt-refused-marker-may-already-be-empty",
        "metadata_only": True,
        "package_member_origin_verified": False,
        "platform_complete": False,
        "abi_qualified": False,
        "freshness_verified": False,
        "current_key_validity_verified": False,
        "runtime_authority": False,
    }


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise QualificationRefused("media_index_deadline")


def _proof_directory(base):
    base.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(base, directory=True)
    with os.scandir(base) as entries:
        for count, entry in enumerate(entries, 1):
            if count >= 4:
                raise QualificationRefused("media_index_attempt_limit")
            path = base / entry.name
            shared._private_path(path, directory=True)
            if (
                not entry.name.startswith(PROOF_PREFIX)
                or stat.S_IMODE(path.lstat().st_mode) != 0o700
            ):
                raise QualificationRefused("media_index_attempt_identity")
    return Path(tempfile.mkdtemp(prefix=PROOF_PREFIX, dir=base))


def _source_info(info, size):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_nlink != 1
        or info.st_size != size
        or not 0 < size <= INPUT_LIMIT
    ):
        raise QualificationRefused("media_index_source_metadata")
    return info


def _source_metadata(fd, size):
    return _source_info(os.fstat(fd), size)


def _preflight(deadline):
    # Metadata-only early refusal. This neither authenticates bytes nor replaces
    # held-descriptor checks after admission; the host can change between stages.
    if sum(pin[2] for pin in INPUTS) > AGGREGATE_LIMIT:
        raise QualificationRefused("media_index_aggregate")
    for _suite, source, size, _digest in INPUTS:
        _check_deadline(deadline)
        path = Path(source)
        if path.resolve(strict=True) != path:
            raise QualificationRefused("media_index_source_path")
        _source_info(path.lstat(), size)
        _check_deadline(deadline)


def _authenticate(state, deadline):
    if sum(pin[2] for pin in INPUTS) > AGGREGATE_LIMIT:
        raise QualificationRefused("media_index_aggregate")
    authenticated = []
    for suite, source, size, digest in INPUTS:
        _check_deadline(deadline)
        path = Path(source)
        if path.resolve(strict=True) != path:
            raise QualificationRefused("media_index_source_path")
        _check_deadline(deadline)
        fd = _helper(state, _open, state, path)
        _check_deadline(deadline)
        _source_metadata(fd, size)
        raw = _read_fd(fd, INPUT_LIMIT, digest, size)
        _source_metadata(fd, size)
        _check_deadline(deadline)
        authenticated.append((suite, raw, digest))
    return authenticated


def _chunks(raw, deadline):
    for offset in range(0, len(raw), 65536):
        _check_deadline(deadline)
        yield raw[offset : offset + 65536]
    _check_deadline(deadline)


def _select(authenticated, deadline):
    results = []
    for suite, raw, digest in authenticated:
        result = selector.parse_index(_chunks(raw, deadline), suite)
        _check_deadline(deadline)
        if (result["suite"], result["bytes"], result["sha256"]) != (suite, len(raw), digest):
            raise QualificationRefused("media_index_parser_identity")
        results.append(result)
    selected = selector.merge_fixed_indexes(results)
    _check_deadline(deadline)
    return selected


def _qualify():
    if os.getresuid() != (1000, 1000, 1000) or os.getresgid() != (1000, 1000, 1000):
        raise QualificationRefused("media_index_operator_ids")
    deadline = time.monotonic() + WHOLE_SECONDS
    _preflight(deadline)
    admission_path = ROOT / ADMISSION
    admission_path.mkdir(mode=0o700, exist_ok=True)
    shared._private_path(admission_path, directory=True)
    specification = policy()
    digest = shared._digest(specification)
    state = _State()
    lease = AdmissionController(admission_path).acquire(digest)
    store = None
    report_attempted = False
    interrupted = False
    try:
        _check_deadline(deadline)
        store = _Store(_helper(state, _proof_directory, ROOT / PROOF_BASE), state)
        store.json("policy.json", specification)
        authenticated = _authenticate(state, deadline)
        selected = _select(authenticated, deadline)
        store.json(
            "authenticated-sources.json",
            {"origin_proof_sha256": ORIGIN_PROOF_SHA, "indexes": selected["indexes"]},
        )
        report = {
            "schema": "zelo-media-local-package-index-proof-v1",
            "policy_digest": digest,
            "selection": selected,
            "index_hash_verified": True,
            "processes_created": 0,
            "support_release_confirmed": False,
            **{
                key: specification[key]
                for key in (
                    "metadata_only",
                    "package_member_origin_verified",
                    "platform_complete",
                    "abi_qualified",
                    "freshness_verified",
                    "current_key_validity_verified",
                    "runtime_authority",
                )
            },
        }
        _check_deadline(deadline)
        report_attempted = True
        raw = store.json("cases.json", report)
        proof_sha = hashlib.sha256(raw).hexdigest()
        proof_path = str(store.path / "cases.json")
        _check_deadline(deadline)
    except Exception as exc:
        if store is not None and not report_attempted:
            report_attempted = True
            # No exception text: external parser/OS diagnostics need not be bounded.
            store.json("cases.json", {"status": "refused", "support_release_confirmed": False})
        raise QualificationRefused("media_index_qualification_refused") from exc
    except BaseException:
        interrupted = True
        raise
    finally:
        if interrupted or (
            sys.exc_info()[0] is not None and not issubclass(sys.exc_info()[0], Exception)
        ):
            # An interrupt can occur even inside failure evidence writing. Retain
            # ownership until operator exit; never infer that hidden closes settled.
            _QUARANTINED.append((state, lease))
        else:
            try:
                released = _release(state, lease)  # The only cleanup invocation.
            except BaseException:
                _QUARANTINED.append((state, lease))
                raise
    if released is not True:
        raise QualificationRefused("media_index_support_cleanup")
    _check_deadline(deadline)
    receipt = {
        "status": "qualified-historical-index-metadata",
        "proof": proof_path,
        "proof_sha256": proof_sha,
        "policy_digest": digest,
        "support_release_confirmed": True,
        "runtime_authority": False,
    }
    return receipt, deadline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    receipt, deadline = _qualify()
    rendered = json.dumps(receipt, sort_keys=True)
    _check_deadline(deadline)
    print(rendered)


if __name__ == "__main__":
    main()
