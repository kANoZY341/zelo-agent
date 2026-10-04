"""Offline ownership and invocation tests; every native launch is a fake."""

import ast
import asyncio
import hashlib
import os
import stat
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
    assert p["declaration_path_component_pattern"] == r"[A-Za-z0-9_+@.-]{1,128}\Z"
    assert p["declaration_size_max"] == 999999999999
    assert p["selected_raw_index_bytes"] == 134217728
    assert p["selected_compressed_index_bytes"] == 33554432
    assert p["attempts"] == 4
    assert p["admission_scope"] == "runtime/media-elf-metadata-admission"
    assert p["proof_scope"] == "runtime/media-origin-proofs/origin-proof-*"
    assert not p["aggregate_cgroup_verified"] and not p["runtime_authority"]


def test_mounts_copy_selected_held_descriptors_before_readonly(tmp_path, monkeypatch):
    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda command: ["bwrap", "--remount-ro", "/", *command]
    )
    fds = dict(tool=100, keyring=101, input=102, bad_tool=103, bad_keyring=104, bad_input=105)
    argv = q._argv(fds, "input", tmp_path / "sentinel", "net:[1]", 0, None)
    mount_pairs = [
        (argv[i + 1], argv[i + 2]) for i, value in enumerate(argv) if value == "--ro-bind-data"
    ]
    assert mount_pairs == [
        ("100", "/opt/zelo-origin-gpgv"),
        ("101", "/opt/zelo-origin-keyring.gpg"),
        ("102", "/opt/zelo-origin.InRelease"),
    ]
    assert argv.index("/tmp/zelo-gnupg") < argv.index("--remount-ro")
    assert argv[1:7] == ["--cap-drop", "ALL", "--uid", "1000", "--gid", "1000"]
    for role in ("tool", "keyring", "input"):
        bad = q._argv(fds, role + "_pin", tmp_path / "sentinel", "net:[1]", 0, None)
        assert str(fds["bad_" + role]) in bad


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
    monkeypatch.setattr(q, "_prepare_fds", lambda *args: ())
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
    monkeypatch.setattr(q, "_prepare_fds", lambda *args: ())
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
    monkeypatch.setattr(q, "_prepare_fds", lambda *args: ())
    monkeypatch.setattr(q.shared, "_own_operation", own)
    monkeypatch.setattr(q, "_repeated_cancel", repeated)
    original_fstat = os.fstat

    def operator_fstat(fd):
        info = original_fstat(fd)
        if stat.S_ISFIFO(info.st_mode):
            # Model the fixed operator identity; CI may run as a different UID.
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_uid=1000,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
            )
        return info

    monkeypatch.setattr(q.os, "fstat", operator_fstat)
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


@pytest.fixture
def origin_job(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "ROOT", tmp_path)
    monkeypatch.setattr(q.shared, "ROOT", tmp_path)
    (tmp_path / "runtime").mkdir(mode=0o700)
    monkeypatch.setattr(q.os, "getresuid", lambda: (1000, 1000, 1000))
    monkeypatch.setattr(q.os, "getresgid", lambda: (1000, 1000, 1000))
    events = []
    original_save = q._Store.save

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
    return events


def test_whole_job_admission_and_release_order(origin_job):
    events = origin_job
    result = asyncio.run(q._qualify())
    assert result["cases"] == 18 and result["admission_release_confirmed"]
    assert events[0] == "admission" and events[-1] == "release"
    with open(result["proof"], "rb") as proof:
        assert hashlib.sha256(proof.read()).hexdigest() == result["proof_sha256"]


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
    monkeypatch.setattr(q, "_prepare_fds", lambda *args: ())
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


