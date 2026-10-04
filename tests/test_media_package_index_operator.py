"""Synthetic local-index orchestration only; never open real distribution indexes."""

import hashlib
import json
import os
import stat
from types import SimpleNamespace

import pytest

from scripts import qualify_media_package_index as m
from tests.test_media_package_index import stanza


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    pins = []
    raws = []
    for index, suite in enumerate(m.selector.SUITES):
        raw = b"".join(stanza(name, version) for name, version in m.selector.TARGETS[index::3])
        path = tmp_path / suite
        path.write_bytes(raw)
        path.chmod(0o644)
        pins.append((suite, str(path), len(raw), hashlib.sha256(raw).hexdigest()))
        raws.append(raw)
    monkeypatch.setattr(m, "INPUTS", tuple(pins))
    real_fstat = os.fstat

    def fake_root(fd):
        info = real_fstat(fd)
        values = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
        values.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**values)

    monkeypatch.setattr(m.os, "fstat", fake_root)
    return pins, raws


@pytest.fixture
def operator(tmp_path, monkeypatch, inputs):
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(m.shared, "ROOT", root)
    monkeypatch.setattr(m.os, "getresuid", lambda: (1000,) * 3)
    monkeypatch.setattr(m.os, "getresgid", lambda: (1000,) * 3)
    events = []

    class Lease:
        def finish(self, *, cleanup_confirmed):
            events.append(("finish", cleanup_confirmed))

    class Admission:
        def __init__(self, path):
            assert path == root / m.ADMISSION

        def acquire(self, digest):
            events.append(("acquire", digest))
            return Lease()

    monkeypatch.setattr(m, "AdmissionController", Admission)
    return root, events


def test_fixed_trust_binding():
    assert [pin[0] for pin in m.INPUTS] == ["noble", "noble-updates", "noble-security"]
    assert [pin[2] for pin in m.INPUTS] == [7165069, 7375524, 5812460]
    assert sum(pin[2] for pin in m.INPUTS) == 20353053
    assert m.ORIGIN_PROOF_SHA == "542570a765e3a6cb3fa3f2ecd1f549e96830007af99345e66dd1e35311951caf"
    assert m.ORIGIN_HEAD == "fbf1cb21aa1108b73818f81583fec5351729bd1a"
    assert [pin[3] for pin in m.INPUTS] == [
        "8f6f71ae839c8cba390a7643fcbbdacddb0bc7d12c1583a2dd80a1f8443a30e5",
        "0929964db78a694c2c35f2ad20337c782794ac121f818facede7243605d24295",
        "96ebaa6dcfaa00ad04c5a56f26f7bdc104a6da15d0fdd22ae29c91c299c3f276",
    ]
    policy = m.policy()
    assert policy["attempts"] == 4
    assert policy["processes_created"] == 0
    assert policy["runtime_authority"] is False
    assert policy["filesystem_deadline"] == "not-hard"
    assert policy["support_reservation_bytes"] == 67108864


def test_distributed_complete_result_and_immutable_sources(inputs):
    pins, raws = inputs
    state = m._State()
    try:
        authenticated = m._authenticate(state, float("inf"))
        assert len(state.fds) == 3
        for pin in pins:
            from pathlib import Path

            Path(pin[1]).write_bytes(b"changed after authentication")
        result = m._select(authenticated, float("inf"))
        assert result == {
            "schema": "zelo-media-fixed-package-selection-v1",
            "indexes": [
                {"suite": pin[0], "bytes": len(raw), "sha256": pin[3]}
                for pin, raw in zip(pins, raws, strict=True)
            ],
            "packages": [
                {
                    "package": name,
                    "version": version,
                    "architecture": "amd64",
                    "filename": f"pool/main/g/glibc/{name}_fixed_amd64.deb",
                    "bytes": 1234,
                    "sha256": "a" * 64,
                    "source": {"name": name, "version": version},
                    "suites": [m.selector.SUITES[index % 3]],
                }
                for index, (name, version) in enumerate(m.selector.TARGETS)
            ],
            "metadata_only": True,
            "package_member_origin_verified": False,
            "runtime_authority": False,
        }
        assert all(type(item[1]) is bytes for item in authenticated)
    finally:
        state.close_all()


