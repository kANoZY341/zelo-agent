import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.api_contracts import ApiEndpointRegistry, ApiEndpointSpec
from qualification.admission import AdmissionController, AdmissionDenied
from qualification.artifacts import ArtifactPolicy, capture_manifest
from qualification.broker import PageSession, Policy
from qualification.browser_seccomp import build_profile
from qualification.dns import QualificationError
from qualification.renderer import DocumentRenderer
from qualification.renderer_contracts import (
    CONTROLLER_DIGEST,
    RENDERER_DIGEST,
    ROLES,
    PreparedImage,
    RendererContract,
    origins_digest,
)
from qualification.renderer_process import (
    CLI,
    ENV,
    LABEL,
    TMPFS,
    DockerHandle,
    create_arguments,
    verify_container,
    verify_image,
)
from qualification.renderer_protocol import (
    CSP,
    MAX_DEPTH,
    MESSAGE_BYTES,
    extract_text,
    render_document,
    strict_json,
)
from qualification.runtime_contracts import ExecutionPolicy, ResourcePolicy


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def setup(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    (bundle / "browser").mkdir()
    for _, path in ROLES:
        file = bundle / path
        file.write_bytes(path.encode())
        file.chmod(0o755)
    manifest = capture_manifest(bundle, ROLES, ArtifactPolicy())
    registry = ApiEndpointRegistry(
        [ApiEndpointSpec(endpoint_id="fixture", origin="https://8.8.8.8")]
    )
    bindings = (registry.bind("fixture"),)
    policy = Policy()
    execution = ExecutionPolicy(
        ResourcePolicy(536870912, 128, 50000, 100000, 0, 33554432, 2),
        "sha256:" + "a" * 64,
        hashlib.sha256(b"browser/chrome-headless-shell").hexdigest(),
        CONTROLLER_DIGEST,
        policy.digest,
        RENDERER_DIGEST,
        origins_digest(registry, bindings),
    )
    contract = RendererContract(
        execution,
        manifest,
        build_profile(),
        PreparedImage(execution.image_identity, manifest.digest),
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    return contract, bundle, runtime, registry, bindings


def text(value):
    return {"nodeType": 3, "nodeName": "#text", "nodeValue": value}


def element(name="BODY", children=()):
    return {
        "nodeType": 1,
        "nodeName": name,
        "children": list(children),
        "childNodeCount": len(children),
    }


class Writer:
    def __init__(self, reader, transform=None):
        self.reader = reader
        self.requests = []
        self.transform = transform

    def write(self, raw):
        request = json.loads(raw[:-1])
        self.requests.append(request)
        method = request["method"]
        result = {}
        if method == "Target.createBrowserContext":
            result = {"browserContextId": "context"}
        elif method == "Target.createTarget":
            result = {"targetId": "target"}
        elif method == "Target.attachToTarget":
            result = {"sessionId": "session"}
        elif method == "Page.getFrameTree":
            result = {"frameTree": {"frame": {"id": "frame", "url": "about:blank"}}}
        elif method == "DOM.getDocument":
            result = {"root": element(children=[text("safe")])}
        response = {"id": request["id"], "result": result}
        if "sessionId" in request:
            response["sessionId"] = request["sessionId"]
        raw = json.dumps(response).encode() + b"\0"
        if self.transform:
            raw = self.transform(request, response, raw)
        if raw is None:
            self.reader.feed_eof()
        else:
            self.reader.feed_data(raw)

    async def drain(self):
        pass

    def close(self):
        pass


async def protocol(transform=None, content="<p>hello</p>", media="text/html"):
    reader = asyncio.StreamReader(limit=MESSAGE_BYTES)
    writer = Writer(reader, transform)
    result = await render_document(
        reader, writer, asyncio.get_running_loop().time() + 1, content, media
    )
    return result, writer.requests


def test_protocol_fixed_commands_and_document_data():
    hostile = "<script>Runtime.evaluate;$(touch /bad)</script><p>secret</p>"
    result, requests = run(protocol(content=hostile))
    methods = [item["method"] for item in requests]
    assert result.text == "safe"
    assert len(methods) == 14
    assert methods.index("Emulation.setScriptExecutionDisabled") < methods.index(
        "Page.setDocumentContent"
    )
    assert "Runtime.evaluate" not in methods and "Page.navigate" not in methods
    assert requests[2]["params"]["url"] == "about:blank"
    assert (
        next(r for r in requests if r["method"] == "Page.setDocumentContent")["params"]["html"]
        == CSP + hostile
    )
    assert (
        next(r for r in requests if r["method"] == "DOM.getDocument")["params"]["depth"]
        == MAX_DEPTH
    )
    _, requests = run(protocol(content="<script>x</script>", media="text/plain"))
    assert "&lt;script&gt;" in requests[11]["params"]["html"]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"id":1,"id":1}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b"\xff",
        b"{",
        b"[" * 1500,
    ],
)
def test_strict_json(raw):
    with pytest.raises(QualificationError, match="invalid_json"):
        strict_json(raw)


