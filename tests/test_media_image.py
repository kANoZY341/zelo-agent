"""Synthetic sealed-byte fixtures; never import package contents or launch guests."""

import dataclasses
import fcntl
import hashlib
import json
import os
import resource
import stat
import threading
import zipfile
from types import ModuleType

import pytest

from qualification import media_image as m
from qualification.media_preparation import _expected
from qualification.media_wheel import MediaArtifactError
from tests.media_wheel_fixtures import inspect, make


@pytest.fixture
def candidate():
    data = make(
        add={"PIL/nested/empty.bin": b"", "PIL/nested/data.bin": b"abc" * 100},
        changes={"PIL/Image.py": b"raise RuntimeError('PACKAGE_EXECUTED')"},
    )
    return data, inspect(data)


@pytest.fixture(autouse=True)
def clean_owner_registry():
    assert not m._LIVE_IMAGES
    yield
    # Close-uncertainty fixtures really close once, then report a simulated error.
    # Clearing this private test seam never retries a possibly reused descriptor.
    for owner in tuple(m._LIVE_IMAGES):
        assert owner.state == "closed_failed" and not owner._files
    m._LIVE_IMAGES.clear()


def snapshot():
    return {int(name) for name in os.listdir("/proc/self/fd")}


def test_complete_sealed_identity_modes_and_cleanup(candidate):
    baseline = snapshot()
    with m._seal_image(*candidate) as image:
        identity = image.identity
        assert identity.manifest == _expected(candidate[1])
        assert identity.archive_sha256 == hashlib.sha256(candidate[0]).hexdigest()
        assert identity.inventory_digest == candidate[1].digest
        assert identity.publisher_attribution == "separate" and identity.instruction_trust == "none"
        assert image.state == "ready" and not image.cleanup_confirmed
        for item, fd, _ in image._files:
            info = os.fstat(fd)
            assert info.st_nlink == 0 and info.st_size == item.size
            assert stat.S_IMODE(info.st_mode) == item.mode
            assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & m.SEALS == m.SEALS
            with pytest.raises(OSError):
                os.ftruncate(fd, item.size + 1)
            with pytest.raises(OSError):
                os.pwrite(fd, b"x", 0)
        assert (
            hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(identity), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            == identity.digest
        )
        assert not any(
            hasattr(image, name) for name in ("fd", "fds", "fileno", "approved", "provenance")
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.archive_sha256 = "other"
    assert image.state == "closed" and image.cleanup_confirmed
    assert snapshot() == baseline and not m._LIVE_IMAGES
    assert image.identity == identity  # Historical identity survives handle closure.
    with pytest.raises(MediaArtifactError, match="not_ready"):
        image._begin_consumer()
    image._close()  # Terminal success is idempotent.


def test_direct_construction_refused():
    for constructor in (m.ManagedPillowImage, m.SealedImageIdentity):
        with pytest.raises(TypeError, match="factory_required"):
            constructor()


@pytest.mark.parametrize(
    "value", [None, True, "wheel", b"", bytearray(b"zip"), memoryview(b"zip"), b"bad pin"]
)
def test_public_pin_rejection_before_allocation(monkeypatch, value):
    monkeypatch.setattr(m, "_seal_image", lambda *a, **k: pytest.fail("allocation reached"))
    with pytest.raises(MediaArtifactError):
        with m.seal_pillow_image(value):
            pass


def test_public_arguments_closed():
    with pytest.raises(TypeError):
        m.seal_pillow_image(b"wheel", "caller-path")


def test_private_source_binding_before_allocation(candidate, monkeypatch):
    monkeypatch.setattr(m.os, "memfd_create", lambda *a: pytest.fail("allocation reached"))
    data, inventory = candidate
    with pytest.raises(MediaArtifactError, match="source"):
        with m._seal_image(data + b"x", inventory):
            pass


def test_fixed_closure_mismatch_before_allocation(candidate, monkeypatch):
    monkeypatch.setattr(m.os, "memfd_create", lambda *a: pytest.fail("allocation reached"))
    with pytest.raises(MediaArtifactError, match="fixed_closure"):
        with m._seal_image(*candidate, _fixed=True):
            pass


def test_single_live_owner_and_sequential_rewind(candidate):
    with m._seal_image(*candidate) as image:
        with pytest.raises(MediaArtifactError, match="live_owner"):
            with m._seal_image(*candidate):
                pass
        for _ in range(2):
            handles = image._begin_consumer()
            assert image.state == "consuming"
            with pytest.raises(MediaArtifactError, match="not_ready"):
                image._begin_consumer()
            for item, fd in handles:
                assert os.lseek(fd, 0, os.SEEK_CUR) == 0
                assert hashlib.sha256(os.read(fd, item.size + 1)).hexdigest() == item.sha256
            image._end_consumer(True)
        assert image.state == "ready"


def test_consumer_pending_exit_retains_then_closes(candidate):
    baseline = snapshot()
    with pytest.raises(MediaArtifactError, match="consumer_pending"):
        with m._seal_image(*candidate) as image:
            image._begin_consumer()
    assert image.state == "consuming" and image in m._LIVE_IMAGES
    assert all(os.fstat(fd) for _, fd, _ in image._files)
    image._end_consumer(True)
    assert image.cleanup_confirmed and image.state == "closed"
    assert snapshot() == baseline


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_unknown_consumer_cleanup_fences_reuse(candidate, value):
    baseline = snapshot()
    with pytest.raises(MediaArtifactError, match="consumer_cleanup_unknown"):
        with m._seal_image(*candidate) as image:
            image._begin_consumer()
            with pytest.raises(MediaArtifactError, match="cleanup_unknown"):
                image._end_consumer(value)
            assert image.state == "failed"
            with pytest.raises(MediaArtifactError, match="not_ready"):
                image._begin_consumer()
    assert image in m._LIVE_IMAGES and not image.cleanup_confirmed
    assert all(os.fstat(fd) for _, fd, _ in image._files)
    with pytest.raises(MediaArtifactError, match="live_owner"):
        with m._seal_image(*candidate):
            pass
    with pytest.raises(MediaArtifactError, match="cleanup_unknown"):
        image._settle_failed_consumer(False)
    image._settle_failed_consumer(True)
    assert image.cleanup_confirmed and image.state == "closed"
    assert snapshot() == baseline


def test_mode_mutation_fails_consumption(candidate):
    with m._seal_image(*candidate) as image:
        os.fchmod(image._files[0][1], 0o600)
        with pytest.raises(MediaArtifactError, match="descriptor"):
            image._begin_consumer()
        assert image.state == "failed"


def test_concurrent_consumer_refused(candidate):
    with m._seal_image(*candidate) as image:
        image._begin_consumer()
        failures = []

        def other():
            try:
                image._begin_consumer()
            except MediaArtifactError as error:
                failures.append(str(error))

        thread = threading.Thread(target=other)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive() and failures == ["media_image_not_ready"]
        image._end_consumer(True)


def test_partial_writes_and_empty_files(candidate, monkeypatch):
    original = os.write
    sizes = []

    def partial(fd, data):
        sizes.append(len(data))
        return original(fd, data[:7])

    monkeypatch.setattr(m.os, "write", partial)
    with m._seal_image(*candidate) as image:
        assert any(item.size == 0 for item, _, _ in image._files)
    assert sizes and max(sizes) <= 65536


@pytest.mark.parametrize("value", [0, -1, None, True, "1", 10**9])
def test_write_no_progress_or_invalid_count_closes_everything(candidate, monkeypatch, value):
    baseline = snapshot()
    monkeypatch.setattr(m.os, "write", lambda *a: value)
    with pytest.raises(MediaArtifactError, match="write_stalled"):
        with m._seal_image(*candidate):
            pass
    assert snapshot() == baseline


@pytest.mark.parametrize("phase", ["create", "stat", "chmod", "seal", "read", "hash", "archive"])
def test_construction_failure_cleanup(candidate, monkeypatch, phase):
    baseline = snapshot()

    def refused(*a, **k):
        raise OSError("fixture failure")

    if phase == "create":
        original = os.memfd_create
        calls = 0

        def create(*a):
            nonlocal calls
            calls += 1
            return original(*a) if calls < 4 else refused()

        monkeypatch.setattr(m.os, "memfd_create", create)
    elif phase == "stat":
        # Pytest also calls os.fstat; inject the failure only into this module.
        isolated_os = ModuleType("media_image_test_os")
        isolated_os.__dict__.update(vars(os))
        monkeypatch.setattr(m, "os", isolated_os)
        monkeypatch.setattr(m.os, "fstat", refused)
    elif phase == "chmod":
        monkeypatch.setattr(m.os, "fchmod", refused)
    elif phase == "seal":
        original = fcntl.fcntl
        monkeypatch.setattr(
            m.fcntl,
            "fcntl",
            lambda fd, op, *a: refused() if op == fcntl.F_ADD_SEALS else original(fd, op, *a),
        )
    elif phase == "read":
        monkeypatch.setattr(m.os, "read", refused)
    elif phase == "hash":
        monkeypatch.setattr(m.os, "read", lambda *a: b"x" * 65536)
    else:
        monkeypatch.setattr(m.zipfile, "ZipFile", refused)
    with pytest.raises((OSError, MediaArtifactError)):
        with m._seal_image(*candidate):
            pass
    assert snapshot() == baseline and not m._LIVE_IMAGES


def test_missing_seals_fail_before_identity(candidate, monkeypatch):
    original = fcntl.fcntl
    monkeypatch.setattr(
        m.fcntl, "fcntl", lambda fd, op, *a: 0 if op == fcntl.F_GET_SEALS else original(fd, op, *a)
    )
    with pytest.raises(MediaArtifactError, match="descriptor"):
        with m._seal_image(*candidate):
            pass


def test_insufficient_descriptor_limit_before_creation(candidate, monkeypatch):
    monkeypatch.setattr(m.resource, "getrlimit", lambda *a: (1, 1))
    monkeypatch.setattr(m.os, "memfd_create", lambda *a: pytest.fail("allocation reached"))
    with pytest.raises(MediaArtifactError, match="descriptor_capacity"):
        with m._seal_image(*candidate):
            pass


def test_file_count_cap_before_creation(monkeypatch):
    data = make(add={f"PIL/extra-{i}": b"x" for i in range(141)})
    inventory = inspect(data)
    monkeypatch.setattr(m.os, "memfd_create", lambda *a: pytest.fail("allocation reached"))
    with pytest.raises(MediaArtifactError, match="file_count"):
        with m._seal_image(data, inventory):
            pass


@pytest.mark.parametrize("index", range(len(inspect(make()).files) + 2))
def test_each_close_failure_attempts_all_and_fences_new_owner(candidate, monkeypatch, index):
    original = os.close
    closed = []
    with pytest.raises(MediaArtifactError, match="close_unknown"):
        with m._seal_image(*candidate) as image:
            fds = [fd for _, fd, _ in image._files]
            assert index < len(fds)

            def close(fd):
                original(fd)
                closed.append(fd)
                if fd == fds[index]:
                    raise OSError("reported-after-close fixture")

            monkeypatch.setattr(m.os, "close", close)
    assert closed == fds and not image.cleanup_confirmed and image.state == "closed_failed"
    with pytest.raises(MediaArtifactError, match="live_owner"):
        with m._seal_image(*candidate):
            pass
    with pytest.raises(MediaArtifactError, match="close_unknown"):
        image._close()
    assert closed == fds  # Failed Linux close is never retried.


def test_larger_streams_keep_read_and_write_buffers_bounded(monkeypatch):
    data = make(add={"PIL/large.dat": b"x" * 200000})
    inventory = inspect(data)
    original = zipfile.ZipExtFile.read
    requests = []

    def bounded(source, count=-1):
        requests.append(count)
        assert 0 < count <= 65536
        return original(source, count)

    monkeypatch.setattr(m.zipfile.ZipExtFile, "read", bounded)
    with m._seal_image(data, inventory):
        pass
    assert max(requests) == 65536


def test_policy_is_uncached_and_limits_are_not_changed(candidate):
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    value = m.image_policy()
    value["seals"] = ()
    assert m.image_policy()["seals"] == ("WRITE", "GROW", "SHRINK", "SEAL")
    with m._seal_image(*candidate):
        pass
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == limits


def test_registration_allocation_failure_closes_unregistered_fd(candidate, monkeypatch):
    baseline = snapshot()
    original = m._new_owner

    class RefusedList(list):
        def append(self, value):
            raise MemoryError("registration fixture")

    def owner():
        result = original()
        result._files = RefusedList()
        return result

    monkeypatch.setattr(m, "_new_owner", owner)
    with pytest.raises(MemoryError):
        with m._seal_image(*candidate):
            pass
    assert snapshot() == baseline and not m._LIVE_IMAGES


def test_registration_close_unknown_blocks_new_owner(candidate, monkeypatch):
    original = m._new_owner
    original_close = os.close

    class RefusedList(list):
        def append(self, value):
            raise MemoryError("registration fixture")

    def owner():
        result = original()
        result._files = RefusedList()
        return result

    def close(fd):
        original_close(fd)
        raise OSError("reported-after-close fixture")

    monkeypatch.setattr(m, "_new_owner", owner)
    monkeypatch.setattr(m.os, "close", close)
    with pytest.raises(MediaArtifactError, match="close_unknown"):
        with m._seal_image(*candidate):
            pass
    assert len(m._LIVE_IMAGES) == 1
    with pytest.raises(MediaArtifactError, match="live_owner"):
        with m._seal_image(*candidate):
            pass
