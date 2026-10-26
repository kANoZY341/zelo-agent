"""Fake-only owned native-reader Docker lifecycle tests."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from qualification import media_reader_container as m
from qualification import media_reader_fixture as fixture
from qualification import media_reader_seccomp as seccomp
from qualification.admission import AdmissionController, AdmissionDenied
from qualification.dns import QualificationError
from qualification.media_reader_process import CLI, DockerReaderHandle
from qualification.resource_owner import LeaseResourceOwner


def run(coro):
    return asyncio.run(coro)


def digest():
    return hashlib.sha256(b"fixed-media-reader-policy").hexdigest()


def image_id():
    return "sha256:" + "a" * 64


def build_identity():
    return "c" * 64


def image_record(identity="e" * 64):
    return {
        "Id": image_id(),
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {
            "Labels": {m.LEASE_LABEL: identity, m.BUNDLE_LABEL: build_identity()},
            "Entrypoint": None,
            "Cmd": None,
            "Env": ["PATH=/nonexistent"],
            "User": "",
            "WorkingDir": "",
            "Volumes": None,
            "ExposedPorts": None,
            "OnBuild": None,
            "Healthcheck": None,
        },
    }


def container_record(name, identity, cid, *, drift=None, running=False):
    profile = json.dumps(json.loads(seccomp.profile_bytes()), separators=(",", ":"))
    value = {
        "Id": cid,
        "Name": "/" + name,
        "Image": image_id(),
        "Config": {
            "Labels": {m.LEASE_LABEL: identity, m.BUNDLE_LABEL: build_identity()},
            "User": m.USER,
            "Entrypoint": [m.ENTRYPOINT],
            "Cmd": list(m.COMMAND),
            "Env": list(m.ENV),
            "OpenStdin": True,
            "Tty": False,
            "WorkingDir": "/",
            "Volumes": None,
            "ExposedPorts": None,
            "Healthcheck": None,
            "StopTimeout": 2,
        },
        "HostConfig": {
            "Memory": m.MEMORY,
            "MemorySwap": m.MEMORY,
            "PidsLimit": m.PIDS,
            "CpuPeriod": m.CPU_PERIOD,
            "CpuQuota": m.CPU_QUOTA,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "ShmSize": m.SHM,
            "Runtime": m.RUNTIME,
            "Init": False,
            "OomKillDisable": False,
            "UsernsMode": "",
            "Ulimits": [{"Name": "nofile", "Hard": m.NOFILE, "Soft": m.NOFILE}],
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "Binds": None,
            "Devices": None,
            "DeviceRequests": None,
            "PortBindings": None,
            "Links": None,
            "ExtraHosts": None,
            "VolumesFrom": None,
            "Dns": None,
            "DnsOptions": None,
            "DnsSearch": None,
            "GroupAdd": None,
            "Sysctls": None,
            "DeviceCgroupRules": None,
            "CgroupParent": "",
            "StorageOpt": None,
            "Tmpfs": None,
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {"Type": "none"},
            "PublishAllPorts": False,
            "AutoRemove": False,
            "SecurityOpt": ["no-new-privileges", "seccomp=" + profile],
        },
        "Mounts": [],
        "State": {"Running": running, "Pid": 43210 if running else 0},
    }
    if drift == "network":
        value["HostConfig"]["NetworkMode"] = "bridge"
    elif drift == "user":
        value["Config"]["User"] = "0:0"
    elif drift == "capability":
        value["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    return value


class Input:
    def __init__(self, process):
        self.process = process
        self.raw = bytearray()
        self.closed = False

    def write(self, raw):
        if self.closed:
            raise RuntimeError("closed")
        self.raw.extend(raw)

    async def drain(self):
        if self.process.mode == "stdin_error":
            raise BrokenPipeError
        await asyncio.sleep(0)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.dead.is_set():
            return
        if self.process.mode == "wrong_stdout":
            output = b"wrong"
        else:
            output = fixture.expected_tar()
        if self.process.mode == "stdout_flood":
            output = b"x" * (fixture.STDOUT_BYTES + 1)
        error = b"problem" if self.process.mode == "stderr" else b""
        self.process.stdout.feed_data(output)
        self.process.stdout.feed_eof()
        self.process.stderr.feed_data(error)
        self.process.stderr.feed_eof()
        self.process.returncode = 1 if self.process.mode == "exit" else 0
        self.process.dead.set()

    async def wait_closed(self):
        await asyncio.sleep(0)


class Process:
    def __init__(self, output=b"", *, attached=False, code=0, mode="success"):
        self.stdout = asyncio.StreamReader(limit=max(fixture.STDOUT_BYTES + 2, 131073))
        self.stderr = asyncio.StreamReader(limit=max(fixture.STDERR_BYTES + 2, 131073))
        self.dead = asyncio.Event()
        self.mode = mode
        self.stdin = Input(self) if attached else None
        self.returncode = None if attached else code
        if not attached:
            self.stdout.feed_data(output)
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.dead.set()

    def kill(self):
        if self.returncode is None:
            self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.dead.set()

    async def wait(self):
        await self.dead.wait()
        return self.returncode


class Daemon:
    def __init__(self, jobs, *, failure=None, pause=None, result_mode="success", drift=None):
        self.jobs = jobs
        self.failure = failure
        self.pause = pause
        self.result_mode = result_mode
        self.drift = drift
        self.exists = False
        self.commands = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.name = self.identity = None
        self.cid = "d" * 64
        self.attached = None

    async def spawn(self, *args, **kwargs):
        assert args[: len(CLI)] == CLI
        assert set(kwargs["env"]) == {"DOCKER_CONFIG", "GOMAXPROCS"}
        assert kwargs["env"]["GOMAXPROCS"] == "1"
        case = Path(kwargs["env"]["DOCKER_CONFIG"])
        assert case.parent == self.jobs and case.name.startswith("native-reader-")
        args = args[len(CLI) :]
        self.commands.append(args)
        stage = args[0] if args[0] in ("create", "start") else " ".join(args[:2])
        if stage == "create":
            self.exists = True
            self.name = args[args.index("--name") + 1]
            label = args[args.index("--label") + 1]
            self.identity = label.split("=", 1)[1]
        if stage == self.pause:
            self.entered.set()
            await self.release.wait()
        if stage == self.failure:
            return Process(code=1)
        if stage == "image inspect":
            return Process(json.dumps([image_record(self.identity)]).encode())
        if stage == "container inspect":
            return Process(
                json.dumps(
                    [
                        container_record(
                            self.name,
                            self.identity,
                            self.cid,
                            drift=self.drift,
                            running=self.attached is not None and self.attached.returncode is None,
                        )
                    ]
                ).encode()
            )
        if stage == "container ls":
            return Process((self.cid + "\n").encode() if self.exists else b"")
        if stage == "container rm":
            self.exists = False
            return Process()
        if stage == "start":
            self.attached = Process(attached=True, mode=self.result_mode)
            return self.attached
        return Process(self.cid.encode())


@pytest.fixture
def setup(tmp_path):
    admission = tmp_path / "admission"
    admission.mkdir(mode=0o700)
    jobs = tmp_path / "jobs"
    jobs.mkdir(mode=0o700)
    return admission, jobs


async def exercise(setup, daemon):
    admission, jobs = setup
    owner = LeaseResourceOwner(AdmissionController(admission), digest(), 2)
    handles = []

    async def factory(lease):
        daemon.identity = lease.identity
        handle = DockerReaderHandle(
            lease,
            m.ReaderContainerContract(image_id(), build_identity()),
            jobs,
            seccomp.profile_bytes(),
            spawn=daemon.spawn,
        )
        handles.append(handle)
        return handle

    result = await owner.run(
        factory,
        lambda handle: handle.stream(fixture.fixture("positive"), owner.deadline),
    )
    return owner, handles[0], result


def test_success_result_only_after_cleanup(setup):
    async def case():
        daemon = Daemon(setup[1])
        owner, handle, result = await exercise(setup, daemon)
        assert owner.cleanup_confirmed is True
        assert not daemon.exists
        assert daemon.attached.returncode == 0
        assert (setup[0] / "active.marker").read_bytes() == b""
        assert not list(setup[1].glob("native-reader-*"))
        accepted = fixture.validate_result(
            "positive",
            result.stdout,
            result.stderr,
            result.exit_code,
            owner.cleanup_confirmed,
        )
        assert accepted["tar_sha256"] == hashlib.sha256(fixture.expected_tar()).hexdigest()
        assert handle.name.startswith("zelo-media-reader-")
        assert len(handle.identity) == 64
        assert not any(command[0] in ("build", "pull") for command in daemon.commands)
        assert not any(command[:2] == ("image", "rm") for command in daemon.commands)

    run(case())


def test_wrong_input_refused_before_docker(setup):
    async def case():
        admission, jobs = setup
        daemon = Daemon(jobs)
        owner = LeaseResourceOwner(AdmissionController(admission), digest(), 2)

        async def factory(lease):
            return DockerReaderHandle(
                lease,
                m.ReaderContainerContract(image_id(), build_identity()),
                jobs,
                seccomp.profile_bytes(),
                spawn=daemon.spawn,
            )

        with pytest.raises(QualificationError, match="owner_failed") as caught:
            await owner.run(factory, lambda handle: handle.stream(b"wrong", owner.deadline))
        assert isinstance(caught.value.__cause__, QualificationError)
        assert str(caught.value.__cause__) == "reader_input_identity"
        assert not daemon.commands and not daemon.exists
        assert (admission / "active.marker").read_bytes() == b""

    run(case())


@pytest.mark.parametrize("stage", ["image inspect", "create", "container inspect", "start"])
def test_stage_failure_cleanup(setup, stage):
    async def case():
        daemon = Daemon(setup[1], failure=stage)
        with pytest.raises(QualificationError):
            await exercise(setup, daemon)
        if stage == "container inspect":
            assert daemon.exists
            with pytest.raises(AdmissionDenied, match="fenced"):
                AdmissionController(setup[0]).acquire("e" * 64)
        else:
            assert not daemon.exists
            assert (setup[0] / "active.marker").read_bytes() == b""

    run(case())


@pytest.mark.parametrize("drift", ["network", "user", "capability"])
def test_container_drift_blocks_start_and_cleans(setup, drift):
    async def case():
        daemon = Daemon(setup[1], drift=drift)
        with pytest.raises(QualificationError):
            await exercise(setup, daemon)
        assert not any(command[0] == "start" for command in daemon.commands)
        assert not daemon.exists

    run(case())


@pytest.mark.parametrize("mode", ["wrong_stdout", "stderr", "exit"])
def test_bad_process_result_not_accepted(setup, mode):
    async def case():
        daemon = Daemon(setup[1], result_mode=mode)
        owner, _, result = await exercise(setup, daemon)
        assert owner.cleanup_confirmed is True
        with pytest.raises(fixture.Refused):
            fixture.validate_result(
                "positive",
                result.stdout,
                result.stderr,
                result.exit_code,
                owner.cleanup_confirmed,
            )

    run(case())


def test_stdout_flood_is_terminal_and_cleanup_confirmed(setup):
    async def case():
        daemon = Daemon(setup[1], result_mode="stdout_flood")
        with pytest.raises(QualificationError, match="owner_failed") as caught:
            await exercise(setup, daemon)
        assert isinstance(caught.value.__cause__, QualificationError)
        assert "output_limit" in str(caught.value.__cause__)
        assert not daemon.exists
        assert daemon.attached.returncode is not None
        assert (setup[0] / "active.marker").read_bytes() == b""

    run(case())


@pytest.mark.parametrize("stage", ["create", "start", "container rm"])
def test_repeated_cancellation_settles_owned_container(setup, stage):
    async def case():
        daemon = Daemon(setup[1], pause=stage)
        task = asyncio.create_task(exercise(setup, daemon))
        await asyncio.wait_for(daemon.entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(AdmissionDenied):
            AdmissionController(setup[0]).acquire("e" * 64)
        daemon.release.set()
        with pytest.raises((asyncio.CancelledError, QualificationError)):
            await task
        assert not daemon.exists
        assert (setup[0] / "active.marker").read_bytes() == b""

    run(case())


def test_lost_create_acknowledgement_fences_if_discovery_cannot_prove_absence(setup):
    async def case():
        daemon = Daemon(setup[1], failure="create")
        original = daemon.spawn

        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            stripped = args[len(CLI) :]
            if stripped and stripped[0] == "create":
                daemon.exists = False
            return child

        daemon.spawn = spawn
        with pytest.raises(QualificationError):
            await exercise(setup, daemon)
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(setup[0]).acquire("e" * 64)

    run(case())


def test_cleanup_remove_failure_fences(setup):
    async def case():
        daemon = Daemon(setup[1], failure="container rm")
        with pytest.raises(QualificationError):
            await exercise(setup, daemon)
        assert daemon.exists
        with pytest.raises(AdmissionDenied, match="fenced"):
            AdmissionController(setup[0]).acquire("e" * 64)

    run(case())


def test_profile_drift_after_create_blocks_start(setup):
    async def case():
        daemon = Daemon(setup[1])
        original = daemon.spawn

        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            stripped = args[len(CLI) :]
            if stripped and stripped[0] == "create":
                next(setup[1].glob("native-reader-*/seccomp.json")).write_bytes(b"{}")
            return child

        daemon.spawn = spawn
        with pytest.raises(QualificationError, match="owner_failed") as caught:
            await exercise(setup, daemon)
        assert isinstance(caught.value.__cause__, QualificationError)
        assert str(caught.value.__cause__) == "reader_profile_drift"
        assert not any(command[0] == "start" for command in daemon.commands)
        assert not daemon.exists

    run(case())


def test_handle_is_one_shot(setup):
    async def case():
        admission, jobs = setup
        daemon = Daemon(jobs)
        lease = AdmissionController(admission).acquire(digest())
        daemon.identity = lease.identity
        handle = DockerReaderHandle(
            lease,
            m.ReaderContainerContract(image_id(), build_identity()),
            jobs,
            seccomp.profile_bytes(),
            spawn=daemon.spawn,
        )
        deadline = asyncio.get_running_loop().time() + 1
        first = await handle.stream(fixture.fixture("positive"), deadline)
        assert first.exit_code == 0
        with pytest.raises(QualificationError, match="reader_terminal"):
            await handle.stream(fixture.fixture("positive"), deadline)
        assert await handle.stop_and_confirm_cleanup()
        lease.finish(cleanup_confirmed=True)

    run(case())


def test_cli_environment_does_not_inherit_home_or_docker_context(setup, monkeypatch):
    async def case():
        monkeypatch.setenv("HOME", "/secret")
        monkeypatch.setenv("DOCKER_HOST", "tcp://bad")
        monkeypatch.setenv("DOCKER_CONTEXT", "bad")
        daemon = Daemon(setup[1])
        await exercise(setup, daemon)

    run(case())


def test_no_product_imports_of_native_reader_modules():
    names = {
        "media_reader_container",
        "media_reader_fixture",
        "media_reader_process",
        "media_reader_seccomp",
    }
    imported = []
    for path in Path("app").rglob("*.py"):
        text = path.read_text()
        if any(name in text for name in names):
            imported.append(path)
    assert imported == []


def test_runtime_probe_runs_before_stdin_and_sees_running_container(setup):
    async def case():
        admission, jobs = setup
        daemon = Daemon(jobs)
        owner = LeaseResourceOwner(AdmissionController(admission), digest(), 2)
        seen = []

        async def probe(data):
            seen.append(
                (data["State"]["Running"], data["State"]["Pid"], bytes(daemon.attached.stdin.raw))
            )
            assert data["State"]["Running"] is True
            assert data["State"]["Pid"] == 43210
            assert daemon.attached is not None
            assert daemon.attached.stdin.raw == b""

        async def factory(lease):
            daemon.identity = lease.identity
            return DockerReaderHandle(
                lease,
                m.ReaderContainerContract(image_id(), build_identity()),
                jobs,
                seccomp.profile_bytes(),
                spawn=daemon.spawn,
                runtime_probe=probe,
            )

        result = await owner.run(
            factory,
            lambda handle: handle.stream(fixture.fixture("positive"), owner.deadline),
        )
        assert result.exit_code == 0
        assert seen == [(True, 43210, b"")]
        assert owner.cleanup_confirmed is True

    run(case())
