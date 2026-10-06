"""Explicit offline renderer integration proof; never Core worker activation.

Operator setup is separate from job execution: prepare a scratch image without
network access, then verify the actual stopped image filesystem against expected
bytes before attaching its content identity to a renderer contract. No daemon,
host policy or existing user image is changed. Only this run's labelled resources
may be removed. Optional host tests are not part of automatic CI.
"""

import hashlib
import io
import json
import os
import selectors
import subprocess
import tarfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from qualification.artifacts import ArtifactManifest

LABEL = "app.zelo.renderer-qualification"
MAX_EXPORT_BYTES = 512 * 1024**2
MAX_EXPORT_ENTRIES = 8192
MAX_COMMAND_LOG = 16384


class _CommandOutputLimit(ValueError):
    def __init__(self, stream: str):
        self.stream = stream
        super().__init__("setup_command_output_limit")


class _CommandFailure(RuntimeError):
    """Bounded output for local classification; never include it in the message."""

    def __init__(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
        argv: tuple[str, ...],
        *,
        stdout_tail: bytes = b"",
        stderr_tail: bytes = b"",
        stdout_bytes_total: int | None = None,
        stderr_bytes_total: int | None = None,
        stdout_sha256: str | None = None,
        stderr_sha256: str | None = None,
        overflowed: tuple[str, ...] = (),
    ):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail
        self.stdout_bytes_total = (
            len(stdout) + len(stdout_tail) if stdout_bytes_total is None else stdout_bytes_total
        )
        self.stderr_bytes_total = (
            len(stderr) + len(stderr_tail) if stderr_bytes_total is None else stderr_bytes_total
        )
        self.stdout_sha256 = (
            hashlib.sha256(stdout + stdout_tail).hexdigest()
            if stdout_sha256 is None
            else stdout_sha256
        )
        self.stderr_sha256 = (
            hashlib.sha256(stderr + stderr_tail).hexdigest()
            if stderr_sha256 is None
            else stderr_sha256
        )
        self.overflowed = overflowed
        self.argv = argv
        super().__init__("setup_command_failed")


@contextmanager
def operator_process(args: list[str]) -> Iterator[tuple[list[str], dict[str, str]]]:
    """No inherited credentials, config, contexts, daemon selectors or loader hooks.

    Setup and runtime always address the same trusted local Docker socket. Each
    Docker CLI receives a fresh private empty configuration directory for its
    entire lifetime; no operator HOME or Docker configuration is consulted.
    """
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
    if not args or args[0] != "/usr/bin/docker":
        yield args, environment
        return
    environment["GOMAXPROCS"] = "2"
    if any(
        arg.startswith(("--host", "--context", "--config", "-H")) or arg == "-c" for arg in args[1:]
    ):
        raise ValueError("setup_daemon_override_forbidden")
    base = Path(__file__).resolve().parents[1] / "runtime"
    base.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix="operator-docker-config-", dir=base) as directory:
        environment["DOCKER_CONFIG"] = directory
        yield [args[0], "--host=unix:///var/run/docker.sock", *args[1:]], environment


def command(args: list[str], timeout: float = 20) -> str:
    with operator_process(args) as (argv, environment):
        return _command(argv, timeout, environment)


