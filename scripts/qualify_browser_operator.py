"""Opt-in public DNS/TLS qualification in a private, disposable Core instance.

Only the fixed informational Example Domain page is fetched. Production settings,
credentials and databases are never loaded. No policy fallback or fetch retry.
"""

import argparse
import asyncio
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.browser_operator import canonical
from app.browser_worker import configured_browser_runtime
from app.config import ROOT, Settings
from app.database import Database
from app.runner import Runner
from app.schemas import BrowserRunInput, BrowserRunResult
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_browser_worker
from qualification.renderer_process import verify_image
from scripts.prepare_browser_worker import prepare
from scripts.qualify_browser_renderer import (
    LABEL,
    cleanup_owned_setup,
    command,
    expected_image_files,
    export_bounded,
    verify_image_archive,
)

ENDPOINT_ID = "example_document"
ORIGIN = "https://example.com"
DOCUMENT_PATH = "/"
ENDPOINTS = (ENDPOINT_ID + "=" + ORIGIN,)
DOCKER = "/usr/bin/docker"
REPORT_LIMIT = 262144


class QualificationSettings(Settings):
    """Init-only configuration: neither .env nor process environment nor secrets."""

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


def diagnostic_settings(root: Path, receipt: Path) -> Settings:
    prepared = receipt.parent
    return QualificationSettings(
        _env_file=None,
        operator_token="diagnostic-placeholder-not-a-credential",
        runtime_dir=root / "core",
        workspace_root=ROOT / "workspaces" / ("browser-diagnostic-" + root.name),
        scheduler_enabled=False,
        browser_worker_enabled=True,
        browser_worker_endpoints=ENDPOINTS,
        browser_worker_receipt=receipt,
        browser_worker_bundle=prepared / "build" / "bundle",
        browser_worker_runtime=prepared / "runtime",
        browser_worker_admission=prepared / "admission",
    )


def save_report(root: Path, report: dict) -> Path:
    data = canonical(report) + b"\n"
    if len(data) > REPORT_LIMIT:
        raise ValueError("operator_report_limit")
    path = root / "result.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return path


def admission_clear(directory: Path) -> bool:
    marker = directory / "active.marker"
    # The runtime confirms its owned lifecycle separately. Never reset a marker.
    return not marker.exists() or marker.stat().st_size == 0


def validate_result(task, contract) -> dict:
    if task.status != "succeeded" or task.result is None:
        raise RuntimeError("operator_task_failed")
    result = BrowserRunResult.model_validate(task.result)
    if (
        result.endpoint_id != ENDPOINT_ID
        or result.source_origin != ORIGIN
        or result.source_path != DOCUMENT_PATH
        or result.contract_digest != contract.digest
        or result.broker_policy_digest != contract.execution.broker_digest
        or result.instruction_trust != "none"
        or not result.text.strip()
        or result.nodes == 0
    ):
        raise RuntimeError("operator_evidence_mismatch")
    return result.model_dump(mode="json")


async def exercise(settings: Settings, report: dict):
    runtime = configured_browser_runtime(settings)
    if runtime is None:
        raise RuntimeError("operator_runtime_missing")
    database = None
    operation = None
    report["runtime_cleanup_confirmed"] = False
    report["cases"] = []
    try:
        database = Database(settings.database_path)
        database.migrate()
        supervisor = Supervisor(WorkerRegistry(((descriptor_for_browser_worker(), runtime),)))
        service = TaskService(database, settings, browser_supervisor=supervisor)
        runner = Runner(service, browser_supervisor=supervisor)
        report["contract_digest"] = runtime.contract.digest
        report["image_identity"] = runtime.contract.execution.image_identity
        for case in ("public_document", "readmission_after_cleanup"):
            started = datetime.now(UTC).isoformat()
            request = BrowserRunInput(
                kind="browser_run",
                payload={"endpoint_id": ENDPOINT_ID, "path": DOCUMENT_PATH},
                timeout_seconds=30,
            )
            task, created = service.submit(request, None)
            if not created or task.browser_spec.policy_version != runtime.contract.digest:
                raise RuntimeError("operator_submit_identity_mismatch")
            execution = service.claim()
            if execution is None or execution.task_id != task.id:
                raise RuntimeError("operator_claim_identity_mismatch")
            operation = asyncio.create_task(runner._run(execution))
            runner.operations[task.id] = operation
            await asyncio.wait_for(asyncio.shield(operation), 40)
            task = service.get(task.id)
            report.setdefault("attempts", []).append(
                {
                    "task_id": task.id,
                    "status": task.status,
                    "case": case,
                    "error_code": task.error
                    if re.fullmatch(r"browser_[a-z_]{1,64}", task.error or "")
                    else None,
                }
            )
            evidence = validate_result(task, runtime.contract)
            if not admission_clear(runtime.admission_directory):
                raise RuntimeError("operator_admission_not_clear")
            report["cases"].append(
                {
                    "case": case,
                    "task_id": task.id,
                    "retrieval_started_at": started,
                    "persisted_evidence": evidence,
                    "cleanup_gated_success": True,
                }
            )
        return runtime.contract
    finally:
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        try:
            await runtime.aclose()
            if not admission_clear(runtime.admission_directory):
                raise RuntimeError("operator_admission_fenced")
            report["runtime_cleanup_confirmed"] = True
        finally:
            if database is not None:
                database.close()


