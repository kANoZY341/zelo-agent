"""Offline ownership and invocation tests; every native launch is a fake."""

import ast
import asyncio
import hashlib
import os
from types import SimpleNamespace

import pytest

from qualification.admission import AdmissionController, AdmissionDenied
from qualification.media_origin_guest import GUEST_BOOTSTRAP, INPUTS, KEYRING, NEGATIVES, TOOL
from scripts import qualify_media_origin as q
from tests.test_media_origin import armor, release, status


def test_bootstrap_is_closed_literal_and_limit_before_access():
    compile(GUEST_BOOTSTRAP, "fixed-origin-bootstrap", "exec")
    assert GUEST_BOOTSTRAP.index("resource.setrlimit") < GUEST_BOOTSTRAP.index("import errno")
    assert GUEST_BOOTSTRAP.index("read(path,size,digest,mode)") < GUEST_BOOTSTRAP.index("os.execve")
    tree = ast.parse(GUEST_BOOTSTRAP)
    imports = [
        name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names
    ]
    assert set(imports) <= {
        "resource",
        "errno",
        "hashlib",
        "json",
        "os",
        "stat",
        "sys",
        "time",
        "socket",
    }
    execs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execve"
    ]
    assert len(execs) == 1
    assert ast.literal_eval(execs[0].args[0]) == "/opt/zelo-origin-gpgv"
    assert ast.literal_eval(execs[0].args[1]) == [
        "/opt/zelo-origin-gpgv",
        "--homedir",
        "/tmp/zelo-gnupg",
        "--keyring",
        "/opt/zelo-origin-keyring.gpg",
        "--log-file",
        "/dev/null",
        "--status-fd",
        "2",
        "--output",
        "-",
        "--",
        "/opt/zelo-origin.InRelease",
    ]
    assert "os.listdir('/proc/self/fd')" in GUEST_BOOTSTRAP
    assert "os.getresuid()!=(1000,1000,1000)" in GUEST_BOOTSTRAP


def test_frozen_identity_and_retention_ledger():
    assert TOOL == (
        "/usr/bin/gpgv",
        310416,
        "f14d026b9eae172c432e015bce227483293b4966f2f3fdcfa582f71d3dbb2ae8",
        0o755,
    )
    assert KEYRING == (
        "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
        3607,
        "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31",
        0o644,
    )
    assert [row["suite"] for row in INPUTS] == ["noble-updates", "noble", "noble-security"]
    assert sum(row["bytes"] for row in INPUTS) == 508102
    p = q.policy()
    assert p["processes"] == len(q.MATRIX) + len(INPUTS) + len(NEGATIVES) == 18
    # 36 channels, pending report, sentinel + three bad pins + one negative, two FIFOs.
    assert p["retained_ledger_entries"] == 2 * 18 + 1 + 5 + 2 == 44
    maximum = 4 * 524288 + 14 * 65536 + 18 * 16384 + 2097152 + 4 * 128 + 126125
    assert p["retained_ledger_bytes"] == maximum < 8388608
    assert p["attempts"] == 4
    assert p["admission_scope"] == "runtime/media-elf-metadata-admission"
    assert p["proof_scope"] == "runtime/media-origin-proofs/origin-proof-*"
    assert not p["aggregate_cgroup_verified"] and not p["runtime_authority"]


