"""Offline acquisition orchestration: synthetic archives, CA and transport only."""

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import qualify_media_package_acquisition as q
from tests.test_media_package_acquisition import WireSocket, wire

m = q.m


@pytest.fixture
def operator(tmp_path, monkeypatch):
    lock = json.loads((q.ROOT / q.LOCK_PATH).read_bytes())
    payloads = {}
    for number, row in enumerate(lock["packages"]):
        raw = (f"synthetic archive {number}\n").encode()
        row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        payloads[row["request_path"]] = raw
    lock["aggregate_bytes"] = sum(map(len, payloads.values()))
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o700)
    path = root / q.LOCK_PATH
    path.parent.mkdir(parents=True)
    raw = json.dumps(lock).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(m, "LOCK_SHA", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(m, "TOTAL_ARCHIVE_BYTES", lock["aggregate_bytes"])
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.shared, "ROOT", root)
    monkeypatch.setattr(q.os, "getresuid", lambda: (1000,) * 3)
    monkeypatch.setattr(q.os, "getresgid", lambda: (1000,) * 3)
    events, sockets = [], []
    state = q._State()
    monkeypatch.setattr(q, "_State", lambda: state)

    class Lease:
        def finish(self, *, cleanup_confirmed):
            events.append(("finish", cleanup_confirmed))

    class Admission:
        def __init__(self, path):
            assert path == root / q.ADMISSION

        def acquire(self, digest):
            events.append(("acquire", digest))
            return Lease()

    monkeypatch.setattr(q, "AdmissionController", Admission)
    monkeypatch.setattr(q, "_baseline", lambda *args: events.append(("baseline",)))
    monkeypatch.setattr(q, "_root_bytes", lambda *args: b"synthetic CA")
    monkeypatch.setattr(m, "tls_context", lambda ca: object())
    monkeypatch.setattr(m.socket, "getaddrinfo", lambda *a: pytest.fail("real DNS"))
    monkeypatch.setattr(m.socket, "socket", lambda *a: pytest.fail("real socket"))
    monkeypatch.setattr(m.ssl, "SSLContext", lambda *a: pytest.fail("native TLS context"))
    original = m.http.client.HTTPConnection
    configuration = SimpleNamespace(transform=lambda path, raw: wire(raw))

    class Connection(original):
        def __init__(self, host, port, *, timeout, context):
            assert (host, port, timeout) == (m.HOST, 443, 10)
            super().__init__(host, port, timeout=timeout)

        def request(self, method, url, **kwargs):
            self.fixture_path = url
            events.append(("request", url))
            super().request(method, url, **kwargs)

        def connect(self):
            self.sock = WireSocket(
                configuration.transform(self.fixture_path, payloads[self.fixture_path])
            )
            sockets.append(self.sock)

    monkeypatch.setattr(m.http.client, "HTTPSConnection", Connection)
    yield SimpleNamespace(
        root=root,
        events=events,
        sockets=sockets,
        state=state,
        lock=lock,
        payloads=payloads,
        configuration=configuration,
    )
    # Test-only teardown for retained, never-closed synthetic support descriptors.
    # Production never retries a partially attempted close sequence.
    if state.fds:
        state.close_all()