def _command(
    args: list[str],
    timeout: float,
    environment: dict[str, str],
    *,
    stdout_limit: int = 131072,
    drain_output_overflow: bool = False,
) -> str:
    """Bounded trusted operator command; no shell, input, or unbounded capture.

    The fixed local compiler/Docker CLI remain trusted. An isolated process group
    lets deadline cleanup cover helper children. Callers may opt to drain and discard
    bytes beyond capture caps, avoiding premature peer disconnects on noisy streams.
    """
    if type(stdout_limit) is not int or not 0 <= stdout_limit <= 131072:
        raise ValueError("setup_command_stdout_limit")
    if type(drain_output_overflow) is not bool:
        raise ValueError("setup_command_overflow_mode")

    import signal

    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=environment,
    )
    assert process.stdout is not None and process.stderr is not None
    buffers = {"out": bytearray(), "err": bytearray()}
    tails = {"out": bytearray(), "err": bytearray()}
    limits = {"out": stdout_limit, "err": MAX_COMMAND_LOG}
    totals = {"out": 0, "err": 0}
    digests = {"out": hashlib.sha256(), "err": hashlib.sha256()}
    overflowed = set()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ, "out")
            ready.register(process.stderr, selectors.EVENT_READ, "err")
            while ready.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("setup_command_timeout")
                for key, _ in ready.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        ready.unregister(key.fileobj)
                    else:
                        stream = key.data
                        totals[stream] += len(chunk)
                        digests[stream].update(chunk)
                        target = buffers[stream]
                        remaining_capture = limits[stream] - len(target)
                        if len(chunk) <= remaining_capture:
                            target.extend(chunk)
                        elif drain_output_overflow:
                            overflowed.add(stream)
                            if remaining_capture > 0:
                                target.extend(chunk[:remaining_capture])
                            tail = tails[stream]
                            tail.extend(chunk[max(remaining_capture, 0) :])
                            if len(tail) > MAX_COMMAND_LOG:
                                del tail[:-MAX_COMMAND_LOG]
                        else:
                            raise _CommandOutputLimit(stream)
        returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if returncode != 0:
            raise _CommandFailure(
                returncode,
                bytes(buffers["out"]),
                bytes(buffers["err"]),
                tuple(args),
                stdout_tail=bytes(tails["out"]),
                stderr_tail=bytes(tails["err"]),
                stdout_bytes_total=totals["out"],
                stderr_bytes_total=totals["err"],
                stdout_sha256=digests["out"].hexdigest(),
                stderr_sha256=digests["err"].hexdigest(),
                overflowed=tuple(sorted(overflowed)),
            )
        return buffers["out"].decode(
            "utf-8", errors="replace" if drain_output_overflow else "strict"
        )
    finally:
        # Until wait()/poll() reaps the leader its PID cannot be reused. Do not
        # signal a group after a successful wait returned and released that PID.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def expected_image_files(
    manifest: ArtifactManifest, bootstrap: Path, entrypoint: str = "browser-pipe"
) -> dict[str, tuple[int, int, str]]:
    """Derive expected image bytes from validated source manifest, never image labels."""
    if entrypoint != "browser-pipe":
        raise ValueError("unexpected_bootstrap_destination")
    result = {}
    for item in manifest.files:
        if item.path.startswith(("browser/", "libs/")):
            target = "opt/" + item.path
        elif item.path == "loader":
            target = "lib64/ld-linux-x86-64.so.2"
        elif item.path == entrypoint:
            target = entrypoint
        elif item.path == "Dockerfile":
            continue  # Build instructions are not a runtime artifact.
        else:
            raise ValueError("unexpected_prepared_bundle_entry")
        result[target] = (item.size, item.mode, item.sha256)
    data = bootstrap.read_bytes()
    metadata = (
        len(data),
        bootstrap.stat().st_mode & 0o7777,
        hashlib.sha256(data).hexdigest(),
    )
    if result.get(entrypoint) != metadata:
        raise ValueError("bootstrap_manifest_mismatch")
    return result