def test_attachment_modes_and_readiness_identity_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda command: ["bwrap", "--remount-ro", "/", *command]
    )
    fds = dict(tool=100, keyring=101, input=102, bad_tool=103, bad_keyring=104, bad_input=105)
    ready = (tmp_path / "cancel-ready.fifo", 17, 42)
    argv = q._argv(fds, "cancel", tmp_path / "sentinel", "net:[1]", 0, ready)
    assert [argv[i + 1 : i + 5] for i, value in enumerate(argv) if value == "--perms"] == [
        ["0755", "--ro-bind-data", "100", "/opt/zelo-origin-gpgv"],
        ["0644", "--ro-bind-data", "101", "/opt/zelo-origin-keyring.gpg"],
        ["0644", "--ro-bind-data", "102", "/opt/zelo-origin.InRelease"],
    ]
    assert "--bind-fd" not in argv and "--ro-bind" not in argv
    assert not any(value.startswith("/proc/self/fd/") for value in argv)
    point = argv.index("--bind")
    assert argv[point + 1 : point + 3] == [str(ready[0]), "/opt/zelo-origin-ready"]
    assert argv[-2:] == ["17", "42"]
    assert (
        q.policy()["copied_bytes_per_case"]
        == TOOL[1] + KEYRING[1] + max(item["bytes"] for item in INPUTS)
        == 569873
    )


@pytest.mark.parametrize("case", ["input", "tool_pin", "keyring_pin", "input_pin"])
def test_selected_copy_offsets_reset_without_touching_unused_descriptors(tmp_path, case):
    state = q._State()
    fds = {}
    try:
        for role in ("tool", "keyring", "input", "bad_tool", "bad_keyring", "bad_input"):
            path = tmp_path / role
            path.write_bytes(b"bounded-copy")
            fds[role] = q._open(state, path)
            os.lseek(fds[role], 12, os.SEEK_SET)
        selected = q._prepare_fds(fds, case)
        assert len(selected) == 3
        for role, fd in fds.items():
            expected = role in ("tool", "keyring", "input")
            if case.endswith("_pin"):
                replaced = case.removesuffix("_pin")
                if role == replaced:
                    expected = False
                elif role == "bad_" + replaced:
                    expected = True
            assert (fd in selected) == expected
            assert os.lseek(fd, 0, os.SEEK_CUR) == (0 if expected else 12)
        for fd in selected:
            assert os.read(fd, 12) == b"bounded-copy"
        assert q._prepare_fds(fds, case) == selected
        assert all(os.lseek(fd, 0, os.SEEK_CUR) == 0 for fd in selected)
    finally:
        assert state.close_all()


def test_descriptor_alias_refuses_before_rewind(monkeypatch):
    monkeypatch.setattr(q.os, "lseek", lambda *args: pytest.fail("must reject aliases first"))
    with pytest.raises(q.QualificationRefused, match="attachment_alias"):
        q._prepare_fds(dict(tool=10, keyring=10, input=11), "input")