@pytest.mark.parametrize(
    "field,value",
    [
        ("st_uid", 1000),
        ("st_gid", 1000),
        ("st_mode", stat.S_IFREG | 0o600),
        ("st_mode", stat.S_IFDIR | 0o644),
        ("st_nlink", 2),
        ("st_size", 0),
    ],
)
def test_exact_source_metadata(monkeypatch, field, value):
    fields = dict(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o644, st_nlink=1, st_size=123)
    fields[field] = value
    monkeypatch.setattr(m.os, "fstat", lambda fd: SimpleNamespace(**fields))
    with pytest.raises(m.QualificationRefused, match="source_metadata"):
        m._source_metadata(99, 123)


@pytest.mark.parametrize("size", [0, -1, 8388609])
def test_source_size_bounds(monkeypatch, size):
    monkeypatch.setattr(
        m.os,
        "fstat",
        lambda fd: SimpleNamespace(
            st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o644, st_nlink=1, st_size=size
        ),
    )
    with pytest.raises(m.QualificationRefused, match="source_metadata"):
        m._source_metadata(99, size)


def test_all_hashes_precede_any_parser(operator, inputs, monkeypatch):
    pins, _ = inputs
    changed = list(pins)
    changed[2] = (*pins[2][:3], "0" * 64)
    monkeypatch.setattr(m, "INPUTS", tuple(changed))
    calls = []
    monkeypatch.setattr(m.selector, "parse_index", lambda *args: calls.append(args))
    with pytest.raises(m.QualificationRefused):
        m._qualify()
    assert calls == []
    assert operator[1][-1] == ("finish", True)


def test_aggregate_precedes_open(inputs, monkeypatch):
    monkeypatch.setattr(m, "AGGREGATE_LIMIT", 1)
    monkeypatch.setattr(m, "_open", lambda *a: pytest.fail("opened over aggregate bound"))
    with pytest.raises(m.QualificationRefused, match="aggregate"):
        m._authenticate(m._State(), float("inf"))


def test_symlink_source_refused(inputs, tmp_path, monkeypatch):
    pins, _ = inputs
    link = tmp_path / "alias"
    link.symlink_to(pins[0][1])
    monkeypatch.setattr(m, "INPUTS", ((pins[0][0], str(link), *pins[0][2:]), *pins[1:]))
    with pytest.raises(m.QualificationRefused, match="source_path"):
        m._authenticate(m._State(), float("inf"))


@pytest.mark.parametrize(
    "field,value", [("suite", "noble-security"), ("bytes", 1), ("sha256", "0" * 64)]
)
def test_parser_identity_refused(inputs, monkeypatch, field, value):
    pins, raws = inputs
    real = m.selector.parse_index

    def wrong(chunks, suite):
        result = real(chunks, suite)
        result[field] = value
        return result

    monkeypatch.setattr(m.selector, "parse_index", wrong)
    with pytest.raises(m.QualificationRefused, match="parser_identity"):
        m._select(
            [(pin[0], raw, pin[3]) for pin, raw in zip(pins, raws, strict=True)], float("inf")
        )