@pytest.mark.parametrize(
    "kind",
    ["mismatch", "unsolicited", "error", "event", "flood", "bytes", "eof", "session", "navigation"],
)
def test_protocol_denies(kind):
    def change(request, response, raw):
        if kind == "mismatch":
            response["id"] += 1
        elif kind == "unsolicited":
            response = {"id": False, "result": {}}
        elif kind == "error":
            response = {"id": 1, "error": {"message": "DO NOT ECHO SECRET"}}
        elif kind == "event":
            response = {"method": "Page.frameNavigated", "params": {}}
        elif kind == "flood":
            if request["method"] != "DOM.getDocument":
                return raw
            event = (
                json.dumps({"method": "DOM.documentUpdated", "sessionId": "session"}).encode()
                + b"\0"
            )
            return event * 33 + raw
        elif kind == "bytes":
            return b"x" * (MESSAGE_BYTES + 1) + b"\0"
        elif kind == "eof":
            return None
        elif kind == "session":
            response["sessionId"] = "wrong"
        elif kind == "navigation":
            if request["method"] != "Page.getFrameTree":
                return raw
            response["result"]["frameTree"]["frame"]["url"] = "https://example.org"
        return json.dumps(response).encode() + b"\0"

    with pytest.raises(QualificationError) as exc:
        run(protocol(change))
    assert "SECRET" not in str(exc.value)


def test_dom_order_exclusions_and_truncation():
    root = element(
        children=[
            text("a"),
            element("SCRIPT", [text("bad")]),
            element("HEAD", [text("instructions")]),
            element("STYLE", [text("bad")]),
            element("DIV", [text("b")]),
            text("c"),
        ]
    )
    assert extract_text(root).text == "abc"
    root["childNodeCount"] += 1
    assert extract_text(root).truncated
    assert extract_text(element(children=[text("é" * 40000)])).truncated
    assert len(extract_text(element(children=[text("é" * 40000)])).text.encode()) == 65536


@pytest.mark.parametrize(
    "root",
    [
        None,
        {},
        {"nodeType": 1, "nodeName": "BODY", "children": None},
        element(children=[text("x")] * 4096),
    ],
)
def test_dom_malformed_and_nodes(root):
    with pytest.raises(QualificationError):
        extract_text(root)


def test_dom_depth_incomplete():
    root = element()
    root["childNodeCount"] = 1
    for _ in range(MAX_DEPTH):
        root = element(children=[root])
    assert extract_text(root).truncated
    root = element(children=[root])
    with pytest.raises(QualificationError):
        extract_text(root)


def image_data(contract):
    return {
        "Id": contract.execution.image_identity,
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {"Entrypoint": ["/browser-pipe"], "Env": ENV},
    }


def container_data(contract, name, identity):
    return {
        "Id": "b" * 64,
        "Name": "/" + name,
        "Image": contract.execution.image_identity,
        "State": {"Running": False},
        "Config": {
            "Labels": {LABEL: identity},
            "User": "65534:65534",
            "Entrypoint": ["/browser-pipe"],
            "Env": ENV,
            "OpenStdin": True,
            "Tty": False,
            "WorkingDir": "",
        },
        "HostConfig": {
            "Memory": 536870912,
            "MemorySwap": 536870912,
            "PidsLimit": 128,
            "CpuPeriod": 100000,
            "CpuQuota": 50000,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "ShmSize": 16777216,
            "CapDrop": ["ALL"],
            "Tmpfs": {"/tmp": TMPFS},
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {"Type": "none"},
            "SecurityOpt": [
                "no-new-privileges",
                "seccomp=" + contract.profile.profile_bytes.decode(),
            ],
        },
    }


