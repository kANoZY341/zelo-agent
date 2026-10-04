#!/usr/bin/env python3
"""Fixed reader-bootstrap ELF declaration profile; reuses existing native reader."""

import argparse
import asyncio
import hashlib
import json
import os
import resource
import stat
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
            raise ValueError("reader_elf_operator_limits")
        resource.setrlimit(kind, (bound, bound))


if __name__ == "__main__":
    _operator_limits()
if not __debug__:
    raise SystemExit("reader_elf_optimized_python_refused")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qualification import media_reader_bootstrap as reader  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from scripts import qualify_media_elf_version_metadata as old  # noqa: E402

LOCK_PATH = "qualification/fixtures/media-reader-elf-target-lock.json"
PROOF_BASE = "runtime/media-elf-version-proofs"
ADMISSION = "runtime/media-elf-metadata-admission"
CASE_COUNT = 40
_RETAINED = []


def policy():
    original = old.policy()
    return {
        **original,
        "schema": "zelo-reader-bootstrap-elf-policy-v1",
        "purpose": "reader-bootstrap-declarations-only",
        "processes": CASE_COUNT,
        "input_lock_sha256": reader.LOCK_SHA,
        "input_bytes_total": reader.TOTAL_BYTES,
        "bootstrap_manifest_sha256": reader.MANIFEST_SHA,
        "inherited_proof_quota": "same-version-proof-prefix-four-total-attempts",
        "independent_binary_provenance": False,
        "actual_dynamic_load_verified": False,
    }


def _time(deadline):
    if time.monotonic() >= deadline:
        raise ValueError("reader_elf_deadline")


def _open_input(state, row):
    path = ROOT / reader.ROOT / row["relative"]
    if path.resolve(strict=True) != path:
        raise ValueError("reader_elf_copy_path")
    fd = old._helper(state, old._open, state, path)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("reader_elf_copy_owner")
    raw = old._helper(state, old._read_fd, fd, 4194304, row["sha256"], row["bytes"])
    return fd, raw


def _setup(state, store):
    """Bind the same prepared optional tool and five unchanged guest sources."""
    store.save("host-only-sentinel", b"fixed-private-metadata-sentinel")
    store.save("bad-source.py", b"raise RuntimeError('UNVERIFIED_INITIALIZER_EXECUTED')\n")
    bad_source = old._open(state, store.path / "bad-source.py")
    store.directory("bad-tool")
    store.save("bad-tool/inventory-anchor", b"# invalid tool tree\n")
    bad_tool_path = store.path / "bad-tool"
    (bad_tool_path / "inventory-anchor").chmod(0o500)
    bad_tool_path.chmod(0o500)
    bad_tool = old._open(state, bad_tool_path, directory=True)
    receipt = json.loads(
        old._helper(
            state,
            old.shared._read,
            ROOT / "runtime/media-elf-tool-real-source-copy.json",
            65536,
            old.TOOL_RECEIPT_SHA,
        )
    )
    tool_path = ROOT / "runtime/media-elf-tool-preparation" / receipt["receipt"]["relative_payload"]
    old.shared._private_path(tool_path, directory=True)
    tool_fd = old._open(state, tool_path, directory=True)
    info = os.fstat(tool_fd)
    if (
        tuple(receipt["receipt"]["root_identity"]) != (info.st_dev, info.st_ino)
        or stat.S_IMODE(info.st_mode) != 0o500
    ):
        raise ValueError("reader_elf_tool_identity")
    if (
        old._helper(
            state, old.m.capture_manifest, tool_path, old.m.TOOL_ROLES, old.m.TOOL_POLICY
        ).digest
        != old.m.TOOL_MANIFEST_DIGEST
    ):
        raise ValueError("reader_elf_tool_pin")
    sources = []
    for name, size, digest in old.SOURCE_PINS:
        fd = old._open(state, ROOT / name)
        if os.fstat(fd).st_uid != os.geteuid():
            raise ValueError("reader_elf_source_owner")
        old._read_fd(fd, 32768, digest, size)
        sources.append(fd)

    def source(relative, size, digest):
        fd = old._open(state, ROOT / relative)
        return old._read_fd(fd, 65536, digest, size)

    positive = source(
        "qualification/fixtures/media-elf-version-positive.bin", 1456, old.FIXTURE_SHA
    )
    unversioned = source(
        "qualification/fixtures/media-elf-version-unversioned.bin", 984, old.UNVERSIONED_SHA
    )
    layout_fd = old._open(state, ROOT / "qualification/fixtures/media-elf-version-positive.json")
    layout = json.loads(old._read_fd(layout_fd, 16384, old.FIXTURE_LAYOUT_SHA))["layout"]
    return tool_fd, tool_path, sources, bad_source, bad_tool, positive, unversioned, layout