def test_pending_report_before_single_release_no_later_io(operator, monkeypatch):
    events = operator[1]
    real_save = m._Store.save
    real_release = m._release
    state_seen = []

    def save(store, name, raw):
        assert not any(event[0] == "finish" for event in events)
        if name == "cases.json":
            assert len(store.state.fds) == 3
            assert json.loads(raw)["support_release_confirmed"] is False
        events.append(("save", name))
        return real_save(store, name, raw)

    def release(state, lease):
        assert events[-1] == ("save", "cases.json")
        assert len(state.fds) == 3
        state_seen.append(state)
        return real_release(state, lease)

    monkeypatch.setattr(m._Store, "save", save)
    monkeypatch.setattr(m, "_release", release)
    receipt, _ = m._qualify()
    assert receipt["support_release_confirmed"] is True
    assert len(state_seen) == 1 and state_seen[0].fds == []
    from pathlib import Path

    raw = Path(receipt["proof"]).read_bytes()
    assert receipt["proof_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(list(Path(receipt["proof"]).parent.iterdir())) == 3


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_body_interrupt_quarantines_without_cleanup(operator, monkeypatch, error):
    state = m._State()
    monkeypatch.setattr(m, "_State", lambda: state)
    monkeypatch.setattr(m, "_authenticate", lambda *args: (_ for _ in ()).throw(error()))
    monkeypatch.setattr(m, "_release", lambda *args: pytest.fail("cleanup retried on interrupt"))
    before = len(m._QUARANTINED)
    with pytest.raises(error):
        m._qualify()
    assert len(m._QUARANTINED) == before + 1
    assert not any(event[0] == "finish" for event in operator[1])


@pytest.mark.parametrize("error", [KeyboardInterrupt, OSError])
def test_release_error_never_retried(operator, monkeypatch, error):
    calls = []
    real_release = m._release

    def release(state, lease):
        calls.append(1)
        real_release(state, lease)
        raise error("late finish failure")

    monkeypatch.setattr(m, "_release", release)
    with pytest.raises(error):
        m._qualify()
    assert calls == [1]


def test_unknown_helper_close_refuses_receipt(operator, monkeypatch):
    def fail(*args):
        raise OSError("hidden close")

    monkeypatch.setattr(m, "_proof_directory", fail)
    with pytest.raises(m.QualificationRefused):
        m._qualify()
    assert operator[1][-1] == ("finish", False)


def test_quota_counts_partial_attempts_across_policies(operator):
    root, _ = operator
    base = root / m.PROOF_BASE
    for _ in range(4):
        path = m._proof_directory(base)
        assert path.is_dir()
    with pytest.raises(m.QualificationRefused, match="attempt_limit"):
        m._proof_directory(base)
    assert len(list(base.iterdir())) == 4


def test_malformed_scope_not_repaired(operator):
    base = operator[0] / m.PROOF_BASE
    base.mkdir(mode=0o700)
    (base / "foreign").mkdir(mode=0o700)
    with pytest.raises(m.QualificationRefused, match="attempt_identity"):
        m._proof_directory(base)
    assert [p.name for p in base.iterdir()] == ["foreign"]


def test_deadline_after_read(inputs, monkeypatch):
    real = m._read_fd
    clock = [0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])

    def read(*args):
        result = real(*args)
        clock[0] = 121
        return result

    monkeypatch.setattr(m, "_read_fd", read)
    state = m._State()
    try:
        with pytest.raises(m.QualificationRefused, match="deadline"):
            m._authenticate(state, 120)
    finally:
        state.close_all()