def verify_image_archive(
    source: io.BufferedIOBase, expected: dict[str, tuple[int, int, str]]
) -> str:
    """Verify tar entries without extracting or following any path/link.

    Export is of an unstarted container from an exact image ID. Docker-generated
    empty/host configuration files may vary; none can introduce executable code.
    Apart from Docker's exact etc/mtab -> /proc/mounts metadata alias, no symlink,
    device, unexpected code file, duplicate or executable-mode mismatch
    is accepted. The caller bounds archive bytes and exporter lifetime separately.
    """
    seen = set()
    verified = set()
    generated = {".dockerenv", "dev/console", "etc/hostname", "etc/hosts", "etc/resolv.conf"}
    total = 0
    with tarfile.open(fileobj=source, mode="r|") as archive:
        for item in archive:
            name = item.name.removeprefix("./").rstrip("/")
            if (
                not name
                or len(name) > 4096
                or name.startswith("/")
                or "\\" in name
                or any(part in ("", ".", "..") for part in name.split("/"))
                or any(ord(c) < 32 or ord(c) > 126 for c in name)
                or name in seen
                or len(seen) >= MAX_EXPORT_ENTRIES
            ):
                raise ValueError("unsafe_image_archive_path")
            seen.add(name)
            # Docker injects this precise mount-table alias into an otherwise
            # scratch filesystem. It is never opened/followed by this verifier.
            if (
                name == "etc/mtab"
                and item.issym()
                and item.linkname == "/proc/mounts"
                and item.size == 0
                and item.mode == 0o777
            ):
                continue
            if item.isdir():
                if item.mode & 0o022 and name not in {"tmp", "dev", "dev/shm"}:
                    raise ValueError("unsafe_image_directory_mode")
                continue
            if not item.isfile():
                raise ValueError("nonregular_image_archive_entry")
            total += item.size
            if total > MAX_EXPORT_BYTES:
                raise ValueError("image_archive_limit")
            if name in expected:
                size, mode, digest = expected[name]
                if item.size != size or item.mode != mode:
                    raise ValueError("image_artifact_metadata_mismatch")
            elif name in generated:
                # Docker exports these injected mount placeholders as empty 0755
                # files on this host; an executable EMPTY file contains no code.
                if (
                    item.size > MAX_COMMAND_LOG
                    or (item.size and item.mode & 0o111)
                    or (name in {".dockerenv", "dev/console"} and item.size != 0)
                ):
                    raise ValueError("unexpected_generated_image_file")
                digest = None
            else:
                raise ValueError("unexpected_image_file")
            reader = archive.extractfile(item)
            if reader is None:
                raise ValueError("image_archive_file_unreadable")
            content = hashlib.sha256()
            count = 0
            with reader:
                while block := reader.read(65536):
                    count += len(block)
                    if count > item.size:
                        raise ValueError("image_archive_file_grew")
                    content.update(block)
            if count != item.size or (digest is not None and content.hexdigest() != digest):
                raise ValueError("image_artifact_content_mismatch")
            if digest is not None:
                verified.add(name)
    if verified != expected.keys():
        raise ValueError("image_artifact_inventory_mismatch")
    return hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def export_bounded(docker: str, container: str, destination: Path) -> None:
    """Copy a Docker export with explicit byte/time/CLI-child ownership bounds."""
    import re

    if re.fullmatch(r"[0-9a-f]{64}", container) is None:
        raise ValueError("invalid_container_identity")
    if docker != "/usr/bin/docker":
        raise ValueError("setup_docker_executable_invalid")
    with operator_process([docker, "export", container]) as (argv, environment):
        _export_bounded(argv, destination, environment)


