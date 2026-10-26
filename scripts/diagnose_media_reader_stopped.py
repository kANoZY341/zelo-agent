#!/usr/bin/env python3
"""Build and verify one stopped Reader container without allocating a proof attempt."""

import argparse
import asyncio
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qualification import media_reader_fixture as fixture  # noqa: E402
from qualification import media_reader_seccomp as seccomp  # noqa: E402
from qualification.admission import AdmissionController  # noqa: E402
from qualification.media_reader_process import DockerReaderHandle  # noqa: E402
from scripts import diagnose_media_native_reader_build as build_diagnostic  # noqa: E402
from scripts import qualify_media_native_reader as reader  # noqa: E402
from scripts import qualify_media_publisher as shared  # noqa: E402

SCHEMA = "zelo-native-reader-stopped-diagnostic-v1"
RECEIPT_DIRECTORY = "media-native-reader-stopped-diagnostics"
CASE_SECONDS = 20


async def _stop_contained(handle: DockerReaderHandle) -> tuple[bool, bool]:
    """Finish owned cleanup even if the operator cancels again while it is awaited."""
    task = asyncio.create_task(handle.stop_and_confirm_cleanup())
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:
            break
    try:
        return task.result() is True, interrupted
    except BaseException:
        return False, interrupted


async def run_stopped_diagnostic(runtime_root: Path) -> tuple[Path, dict]:
    """Share qualification admission, attest an image, and inspect one stopped container."""
    runtime = build_diagnostic._directory(Path(runtime_root), create=False)
    if not runtime.is_absolute():
        raise ValueError("diagnostic_runtime_path")
    admission = reader._private_runtime_path(runtime / reader.RUNTIME_ADMISSION, runtime)
    receipt_dir = build_diagnostic._directory(
        runtime / RECEIPT_DIRECTORY, create=True, private=True
    )
    reader._docker_readiness(runtime)
    lease = AdmissionController(admission).acquire(
        shared._digest({"schema": SCHEMA, "case": "positive", "reader_started": False})
    )
    diagnostic_id = secrets.token_hex(32)
    stage = "setup"
    failure = None
    image_identity = None
    bundle_sha256 = None
    payload_sha256 = None
    stopped_verified = False
    cleanup_confirmed = False
    context = None
    environment = None
    handle = None
    started = time.monotonic()
    try:
        context = Path(tempfile.mkdtemp(prefix="native-reader-stopped-", dir=runtime))
        os.chmod(context, 0o700)
        config = context / "docker-config"
        config.mkdir(mode=0o700)
        environment = reader._docker_environment(config)
        reader._require_empty_lease("container", lease.identity, environment)
        reader._require_empty_lease("image", lease.identity, environment)
        stage = "build_attestation"
        _, manifest, contract, payload_sha256 = reader._build_and_attest(
            context, lease.identity, environment, runtime
        )
        image_identity = contract.image_identity
        bundle_sha256 = manifest.digest
        stage = "stopped_create"
        handle = DockerReaderHandle(lease, contract, context, seccomp.profile_bytes())
        case = await handle.prepare_stopped(
            fixture.fixture("positive"), asyncio.get_running_loop().time() + CASE_SECONDS
        )
        if case != "positive":
            raise ValueError("reader_native_diagnostic_case")
        stopped_verified = True
    except Exception as error:
        evidence = (
            reader._build_failure_evidence(error)
            if stage == "build_attestation"
            else reader._matrix_failure_evidence(error)
        )
        failure = {"stage": stage, **evidence}
    except asyncio.CancelledError:
        failure = {"stage": stage, "kind": "cancelled"}
    finally:
        container_clean = True
        if handle is not None:
            container_clean, interrupted = await _stop_contained(handle)
            if interrupted and failure is None:
                failure = {"stage": "cleanup", "kind": "cancelled"}
        image_clean = environment is None
        if environment is not None:
            try:
                image_clean = reader._cleanup_owned(lease.identity, environment) is True
            except Exception:
                image_clean = False
        context_clean = context is None
        if context is not None:
            try:
                shutil.rmtree(context)
                context_clean = True
            except OSError:
                context_clean = False
        cleanup_confirmed = container_clean and image_clean and context_clean

    receipt = {
        "schema": SCHEMA,
        "diagnostic_id": diagnostic_id,
        "lease_identity": lease.identity,
        "status": (
            "verified_stopped"
            if stopped_verified and cleanup_confirmed and failure is None
            else "refused"
        ),
        "failure": failure,
        "image_identity": image_identity,
        "bundle_sha256": bundle_sha256,
        "image_payload_sha256": payload_sha256,
        "reader_started": False,
        "proof_ledger_touched": False,
        "cleanup_confirmed": cleanup_confirmed,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    try:
        return build_diagnostic._write_receipt(receipt_dir, receipt)
    finally:
        lease.finish(cleanup_confirmed=cleanup_confirmed and sys.exc_info()[0] is None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        reader._operator_limits()
        path, receipt = asyncio.run(run_stopped_diagnostic(args.runtime_root))
    except Exception:
        print("reader_stopped_diagnostic_refused", file=sys.stderr)
        return 1
    print(json.dumps({"receipt": str(path), "status": receipt["status"]}, sort_keys=True))
    return 0 if receipt["status"] == "verified_stopped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