def test_mounts_bind_held_executable_key_and_input_before_readonly(tmp_path, monkeypatch):
    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda command: ["bwrap", "--remount-ro", "/", *command]
    )
    fds = dict(tool=100, keyring=101, input=102, bad_tool=103, bad_keyring=104, bad_input=105)
    argv = q._argv(fds, "input", tmp_path / "sentinel", "net:[1]", 0, -1)
    mount_pairs = [
        (argv[i + 1], argv[i + 2]) for i, value in enumerate(argv) if value == "--ro-bind"
    ]
    assert mount_pairs == [
        ("/proc/self/fd/100", "/opt/zelo-origin-gpgv"),
        ("/proc/self/fd/101", "/opt/zelo-origin-keyring.gpg"),
        ("/proc/self/fd/102", "/opt/zelo-origin.InRelease"),
    ]
    assert argv.index("/tmp/zelo-gnupg") < argv.index("--remount-ro")
    assert argv[1:7] == ["--cap-drop", "ALL", "--uid", "1000", "--gid", "1000"]
    for role in ("tool", "keyring", "input"):
        bad = q._argv(fds, role + "_pin", tmp_path / "sentinel", "net:[1]", 0, -1)
        assert f"/proc/self/fd/{fds['bad_' + role]}" in bad


def test_four_attempt_quota_counts_failed_partial_across_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(q.shared, "ROOT", tmp_path)
    (tmp_path / "runtime").mkdir(mode=0o700)
    base = tmp_path / "runtime/proofs"
    paths = [q._proof_directory(base) for _ in range(4)]
    (paths[0] / "partial").write_bytes(b"failed")
    (paths[1] / "policy.json").write_text('{"digest":"other-policy"}')
    with pytest.raises(q.QualificationRefused, match="attempt_limit"):
        q._proof_directory(base)
    assert len(list(base.iterdir())) == 4
    assert (paths[0] / "partial").read_bytes() == b"failed"