def test_prior_unsettled_operation_refuses_before_rewind(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_prepare_fds", lambda *args: pytest.fail("unsettled offset mutation"))
    state = q._State()
    state.observations.append(SimpleNamespace(cleanup_confirmed=False))
    with pytest.raises(q.QualificationRefused, match="previous_operation_unsettled"):
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


def close_segment():
    start = GUEST_BOOTSTRAP.index("names=os.listdir('/proc/self/fd')")
    end = GUEST_BOOTSTRAP.index("if len(sys.argv)", start)
    return compile(GUEST_BOOTSTRAP[start:end], "offline-child-fd-close", "exec")


@pytest.mark.parametrize("failure", [None, "fstat", "close"])
def test_child_closes_support_once_and_only_ignores_scanner_ebadf(failure):
    import errno

    calls = []

    def fstat(fd):
        calls.append(("fstat", fd))
        if fd == 9:
            raise OSError(errno.EBADF, "ephemeral proc scanner")
        if failure == "fstat" and fd == 8:
            raise OSError(errno.EIO, "unproven identity")
        return object()

    def close(fd):
        calls.append(("close", fd))
        if failure == "close" and fd == 8:
            raise OSError(errno.EINTR, "ambiguous close")

    fake = SimpleNamespace(
        listdir=lambda path: ["0", "1", "2", "7", "8", "9"], fstat=fstat, close=close
    )
    scope = dict(os=fake, errno=errno)
    if failure is None:
        exec(close_segment(), scope)
        assert calls == [("fstat", 7), ("close", 7), ("fstat", 8), ("close", 8), ("fstat", 9)]
    else:
        with pytest.raises(OSError):
            exec(close_segment(), scope)
        assert calls.count(("close", 8)) == (1 if failure == "close" else 0)
    assert GUEST_BOOTSTRAP.count("names=os.listdir('/proc/self/fd')") == 2
    assert GUEST_BOOTSTRAP.index("os.close(fd)") < GUEST_BOOTSTRAP.index("def read(path")


@pytest.mark.parametrize("names", [["0", "unexpected"], [str(n) for n in range(257)]])
def test_child_fd_inventory_must_be_bounded_numeric(names):
    fake = SimpleNamespace(
        listdir=lambda path: names,
        fstat=lambda fd: pytest.fail("refuse before descriptor operations"),
    )
    with pytest.raises(SystemExit) as stopped:
        exec(close_segment(), dict(os=fake))
    assert stopped.value.code == 31


@pytest.mark.parametrize("change", [None, "device", "inode", "type", "mode", "links"])
def test_cancel_writes_only_to_opened_expected_fifo(change):
    import stat

    calls = []
    info = dict(st_dev=17, st_ino=42, st_mode=stat.S_IFIFO | 0o600, st_nlink=1)
    replacements = {
        "device": ("st_dev", 18),
        "inode": ("st_ino", 43),
        "type": ("st_mode", stat.S_IFREG | 0o600),
        "mode": ("st_mode", stat.S_IFIFO | 0o644),
        "links": ("st_nlink", 2),
    }
    if change:
        key, value = replacements[change]
        info[key] = value
    fake = SimpleNamespace(
        open=lambda *args: 7,
        fstat=lambda fd: SimpleNamespace(**info),
        write=lambda fd, value: calls.append(("write", fd, value)) or 1,
        close=lambda fd: calls.append(("close", fd)),
        O_WRONLY=1,
        O_NOFOLLOW=2,
        O_NONBLOCK=4,
        O_CLOEXEC=8,
    )
    start = GUEST_BOOTSTRAP.index("    ready=os.open(")
    end = GUEST_BOOTSTRAP.index("    time.sleep(300)", start)
    import textwrap

    code = compile(textwrap.dedent(GUEST_BOOTSTRAP[start:end]), "offline-fifo-identity", "exec")
    scope = dict(os=fake, stat=stat, ready_identity=(17, 42))
    if change:
        with pytest.raises(SystemExit) as stopped:
            exec(code, scope)
        assert stopped.value.code == 32
        assert calls == [("close", 7)]
    else:
        exec(code, scope)
        assert calls == [("write", 7, b"R"), ("close", 7)]


@pytest.mark.parametrize("links", [0, 1, 2])
def test_guest_authenticates_unlinked_copy_not_original_inode(links):
    import stat

    data = b"authenticated-private-copy"
    events = []
    chunks = iter([data, b""])
    info = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o755,
        st_nlink=links,
        st_size=len(data),
        st_dev=1,
        st_ino=2,
        st_mtime_ns=3,
        st_ctime_ns=4,
    )
    fake = SimpleNamespace(
        open=lambda *args: 7,
        fstat=lambda fd: info,
        read=lambda *args: next(chunks),
        close=lambda fd: events.append(fd),
        O_RDONLY=0,
        O_NOFOLLOW=1,
        O_NONBLOCK=2,
        O_CLOEXEC=4,
    )
    start = GUEST_BOOTSTRAP.index("def read(path")
    end = GUEST_BOOTSTRAP.index("target=NEGATIVES", start)
    scope = dict(os=fake, stat=stat, hashlib=hashlib)
    exec(compile(GUEST_BOOTSTRAP[start:end], "offline-copy-auth", "exec"), scope)
    if links:
        with pytest.raises(ValueError, match="identity"):
            scope["read"]("/fixed", len(data), hashlib.sha256(data).hexdigest(), 0o755)
    else:
        scope["read"]("/fixed", len(data), hashlib.sha256(data).hexdigest(), 0o755)
    assert events == [7]