def test_success_pending_proof_and_all_archives_held_before_single_release(operator, monkeypatch):
    real_save, real_release = q._Store.save, q._release

    def save(store, name, raw):
        assert not any(event[0] == "finish" for event in operator.events)
        if name == "cases.json":
            assert len(operator.state.fds) == 9  # fixture lock plus eight archive FDs
            assert json.loads(raw)["support_release_confirmed"] is False
        operator.events.append(("save", name))
        return real_save(store, name, raw)

    def release(state, lease):
        assert operator.events[-1] == ("save", "cases.json")
        assert len(state.fds) == 9
        return real_release(state, lease)

    monkeypatch.setattr(q._Store, "save", save)
    monkeypatch.setattr(q, "_release", release)
    receipt, _ = q._qualify()
    assert operator.events[-1] == ("finish", True)
    assert operator.state.fds == []
    assert receipt["support_release_confirmed"] is True
    path = Path(receipt["proof"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["proof_sha256"]
    assert len(list(path.parent.iterdir())) == 11
    manifest_raw = (path.parent / "manifest.json").read_bytes()
    assert (
        json.loads(path.read_bytes())["manifest_sha256"] == hashlib.sha256(manifest_raw).hexdigest()
    )
    manifest = json.loads(manifest_raw)
    assert manifest["archive_hash_verified"] is True
    assert manifest["package_member_origin_verified"] is False
    assert manifest["runtime_authority"] is False
    assert len(operator.sockets) == 8
    assert all(sock.file.closures == sock.closures == 1 for sock in operator.sockets)
    for row in operator.lock["packages"]:
        assert (path.parent / row["output_name"]).read_bytes() == operator.payloads[
            row["request_path"]
        ]


@pytest.mark.parametrize("fault", ["short", "hash", "length", "redirect", "duplicate"])
def test_bad_transfer_never_receipts_or_requests_second_archive(operator, fault):
    def transform(path, raw):
        if fault == "short":
            return wire(raw[:-1], headers=[f"Content-Length: {len(raw)}".encode()])
        if fault == "hash":
            return wire(b"x" * len(raw))
        if fault == "length":
            return wire(raw, headers=[b"Content-Length: 999"])
        if fault == "redirect":
            return b"HTTP/1.1 302 Found\r\nLocation: https://other.invalid/\r\n\r\n"
        return wire(raw, headers=[f"Content-Length: {len(raw)}".encode()] * 2)

    operator.configuration.transform = transform
    with pytest.raises(m.Refused):
        q._qualify()
    assert len(operator.sockets) == 1
    proof = next((operator.root / q.PROOF_BASE).glob("*/cases.json"))
    assert json.loads(proof.read_bytes()) == {
        "status": "refused",
        "support_release_confirmed": False,
    }
    assert len(proof.read_bytes()) < 100
    assert operator.events[-1] == ("finish", fault != "redirect")


def test_held_archive_readback_detects_post_transfer_corruption(operator, monkeypatch):
    original = q._fetch
    count = []

    def fetch(*args):
        fd = original(*args)
        count.append(fd)
        if len(count) == 8:
            os.pwrite(count[0], b"X", 0)
        return fd

    monkeypatch.setattr(q, "_fetch", fetch)
    with pytest.raises(m.Refused):
        q._qualify()
    assert len(count) == 8 and operator.events[-1] == ("finish", True)


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_late_release_error_not_retried(operator, monkeypatch, error):
    original = q._release
    calls = []

    def release(state, lease):
        calls.append(1)
        original(state, lease)
        raise error("late finish failure")

    monkeypatch.setattr(q, "_release", release)
    with pytest.raises(error):
        q._qualify()
    assert calls == [1] and operator.events[-1] == ("finish", True)


def test_interrupt_preserves_support_ownership_without_release(operator, monkeypatch):
    monkeypatch.setattr(q, "_baseline", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(q, "_release", lambda *a: pytest.fail("release after interrupt"))
    with pytest.raises(KeyboardInterrupt):
        q._qualify()
    assert not any(event[0] == "finish" for event in operator.events)
    assert q._QUARANTINED[-1][0] is operator.state


def test_unconfirmed_cleanup_refuses_success(operator, monkeypatch):
    original = q._release

    def release(state, lease):
        state.close_ok = False
        return original(state, lease)

    monkeypatch.setattr(q, "_release", release)
    with pytest.raises(m.Refused, match="support_cleanup"):
        q._qualify()
    assert operator.events[-1] == ("finish", False)


def test_four_partial_attempts_exhaust_scope(operator):
    base = operator.root / q.PROOF_BASE
    for _ in range(4):
        q._proof_directory(base)
    with pytest.raises(m.Refused, match="attempt_limit"):
        q._proof_directory(base)
    assert len(list(base.iterdir())) == 4


@pytest.mark.parametrize("fault", ["foreign", "symlink", "mode", "oversize", "entries"])
def test_old_proof_malformed_refused_without_repair(operator, fault):
    base = operator.root / q.PROOF_BASE
    base.mkdir(mode=0o700)
    path = base / ("foreign" if fault == "foreign" else q.PREFIX + "old")
    path.mkdir(mode=0o700)
    if fault == "symlink":
        (path / "link").symlink_to(operator.root / q.LOCK_PATH)
    elif fault == "mode":
        path.chmod(0o755)
    elif fault in ("oversize", "entries"):
        for n in range(17 if fault == "entries" else 1):
            file = path / str(n)
            with file.open("wb") as stream:
                if fault == "oversize":
                    stream.truncate(m.ARCHIVE_BYTES + 1)
            file.chmod(0o600)
    with pytest.raises(ValueError):
        q._proof_directory(base)
    assert list(base.iterdir()) == [path]


@pytest.fixture
def ledger(tmp_path):
    state = q._State()
    value = q.Ledger(tmp_path, state)
    yield value
    state.close_all()


def test_archive_exclusive_creation_and_precharged_failure(ledger):
    row = {"output_name": "fixed.deb", "bytes": 3}
    target = ledger.path / row["output_name"]
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        ledger.archive(row)
    assert ledger.reserved == ledger.archive_reserved == 3
    assert target.read_bytes() == b"old"


def test_short_writes_loop_and_charge_once(ledger, monkeypatch):
    row = {"output_name": "fixed.deb", "bytes": 3}
    fd = ledger.archive(row)
    original = os.write
    calls = []

    def partial(fd, view):
        calls.append(len(view))
        return original(fd, view[:1])

    monkeypatch.setattr(q.os, "write", partial)
    ledger.write(row, fd, b"abc", float("inf"))
    assert calls == [3, 2, 1] and ledger.written["fixed.deb"] == 3
    assert (ledger.path / "fixed.deb").read_bytes() == b"abc"
    with pytest.raises(m.Refused, match="write_budget"):
        ledger.write(row, fd, b"x", float("inf"))


def test_zero_write_retains_full_charge(ledger, monkeypatch):
    row = {"output_name": "fixed.deb", "bytes": 3}
    fd = ledger.archive(row)
    monkeypatch.setattr(q.os, "write", lambda *a: 0)
    with pytest.raises(m.Refused, match="short_write"):
        ledger.write(row, fd, b"abc", float("inf"))
    assert ledger.written["fixed.deb"] == 3


@pytest.mark.parametrize("fault", ["bytes", "entries", "duplicate"])
def test_ledger_reservation_precedes_file_creation(ledger, fault):
    if fault == "bytes":
        ledger.reserved = q.ATTEMPT_BYTES
    elif fault == "entries":
        ledger.names = {str(i) for i in range(q.ATTEMPT_ENTRIES)}
    else:
        ledger.names.add("policy.json")
    with pytest.raises(m.Refused, match="retained_budget"):
        ledger.json("policy.json", {})
    assert list(ledger.path.iterdir()) == []


def test_json_refuses_overwrite_and_reserves_worst_case(ledger):
    ledger.json("cases.json", {"status": "pending"})
    assert ledger.reserved == q.JSON_BYTES
    with pytest.raises(m.Refused):
        ledger.json("cases.json", {"status": "success"})
    assert json.loads((ledger.path / "cases.json").read_bytes()) == {"status": "pending"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("st_uid", 1000),
        ("st_gid", 1000),
        ("st_mode", stat.S_IFREG | 0o600),
        ("st_nlink", 2),
        ("st_size", 0),
    ],
)
def test_root_metadata_exact_without_runner_uid_assumptions(monkeypatch, field, value):
    values = dict(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o644, st_nlink=1, st_size=3)
    values[field] = value
    monkeypatch.setattr(q.os, "fstat", lambda fd: SimpleNamespace(**values))
    with pytest.raises(m.Refused, match="root_source"):
        q._root_info(999, 10, 3)


def test_cli_error_is_constant_no_peer_text(monkeypatch, capsys):
    monkeypatch.setattr(q.sys, "argv", ["qualify", "--run"])
    monkeypatch.setattr(q, "_qualify", lambda: (_ for _ in ()).throw(ValueError("PEER SECRET")))
    assert q.main() == 1
    result = capsys.readouterr()
    assert result.out == "" and result.err == "acquisition_refused\n"


@pytest.mark.parametrize("stage", ["request", "read", "close"])
def test_opaque_network_failure_fences_without_retry(operator, monkeypatch, stage):
    original = m.NetworkOwner
    calls = []

    class Failing(original):
        def request(self, path):
            if stage == "request":
                self.unknown()
                raise OSError("opaque request")
            return super().request(path)

        def read1(self, count):
            if stage == "read":
                self.unknown()
                raise OSError("opaque read")
            return super().read1(count)

        def cleanup(self):
            calls.append(1)
            if stage == "close":
                # A fully closed synthetic transport followed by an opaque failure.
                super().cleanup()
                self.unknown()
                raise OSError("opaque close")
            return super().cleanup()

    monkeypatch.setattr(m, "NetworkOwner", Failing)
    with pytest.raises(m.Refused):
        q._qualify()
    assert calls == [1] and operator.events[-1] == ("finish", False)
    assert len(operator.sockets) <= 1


def test_deadline_after_release_refuses_receipt(operator, monkeypatch):
    original = q._release
    clock = [0]
    monkeypatch.setattr(q.time, "monotonic", lambda: clock[0])

    def release(state, lease):
        result = original(state, lease)
        clock[0] = q.WHOLE_SECONDS
        return result

    monkeypatch.setattr(q, "_release", release)
    with pytest.raises(m.Refused, match="deadline"):
        q._qualify()
    assert operator.events[-1] == ("finish", True)


def test_no_filesystem_work_after_release(operator, monkeypatch):
    original = q._release

    def release(state, lease):
        result = original(state, lease)
        for name in ("open", "read", "write", "fstat", "stat", "fsync", "close"):
            monkeypatch.setattr(q.os, name, lambda *a, **k: pytest.fail("post-release file IO"))
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(q, "_release", release)
        receipt, _ = q._qualify()
        assert receipt["support_release_confirmed"] is True
    # Undo the os patches before pytest's own capture/teardown needs file IO.
    monkeypatch.undo()


def test_combined_archive_and_json_budget(ledger, monkeypatch):
    monkeypatch.setattr(m, "TOTAL_ARCHIVE_BYTES", q.ATTEMPT_BYTES)
    for n in range(3):
        ledger.archive({"output_name": f"{n}.deb", "bytes": m.ARCHIVE_BYTES})
    ledger.json("policy.json", {})
    ledger.json("manifest.json", {})
    with pytest.raises(m.Refused, match="retained_budget"):
        ledger.json("cases.json", {})
    assert not (ledger.path / "cases.json").exists()


def test_archive_aggregate_precedes_creation(ledger, monkeypatch):
    monkeypatch.setattr(m, "TOTAL_ARCHIVE_BYTES", 2)
    with pytest.raises(m.Refused, match="archive_budget"):
        ledger.archive({"output_name": "fixed.deb", "bytes": 3})
    assert list(ledger.path.iterdir()) == [] and ledger.reserved == 0


def test_baseline_rejects_wrong_import_before_read(monkeypatch, tmp_path):
    wrong = tmp_path / "client.py"
    wrong.write_bytes(b"synthetic")
    monkeypatch.setattr(m.http.client, "__file__", str(wrong))
    monkeypatch.setattr(q, "_root_bytes", lambda *a: pytest.fail("read wrong imported baseline"))
    with pytest.raises(m.Refused, match="import_baseline"):
        q._baseline(q._State(), float("inf"))


def test_root_source_symlink_rejected_before_open(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic")
    link = tmp_path / "link"
    link.symlink_to(source)
    monkeypatch.setattr(q, "_open", lambda *a: pytest.fail("opened alias"))
    with pytest.raises(m.Refused, match="root_path"):
        q._root_bytes(q._State(), str(link), 20)
