"""Fake native backends and synthetic streams; never launch the host qualifier."""

import asyncio
import hashlib
import io
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.developer_process import ProcessResult
from qualification import media_tar_inventory as scanner
from qualification import media_tar_isolation as m
from scripts import qualify_media_tar_isolation as q

RUN = "d" * 32
POLICY = "e" * 64


def result(case="positive"):
    return ProcessResult(0, m.canonical(m.expected_value(case, RUN, POLICY)), b"", 0.01)


def test_fixture_positive_has_independent_expected_inventory():
    raw = m.fixture("positive")
    binding = m.contract.ExpectedBinding(**m.expected_binding("positive", RUN, POLICY))
    parsed = scanner.scan_tar(io.BytesIO(raw), binding)
    expected = m.expected_value("positive", RUN, POLICY)
    assert json.loads(parsed.manifest_raw) == expected["manifest"]
    assert parsed.tar_sha256 == expected["tar_sha256"]
    assert parsed.member_count == 5 and len(raw) == 4096


@pytest.mark.parametrize("case,code", list(m.MALFORMED.items()))
def test_fixed_bad_fixture_has_exact_scanner_refusal(case, code):
    raw = m.fixture(case)
    binding = m.contract.ExpectedBinding(**m.expected_binding(case, RUN, POLICY))
    with pytest.raises(scanner.Refused) as found:
        scanner.scan_tar(io.BytesIO(raw), binding)
    assert str(found.value) == code


