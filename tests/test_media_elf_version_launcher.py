"""Offline profile/binding/ownership fixtures; no guest/parser/native execution."""

import ast
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from qualification.admission import AdmissionController, AdmissionDenied
from qualification.media_elf_version_metadata_guest import GUEST_BOOTSTRAP, SOURCE_PINS
from scripts import qualify_media_elf_version_metadata as q
from tests.media_elf_version_fixtures import make
from tests.test_media_elf_version_metadata import inspect


def report():
    candidate = make()
    output = inspect(candidate)
    return candidate[0], json.loads(output), output + b"\n"


def test_bootstrap_compiles_source_chain_pins_and_no_bytecode_mount():
    compile(GUEST_BOOTSTRAP, "fixed-elf-bootstrap", "exec")
    assert len(SOURCE_PINS) == 5 and sum(size for _, size, _ in SOURCE_PINS) <= 65536
    for path, size, digest in SOURCE_PINS:
        raw = (q.ROOT / path).read_bytes()
        assert len(raw) == size <= 32768 and hashlib.sha256(raw).hexdigest() == digest
    assert GUEST_BOOTSTRAP.index("for name,size,digest in SOURCE_PINS") < GUEST_BOOTSTRAP.index(
        "from qualification import"
    )
    assert GUEST_BOOTSTRAP.index("manifest.digest!=m.TOOL_MANIFEST_DIGEST") < GUEST_BOOTSTRAP.index(
        "version.inspect_elf_metadata(raw)"
    )
    tree = ast.parse(GUEST_BOOTSTRAP)
    writes = [
        n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, bytes)
    ]
    assert any(n.value == b"\n" for n in writes)
    assert not any(n.value == b"\\n" for n in writes)


def test_exact_inert_fixture_and_target_lock_pins():
    raw, layout, *_ = make()
    assert len(raw) == 1456 and hashlib.sha256(raw).hexdigest() == q.FIXTURE_SHA
    assert raw == (q.ROOT / "qualification/fixtures/media-elf-version-positive.bin").read_bytes()
    assert layout["section_table"] + 9 * 64 == len(raw)
    lock_raw = (q.ROOT / "qualification/fixtures/media-elf-target-lock.json").read_bytes()
    assert hashlib.sha256(lock_raw).hexdigest() == q.TARGET_LOCK_SHA
    lock = json.loads(lock_raw)
    assert (
        len(lock["files"]) == 7
        and sum(x["bytes"] for x in lock["files"]) == lock["aggregate_bytes"] == 11487248
    )
    assert not lock["platform_complete"] and not lock["runtime_authority"]


def test_fixed_profile_and_compatible_hard_limits(monkeypatch):
    captured = []
    fake = SimpleNamespace(
        RLIMIT_AS=1,
        RLIMIT_CPU=2,
        RLIMIT_NOFILE=3,
        RLIMIT_FSIZE=4,
        RLIMIT_CORE=5,
        RLIM_INFINITY=-1,
        getrlimit=lambda kind: (-1, -1),
        setrlimit=lambda kind, bounds: captured.append((kind, bounds)),
    )
    monkeypatch.setattr(q, "resource", fake)
    q._operator_limits()
    assert captured == [
        (1, (536870912, 536870912)),
        (2, (30, 30)),
        (3, (256, 256)),
        (4, (8388608, 8388608)),
        (5, (0, 0)),
    ]
    profile = q.policy()
    assert profile["processes"] == len(q.MATRIX) + 1 + len(q.MALFORMED) + 7 == 39
    assert (
        profile["whole_seconds"]
        == profile["setup_seconds"] + profile["matrix_seconds"] + profile["metadata_seconds"]
    )
    assert profile["environment"] == q.ENV and not profile["runtime_authority"]