@pytest.mark.parametrize("kind", ["name", "mode", "file", "symlink"])
def test_malformed_quota_state_refuses_without_repair(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(q.shared, "ROOT", tmp_path)
    (tmp_path / "runtime").mkdir(mode=0o700)
    base = tmp_path / "runtime/proofs"
    base.mkdir(mode=0o700)
    entry = base / ("unrelated" if kind == "name" else "origin-proof-old")
    if kind == "file":
        entry.write_bytes(b"x")
    elif kind == "symlink":
        entry.symlink_to(tmp_path)
    else:
        entry.mkdir(mode=0o755 if kind == "mode" else 0o700)
    with pytest.raises(ValueError):
        q._proof_directory(base)
    assert entry.exists() and len(list(base.iterdir())) == 1


def test_origin_uses_same_capacity_one_as_metadata(tmp_path):
    path = tmp_path / q.ADMISSION
    path.mkdir(parents=True, mode=0o700)
    lease = AdmissionController(path).acquire("a" * 64)
    try:
        with pytest.raises(AdmissionDenied):
            AdmissionController(path).acquire(q.shared._digest(q.policy()))
    finally:
        lease.finish(cleanup_confirmed=True)


@pytest.mark.parametrize("kind", ["size", "digest"])
def test_stable_descriptor_refuses_mismatched_tool_identity(tmp_path, kind):
    path = tmp_path / "tool"
    path.write_bytes(b"same-trusted-tool")
    path.chmod(0o644)
    state = q._State()
    fd = q._open(state, path)
    try:
        with pytest.raises(q.QualificationRefused, match="source_file|source_pin"):
            q._read_fd(
                fd,
                524288,
                "0" * 64 if kind == "digest" else hashlib.sha256(path.read_bytes()).hexdigest(),
                1 if kind == "size" else len(path.read_bytes()),
            )
    finally:
        assert state.close_all()


def test_failed_helpers_conservatively_fence_and_close_once(tmp_path, monkeypatch):
    state = q._State()
    fd = q._open(state, tmp_path, directory=True)
    closes = []
    original = os.close

    def close(candidate):
        closes.append(candidate)
        original(candidate)

    monkeypatch.setattr(q.helpers.os, "close", close)

    def fail():
        raise ValueError("unknown hidden helper close")

    with pytest.raises(ValueError):
        q._helper(state, fail)
    lease = SimpleNamespace(finish=lambda **kwargs: closes.append(kwargs))
    assert not q._release(state, lease)
    assert closes == [fd, {"cleanup_confirmed": False}]


def test_unsettled_process_retains_support_descriptor(tmp_path):
    state = q._State()
    fd = q._open(state, tmp_path, directory=True)
    state.observations.append(SimpleNamespace(cleanup_confirmed=False))
    lease = SimpleNamespace(finish=lambda **kwargs: None)
    assert not q._release(state, lease)
    assert os.fstat(fd)
    assert state in q.helpers._RETAINED
    # Explicit test cleanup only; no production recovery or stale-fence clearing.
    state.observations[0].cleanup_confirmed = True
    assert state.close_all()
    q.helpers._RETAINED.remove(state)


@pytest.mark.parametrize("settled", [False, True])
def test_native_acceptance_requires_observed_settlement(tmp_path, monkeypatch, settled):
    async def launch(*args, **kwargs):
        assert len(state.observations) == 1
        assert kwargs["observation"] is state.observations[0]
        if settled:
            kwargs["observation"]._no_child()
        assert kwargs["stdout_limit"] == 524288
        assert kwargs.get("stdin") is None
        return SimpleNamespace(exit_code=0, stdout=release(), stderr=status())

    monkeypatch.setattr(q, "run_process", launch)
    monkeypatch.setattr(q, "_argv", lambda *args: ["fake"])
    monkeypatch.setattr(q, "INPUTS", ({"suite": "noble"},))
    state = q._State()
    store = q._Store(tmp_path, state)
    call = q._native(state, store, {}, "input", armor(), "input", 0, q.time.monotonic() + 30)
    if settled:
        record = asyncio.run(call)
        assert record["passed"] and record["evidence"]["release"]["suite"] == "noble"
        assert len(list(tmp_path.iterdir())) == 2
    else:
        with pytest.raises(q.QualificationRefused, match="cleanup_unproven"):
            asyncio.run(call)
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("code", [1, 2, -9])
def test_good_status_does_not_override_failed_exit(tmp_path, monkeypatch, code):
    async def launch(*args, **kwargs):
        kwargs["observation"]._no_child()
        return SimpleNamespace(exit_code=code, stdout=release(), stderr=status())

    monkeypatch.setattr(q, "run_process", launch)
    monkeypatch.setattr(q, "_argv", lambda *args: ["fake"])
    monkeypatch.setattr(q, "INPUTS", ({"suite": "noble"},))
    state = q._State()
    with pytest.raises((q.QualificationRefused, q.origin.OriginError)):
        asyncio.run(
            q._native(
                state,
                q._Store(tmp_path, state),
                {},
                "input",
                armor(),
                "input",
                0,
                q.time.monotonic() + 30,
            )
        )
    assert (tmp_path / "input-stdout.bin").read_bytes() == release()


@pytest.mark.parametrize("error", ["output", "timeout", "cancel", "cancel_repeated"])
def test_error_controls_require_specific_owned_result(tmp_path, monkeypatch, error):
    expected = {
        "output": "process_output_limit",
        "timeout": "process_timeout",
        "cancel": "operator_cancelled",
        "cancel_repeated": "operator_repeated_cancelled",
    }

    async def launch(*args, **kwargs):
        kwargs["observation"]._no_child()
        return None

    async def own(task, **kwargs):
        await task
        return None, expected[error]

    async def repeated(task, *args):
        return await own(task)

    monkeypatch.setattr(q, "run_process", launch)
    monkeypatch.setattr(q, "_argv", lambda *args: ["fake"])
    monkeypatch.setattr(q.shared, "_own_operation", own)
    monkeypatch.setattr(q, "_repeated_cancel", repeated)
    state = q._State()
    try:
        record = asyncio.run(
            q._native(
                state,
                q._Store(tmp_path, state),
                {},
                error,
                b"input",
                error,
                0,
                q.time.monotonic() + 30,
            )
        )
        assert record["passed"]
        assert record["cancel_requests"] == (
            2 if error == "cancel_repeated" else 1 if error == "cancel" else 0
        )
    finally:
        assert state.close_all()


def test_whole_job_admission_and_release_order(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "ROOT", tmp_path)
    monkeypatch.setattr(q.shared, "ROOT", tmp_path)
    (tmp_path / "runtime").mkdir(mode=0o700)
    monkeypatch.setattr(q.os, "getresuid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(q.os, "getresgid", lambda: (1000, 1000, 1000))
    events = []
    original_save = q._Store.save
    original_read = q._read_fd

    def host_file(state, *pin):
        events.append("source")
        assert (tmp_path / q.ADMISSION).exists()
        return q._open(state, tmp_path, directory=True), armor()

    async def native(state, store, fds, case, raw, label, index, deadline):
        assert not any(event == "release" for event in events)
        state.observations.append(SimpleNamespace(cleanup_confirmed=True))
        return {"case": case, "passed": True, "cleanup_confirmed": True}

    def save(self, name, value):
        assert "release" not in events
        events.append(name)
        return original_save(self, name, value)

    class Lease:
        def finish(self, *, cleanup_confirmed):
            assert cleanup_confirmed
            assert events[-1] == "cases.json"
            events.append("release")

    class Admission:
        def __init__(self, path):
            assert path == tmp_path / q.ADMISSION

        def acquire(self, digest):
            assert not events
            events.append("admission")
            return Lease()

    monkeypatch.setattr(q, "AdmissionController", Admission)
    monkeypatch.setattr(q, "_host_file", host_file)
    monkeypatch.setattr(q, "_native", native)
    monkeypatch.setattr(q, "_negative", lambda *args: armor())
    monkeypatch.setattr(q._Store, "save", save)
    monkeypatch.setattr(q, "_read_fd", lambda *args: b"checked")
    result = asyncio.run(q._qualify())
    assert result["cases"] == 18 and result["admission_release_confirmed"]
    assert events[0] == "admission" and events[-1] == "release"
    with open(result["proof"], "rb") as proof:
        assert hashlib.sha256(proof.read()).hexdigest() == result["proof_sha256"]
    # Avoid leaking monkeypatched shared symbols into other tests.
    assert original_read is q.helpers._read_fd


def probe_segment():
    # Run only the fixed pure probe block with fake filesystem operations.
    # Never execute the guest bootstrap or any real file access.
    start = GUEST_BOOTSTRAP.index("if case in ('network','write','host_file','imports','memory'):")
    end = GUEST_BOOTSTRAP.index("# No caller code", start)
    return compile(GUEST_BOOTSTRAP[start:end], "isolated-offline-probe-control", "exec")


@pytest.mark.parametrize(
    "case,number,expected",
    [
        ("write", 30, True),
        ("write", 27, False),
        ("write", 13, False),
        ("write", 5, False),
        ("host_file", 2, True),
        ("host_file", 13, False),
        ("host_file", 5, False),
    ],
)
def test_probe_requires_creation_erofs_or_specific_absence(case, number, expected):
    import errno
    import json

    observed = []

    def opening(*args):
        raise OSError(number, "synthetic")

    fake = SimpleNamespace(
        open=opening,
        close=lambda fd: pytest.fail("nothing was opened"),
        O_RDONLY=0,
        O_WRONLY=1,
        O_CREAT=2,
        O_EXCL=4,
        O_CLOEXEC=8,
        O_NOFOLLOW=16,
        O_NONBLOCK=32,
    )
    with pytest.raises(SystemExit) as stopped:
        exec(
            probe_segment(),
            dict(
                case=case,
                os=fake,
                sys=SimpleNamespace(argv=["program", case, "/host/sentinel"]),
                errno=errno,
                json=json,
                print=lambda value: observed.append(json.loads(value)),
            ),
        )
    assert stopped.value.code == (0 if expected else 28)
    assert observed == [{"status": "probe", "case": case, "enforced": expected}]


def test_creation_success_cannot_pass_via_later_efbig():
    import errno
    import json

    calls = []
    observed = []

    class LateFileSizeFailure:
        def __enter__(self):
            calls.append("buffered-created")
            return self

        def __exit__(self, *args):
            return False

        def write(self, value):
            raise OSError(errno.EFBIG, "zero FSIZE after creation")

    fake = SimpleNamespace(
        open=lambda *args: calls.append("created") or 42,
        close=lambda fd: calls.append(("closed", fd)),
        O_WRONLY=1,
        O_CREAT=2,
        O_EXCL=4,
        O_CLOEXEC=8,
    )
    with pytest.raises(SystemExit) as stopped:
        exec(
            probe_segment(),
            dict(
                case="write",
                os=fake,
                errno=errno,
                json=json,
                open=lambda *args: LateFileSizeFailure(),
                print=lambda value: observed.append(json.loads(value)),
            ),
        )
    assert stopped.value.code == 28
    assert observed == [{"status": "probe", "case": "write", "enforced": False}]
    assert calls == ["created", ("closed", 42)]


@pytest.mark.parametrize(
    "stderr",
    [
        b"",
        b"[GNUPG:] FAILURE unrelated 2\n",
        b"embedded [GNUPG:] BADSIG 871920D1991BC93C fake\n",
    ],
)
def test_negative_native_exit_two_alone_never_passes(tmp_path, monkeypatch, stderr):
    async def launch(*args, **kwargs):
        kwargs["observation"]._no_child()
        return SimpleNamespace(exit_code=2, stdout=b"", stderr=stderr)

    monkeypatch.setattr(q, "run_process", launch)
    monkeypatch.setattr(q, "_argv", lambda *args: ["fake"])
    state = q._State()
    with pytest.raises(q.QualificationRefused, match="case_failed"):
        asyncio.run(
            q._native(
                state,
                q._Store(tmp_path, state),
                {},
                "altered_text",
                b"input",
                "altered",
                0,
                q.time.monotonic() + 30,
            )
        )


def identity_segment():
    start = GUEST_BOOTSTRAP.index("def identity_refused(")
    end = GUEST_BOOTSTRAP.index("def read(path", start)
    return compile(GUEST_BOOTSTRAP[start:end], "isolated-identity-control", "exec")


@pytest.mark.parametrize(
    "uid,gid,cap,mapping,expected",
    [
        (1000, 1000, 0, b"1000 0 1\n", None),
        (1000, 1000, 0, b"1000 1000 1\n", "uid_map"),
        (1000, 1000, 0, b"0 0 1\n", "uid_map"),
        (1000, 1000, 0, b"1000 0 2\n", "uid_map"),
        (1000, 1000, 0, b"1000 0 1\n0 1 1\n", "uid_map"),
        (0, 1000, 0, b"1000 0 1\n", "ids"),
        (1000, 0, 0, b"1000 0 1\n", "ids"),
        (1000, 1000, 1, b"1000 0 1\n", "capabilities"),
    ],
)
def test_identity_mapping_is_exact_two_level_not_host_root(uid, gid, cap, mapping, expected):
    import io
    import json

    caps = b"".join(
        name.encode() + f":\t{cap:016x}\n".encode()
        for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    )
    observed = []

    def opening(path, mode):
        assert mode == "rb"
        assert path in ("/proc/self/status", "/proc/self/uid_map", "/proc/self/gid_map")
        return io.BytesIO(caps if path.endswith("/status") else mapping)

    scope = dict(
        os=SimpleNamespace(getresuid=lambda: (uid,) * 3, getresgid=lambda: (gid,) * 3),
        json=json,
        open=opening,
        print=lambda value: observed.append(json.loads(value)),
    )
    if expected is None:
        exec(identity_segment(), scope)
        assert not observed
    else:
        with pytest.raises(SystemExit) as stopped:
            exec(identity_segment(), scope)
        assert stopped.value.code == 22
        assert observed[0]["status"] == "identity_refused"
        assert observed[0]["check"] == expected