def test_contract_and_command(setup):
    contract, bundle, runtime, registry, bindings = setup
    contract.verify_bundle(bundle)
    contract.verify_broker(registry, bindings, Policy())
    args = create_arguments(contract, "name", "lease", runtime / "seccomp.json")
    assert args[0] == "create" and args[-1] == contract.execution.image_identity
    assert "--pull=never" in args and "--network=none" in args
    assert not any(x in args for x in ("build", "pull", "--privileged", "--volume"))
    verify_image(image_data(contract), contract)
    verify_container(container_data(contract, "name", "lease"), contract, "name", "lease")
    with pytest.raises(ValueError):
        replace(contract, prepared=replace(contract.prepared, manifest_digest="b" * 64)).validate()
    with pytest.raises(ValueError):
        replace(
            contract, execution=replace(contract.execution, controller_digest="b" * 64)
        ).validate()
    with pytest.raises(ValueError):
        contract.verify_broker(registry, (), Policy())
    (bundle / "browser-pipe").write_bytes(b"drift")
    with pytest.raises(ValueError):
        contract.verify_bundle(bundle)


@pytest.mark.parametrize(
    "key,value",
    [
        ("NetworkMode", "host"),
        ("MemorySwap", -1),
        ("PidMode", "host"),
        ("Privileged", True),
        ("Binds", ["/:/host"]),
        ("CapAdd", ["SYS_ADMIN"]),
        ("SecurityOpt", ["seccomp=unconfined"]),
        ("Memory", True),
        ("Tmpfs", {}),
    ],
)
def test_config_drift(setup, key, value):
    contract = setup[0]
    data = container_data(contract, "name", "lease")
    data["HostConfig"][key] = value
    with pytest.raises(QualificationError):
        verify_container(data, contract, "name", "lease")


class Process:
    def __init__(self, output=b"", *, attached=False, transform=None, code=0):
        self.stdout = asyncio.StreamReader(limit=MESSAGE_BYTES)
        self.stderr = asyncio.StreamReader()
        self.stdin = Writer(self.stdout, transform) if attached else None
        self.returncode = None if attached else code
        self.dead = asyncio.Event()
        if not attached:
            self.stdout.feed_data(output)
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.dead.set()

    def kill(self):
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.dead.set()

    async def wait(self):
        await self.dead.wait()
        return self.returncode


class Daemon:
    def __init__(self, contract, runtime, *, failure=None, pause=None):
        self.contract, self.runtime = contract, runtime
        self.failure, self.pause = failure, pause
        self.exists = False
        self.commands = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.attached = None
        self.identity = self.name = None

    async def spawn(self, *args, **kwargs):
        assert args[: len(CLI)] == CLI
        assert set(kwargs["env"]) == {"DOCKER_CONFIG"}
        assert Path(kwargs["env"]["DOCKER_CONFIG"]).parent == self.runtime
        assert (self.runtime / "active.marker").stat().st_size > 0
        args = args[len(CLI) :]
        self.commands.append(args)
        stage = args[0] if args[0] in ("create", "start") else " ".join(args[:2])
        if stage == "create":
            self.exists = True
            self.name = args[args.index("--name") + 1]
            self.identity = args[args.index("--label") + 1].split("=", 1)[1]
        if stage == self.pause:
            self.entered.set()
            await self.release.wait()
        if stage == self.failure:
            return Process(b"", code=1)
        if stage == "image inspect":
            return Process(json.dumps([image_data(self.contract)]).encode())
        if stage == "container inspect":
            return Process(
                json.dumps([container_data(self.contract, self.name, self.identity)]).encode()
            )
        if stage == "container ls":
            return Process(("b" * 64 + "\n").encode() if self.exists else b"")
        if stage == "container rm":
            self.exists = False
        if stage == "start":
            self.attached = Process(attached=True)
            return self.attached
        return Process(("b" * 64).encode())


class Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"<p>safe</p>"


class Resolver:
    async def aclose(self):
        pass


