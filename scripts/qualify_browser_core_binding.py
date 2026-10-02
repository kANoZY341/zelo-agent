"""Explicit real-host Core browser proof; deterministic DNS/HTTP, actual Chromium.

No live Core config/database, external website, credentials or user file is used.
Setup creates only diagnostic-owned labelled resources and removes them afterward.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from scripts.qualify_browser_renderer import (
    cleanup_owned_setup,
    command,
    expected_image_files,
    prepare_candidate,
)


async def exercise_core(image, bundle, manifest, attachment, root):
    import httpx

    from app.api_contracts import ApiEndpointRegistry
    from app.browser_operator import canonical, contract_data, load_receipt
    from app.browser_worker import BrowserRuntime
    from app.config import Settings
    from app.database import Database
    from app.runner import Runner
    from app.schemas import BrowserRunInput
    from app.tasks import TaskService
    from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_browser_worker
    from qualification.broker import PageSession, Policy
    from qualification.browser_seccomp import build_profile
    from qualification.dns import QualificationError
    from qualification.renderer import DocumentRenderer
    from qualification.renderer_contracts import (
        CONTROLLER_DIGEST,
        RENDERER_DIGEST,
        PreparedImage,
        RendererContract,
        origins_digest,
    )
    from qualification.renderer_process import DockerHandle
    from qualification.runtime_contracts import ExecutionPolicy, ResourcePolicy

    registry = ApiEndpointRegistry.from_entries(("fixture=https://example.com",))
    bindings = (registry.bind("fixture"),)
    policy = Policy()
    browser = next(x.sha256 for x in manifest.files if x.path == "browser/chrome-headless-shell")
    contract = RendererContract(
        ExecutionPolicy(
            ResourcePolicy(536870912, 128, 50000, 100000, 0, 33554432, 30),
            image,
            browser,
            CONTROLLER_DIGEST,
            policy.digest,
            RENDERER_DIGEST,
            origins_digest(registry, bindings),
        ),
        manifest,
        build_profile(),
        PreparedImage(image, manifest.digest),
    )
    inventory = expected_image_files(manifest, bundle / "browser-pipe")
    payload_digest = hashlib.sha256(canonical(inventory)).hexdigest()
    assert payload_digest == attachment["image_payload_digest"]
    receipt = root / "operator-receipt.json"
    receipt.touch(mode=0o600)
    receipt.write_bytes(
        canonical(
            {
                "schema": "zelo-browser-operator-v1",
                "contract": contract_data(contract),
                "image_payload": inventory,
                "image_payload_digest": payload_digest,
            }
        )
    )
    loaded, receipt_identity = load_receipt(receipt, bundle, registry)
    assert loaded == contract
    admission = root / "admission"
    jobs = root / "jobs"
    core = root / "core"
    for directory in (admission, jobs, core):
        directory.mkdir(mode=0o700)
    state = {"body": "", "handle": "normal"}
    requests, renderers, handles, reports = [], [], [], []
    rendered = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class Resolver:
        async def resolve(self, host, port, deadline):
            assert host == "example.com" and port == 443
            return ("8.8.8.8",)

        async def aclose(self):
            pass

    class Held(DockerHandle):
        async def render(self, *args):
            result = await super().render(*args)
            rendered.set()
            await asyncio.Event().wait()
            return result

    class LostAck(DockerHandle):
        async def _command(self, *args):
            value = await super()._command(*args)
            if args[0] == "create":
                raise QualificationError("controlled_lost_ack")
            return value

    class DelayedCleanup(DockerHandle):
        async def _cleanup(self):
            cleanup_started.set()
            await cleanup_release.wait()
            return await super()._cleanup()

    def renderer_factory(*args):
        body = state["body"].encode()
        handle_kind = state["handle"]

        def session_factory(*session_args, **kwargs):
            def response(request):
                assert request.method == "GET" and request.url.host == "8.8.8.8"
                assert request.headers["host"] == "example.com"
                requests.append(str(request.url))
                return httpx.Response(
                    200, headers={"Content-Type": "text/html"}, stream=httpx.ByteStream(body)
                )

            return PageSession(
                *session_args,
                **kwargs,
                resolver=Resolver(),
                transport=httpx.MockTransport(response),
            )

        def handle_factory(*handle_args):
            kind = {
                "normal": DockerHandle,
                "held": Held,
                "lost": LostAck,
                "cleanup": DelayedCleanup,
            }[handle_kind]
            handle = kind(*handle_args)
            handles.append(handle)
            return handle

        renderer = DocumentRenderer(
            *args, session_factory=session_factory, handle_factory=handle_factory
        )
        renderers.append(renderer)
        return renderer

    runtime = BrowserRuntime(
        registry,
        contract,
        bundle,
        jobs,
        admission,
        receipt_identity,
        renderer_factory=renderer_factory,
        receipt_path=receipt,
    )
    supervisor = Supervisor(WorkerRegistry(((descriptor_for_browser_worker(), runtime),)))
    settings = Settings(
        _env_file=None, operator_token="host-proof-only-not-a-real-credential", runtime_dir=core
    )
    database = Database(settings.database_path)
    database.migrate()
    service = TaskService(database, settings, browser_supervisor=supervisor)
    runner = Runner(service, browser_supervisor=supervisor)
    operations = []

    def contender_denied():
        # A separate Python process cannot inherit Core's in-process capacity state.
        code = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from qualification.admission import AdmissionController, AdmissionDenied
try:
    AdmissionController(Path(sys.argv[2])).acquire(sys.argv[3])
except AdmissionDenied:
    print("denied")
else:
    raise SystemExit(2)
"""
        assert (
            command(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    code,
                    str(Path(__file__).resolve().parents[1]),
                    str(admission),
                    contract.digest,
                ]
            )
            == "denied\n"
        )

    def start(body, kind="normal", timeout=20):
        state.update(body=body, handle=kind)
        request = BrowserRunInput(
            kind="browser_run",
            payload={"endpoint_id": "fixture", "path": "/document"},
            timeout_seconds=timeout,
        )
        task, created = service.submit(request, None)
        assert created and task.browser_spec.policy_version == contract.digest
        execution = service.claim()
        assert execution is not None and execution.task_id == task.id
        operation = asyncio.create_task(runner._run(execution))
        runner.operations[task.id] = operation
        operations.append(operation)
        return task.id, operation

    def assert_clean():
        assert (admission / "active.marker").stat().st_size == 0
        assert json.loads(command(["/usr/bin/docker", "image", "inspect", image]))[0]["Id"] == image

    try:
        task_id, operation = start(
            '<main>CORE_BROWSER_OK</main><script>document.body.textContent="SCRIPT_EXECUTED"</script>'
        )
        await asyncio.wait_for(operation, 25)
        task = service.get(task_id)
        assert task.status == "succeeded" and task.result["text"] == "CORE_BROWSER_OK"
        assert task.result["contract_digest"] == contract.digest
        assert task.result["source_origin"] == "https://example.com"
        assert task.result["instruction_trust"] == "none"
        assert not task.result["truncated"]
        assert_clean()
        reports.append(
            {
                "case": "Core task reaches actual Chromium; script disabled and evidence persisted",
                "passed": True,
            }
        )

        task_id, operation = start("<main>" + "x" * 70000 + "</main>")
        await asyncio.wait_for(operation, 25)
        task = service.get(task_id)
        assert task.status == "succeeded" and task.result["truncated"] is True
        assert task.result["text"] == "x" * 10000 + "…"
        assert_clean()
        reports.append(
            {
                "case": "Core preserves truthful Chromium long-node preview truncation",
                "passed": True,
            }
        )

        task_id, operation = start("<main>cleanup gate</main>", "cleanup")
        await asyncio.wait_for(cleanup_started.wait(), 20)
        assert service.get(task_id).status == "running" and service.get(task_id).result is None
        assert not operation.done() and (admission / "active.marker").stat().st_size > 0
        contender_denied()
        cleanup_release.set()
        await asyncio.wait_for(operation, 20)
        assert service.get(task_id).status == "succeeded"
        assert_clean()
        reports.append(
            {
                "case": "Core success waits for real cleanup; other process denied",
                "passed": True,
            }
        )

        task_id, operation = start("<main>lost acknowledgement</main>", "lost")
        await asyncio.wait_for(operation, 20)
        task = service.get(task_id)
        assert task.status == "failed" and task.result is None
        assert_clean()
        reports.append(
            {
                "case": "Core handles lost Docker-create acknowledgement without result leakage",
                "passed": True,
            }
        )

        rendered.clear()
        task_id, operation = start("<main>cancel before result</main>", "held")
        await asyncio.wait_for(rendered.wait(), 20)
        assert service.get(task_id).result is None
        contender_denied()
        service.cancel(task_id)
        runner.cancel(task_id)
        await asyncio.wait_for(operation, 20)
        assert service.get(task_id).status == "cancelled" and service.get(task_id).result is None
        await asyncio.wait_for(
            asyncio.gather(
                *(r.owner._lifecycle for r in renderers if r.owner._lifecycle is not None),
                return_exceptions=True,
            ),
            20,
        )
        assert_clean()
        reports.append(
            {
                "case": "Core cancellation after actual render preserves cleanup and admission",
                "passed": True,
            }
        )

        rendered.clear()
        task_id, operation = start("<main>deadline before result</main>", "held", timeout=10)
        await asyncio.wait_for(rendered.wait(), 8)
        await asyncio.wait_for(operation, 12)
        assert service.get(task_id).status == "failed" and service.get(task_id).result is None
        await asyncio.wait_for(
            asyncio.gather(
                *(r.owner._lifecycle for r in renderers if r.owner._lifecycle is not None),
                return_exceptions=True,
            ),
            20,
        )
        assert_clean()
        reports.append(
            {
                "case": "Core deadline after actual render confirms cleanup without evidence",
                "passed": True,
            }
        )

        cleanup_started.clear()
        cleanup_release.clear()
        task_id, operation = start("<main>deadline during cleanup</main>", "cleanup", timeout=10)
        await asyncio.wait_for(cleanup_started.wait(), 8)
        await asyncio.wait_for(operation, 14)
        assert service.get(task_id).status == "failed" and service.get(task_id).result is None
        assert (admission / "active.marker").stat().st_size > 0
        contender_denied()
        before_requests, before_handles = len(requests), len(handles)
        busy_task_id, busy_operation = start("<main>must not fetch while fenced</main>")
        await asyncio.wait_for(busy_operation, 10)
        assert service.get(busy_task_id).status == "failed"
        assert service.get(busy_task_id).result is None
        assert len(requests) == before_requests and len(handles) == before_handles
        assert (admission / "active.marker").stat().st_size > 0
        cleanup_release.set()
        await asyncio.wait_for(
            asyncio.gather(
                *(r.owner._lifecycle for r in renderers if r.owner._lifecycle is not None),
                return_exceptions=True,
            ),
            20,
        )
        assert service.get(task_id).status == "failed" and service.get(task_id).result is None
        assert_clean()
        reports.append(
            {
                "case": "Terminal Core task retains pending-cleanup fence; next task cannot fetch",
                "passed": True,
            }
        )

        task_id, operation = start("<main>READMISSION_OK</main>")
        await asyncio.wait_for(operation, 25)
        assert (
            service.get(task_id).status == "succeeded"
            and service.get(task_id).result["text"] == "READMISSION_OK"
        )
        assert_clean()
        reports.append({"case": "Core readmission after confirmed cleanup", "passed": True})
        await runtime.aclose()
        return {
            "cases": reports,
            "requests": len(requests),
            "contract_digest": contract.digest,
            "prepared_image_preserved_by_runtime": True,
            "real_core_task_pipeline": True,
            "http_dns": "controlled fixtures",
            "actual_renderer_container_owner": True,
        }
    finally:
        cleanup_release.set()
        for operation in operations:
            if not operation.done():
                operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
        await asyncio.gather(*(r.aclose() for r in renderers), return_exceptions=True)
        lifecycles = [r.owner._lifecycle for r in renderers if r.owner._lifecycle is not None]
        if lifecycles:
            _, pending = await asyncio.wait(lifecycles, timeout=20)
            if pending:
                raise RuntimeError("core_proof_cleanup_pending")
        try:
            await runtime.aclose()
        finally:
            database.close()
        if any(
            h._close is None
            or not h._close.done()
            or h._close.cancelled()
            or h._close.exception() is not None
            or h._close.result() is not True
            for h in handles
        ):
            raise RuntimeError("core_proof_cleanup_unproven")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-owned-containers", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-digest", required=True)
    args = parser.parse_args()
    if not args.allow_owned_containers:
        parser.error("Explicit --allow-owned-containers required; no setup or browser was started")
    repo = Path(__file__).resolve().parents[1]
    base = repo / "runtime" / "browser-core-binding"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="core-proof-", dir=base))
    run = uuid4().hex
    report = {
        "run_id": run,
        "status": "incomplete",
        "external_network_used": False,
        "production_browser_worker_activated": False,
    }
    failure = None
    try:
        image, bundle, manifest, attachment = prepare_candidate(
            args.bundle,
            args.expected_bundle_digest,
            repo / "qualification/fixtures/browser_pipe.c",
            root,
            run,
        )
        report["image_attachment"] = attachment
        report.update(asyncio.run(exercise_core(image, bundle, manifest, attachment, root)))
        report["status"] = "passed"
    except BaseException as error:
        report["failure_type"] = type(error).__name__
        failure = error
    finally:
        report["setup_cleanup_errors"] = cleanup_owned_setup("/usr/bin/docker", run)
        if report["setup_cleanup_errors"]:
            report["status"] = "cleanup_unproven"
        (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        print("EVIDENCE=" + str(root / "result.json"))
    if failure is not None:
        raise failure
    if report["status"] != "passed":
        raise RuntimeError("core_browser_proof_not_verified")


if __name__ == "__main__":
    main()