async def _qualify():
    started = time.monotonic()
    deadline = started + 420
    admission = ROOT / ADMISSION
    admission.mkdir(mode=0o700, exist_ok=True)
    old.shared._private_path(admission, directory=True)
    spec = policy()
    digest = old.shared._digest(spec)
    state = old._State()
    lease = AdmissionController(admission).acquire(digest)
    store = None
    records = []
    stage = "setup"
    try:
        # The same helper/prefix sees both prior attempts. No extra proof scope.
        store = old._Store(old._helper(state, old._proof_directory, ROOT / PROOF_BASE), state)
        lock_fd = old._open(state, ROOT / LOCK_PATH)
        lock = reader.load_lock(old._read_fd(lock_fd, 16384, reader.LOCK_SHA))
        manifest_fd = old._open(state, ROOT / reader.ROOT / "manifest.json")
        old._read_fd(manifest_fd, 65536, reader.MANIFEST_SHA)
        tool_fd, tool_path, sources, bad_source, bad_tool, positive, unversioned, layout = _setup(
            state, store
        )
        _time(started + 30)
        matrix_deadline = min(deadline, time.monotonic() + 180)
        for case in (*old.MATRIX, "unversioned_fixture"):
            stage = case
            raw = unversioned if case == "unversioned_fixture" else positive
            if case == "malformed":
                changed = bytearray(raw)
                changed[4] = 1
                raw = bytes(changed)
            records.append(
                await old._native(
                    state,
                    store,
                    tool_fd,
                    sources,
                    bad_source,
                    bad_tool,
                    case,
                    raw,
                    case,
                    matrix_deadline,
                )
            )
        for name in old.MALFORMED:
            stage = "malformed-" + name
            raw = old._malformed(positive, layout, name)
            records.append(
                await old._native(
                    state,
                    store,
                    tool_fd,
                    sources,
                    bad_source,
                    bad_tool,
                    "malformed",
                    raw,
                    stage,
                    matrix_deadline,
                )
            )
        metadata_deadline = min(deadline, time.monotonic() + 210)
        inputs = []
        reports = []
        total = 0
        for index, row in enumerate(lock["files"]):
            stage = f"reader-input-{index:02d}"
            fd, raw = _open_input(state, row)
            total += len(raw)
            if total > reader.TOTAL_BYTES:
                raise ValueError("reader_elf_input_budget")
            inputs.append((fd, row))
            record = await old._native(
                state,
                store,
                tool_fd,
                sources,
                bad_source,
                bad_tool,
                "input",
                raw,
                stage,
                metadata_deadline,
            )
            # _native applies unchanged version and loader acceptance to raw bytes.
            reports.append(record["metadata"])
            records.append(record)
        if len(records) != CASE_COUNT or total != reader.TOTAL_BYTES:
            raise ValueError("reader_elf_count")
        stage = "declaration_graph"
        graph = reader.declarations(lock, reports)
        stage = "readback"
        for fd, row in inputs:
            old._helper(state, old._read_fd, fd, 4194304, row["sha256"], row["bytes"])
        for (_, size, sha), fd in zip(old.SOURCE_PINS, sources, strict=True):
            old._read_fd(fd, 32768, sha, size)
        if (
            old._helper(
                state, old.m.capture_manifest, tool_path, old.m.TOOL_ROLES, old.m.TOOL_POLICY
            ).digest
            != old.m.TOOL_MANIFEST_DIGEST
        ):
            raise ValueError("reader_elf_tool_drift")
        _time(deadline)
        report = {
            "schema": "zelo-reader-bootstrap-elf-proof-v1",
            "status": "cases-passed",
            "policy_digest": digest,
            "policy": spec,
            "input_lock_sha256": reader.LOCK_SHA,
            "bootstrap_manifest_sha256": reader.MANIFEST_SHA,
            "input_bytes": total,
            "records": records,
            "declarations": graph,
            "support_release_confirmed": False,
            "runtime_authority": False,
            "abi_qualified": False,
            "platform_complete": False,
            "independent_binary_provenance": False,
            "actual_dynamic_load_verified": False,
        }
        proof_raw = store.json("cases.json", report)
        proof_sha = hashlib.sha256(proof_raw).hexdigest()
        proof_path = str(store.path / "cases.json")
    except Exception:
        # The unchanged native helper may already have persisted its fixed refusal.
        if store is not None and not (store.path / "cases.json").exists():
            store.json(
                "cases.json",
                {
                    "status": "refused",
                    "stage": stage,
                    "records": records,
                    "support_release_confirmed": False,
                    "runtime_authority": False,
                },
            )
        raise
    finally:
        error_type = sys.exc_info()[0]
        if error_type is not None and not issubclass(error_type, Exception):
            _RETAINED.append((state, lease))
        else:
            try:
                released = old._release(state, lease)
            except BaseException:
                _RETAINED.append((state, lease))
                raise
    if released is not True:
        raise ValueError("reader_elf_support_cleanup")
    _time(deadline)
    return {
        "status": "qualified-reader-bootstrap-declarations",
        "proof": proof_path,
        "proof_sha256": proof_sha,
        "policy_digest": digest,
        "cases": CASE_COUNT,
        "metadata_inputs": 8,
        "support_release_confirmed": True,
        "runtime_authority": False,
    }, deadline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    try:
        receipt, deadline = asyncio.run(_qualify())
        rendered = json.dumps(receipt, sort_keys=True)
        _time(deadline)
    except Exception:
        print("reader_elf_refused", file=sys.stderr)
        return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