def reinspect_image(contract, bundle: Path, root: Path, owner: str) -> str:
    image = contract.execution.image_identity
    verify_image(json.loads(command([DOCKER, "image", "inspect", image]))[0], contract)
    cid = command(
        [
            DOCKER,
            "create",
            "--pull=never",
            "--name",
            "zelo-operator-inspect-" + owner,
            "--label",
            f"{LABEL}={owner}",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--restart=no",
            image,
        ]
    ).strip()
    if re.fullmatch(r"[0-9a-f]{64}", cid) is None:
        raise ValueError("operator_inspection_identity")
    info = json.loads(command([DOCKER, "inspect", cid]))[0]
    if (
        info["Image"] != image
        or info["Config"]["Labels"].get(LABEL) != owner
        or info["State"]["Running"]
        or info["State"]["Pid"]
    ):
        raise ValueError("operator_inspection_not_stopped")
    archive = root / "post-run-image.tar"
    export_bounded(DOCKER, cid, archive)
    with archive.open("rb") as source:
        digest = verify_image_archive(
            source, expected_image_files(contract.manifest, bundle / "browser-pipe")
        )
    command([DOCKER, "rm", cid])
    return digest


def revision() -> dict:
    repo = str(Path(__file__).resolve().parents[1])
    commit = command(["/usr/bin/git", "-C", repo, "rev-parse", "HEAD"]).strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("operator_revision_invalid")
    dirty = command(["/usr/bin/git", "-C", repo, "status", "--porcelain"])
    return {"git_commit": commit, "worktree_clean": not bool(dirty)}


def qualify(source: Path, expected_digest: str, base: Path) -> tuple[Path, bool]:
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="operator-proof-", dir=base)).resolve()
    report = {
        "status": "incomplete",
        "target": {"origin": ORIGIN, "path": DOCUMENT_PATH},
        "external_network_attempted": False,
        "network_adapter": "default OwnedResolver and verified-TLS PageSession",
        "production_browser_worker_activated": False,
        "runtime_cleanup_confirmed": False,
        "receipt_is_signature_or_provenance": False,
        "started_at": datetime.now(UTC).isoformat(),
        "source_manifest_digest": expected_digest,
    }
    owner = None
    image = None
    try:
        report.update(revision())
        receipt = prepare(source, expected_digest, root / "prepared", ENDPOINTS)
        payload = json.loads(receipt.read_bytes())
        image = payload["contract"]["execution"]["image_identity"]
        info = json.loads(command([DOCKER, "image", "inspect", image]))[0]
        owner = info.get("Config", {}).get("Labels", {}).get(LABEL)
        if info.get("Id") != image or re.fullmatch(r"[0-9a-f]{32}", owner or "") is None:
            owner = None
            raise ValueError("operator_setup_owner_invalid")
        report["setup_owner"] = owner
        settings = diagnostic_settings(root, receipt)
        report["external_network_attempted"] = True
        contract = asyncio.run(exercise(settings, report))
        if command([DOCKER, "ps", "-a", "--filter", "ancestor=" + image, "--format", "{{.ID}}"]):
            raise RuntimeError("operator_image_containers_remain")
        digest = reinspect_image(contract, settings.browser_worker_bundle, root, owner)
        if digest != payload["image_payload_digest"]:
            raise ValueError("operator_post_run_image_mismatch")
        report["post_run_image_payload_digest"] = digest
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
    finally:
        if report["external_network_attempted"] and not report["runtime_cleanup_confirmed"]:
            report["status"] = "cleanup_unproven"
        # Uncertain runtime cleanup preserves the image, receipt and admission fence.
        if owner is not None:
            try:
                errors = cleanup_owned_setup(
                    DOCKER,
                    owner,
                    preserve_verified_image=None if report["runtime_cleanup_confirmed"] else image,
                )
            except Exception as error:
                errors = ["setup_cleanup_dispatch_failed:" + type(error).__name__]
            report["setup_cleanup_errors"] = errors
            if errors:
                report["status"] = "cleanup_unproven"
        report["finished_at"] = datetime.now(UTC).isoformat()
        path = save_report(root, report)
    return path, report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-public-network-and-owned-containers", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-digest", required=True)
    args = parser.parse_args()
    if not args.allow_public_network_and_owned_containers:
        parser.error(
            "Explicit --allow-public-network-and-owned-containers required; no setup started"
        )
    base = Path(__file__).resolve().parents[1] / "runtime" / "browser-operator"
    path, passed = qualify(args.bundle, args.expected_bundle_digest, base)
    print("EVIDENCE=" + str(path))
    if not passed:
        parser.exit(
            1, "operator qualification failed; inspect private evidence and retained fences\n"
        )


if __name__ == "__main__":
    main()