def _export_bounded(argv: list[str], destination: Path, environment: dict[str, str]) -> None:
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=environment,
    )
    assert process.stdout is not None and process.stderr is not None
    deadline = time.monotonic() + 60
    total = errors = 0
    try:
        with selectors.DefaultSelector() as ready, destination.open("xb") as output:
            ready.register(process.stdout, selectors.EVENT_READ, "out")
            ready.register(process.stderr, selectors.EVENT_READ, "err")
            while ready.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("image_export_timeout")
                for key, _ in ready.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        ready.unregister(key.fileobj)
                    elif key.data == "out":
                        total += len(chunk)
                        if total > MAX_EXPORT_BYTES:
                            raise ValueError("image_export_limit")
                        output.write(chunk)
                    else:
                        errors += len(chunk)
                        if errors > MAX_COMMAND_LOG:
                            raise ValueError("image_export_log_limit")
        if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
            raise ValueError("image_export_failed")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def cleanup_owned_setup(
    docker: str, run_id: str, *, preserve_verified_image: str | None = None
) -> list[str]:
    """Remove only setup resources labelled by this unpredictable probe run."""
    import re

    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("invalid_setup_owner")
    errors = []
    for kind in ("container", "image"):
        listing = [docker, "ps", "-a"] if kind == "container" else [docker, "image", "ls"]
        try:
            ids = set(
                command(
                    [
                        *listing,
                        "--no-trunc",
                        "--filter",
                        f"label={LABEL}={run_id}",
                        "--format",
                        "{{.ID}}",
                    ]
                ).splitlines()
            )
        except Exception as exc:
            errors.append(f"{kind}_inspection_unavailable:{type(exc).__name__}")
            continue
        for identity in ids:
            try:
                pattern = r"[0-9a-f]{64}" if kind == "container" else r"sha256:[0-9a-f]{64}"
                if re.fullmatch(pattern, identity) is None:
                    raise ValueError("invalid_owned_identity")
                prefix = [docker] if kind == "container" else [docker, "image"]
                data = json.loads(command([*prefix, "inspect", identity]))[0]
                if data.get("Config", {}).get("Labels", {}).get(LABEL) != run_id:
                    raise ValueError("setup_owner_mismatch")
                if kind == "image" and identity == preserve_verified_image:
                    continue
                command([*prefix, "rm", *(["-f"] if kind == "container" else []), identity])
            except Exception as exc:
                errors.append(f"{kind}_cleanup_unproven:{type(exc).__name__}")
    return errors