def test_mounts_only_authenticated_files_no_whole_source_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        q, "python_compute_sandbox", lambda command: ["bwrap", "--remount-ro", "/", *command]
    )
    argv = q._argv(
        100, (101, 102, 103, 104, 107), "fixture", tmp_path / "sentinel", "net:[1]", 105, 106, -1
    )
    assert ["--cap-drop", "ALL"] == argv[1:3]
    mounts = [argv[i + 2] for i, item in enumerate(argv) if item == "--ro-bind"]
    assert set(mounts) == {
        q.m.TOOL_ROOT,
        *("/opt/zelo-elf-source/" + name for name, _, _ in SOURCE_PINS),
    }
    assert "__pycache__" not in " ".join(argv)
    assert argv.count("-I") == argv.count("-S") == argv.count("-B") == 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("runtime_authority", True),
        ("abi_qualified", True),
        ("platform_complete", True),
        ("symbol_content_verified", True),
        ("source_bytes", True),
        ("source_sha256", "0" * 64),
        ("instruction_trust", "trusted"),
        ("extra", 1),
    ],
)
def test_result_binding_claims_and_extra_fields_refused(key, value):
    raw, result, _ = report()
    result[key] = value
    stdout = json.dumps(result, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(q.QualificationRefused):
        q._metadata_result(stdout, raw)


def test_result_serialization_identity_and_complete_output():
    raw, result, stdout = report()
    assert q._metadata_result(stdout, raw) == result
    with pytest.raises(q.QualificationRefused):
        q._metadata_result(json.dumps(result, indent=2).encode() + b"\n", raw)
    with pytest.raises(q.QualificationRefused):
        q._metadata_result(stdout[:-1], raw)


def test_store_aggregate_bound_precedes_write(tmp_path, monkeypatch):
    state = q._State()
    store = q._Store(tmp_path, state)
    store.bytes = 8388608
    monkeypatch.setattr(q.shared, "_save", lambda *args: pytest.fail("storage reached"))
    with pytest.raises(q.QualificationRefused, match="retained_bound"):
        store.save("next", b"x")


def test_store_close_uncertainty_fences_state(tmp_path, monkeypatch):
    state = q._State()
    store = q._Store(tmp_path, state)

    def refuse(*args):
        raise OSError("reported close unknown")

    monkeypatch.setattr(q.shared, "_save", refuse)
    with pytest.raises(OSError):
        store.save("result", b"x")
    assert state.close_ok is False


def lease_at(path):
    path.mkdir(mode=0o700)
    controller = AdmissionController(path)
    return controller, controller.acquire("1" * 64)


def test_known_owned_cleanup_closes_support_then_releases(tmp_path):
    controller, lease = lease_at(tmp_path / "admission")
    state = q._State()
    fd = state.hold(os.open(tmp_path / "support", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600))
    state.observations.append(SimpleNamespace(cleanup_confirmed=True))
    assert q._release(state, lease) is True
    with pytest.raises(OSError):
        os.fstat(fd)
    next_lease = controller.acquire("2" * 64)
    next_lease.finish(cleanup_confirmed=True)


def test_unknown_owned_cleanup_retains_support_and_persistent_marker(tmp_path, monkeypatch):
    controller, lease = lease_at(tmp_path / "admission")
    monkeypatch.setattr(q, "_RETAINED", [])
    state = q._State()
    fd = state.hold(os.open(tmp_path / "support", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600))
    state.observations.append(SimpleNamespace(cleanup_confirmed=False))
    try:
        assert q._release(state, lease) is False
        assert os.fstat(fd).st_nlink == 1 and q._RETAINED == [state]
        with pytest.raises(AdmissionDenied, match="fenced"):
            controller.acquire("2" * 64)
    finally:
        os.close(fd)  # Fixture teardown only; no owned guest was created.
        state.fds.clear()


def test_support_close_error_attempts_every_fd_and_never_retries(tmp_path, monkeypatch):
    state = q._State()
    state.fds = [101, 102, 103]
    calls = []

    def close(fd):
        calls.append(fd)
        if fd == 102:
            raise OSError("close unknown")

    monkeypatch.setattr(q.os, "close", close)
    assert state.close_all() is False
    assert calls == [101, 102, 103] and state.fds == []
    assert state.close_all() is False and calls == [101, 102, 103]


def test_release_error_refuses_after_possible_marker_clear(tmp_path):
    controller, lease = lease_at(tmp_path / "admission")
    real_finish = lease.finish

    def failed_release(*, cleanup_confirmed):
        real_finish(cleanup_confirmed=cleanup_confirmed)
        raise OSError("late release error")

    lease.finish = failed_release
    with pytest.raises(OSError):
        q._release(q._State(), lease)
    assert (tmp_path / "admission/active.marker").read_bytes() == b""
    later = controller.acquire("2" * 64)
    later.finish(cleanup_confirmed=True)


def test_read_bound_pin_and_drift_before_use(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"test")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        assert q._read_fd(fd, 4, hashlib.sha256(b"test").hexdigest(), 4) == b"test"
        with pytest.raises(q.QualificationRefused):
            q._read_fd(fd, 3)
        with pytest.raises(q.QualificationRefused):
            q._read_fd(fd, 4, "0" * 64)
    finally:
        os.close(fd)


def test_pending_creation_repeated_cancel_keeps_support_until_settlement(tmp_path, monkeypatch):
    async def scenario():
        created = asyncio.Event()
        cleaning = asyncio.Event()
        finish = asyncio.Event()
        observation = SimpleNamespace(cleanup_confirmed=False)
        monkeypatch.setattr(q, "NativeExecutionObservation", lambda: observation)
        state = q._State()
        fd = state.hold(os.open(tmp_path / "support", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600))

        async def fake_native(*args, **kwargs):
            assert state.observations == [observation]
            created.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()

                async def settle():
                    await finish.wait()
                    observation.cleanup_confirmed = True

                await q.shared.uncancellable(asyncio.create_task(settle()))

        monkeypatch.setattr(q, "run_process", fake_native)
        monkeypatch.setattr(q, "_argv", lambda *args: ["fixed-inert-fixture"])
        task = asyncio.create_task(
            q._native(
                state,
                q._Store(tmp_path, state),
                fd,
                (),
                fd,
                fd,
                "fixture",
                b"data",
                "fixture",
                q.time.monotonic() + 10,
            )
        )
        await created.wait()
        task.cancel()
        await cleaning.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not state.processes_settled
        assert os.fstat(fd).st_nlink == 1
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state.processes_settled and state.close_all()
        with pytest.raises(OSError):
            os.fstat(fd)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", q.MALFORMED)
def test_fixed_malformed_case_changes_only_bounded_inert_input(name):
    raw, layout, *_ = make()
    changed = q._malformed(raw, layout, name)
    assert type(changed) is bytes and len(changed) == len(raw)
    assert changed != raw and raw == make()[0]
    assert changed == q._malformed(raw, layout, name)


def test_unlisted_malformed_case_refused():
    raw, layout, *_ = make()
    with pytest.raises(q.QualificationRefused, match="malformed_case"):
        q._malformed(raw, layout, "unlisted")


@pytest.mark.parametrize("stage", ["directory", "receipt", "initial-manifest", "final-manifest"])
def test_helper_internal_close_failures_fence_whole_job(tmp_path, monkeypatch, stage):
    # Full launcher control flow with inert fake workers; no optional parser/native run.
    original_root = q.ROOT
    root = tmp_path / "root"
    (root / "runtime").mkdir(parents=True, mode=0o700)
    for name, _, _ in SOURCE_PINS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((original_root / name).read_bytes())
    for name in (
        "media-elf-version-positive.bin",
        "media-elf-version-positive.json",
        "media-elf-version-unversioned.bin",
    ):
        path = root / "qualification/fixtures" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((original_root / "qualification/fixtures" / name).read_bytes())
    tool = root / "runtime/media-elf-tool-preparation/fake/payload"
    tool.mkdir(parents=True, mode=0o500)
    info = tool.stat()
    receipt = {
        "receipt": {"relative_payload": "fake/payload", "root_identity": [info.st_dev, info.st_ino]}
    }
    target_path = q.Path("/usr/bin/env").resolve(strict=True)
    target = {
        "source": str(target_path),
        "resolved_source": str(target_path),
        "mode": target_path.stat().st_mode & 0o777,
        "bytes": 1,
        "sha256": hashlib.sha256(b"x").hexdigest(),
    }
    lock = {"files": [target] * 7, "aggregate_bytes": 7}
    (root / "qualification/fixtures/media-elf-target-lock.json").write_bytes(b"fake lock")
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.shared, "_private_path", lambda path, **kwargs: path)
    real_read_fd = q._read_fd

    def read_fd(fd, bound, digest=None, size=None):
        if digest == q.TARGET_LOCK_SHA:
            return json.dumps(lock).encode()
        if bound == q.m.INPUT_BYTES:
            return b"x"
        return real_read_fd(fd, bound, digest, size)

    monkeypatch.setattr(q, "_read_fd", read_fd)
    states = []
    original_state = q._State

    def new_state():
        state = original_state()
        states.append(state)
        return state

    monkeypatch.setattr(q, "_State", new_state)
    monkeypatch.setattr(q.shared, "_read", lambda *args: json.dumps(receipt).encode())
    scans = []

    def manifest(*args):
        scans.append(args)
        if stage == "initial-manifest" or (stage == "final-manifest" and len(scans) == 2):
            raise ValueError("artifact_unavailable")  # Shared scanner hides close OSError.
        return SimpleNamespace(digest=q.m.TOOL_MANIFEST_DIGEST)

    monkeypatch.setattr(q.m, "capture_manifest", manifest)

    def close_failure(*args):
        raise OSError("helper internal close uncertain")

    if stage == "receipt":
        monkeypatch.setattr(q.shared, "_read", close_failure)
    if stage == "directory":
        monkeypatch.setattr(q, "_proof_directory", close_failure)

    async def inert_native(state, *args):
        state.observations.append(SimpleNamespace(cleanup_confirmed=True))
        return {"passed": True}

    monkeypatch.setattr(q, "_native", inert_native)
    with pytest.raises((OSError, ValueError)):
        asyncio.run(q._qualify())
    state = states[0]
    assert state.close_ok is False and state.fds == [] and state.processes_settled
    admission = root / "runtime/media-elf-metadata-admission"
    assert (admission / "active.marker").read_bytes()
    with pytest.raises(AdmissionDenied, match="fenced"):
        AdmissionController(admission).acquire("2" * 64)
    if stage == "final-manifest":
        assert len(state.observations) == 39 and len(scans) == 2
    assert not list((root / "runtime").glob("media-elf-version-proofs/*/cases.json"))