def document_renderer(setup, daemon, *, gate=None, failed_cleanup=False):
    contract, bundle, runtime, registry, bindings = setup

    class Session(PageSession):
        async def fetch(self, endpoint_id, path):
            assert (runtime / "active.marker").stat().st_size > 0
            assert (
                self._deadline
                <= asyncio.get_running_loop().time() + contract.execution.resources.deadline_seconds
            )
            if gate:
                gate[0].set()
                await gate[1].wait()
            return await super().fetch(endpoint_id, path)

        async def aclose(self):
            await super().aclose()
            if failed_cleanup:
                raise QualificationError("cleanup_fixture_failure")

    def session(*args, **kwargs):
        return Session(
            *args,
            **kwargs,
            resolver=Resolver(),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"Content-Type": "text/html"}, stream=Body())
            ),
        )

    return DocumentRenderer(
        AdmissionController(runtime),
        contract,
        bundle,
        runtime,
        registry,
        bindings,
        session_factory=session,
        handle_factory=lambda *args: DockerHandle(*args, spawn=daemon.spawn),
    )


def test_combined_success_and_result_after_cleanup(setup):
    async def exercise():
        daemon = Daemon(setup[0], setup[2], pause="container rm")
        renderer = document_renderer(setup, daemon)
        task = asyncio.create_task(renderer.render("fixture", "/page"))
        await asyncio.wait_for(daemon.entered.wait(), 4)
        assert not task.done()
        marker = json.loads((setup[2] / "active.marker").read_text())
        assert marker["policy"] == setup[0].digest
        with pytest.raises(AdmissionDenied):
            AdmissionController(setup[2]).acquire("c" * 64)
        daemon.release.set()
        result = await task
        assert result.text == "safe" and result.source_path == "/page"
        assert result.instruction_trust == "none" and not daemon.exists
        assert daemon.attached.returncode == -9
        assert (setup[2] / "active.marker").read_bytes() == b""
        assert not list(setup[2].glob("renderer-*"))
        await asyncio.gather(renderer.aclose(), renderer.aclose())

    run(exercise())


@pytest.mark.parametrize("stage", ["image inspect", "create", "container inspect", "start"])
def test_stage_failures_cleanup(setup, stage):
    async def exercise():
        daemon = Daemon(setup[0], setup[2], failure=stage)
        renderer = document_renderer(setup, daemon)
        with pytest.raises(QualificationError):
            await renderer.render("fixture", "/")
        if stage == "container inspect":
            assert daemon.exists
            with pytest.raises(AdmissionDenied, match="fenced"):
                AdmissionController(setup[2]).acquire("c" * 64)
        else:
            assert not daemon.exists
            assert (setup[2] / "active.marker").read_bytes() == b""

    run(exercise())


@pytest.mark.parametrize(
    "stage", ["create", "start", "image inspect", "container inspect", "container rm"]
)
def test_repeated_cancellation_and_close_races(setup, stage):
    async def exercise():
        daemon = Daemon(setup[0], setup[2], pause=stage)
        renderer = document_renderer(setup, daemon)
        task = asyncio.create_task(renderer.render("fixture", "/"))
        await asyncio.wait_for(daemon.entered.wait(), 4)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        closers = [asyncio.create_task(renderer.aclose()) for _ in range(2)]
        with pytest.raises(AdmissionDenied):
            AdmissionController(setup[2]).acquire("c" * 64)
        daemon.release.set()
        results = await asyncio.gather(task, *closers, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert not daemon.exists
        assert (setup[2] / "active.marker").read_bytes() == b""

    run(exercise())


def test_fetch_cancel_no_container(setup):
    async def exercise():
        gate = (asyncio.Event(), asyncio.Event())
        daemon = Daemon(setup[0], setup[2])
        renderer = document_renderer(setup, daemon, gate=gate)
        task = asyncio.create_task(renderer.render("fixture", "/"))
        await gate[0].wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not daemon.commands
        assert (setup[2] / "active.marker").read_bytes() == b""

    run(exercise())


@pytest.mark.parametrize("failed_broker", [False, True])
def test_cleanup_failure_fences(setup, failed_broker):
    async def exercise():
        daemon = Daemon(setup[0], setup[2], failure=None if failed_broker else "container rm")
        renderer = document_renderer(setup, daemon, failed_cleanup=failed_broker)
        with pytest.raises(QualificationError):
            await renderer.render("fixture", "/")
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(setup[2]).acquire("c" * 64)

    run(exercise())


def test_lost_create_without_discovery_fences(setup):
    async def exercise():
        daemon = Daemon(setup[0], setup[2], failure="create")
        original = daemon.spawn

        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            if args[len(CLI)] == "create":
                daemon.exists = False  # cannot distinguish absence from a delayed daemon operation
            return child

        daemon.spawn = spawn
        with pytest.raises(QualificationError):
            await document_renderer(setup, daemon).render("fixture", "/")
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(setup[2]).acquire("c" * 64)

    run(exercise())


def test_real_controlled_cli_output_overrun_reaped(setup):
    async def exercise():
        children = []

        async def spawn(*args, **kwargs):
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys,time;sys.stdout.write('x'*200000);sys.stdout.flush();time.sleep(30)",
                **kwargs,
            )
            children.append(child)
            return child

        lease = AdmissionController(setup[2]).acquire(setup[0].digest)
        handle = DockerHandle(lease, setup[0], setup[1], setup[2], spawn=spawn)
        try:
            with pytest.raises(QualificationError, match="output_limit"):
                await handle._command("image", "inspect", setup[0].execution.image_identity)
            assert await handle.stop_and_confirm_cleanup()
            assert children[0].returncode is not None
        finally:
            lease.finish(cleanup_confirmed=True)

    run(exercise())