def prepare_candidate(
    source: Path, expected_digest: str, bootstrap_source: Path, root: Path, run_id: str
):
    """Offline operator setup; runtime execution itself never builds or pulls.

    Builds only from a verified prepared bundle and repository-owned bootstrap,
    then scans an export of a STOPPED exact-image container to close the
    verification-before-build mutation window for its payload files. The caller
    must remove this run's labelled image/setup container after diagnostic use.
    """
    import re
    import shutil

    from qualification.artifacts import ArtifactPolicy, capture_manifest, verify_manifest
    from qualification.renderer_contracts import ROLES
    from scripts.qualify_browser_native_sandbox import BROWSER_SHA256

    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("invalid_setup_owner")
    policy = ArtifactPolicy()
    source_roles = (("browser", "browser/chrome-headless-shell"), ("loader", "loader"))
    original = capture_manifest(source, source_roles, policy)
    verify_manifest(source, source_roles, policy, original, expected_digest)
    binary = next(item for item in original.files if item.path == "browser/chrome-headless-shell")
    if binary.sha256 != BROWSER_SHA256:
        raise ValueError("unqualified_browser_binary")
    build = root / "build"
    build.mkdir(mode=0o700)
    bundle = build / "bundle"
    shutil.copytree(source, bundle, symlinks=True)
    verify_manifest(bundle, source_roles, policy, original, expected_digest)
    # The old diagnostic Dockerfile is not used and is not a runtime file.
    (bundle / "Dockerfile").unlink()
    bootstrap = bundle / "browser-pipe"
    command(
        [
            "/usr/bin/gcc",
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(bootstrap),
            str(bootstrap_source),
        ],
        30,
    )
    bootstrap.chmod(0o755)
    manifest = capture_manifest(bundle, ROLES, policy)
    expected = expected_image_files(manifest, bootstrap)
    dockerfile = """FROM scratch
COPY bundle/browser /opt/browser
COPY bundle/libs /opt/libs
COPY bundle/loader /lib64/ld-linux-x86-64.so.2
COPY bundle/browser-pipe /browser-pipe
USER 65534:65534
ENV LD_LIBRARY_PATH=/opt/libs HOME=/tmp
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent PATH=/opt/browser:/opt/libs
ENTRYPOINT ["/browser-pipe"]
"""
    (build / "Dockerfile").write_text(dockerfile)
    command(
        [
            "/usr/bin/docker",
            "build",
            "--network=none",
            "--pull=false",
            "--label",
            f"{LABEL}={run_id}",
            "--iidfile",
            str(root / "image-id"),
            str(build),
        ],
        90,
    )
    image = (root / "image-id").read_text().strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
        raise ValueError("unbound_image_identity")
    cid = command(
        [
            "/usr/bin/docker",
            "create",
            "--name",
            "zelo-renderer-setup-" + run_id,
            "--label",
            f"{LABEL}={run_id}",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--restart=no",
            image,
        ]
    ).strip()
    info = json.loads(command(["/usr/bin/docker", "inspect", cid]))[0]
    if info["Image"] != image or info["State"]["Running"] or info["State"]["Pid"]:
        raise ValueError("setup_container_not_stopped")
    archive = root / "image-rootfs.tar"
    export_bounded("/usr/bin/docker", cid, archive)
    with archive.open("rb") as stream:
        payload_digest = verify_image_archive(stream, expected)
    command(["/usr/bin/docker", "rm", cid])
    receipt = {
        "image_identity": image,
        "manifest_digest": manifest.digest,
        "image_payload_digest": payload_digest,
        "expected_files": len(expected),
        "bootstrap_sha256": hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
        "bootstrap_source_sha256": hashlib.sha256(bootstrap_source.read_bytes()).hexdigest(),
        "proof": "actual stopped image filesystem matches expected file bytes and modes",
        "code_signing_or_independent_provenance": False,
    }
    (root / "image-attachment-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return image, bundle, manifest, receipt


async def exercise_renderer(image, bundle, manifest, root):
    """Use the actual lease, broker, private pipe, container and cleanup path.

    Only HTTP content delivery is a deterministic fixture; there is no external
    DNS or network request. Renderer/controller/daemon/lifecycle are actual.
    """
    import asyncio
    from dataclasses import asdict

    import httpx

    from app.api_contracts import ApiEndpointRegistry
    from qualification.admission import AdmissionController, AdmissionDenied
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

    registry = ApiEndpointRegistry.from_entries(["fixture=https://example.com"])
    bindings = (registry.bind("fixture"),)
    policy = Policy()
    browser_digest = next(
        x.sha256 for x in manifest.files if x.path == "browser/chrome-headless-shell"
    )
    contract = RendererContract(
        ExecutionPolicy(
            resources=ResourcePolicy(536870912, 128, 50000, 100000, 0, 33554432, 30),
            image_identity=image,
            browser_digest=browser_digest,
            controller_digest=CONTROLLER_DIGEST,
            broker_digest=policy.digest,
            renderer_policy_digest=RENDERER_DIGEST,
            approved_origins_digest=origins_digest(registry, bindings),
        ),
        manifest,
        build_profile(),
        PreparedImage(image, manifest.digest),
    )
    admission_root = root / "admission"
    admission_root.mkdir(mode=0o700)
    resources = root / "jobs"
    resources.mkdir(mode=0o700)
    admission = AdmissionController(admission_root)
    reports = []
    handles = []
    renderers = []
    requests = []

    class Resolver:
        async def resolve(self, host, port, deadline):
            assert host == "example.com" and port == 443
            return ("8.8.8.8",)

        async def aclose(self):
            pass

    class Stream(httpx.AsyncByteStream):
        def __init__(self, body):
            self.body = body

        async def __aiter__(self):
            yield self.body

    def job(content, media="text/html", handle_class=DockerHandle):
        body = content.encode()

        def handle_factory(*args):
            handle = handle_class(*args)
            handles.append(handle)
            return handle

        def session_factory(*args, **kwargs):
            def response(request):
                assert request.method == "GET" and request.url.host == "8.8.8.8"
                assert request.headers["host"] == "example.com"
                requests.append(str(request.url))
                return httpx.Response(200, headers={"Content-Type": media}, stream=Stream(body))

            return PageSession(
                *args,
                **kwargs,
                resolver=Resolver(),
                transport=httpx.MockTransport(response),
            )

        renderer = DocumentRenderer(
            admission,
            contract,
            bundle,
            resources,
            registry,
            bindings,
            policy=policy,
            session_factory=session_factory,
            handle_factory=handle_factory,
        )
        renderers.append(renderer)
        return renderer

    async def success(name, content, expected, media="text/html", truncated=False):
        renderer = job(content, media)
        result = await renderer.render("fixture", "/document")
        assert result.text == expected and result.truncated is truncated
        assert result.source_digest == hashlib.sha256(content.encode()).hexdigest()
        assert result.contract_digest == contract.digest and result.instruction_trust == "none"
        assert (admission_root / "active.marker").stat().st_size == 0
        reports.append(
            {
                "case": name,
                "passed": True,
                "text_bytes": len(result.text.encode()),
                "truncated": result.truncated,
                "cleanup_confirmed": True,
            }
        )

    try:
        await success(
            "actual broker-to-browser text; page script disabled",
            '<main>ZELO_COMPLETE_PATH_OK</main><script>document.body.textContent="SCRIPT_EXECUTED"</script>',
            "ZELO_COMPLETE_PATH_OK",
        )
        await success(
            "plain text stays data",
            "<script>not code</script> & literal",
            "<script>not code</script> & literal",
            "text/plain",
        )
        await success(
            "Chromium long-node preview is explicitly incomplete",
            "<main>" + "x" * 70000 + "</main>",
            "x" * 10000 + "\u2026",
            truncated=True,
        )
        await success(
            "aggregate text output remains bounded across full nodes",
            "<main>" + ("<span>" + "x" * 7000 + "</span>") * 10 + "</main>",
            "x" * 65536,
            truncated=True,
        )

        for name, document in (
            ("iframe navigation denied", '<iframe src="http://127.0.0.1:9/canary"></iframe>'),
            (
                "meta refresh denied",
                '<meta http-equiv="refresh" content="0;url=file:///etc/hosts">',
            ),
            ("subresource flood bounded", '<img src="http://127.0.0.1:9/canary">' * 200),
        ):
            renderer = job(document)
            denied = False
            try:
                result = await renderer.render("fixture", "/document")
                # An inert unsupported element may remain empty, but cannot yield
                # content from a network/file target or an unbounded event stream.
                assert result.text == ""
            except QualificationError:
                denied = True
            if renderer.owner._lifecycle is not None:
                await asyncio.wait_for(
                    asyncio.gather(renderer.owner._lifecycle, return_exceptions=True), 12
                )
            assert (admission_root / "active.marker").stat().st_size == 0
            reports.append(
                {
                    "case": name,
                    "passed": True,
                    "inert_or_denied": True,
                    "denied": denied,
                    "cleanup_confirmed": True,
                }
            )

        class LostAck(DockerHandle):
            async def _command(self, *args):
                data = await super()._command(*args)
                if args[0] == "create":
                    raise QualificationError("controlled_lost_create_ack")
                return data

        renderer = job("<main>lost ack</main>", handle_class=LostAck)
        try:
            await renderer.render("fixture", "/document")
            raise AssertionError("lost create acknowledgement incorrectly returned a result")
        except QualificationError:
            pass
        assert (admission_root / "active.marker").stat().st_size == 0
        reports.append(
            {
                "case": "lost create acknowledgement after actual daemon creation",
                "passed": True,
                "result_not_released": True,
                "cleanup_confirmed": True,
            }
        )

        held = asyncio.Event()

        class Held(DockerHandle):
            async def render(self, *args):
                result = await super().render(*args)
                held.set()
                await asyncio.Event().wait()  # Controlled cancellation after an actual render.
                return result

        renderer = job("<main>cancel before result release</main>", handle_class=Held)
        task = asyncio.create_task(renderer.render("fixture", "/document"))
        await asyncio.wait_for(held.wait(), 20)
        count = len(requests)
        other = job("second job must not start")
        try:
            await other.render("fixture", "/document")
            raise AssertionError("overlapping job admitted")
        except AdmissionDenied:
            pass
        assert len(requests) == count
        task.cancel()
        try:
            await task
            raise AssertionError("cancelled render returned evidence")
        except (asyncio.CancelledError, QualificationError):
            pass
        # Keep retained cleanup alive; cancellation never means resources are absent.
        if renderer.owner._lifecycle is not None:
            await asyncio.wait_for(
                asyncio.gather(renderer.owner._lifecycle, return_exceptions=True), 15
            )
        assert (admission_root / "active.marker").stat().st_size == 0
        reports.append(
            {
                "case": "exclusive admission and cancellation while actual browser is alive",
                "passed": True,
                "overlapping_fetch_denied": True,
                "cleanup_confirmed": True,
            }
        )
        await success(
            "readmission after confirmed cleanup", "<main>NEW_LEASE_OK</main>", "NEW_LEASE_OK"
        )

        for handle in handles:
            assert (
                handle._close is not None
                and handle._close.done()
                and handle._close.result() is True
            )
        assert json.loads(command(["/usr/bin/docker", "image", "inspect", image]))[0]["Id"] == image
        return {
            "cases": reports,
            "contract": asdict(contract.execution),
            "contract_digest": contract.digest,
            "prepared_image_preserved_by_runtime": True,
            "real_pipeline": True,
            "http_transport": "deterministic fixture",
            "requests": len(requests),
        }
    finally:
        # Expected job failures can still have confirmed cleanup. Preserve the
        # distinction and never let a failing diagnostic abandon live resources.
        await asyncio.gather(*(renderer.aclose() for renderer in renderers), return_exceptions=True)
        lifecycles = [
            renderer.owner._lifecycle
            for renderer in renderers
            if renderer.owner._lifecycle is not None
        ]
        if lifecycles:
            _, pending = await asyncio.wait(lifecycles, timeout=12)
            if pending:
                raise RuntimeError("diagnostic_lifecycle_cleanup_pending")
        if any(
            handle._close is None
            or not handle._close.done()
            or handle._close.cancelled()
            or handle._close.exception() is not None
            or handle._close.result() is not True
            for handle in handles
        ):
            raise RuntimeError("diagnostic_renderer_cleanup_unproven")


def main():
    import argparse
    import asyncio
    import tempfile
    from uuid import uuid4

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-owned-containers", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-digest", required=True)
    args = parser.parse_args()
    if not args.allow_owned_containers:
        parser.error("Explicit --allow-owned-containers required; no setup or browser was started")
    repo = Path(__file__).resolve().parents[1]
    base = repo / "runtime" / "browser-renderer"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="integration-", dir=base))
    run = uuid4().hex
    report = {
        "run_id": run,
        "status": "incomplete",
        "worker_qualified": False,
        "external_network_used": False,
    }
    failure = None
    try:
        image, bundle, manifest, receipt = prepare_candidate(
            args.bundle,
            args.expected_bundle_digest,
            repo / "qualification/fixtures/browser_pipe.c",
            root,
            run,
        )
        report["image_attachment"] = receipt
        report.update(asyncio.run(exercise_renderer(image, bundle, manifest, root)))
        report["status"] = "passed"
    except BaseException as exc:
        report["failure_type"] = type(exc).__name__
        failure = exc
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
        raise RuntimeError("renderer_probe_not_verified")


if __name__ == "__main__":
    main()