def test_directory_entry_bound_precedes_creation(tmp_path):
    store = q._Store(tmp_path, q._State())
    store.entries = 96
    with pytest.raises(q.QualificationRefused, match="retained_entries"):
        store.directory("bad-tool")
    assert not (tmp_path / "bad-tool").exists()


@pytest.mark.parametrize("versioned", [True, False])
def test_full_fixture_oracles(versioned):
    candidate = make(versioned)
    report = q._metadata_result(inspect(candidate) + b"\n", candidate[0])
    q._fixture_oracle(report, versioned)
    if versioned:
        report["definitions"][1]["hash"] += 1
        with pytest.raises(q.QualificationRefused, match="fixture_oracle"):
            q._fixture_oracle(report, versioned)


def test_distinct_proof_scope_and_original_admission_preserved():
    source = q.Path(q.__file__).read_text()
    assert 'ROOT / "runtime/media-elf-version-proofs"' in source
    assert 'ROOT / "runtime/media-elf-metadata-admission"' in source
    assert 'ROOT / "runtime/media-elf-metadata-proofs"' not in source
    assert 'store.json(label + "-failed.json"' not in source
    assert q.policy()["attempts"] == 4


def test_four_attempt_cap_is_not_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(q.shared, "ROOT", tmp_path)
    (tmp_path / "runtime").mkdir(mode=0o700)
    base = tmp_path / "runtime/proofs"
    paths = [q._proof_directory(base) for _ in range(4)]
    assert len(set(paths)) == 4
    with pytest.raises(q.QualificationRefused, match="attempt_limit"):
        q._proof_directory(base)
    assert all(path.is_dir() for path in paths)