@pytest.mark.parametrize("case", m.MATRIX)
def test_every_input_is_fixed_bounded_and_bound(case):
    raw = m.fixture(case)
    assert type(raw) is bytes and 0 < len(raw) <= m.FIXTURE_MAX
    binding = m.expected_binding(case, RUN, POLICY)
    assert binding["archive_bytes"] == len(raw)
    assert binding["archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert binding["run_id"] == RUN and binding["reader_policy_sha256"] == POLICY


@pytest.mark.parametrize("case", ["arbitrary", "", "--extract", None, 1])
def test_arbitrary_case_refused(case):
    with pytest.raises(ValueError, match="^ustar_case$"):
        m.fixture(case)


@pytest.mark.parametrize("case", [c for c in m.MATRIX if c not in m.NATIVE_ERRORS])
def test_exact_output_is_accepted_only_with_cleanup(case):
    good = result(case)
    value = m.validate_result(case, good, None, True, RUN, POLICY)
    assert value["status"] == "passed" and value["cleanup_confirmed"] is True
    for cleanup in (False, None, 1):
        with pytest.raises(ValueError, match="^ustar_cleanup_unknown$"):
            m.validate_result(case, good, None, cleanup, RUN, POLICY)


@pytest.mark.parametrize("case,error", list(m.NATIVE_ERRORS.items()))
def test_expected_native_refusal_requires_no_result_and_exact_error(case, error):
    assert m.validate_result(case, None, error, True, RUN, POLICY)["status"] == "passed"
    for bad_result, bad_error in ((None, None), (None, "process_timeout_wrong"), (result(), error)):
        with pytest.raises(ValueError, match="^ustar_native_control$"):
            m.validate_result(case, bad_result, bad_error, True, RUN, POLICY)
    with pytest.raises(ValueError, match="^ustar_cleanup_unknown$"):
        m.validate_result(case, None, error, False, RUN, POLICY)


@pytest.mark.parametrize(
    "field,value",
    [
        ("exit_code", 1),
        ("exit_code", False),
        ("stdout", b"x" * 65537),
        ("stderr", b"not empty"),
        ("stdout", "text"),
    ],
)
def test_native_result_basics_refused(field, value):
    with pytest.raises(ValueError, match="^ustar_(guest_failure|output_bound)$"):
        m.validate_result("positive", replace(result(), **{field: value}), None, True, RUN, POLICY)


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\xff", b"NaN", b'{"a":1,"a":2}', b"{} {}"])
def test_malformed_output_is_sanitized(raw):
    with pytest.raises(ValueError, match="^ustar_result_mismatch$"):
        m.validate_result("positive", replace(result(), stdout=raw), None, True, RUN, POLICY)


@pytest.mark.parametrize(
    "change",
    [
        "case",
        "schema",
        "fixture",
        "source",
        "authority",
        "extra",
        "run",
        "payload",
        "size",
        "kind",
        "count",
        "boolean_count",
    ],
)
def test_output_tampering_fails_exact_binding(change):
    value = m.expected_value("positive", RUN, POLICY)
    if change == "case":
        value["case"] = "readonly"
    elif change == "schema":
        value["schema"] = "other"
    elif change == "fixture":
        value["fixture_sha256"] = "0" * 64
    elif change == "source":
        value["source_hashes"][m.SOURCE_FILES[0][1]] = "0" * 64
    elif change == "authority":
        value["runtime_authority"] = True
    elif change == "extra":
        value["cleanup_confirmed"] = True
    elif change == "run":
        value["manifest"]["binding"]["run_id"] = "0" * 32
    elif change == "payload":
        value["manifest"]["members"][2]["sha256"] = "0" * 64
    elif change == "size":
        value["manifest"]["expanded_bytes"] += 512
    elif change == "kind":
        value["manifest"]["members"][2]["kind"] = "symlink"
    elif change == "count":
        value["member_count"] = 6
    else:
        value["manifest"]["members"][1]["ordinal"] = True
    with pytest.raises(ValueError, match="^ustar_result_mismatch$"):
        m.validate_result(
            "positive", replace(result(), stdout=m.canonical(value)), None, True, RUN, POLICY
        )


@pytest.fixture
def operator(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.shared, "ROOT", root)
    sources = []
    for index, (_, destination, _, _) in enumerate(m.SOURCE_FILES):
        relative = f"qualification/fake-{index}.py"
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        raw = f"fake immutable source {index}\n".encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        sources.append((relative, destination, len(raw), hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(m, "SOURCE_FILES", tuple(sources))
    events = []
    state = q._State()
    monkeypatch.setattr(q, "_State", lambda: state)

    class Lease:
        identity = RUN * 2

        def finish(self, *, cleanup_confirmed):
            events.append(("finish", cleanup_confirmed))

    class Admission:
        def __init__(self, path):
            assert path == root / q.ADMISSION

        def acquire(self, digest):
            events.append(("acquire", digest))
            return Lease()

    monkeypatch.setattr(q, "AdmissionController", Admission)
    monkeypatch.setattr(q, "run_process", lambda *a, **k: pytest.fail("native launch in fake test"))

    async def fake_case(current, fds, case, attempt, run_id, digest, deadline):
        assert current is state and len(fds) == 3
        assert all(os.fstat(fd).st_size > 0 for fd in fds)
        assert not any(e[0] == "finish" for e in events)
        state.observations.append(SimpleNamespace(cleanup_confirmed=True))
        events.append(("case", case))
        return {"case": case, "status": "passed", "cleanup_confirmed": True}

    monkeypatch.setattr(q, "_case", fake_case)
    yield SimpleNamespace(root=root, state=state, events=events, case=fake_case)
    if state.fds:
        state.close_all()  # Synthetic descriptor teardown, never a production retry.


def test_fake_matrix_pending_proof_precedes_single_release(operator, monkeypatch):
    real = q._Store.save

    def save(store, name, raw):
        assert not any(e[0] == "finish" for e in operator.events)
        if name == "cases.json":
            assert len(operator.state.observations) == len(m.MATRIX)
            assert json.loads(raw)["support_release_confirmed"] is False
            assert len(operator.state.fds) == 3
        return real(store, name, raw)

    monkeypatch.setattr(q._Store, "save", save)
    receipt, _ = asyncio.run(q._qualify())
    assert operator.events[-1] == ("finish", True)
    assert not operator.state.fds
    proof = operator.root / q.PROOF_BASE
    paths = list(proof.glob("*/cases.json"))
    assert len(paths) == 1
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == receipt["proof_sha256"]
    assert receipt["support_release_confirmed"] is True and receipt["runtime_authority"] is False


@pytest.mark.parametrize("index", [0, 1, 2])
def test_source_corruption_stops_before_any_case(operator, index):
    path = operator.root / m.SOURCE_FILES[index][0]
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert not any(e[0] == "case" for e in operator.events)
    assert operator.events[-1] == ("finish", False)


def test_source_symlink_refused_without_following(operator):
    path = operator.root / m.SOURCE_FILES[0][0]
    old = path.with_name("target")
    path.rename(old)
    path.symlink_to(old)
    with pytest.raises(ValueError, match="^ustar_source_path$"):
        asyncio.run(q._qualify())
    assert not any(e[0] == "case" for e in operator.events)


def test_refusal_stops_matrix_and_retains_partial_record(operator, monkeypatch):
    async def bad(*args):
        if args[2] == "checksum":
            raise ValueError("fixed refusal")
        return await operator.case(*args)

    monkeypatch.setattr(q, "_case", bad)
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert [e[1] for e in operator.events if e[0] == "case"] == ["positive"]
    proof = next((operator.root / q.PROOF_BASE).glob("*/cases.json"))
    value = json.loads(proof.read_bytes())
    assert value["status"] == "refused" and value["stage"] == "checksum"
    assert operator.events[-1] == ("finish", True)


def test_unknown_cleanup_refuses_release_confirmation(operator, monkeypatch):
    async def uncertain(state, *args):
        state.observations.append(SimpleNamespace(cleanup_confirmed=False))
        raise ValueError("unknown native cleanup")

    monkeypatch.setattr(q, "_case", uncertain)
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert operator.events[-1] == ("finish", False)
    assert len(operator.state.fds) == 3


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_release_error_has_no_retry(operator, monkeypatch, error):
    called = []
    real = q._release

    def failing(state, lease):
        called.append(1)
        real(state, lease)
        raise error("late close")

    monkeypatch.setattr(q, "_release", failing)
    with pytest.raises(error):
        asyncio.run(q._qualify())
    assert called == [1]


def test_two_attempts_are_preserved_and_third_refused(operator):
    first = q._attempt_directory()
    second = q._attempt_directory()
    with pytest.raises(ValueError, match="^ustar_attempt_limit$"):
        q._attempt_directory()
    assert set((operator.root / q.PROOF_BASE).iterdir()) == {first, second}


@pytest.mark.parametrize("fault", ["unknown", "mode", "symlink", "oversize", "fifo_wrong"])
def test_malformed_prior_state_not_repaired(operator, fault):
    directory = q._attempt_directory()
    path = directory / ("other" if fault == "unknown" else "cases.json")
    if fault == "symlink":
        path.symlink_to(operator.root)
    elif fault == "fifo_wrong":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_bytes(b"")
        path.chmod(0o644 if fault == "mode" else 0o600)
        if fault == "oversize":
            with path.open("wb") as out:
                out.truncate(2097153)
    with pytest.raises(ValueError):
        q._attempt_directory()
    assert list((operator.root / q.PROOF_BASE).iterdir()) == [directory]


def test_evidence_reserves_and_refuses_duplicate(operator):
    path = q._attempt_directory()
    store = q._Evidence(path, operator.state)
    store.json("policy.json", {})
    assert store.reserved == 524288
    with pytest.raises(ValueError, match="^ustar_evidence_name$"):
        store.json("policy.json", {})
    with pytest.raises(ValueError, match="^ustar_evidence_name$"):
        store.json("other.json", {})
    with pytest.raises(ValueError, match="^ustar_evidence_budget$"):
        store.json("sources.json", "x" * 524288)
    assert not (path / "sources.json").exists()


@pytest.mark.parametrize("case", m.MATRIX)
def test_rendered_argv_has_only_fixed_copies_and_no_project_mount(monkeypatch, case):
    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda cmd: ["/usr/bin/bwrap", "--remount-ro", "/", *cmd]
    )
    fds = (11, 12, 13)
    ready = 14 if case in ("cancel", "cancel_repeated") else None
    argv = q._argv(fds, case, "net:[test]", RUN, POLICY, ready)
    assert argv.count("--file") == 3 and argv.count("--cap-drop") == 1
    assert argv.count("--bind-fd") == int(ready is not None)
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "-I" in argv and "-S" in argv and "-B" in argv
    assert not any("/home/" in value for value in argv)
    for fd, (_, destination, _, _) in zip(fds, m.SOURCE_FILES, strict=True):
        index = argv.index(str(fd))
        assert argv[index - 1] == "--file" and argv[index + 1] == destination


@pytest.mark.parametrize(
    "case,ready", [("positive", 99), ("cancel", None), ("cancel_repeated", None)]
)
def test_ready_mount_cannot_widen_other_cases(case, ready):
    with pytest.raises(ValueError, match="^ustar_ready_identity$"):
        q._argv((1, 2, 3), case, "net:[test]", RUN, POLICY, ready)


def test_no_case_launch_on_module_import_and_resource_before_parser():
    assert q.policy()["runtime_authority"] is False
    assert "resource.setrlimit" in m.GUEST_BOOTSTRAP
    assert m.GUEST_BOOTSTRAP.index("resource.setrlimit") < m.GUEST_BOOTSTRAP.index(
        "from qualification"
    )
    assert m.GUEST_BOOTSTRAP.index(
        "hashlib.sha256(raw).hexdigest()!=digest"
    ) < m.GUEST_BOOTSTRAP.index("from qualification")
    assert "tarfile.__file__!=root+'/tarfile.py'" in m.GUEST_BOOTSTRAP


def test_cli_failure_is_fixed_not_native_detail(monkeypatch, capsys):
    monkeypatch.setattr(q.sys, "argv", ["qualify", "--run"])

    async def bad():
        raise ValueError("PRIVATE INFORMATION")

    monkeypatch.setattr(q, "_qualify", bad)
    assert q.main() == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == "ustar_isolation_refused\n"


@pytest.mark.parametrize("case", m.MATRIX)
def test_real_case_orchestration_with_fake_owned_backend(tmp_path, monkeypatch, case):
    from app.developer_process import DeveloperError

    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda cmd: ["/usr/bin/bwrap", "--remount-ro", "/", *cmd]
    )
    state = q._State()
    fds = []
    for n in range(3):
        path = tmp_path / f"source-{n}"
        path.write_bytes(b"source")
        fds.append(state.hold(os.open(path, os.O_RDONLY)))
    observation = SimpleNamespace(cleanup_confirmed=False)
    monkeypatch.setattr(q, "NativeExecutionObservation", lambda: observation)
    starts = []

    async def native(argv, **kwargs):
        starts.append(case)
        assert kwargs["observation"] is observation
        assert kwargs["stdin"] == m.fixture(case)
        assert kwargs["env"] == m.ENV
        assert kwargs["stdout_limit"] == 65536 and kwargs["stderr_limit"] == 16384
        assert all(os.lseek(fd, 0, os.SEEK_CUR) == 0 for fd in fds)
        try:
            if case in ("cancel", "cancel_repeated"):
                await asyncio.Event().wait()
            if case in m.NATIVE_ERRORS:
                raise DeveloperError(m.NATIVE_ERRORS[case])
            return result(case)
        finally:
            assert all(os.fstat(fd).st_size == 6 for fd in fds)
            observation.cleanup_confirmed = True

    async def ready(fd, deadline):
        await asyncio.sleep(0)
        assert starts == [case]
        assert os.fstat(fd).st_size == 0

    monkeypatch.setattr(q, "run_process", native)
    monkeypatch.setattr(q.shared, "_ready", ready)
    try:
        value = asyncio.run(
            q._case(state, tuple(fds), case, tmp_path, RUN, POLICY, q.time.monotonic() + 20)
        )
        assert value["status"] == "passed" and value["cleanup_confirmed"] is True
        assert starts == [case] and state.observations == [observation]
        assert len(state.fds) == 3 + int(case in ("cancel", "cancel_repeated"))
        assert all(os.fstat(fd) for fd in state.fds)
    finally:
        state.close_all()


def test_unknown_native_startup_does_not_become_success(tmp_path, monkeypatch):
    from app.developer_process import DeveloperError

    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda cmd: ["bwrap", "--remount-ro", "/", *cmd]
    )
    state = q._State()
    fds = []
    for n in range(3):
        path = tmp_path / str(n)
        path.write_bytes(b"source")
        fds.append(state.hold(os.open(path, os.O_RDONLY)))
    observation = SimpleNamespace(cleanup_confirmed=False)
    monkeypatch.setattr(q, "NativeExecutionObservation", lambda: observation)

    async def unknown(*a, **k):
        raise DeveloperError("process_identity_lost")

    monkeypatch.setattr(q, "run_process", unknown)
    try:
        with pytest.raises(ValueError, match="^ustar_cleanup_unknown$"):
            asyncio.run(
                q._case(
                    state, tuple(fds), "positive", tmp_path, RUN, POLICY, q.time.monotonic() + 20
                )
            )
        assert len(state.fds) == 3 and state.processes_settled is False
    finally:
        state.close_all()