def test_bootstrap_fixed_native_sandbox():
    source = Path("qualification/fixtures/browser_pipe.c").read_text()
    assert "sigaction(SIGALRM" in source and "alarm(25)" in source
    assert "getpid() != 1" in source and "waitpid(child" in source
    assert "dup2(0, 3)" in source and "dup2(1, 4)" in source
    assert "--remote-debugging-pipe" in source
    assert "--no-sandbox" not in source and "--remote-debugging-port" not in source
    assert "--user-data-dir=/tmp/profile" in source


@pytest.mark.parametrize(
    "field,value", [("Architecture", "arm64"), ("Os", "windows"), ("Id", "sha256:" + "f" * 64)]
)
def test_image_identity_drift(setup, field, value):
    data = image_data(setup[0])
    data[field] = value
    with pytest.raises(QualificationError):
        verify_image(data, setup[0])


@pytest.mark.parametrize(
    "path",
    ["//other.example/a", "/?secret=1", "/a#b", "https://example.org/", "/../private", "/%2fother"],
)
def test_task_input_denied_before_admission(setup, path):
    async def exercise():
        daemon = Daemon(setup[0], setup[2])
        with pytest.raises(ValueError):
            await document_renderer(setup, daemon).render("fixture", path)
        assert not daemon.commands
        assert not (setup[2] / "active.marker").exists()

    run(exercise())


def test_profile_drift_before_start(setup):
    async def exercise():
        daemon = Daemon(setup[0], setup[2])
        original = daemon.spawn

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            if args[len(CLI)] == "create":
                next(setup[2].glob("renderer-*/seccomp.json")).write_bytes(b"{}")
            return process

        daemon.spawn = spawn
        with pytest.raises(QualificationError):
            await document_renderer(setup, daemon).render("fixture", "/")
        assert not any(args[0] == "start" for args in daemon.commands)
        assert not daemon.exists
        assert (setup[2] / "active.marker").read_bytes() == b""

    run(exercise())


@pytest.mark.parametrize(
    "stage", ["fetch", "create", "start", "Page.setDocumentContent", "DOM.getDocument"]
)
@pytest.mark.parametrize("terminate", ["cancel", "timeout"])
def test_deadline_and_cancellation_across_pipe_stages(setup, stage, terminate):
    async def exercise():
        contract, *rest = setup
        contract = replace(
            contract,
            execution=replace(
                contract.execution,
                resources=replace(contract.execution.resources, deadline_seconds=0.08),
            ),
        )
        local = (contract, *rest)
        gate = (asyncio.Event(), asyncio.Event())
        daemon = Daemon(contract, setup[2], pause=stage if stage in ("create", "start") else None)
        original = daemon.spawn

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            if args[len(CLI)] == "start" and stage.startswith(("Page.", "DOM.")):

                def transform(request, response, raw):
                    if request["method"] == stage:
                        gate[0].set()
                        return b""  # stalled document transfer/output
                    return raw

                process.stdin.transform = transform
            return process

        daemon.spawn = spawn
        renderer = document_renderer(local, daemon, gate=gate if stage == "fetch" else None)
        task = asyncio.create_task(renderer.render("fixture", "/"))
        entered = daemon.entered if stage in ("create", "start") else gate[0]
        await asyncio.wait_for(entered.wait(), 1)
        if terminate == "cancel":
            task.cancel()
        else:
            await asyncio.sleep(0.10)
        daemon.release.set()
        with pytest.raises((asyncio.CancelledError, TimeoutError, QualificationError)):
            await task
        assert not daemon.exists
        assert (setup[2] / "active.marker").read_bytes() == b""
        if daemon.attached is not None:
            assert daemon.attached.returncode is not None

    run(exercise())