def test_deadline_after_release_no_receipt(operator, monkeypatch):
    clock = [0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    real = m._release

    def release(*args):
        result = real(*args)
        clock[0] = 121
        return result

    monkeypatch.setattr(m, "_release", release)
    with pytest.raises(m.QualificationRefused, match="deadline"):
        m._qualify()
    assert operator[1][-1] == ("finish", True)


def test_chunk_bound_and_deadline(monkeypatch):
    monkeypatch.setattr(m.time, "monotonic", lambda: 0)
    assert [len(x) for x in m._chunks(b"x" * 131073, 1)] == [65536, 65536, 1]
    with pytest.raises(m.QualificationRefused, match="deadline"):
        list(m._chunks(b"x", 0))


def test_interrupted_descriptor_loop_not_reentered(operator, monkeypatch):
    calls = []

    real_state = m._State

    class InterruptedState(real_state):
        def close_all(self):
            # Model a successful first close followed by interruption before the
            # helper's final list clear. Never operate on fabricated real FDs.
            calls.append("first-close")
            raise KeyboardInterrupt()

    state = InterruptedState()
    monkeypatch.setattr(m, "_State", lambda: state)
    try:
        with pytest.raises(KeyboardInterrupt):
            m._qualify()
        assert calls == ["first-close"]
        assert not any(event[0] == "finish" for event in operator[1])
    finally:
        # This fake never closed a descriptor; test teardown owns these actual FDs.
        real_state.close_all(state)


def test_pending_report_write_failure_is_not_overwritten(operator, monkeypatch):
    names = []
    real = m._Store.save

    def save(store, name, raw):
        names.append(name)
        real(store, name, raw)
        if name == "cases.json":
            raise OSError("after exclusive create")

    monkeypatch.setattr(m._Store, "save", save)
    with pytest.raises(m.QualificationRefused):
        m._qualify()
    assert names == ["policy.json", "authenticated-sources.json", "cases.json"]


def test_failure_evidence_interrupt_does_not_release(operator, monkeypatch):
    real_save = m._Store.save
    monkeypatch.setattr(m, "_authenticate", lambda *args: (_ for _ in ()).throw(ValueError()))

    def save(store, name, raw):
        if name == "cases.json":
            raise KeyboardInterrupt()
        return real_save(store, name, raw)

    monkeypatch.setattr(m._Store, "save", save)
    with pytest.raises(KeyboardInterrupt):
        m._qualify()
    assert not any(event[0] == "finish" for event in operator[1])


@pytest.mark.parametrize("kind", ["missing", "conflict"])
def test_incomplete_or_conflicting_selection_refused(inputs, kind):
    pins, raws = inputs
    if kind == "missing":
        raws[0] = stanza("unselected", "1")
    else:
        name, version = m.selector.TARGETS[0]
        raws[1] += stanza(name, version).replace(b"Size: 1234", b"Size: 1235")
    authenticated = [
        (pin[0], raw, hashlib.sha256(raw).hexdigest()) for pin, raw in zip(pins, raws, strict=True)
    ]
    with pytest.raises(ValueError):
        m._select(authenticated, float("inf"))


def test_metadata_checked_again_after_read(inputs, monkeypatch):
    real_check = m._source_metadata
    calls = []

    def check(fd, size):
        calls.append(fd)
        if len(calls) == 2:
            raise m.QualificationRefused("post_read_metadata")
        return real_check(fd, size)

    monkeypatch.setattr(m, "_source_metadata", check)
    state = m._State()
    try:
        with pytest.raises(m.QualificationRefused, match="post_read_metadata"):
            m._authenticate(state, float("inf"))
        assert len(calls) == 2 and calls[0] == calls[1]
    finally:
        state.close_all()


def test_import_does_not_change_limits(monkeypatch):
    import importlib

    calls = []
    monkeypatch.setattr(m.resource, "setrlimit", lambda *args: calls.append(args))
    importlib.reload(m)
    assert calls == []


def test_explicit_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(m.resource, "getrlimit", lambda kind: (-1, -1))
    monkeypatch.setattr(m.resource, "setrlimit", lambda kind, bound: calls.append((kind, bound)))
    m._operator_limits()
    assert calls == [
        (m.resource.RLIMIT_AS, (536870912, 536870912)),
        (m.resource.RLIMIT_CPU, (30, 30)),
        (m.resource.RLIMIT_NOFILE, (64, 64)),
        (m.resource.RLIMIT_FSIZE, (8388608, 8388608)),
        (m.resource.RLIMIT_CORE, (0, 0)),
    ]


def test_lower_existing_limit_refused(monkeypatch):
    monkeypatch.setattr(m.resource, "getrlimit", lambda kind: (1, 1))
    monkeypatch.setattr(m.resource, "setrlimit", lambda *args: pytest.fail("raised a limit"))
    with pytest.raises(ValueError, match="operator_limits"):
        m._operator_limits()