def test_source_drift_after_matrix_is_not_accepted(operator, monkeypatch):
    async def drifting(*args):
        value = await operator.case(*args)
        if args[2] == m.MATRIX[-1]:
            path = operator.root / m.SOURCE_FILES[0][0]
            raw = path.read_bytes()
            path.write_bytes(b"X" + raw[1:])
        return value

    monkeypatch.setattr(q, "_case", drifting)
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert operator.events[-1] == ("finish", False)
    record = json.loads(next((operator.root / q.PROOF_BASE).glob("*/cases.json")).read_bytes())
    assert record["status"] == "refused" and record["stage"] == "source_readback"


def test_deadline_after_release_cannot_emit_receipt(operator, monkeypatch):
    real = q._release
    clock = [0]
    monkeypatch.setattr(q.time, "monotonic", lambda: clock[0])

    def release(state, lease):
        value = real(state, lease)
        clock[0] = q.WHOLE_SECONDS
        return value

    monkeypatch.setattr(q, "_release", release)
    with pytest.raises(ValueError, match="^ustar_operator_deadline$"):
        asyncio.run(q._qualify())
    assert operator.events[-1] == ("finish", True)


def test_operator_limits_are_cli_only_and_exact(monkeypatch):
    calls = []
    monkeypatch.setattr(q.resource, "getrlimit", lambda kind: (q.resource.RLIM_INFINITY,) * 2)
    monkeypatch.setattr(q.resource, "setrlimit", lambda kind, value: calls.append((kind, value)))
    q._operator_limits()
    assert len(calls) == 5 and all(soft == hard for _, (soft, hard) in calls)
    assert (q.resource.RLIMIT_CORE, (0, 0)) in calls
    assert (q.resource.RLIMIT_AS, (536870912, 536870912)) in calls
    calls.clear()
    monkeypatch.setattr(q.resource, "getrlimit", lambda kind: (1, 1))
    with pytest.raises(ValueError, match="^ustar_operator_limits$"):
        q._operator_limits()
    assert calls == []


@pytest.mark.parametrize("which", ["base", "attempt"])
def test_proof_directories_require_exact_private_mode(operator, which):
    path = q._attempt_directory()
    target = operator.root / q.PROOF_BASE if which == "base" else path
    target.chmod(0o755)
    with pytest.raises(ValueError, match="^ustar_proof_private$"):
        q._attempt_directory()
    assert target.stat().st_mode & 0o777 == 0o755