def test_stderr_flood_aborts_and_reaps(setup):
    async def exercise():
        daemon = Daemon(setup[0], setup[2])
        original = daemon.spawn

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            if args[len(CLI)] == "start":
                process.stderr.feed_data(b"secret" * 3000)
            return process

        daemon.spawn = spawn
        with pytest.raises(QualificationError) as error:
            await document_renderer(setup, daemon).render("fixture", "/")
        assert "secret" not in str(error.value)
        assert not daemon.exists and daemon.attached.returncode is not None

    run(exercise())


def test_cleanup_grace_retains_lease_until_cleanup(setup):
    async def exercise():
        daemon = Daemon(setup[0], setup[2], pause="container rm")
        renderer = document_renderer(setup, daemon)
        task = asyncio.create_task(renderer.render("fixture", "/"))
        await asyncio.wait_for(daemon.entered.wait(), 1)
        renderer.owner._grace = 0.01
        with pytest.raises(QualificationError, match="cleanup_pending"):
            await renderer.aclose()
        with pytest.raises(AdmissionDenied):
            AdmissionController(setup[2]).acquire("c" * 64)
        daemon.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await renderer.aclose()
        assert not daemon.exists
        assert (setup[2] / "active.marker").read_bytes() == b""

    run(exercise())


def test_total_protocol_bytes():
    def change(request, response, raw):
        if "sessionId" not in request:
            return raw
        response["result"]["padding"] = "x" * 500000
        return json.dumps(response).encode() + b"\0"

    with pytest.raises(QualificationError, match="pipe_bytes"):
        run(protocol(change))


def test_no_production_imports_renderer():
    for path in Path("app").rglob("*.py"):
        assert "qualification.renderer" not in path.read_text()


@pytest.mark.parametrize("wrong", [False, True])
def test_only_requested_attachment_event(wrong):
    def change(request, response, raw):
        if request["method"] != "Target.attachToTarget":
            return raw
        event = {
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": "session",
                "waitingForDebugger": False,
                "targetInfo": {
                    "targetId": "wrong" if wrong else "target",
                    "type": "page",
                    "url": "about:blank",
                    "browserContextId": "context",
                },
            },
        }
        return json.dumps(event).encode() + b"\0" + raw

    if wrong:
        with pytest.raises(QualificationError, match="forbidden_attachment"):
            run(protocol(change))
    else:
        assert run(protocol(change))[0].text == "safe"


def test_real_cli_creation_cancellation_retains_child(setup):
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        children = []

        async def spawn(*args, **kwargs):
            child = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-S", "-c", "import time;time.sleep(30)", **kwargs
            )
            children.append(child)
            entered.set()
            await release.wait()
            return child

        lease = AdmissionController(setup[2]).acquire(setup[0].digest)
        handle = DockerHandle(lease, setup[0], setup[1], setup[2], spawn=spawn)
        command = asyncio.create_task(
            handle._command("image", "inspect", setup[0].execution.image_identity)
        )
        await entered.wait()
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
        closing = asyncio.create_task(handle.stop_and_confirm_cleanup())
        await asyncio.sleep(0)
        assert not closing.done() and children[0].returncode is None
        release.set()
        # Cancel the owned CLI operation to exercise its retained kill/drain/reap.
        next(iter(handle._commands)).cancel()
        assert await closing
        assert children[0].returncode is not None
        lease.finish(cleanup_confirmed=True)

    run(exercise())