def test_native_passes_only_selected_copy_fds_and_retains_parent_ownership(tmp_path, monkeypatch):
    state = q._State()
    fds = {}
    for role in ("tool", "keyring", "input", "bad_tool", "bad_keyring", "bad_input"):
        path = tmp_path / role
        path.write_bytes(b"bounded-copy")
        fds[role] = q._open(state, path)
        os.lseek(fds[role], 12, os.SEEK_SET)
    expected = (fds["bad_tool"], fds["keyring"], fds["input"])

    async def launch(*args, **kwargs):
        assert kwargs["pass_fds"] == expected
        assert len(state.observations) == 1
        assert all(os.read(fd, 12) == b"bounded-copy" for fd in expected)
        assert all(os.fstat(fd) for fd in fds.values())
        kwargs["observation"]._no_child()
        return SimpleNamespace(exit_code=0, stdout=b'{"status":"tool_refused"}', stderr=b"")

    monkeypatch.setattr(q, "run_process", launch)
    monkeypatch.setattr(q, "_argv", lambda *args: ["fake"])
    try:
        record = asyncio.run(
            q._native(
                state,
                q._Store(tmp_path, state),
                fds,
                "tool_pin",
                b"input",
                "tool_pin",
                0,
                q.time.monotonic() + 30,
            )
        )
        assert record["passed"]
        assert all(os.fstat(fd) for fd in fds.values())
    finally:
        assert state.close_all()


@pytest.mark.parametrize("uid", [0, 1001])
def test_readiness_wrong_owner_refuses_before_launch(tmp_path, monkeypatch, uid):
    original_fstat = os.fstat

    def wrong_owner(fd):
        info = original_fstat(fd)
        if stat.S_ISFIFO(info.st_mode):
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink, st_uid=uid)
        return info

    monkeypatch.setattr(q.os, "fstat", wrong_owner)
    monkeypatch.setattr(q, "_prepare_fds", lambda *args: pytest.fail("must refuse before launch"))
    state = q._State()
    try:
        with pytest.raises(q.QualificationRefused, match="readiness_identity"):
            asyncio.run(
                q._native(
                    state,
                    q._Store(tmp_path, state),
                    {},
                    "cancel",
                    b"input",
                    "cancel",
                    0,
                    q.time.monotonic() + 30,
                )
            )
        assert not state.observations
    finally:
        assert state.close_all()


def test_refreshed_candidate_pins_are_literal():
    assert [row["sha256"] for row in INPUTS] == [
        "da1cdfa3f8ddfbbc9f9e7dfedfde41bd2d80ceaa9992f8a96b61526dfe05fb1a",
        "cdb2f31d809f589719a53c6ad15f255b27569c4059542ada282aaa21b8e164b0",
        "a7ed9f9b84a70795e01c3763cd8fbcaf678a5683b14074fcd880f442f379e19a",
    ]
    assert NEGATIVES == {
        "altered_text": {
            "bytes": 126125,
            "sha256": "bf9f9e4791647b9f516204a79889c06bd2d3f729d94f9c4bf4aaa4a0ac22b07c",
        }
    }


@pytest.fixture
def synthetic_support(origin_job, monkeypatch):
    state = q._State()
    monkeypatch.setattr(q, "_State", lambda: state)
    monkeypatch.setattr(
        q, "_open", lambda state, *args, **kwargs: state.hold(70000 + len(state.fds))
    )
    calls = []
    original_close = os.close

    def close(fd):
        if 70000 <= fd <= 80002:
            calls.append(fd)
        else:
            original_close(fd)

    monkeypatch.setattr(os, "close", close)
    return state, calls, close