def test_all_five_source_pins_reject_changed_bytes(tmp_path):
    for index, (name, size, digest) in enumerate(SOURCE_PINS):
        raw = bytearray((q.ROOT / name).read_bytes())
        raw[-1] ^= 1
        path = tmp_path / str(index)
        path.write_bytes(raw)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            with pytest.raises(q.QualificationRefused, match="source_pin"):
                q._read_fd(fd, 32768, digest, size)
        finally:
            os.close(fd)


def test_loader_claim_tamper_even_with_rebound_hash():
    raw, value, _ = report()
    value["loader_report"]["abi_qualified"] = True
    value["loader_report_sha256"] = hashlib.sha256(
        q.version.canonical(value["loader_report"])
    ).hexdigest()
    with pytest.raises(q.QualificationRefused):
        q._metadata_result(q.version.canonical(value) + b"\n", raw)


@pytest.mark.parametrize(
    "name,section,field_offset,form,value,kind,record_offset,key,family",
    [
        ("need-revision", ".gnu.version_r", 0, "<H", 2, "Elf_Verneed", 0, "vn_version", "revision"),
        (
            "definition-revision",
            ".gnu.version_d",
            0,
            "<H",
            2,
            "Elf_Verdef",
            0,
            "vd_version",
            "revision",
        ),
        ("parent-count", ".dynamic", 136, "<Q", 129, "Elf_Dyn", 128, "d_val", "count"),
        ("aux-count", ".gnu.version_d", 6, "<H", 65, "Elf_Verdef", 0, "vd_cnt", "count"),
        ("parent-range", ".gnu.version_d", 16, "<I", 65536, "Elf_Verdef", 0, "vd_next", "extent"),
        ("aux-range", ".gnu.version_r", 8, "<I", 65536, "Elf_Verneed", 0, "vn_aux", "extent"),
        ("parent-cycle", ".gnu.version_d", 16, "<I", 0, "Elf_Verdef", 0, "vd_next", "chain"),
        ("aux-cycle", ".gnu.version_d", 88, "<I", 0, "Elf_Verdaux", 84, "vda_next", "chain"),
        ("parent-overlap", ".gnu.version_d", 16, "<I", 4, "Elf_Verdef", 0, "vd_next", "chain"),
        ("aux-overlap", ".gnu.version_d", 12, "<I", 4, "Elf_Verdef", 0, "vd_aux", "chain"),
        ("nonzero-padding", ".gnu.version_d", 28, "<B", 1, None, 0, None, "padding"),
        ("unknown-flags", ".gnu.version_d", 2, "<H", 4, "Elf_Verdef", 0, "vd_flags", "flags"),
        ("reserved-index", ".gnu.version", 8, "<H", 0xFF00, "Elf_Versym", 8, "ndx", "index"),
        ("duplicate-index", ".gnu.version_d", 36, "<H", 1, "Elf_Verdef", 32, "vd_ndx", "index"),
        ("missing-index", ".gnu.version", 8, "<H", 5, "Elf_Versym", 8, "ndx", "reference"),
        ("invalid-string", ".dynstr", 22, "<B", 255, None, 0, None, "string"),
    ],
)
def test_each_native_mutation_exact_field_and_fake_record_causal_oracle(
    name, section, field_offset, form, value, kind, record_offset, key, family
):
    import struct

    candidate = list(make())
    raw, layout, _, records, _, _ = candidate
    assert inspect(candidate)  # Independently valid baseline for every mutation.
    # This independent field table does not use the launcher's mutation table.
    begin = layout["sections"][section]
    expected = bytearray(raw)
    struct.pack_into(form, expected, begin + field_offset, value)
    changed = q._malformed(raw, layout, name)
    assert changed == bytes(expected)
    assert changed[: begin + field_offset] == raw[: begin + field_offset]
    assert (
        changed[begin + field_offset + struct.calcsize(form) :]
        == raw[begin + field_offset + struct.calcsize(form) :]
    )
    candidate[0] = changed
    if kind is not None:
        records[kind, begin + record_offset][key] = value
    with pytest.raises(q.version.ElfMetadataError, match="^media_elf_version_" + family + "$"):
        inspect(candidate)