def test_support_closure_once_after_proof(origin_job, synthetic_support):
    state, calls, _ = synthetic_support
    result = asyncio.run(q._qualify())
    assert result["admission_release_confirmed"] is True
    assert calls == list(range(70000, 70009))
    assert state.fds == [] and len(state.observations) == 18
    assert origin_job[-2:] == ["cases.json", "release"]


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_interrupted_support_loop_never_retries(origin_job, synthetic_support, monkeypatch, error):
    state, calls, close = synthetic_support

    def interrupted(fd):
        if fd == 70001:
            calls.append(fd)
            raise error()
        close(fd)

    monkeypatch.setattr(os, "close", interrupted)
    with pytest.raises(error):
        asyncio.run(q._qualify())
    assert calls == [70000, 70001]
    assert state.fds == list(range(70000, 70009))
    assert "release" not in origin_job
    assert q._QUARANTINED[-1][0] is state


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_body_abort_retains_support_without_release(
    origin_job, synthetic_support, monkeypatch, error
):
    state, calls, _ = synthetic_support

    async def abort(*args):
        state.observations.append(SimpleNamespace(cleanup_confirmed=None))
        raise error()

    monkeypatch.setattr(q, "_native", abort)
    with pytest.raises(error):
        asyncio.run(q._qualify())
    assert calls == [] and state.fds
    assert "release" not in origin_job
    assert q._QUARANTINED[-1][0] is state


def test_ordinary_release_error_is_not_retried(origin_job, synthetic_support, monkeypatch):
    state, calls, _ = synthetic_support
    attempts = []

    def fail(*args):
        attempts.append(1)
        raise OSError("release")

    monkeypatch.setattr(q, "_release", fail)
    with pytest.raises(OSError, match="release"):
        asyncio.run(q._qualify())
    assert attempts == [1] and calls == []
    assert q._QUARANTINED[-1][0] is state


@pytest.mark.parametrize("failed_fd", [80002, 80001, 80000])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_real_finish_close_failure_not_retried(
    origin_job, synthetic_support, monkeypatch, failed_fd, error
):
    from qualification import admission

    state, calls, close = synthetic_support
    lease = admission.Lease(80000, 80001, 80002, "synthetic", "a" * 64)
    finishes = []
    original_finish = lease.finish

    def finish(**kwargs):
        finishes.append(kwargs)
        original_finish(**kwargs)

    lease.finish = finish
    monkeypatch.setattr(
        q, "AdmissionController", lambda path: SimpleNamespace(acquire=lambda d: lease)
    )
    monkeypatch.setattr(admission, "_private", lambda *args, **kwargs: None)
    original_truncate, original_sync = os.ftruncate, os.fsync
    writes = []

    def truncate(fd, length):
        if fd == 80002:
            writes.append((fd, length))
        else:
            original_truncate(fd, length)

    def sync(fd):
        if fd not in (80000, 80002):
            original_sync(fd)

    def interrupted(fd):
        if fd == failed_fd:
            calls.append(fd)
            raise error()
        close(fd)

    monkeypatch.setattr(os, "ftruncate", truncate)
    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "close", interrupted)
    with pytest.raises(error):
        asyncio.run(q._qualify())
    closure = [80002, 80001, 80000]
    assert calls == list(range(70000, 70009)) + closure[: closure.index(failed_fd) + 1]
    assert finishes == [{"cleanup_confirmed": True}]
    assert writes == [(80002, 0)]  # Failure can follow marker clearing.
    assert lease._fds is None
    assert q._QUARANTINED[-1] == (state, lease)


def test_unsettled_native_job_fences_without_support_close(
    origin_job, synthetic_support, monkeypatch
):
    state, calls, _ = synthetic_support
    finishes = []
    lease = SimpleNamespace(finish=lambda **kwargs: finishes.append(kwargs))
    monkeypatch.setattr(
        q, "AdmissionController", lambda path: SimpleNamespace(acquire=lambda d: lease)
    )

    async def unsettled(*args):
        state.observations.append(SimpleNamespace(cleanup_confirmed=None))
        raise ValueError("unsettled")

    monkeypatch.setattr(q, "_native", unsettled)
    with pytest.raises(ValueError, match="unsettled"):
        asyncio.run(q._qualify())
    assert calls == [] and state.fds
    assert finishes == [{"cleanup_confirmed": False}]
