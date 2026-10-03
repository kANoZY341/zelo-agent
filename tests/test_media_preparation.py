"""Private synthetic bytes-to-file fixtures; never import prepared package code."""

import dataclasses
import hashlib
import io
import json
import os
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from qualification import media_preparation as m
from qualification import media_wheel as wheel
from tests.media_wheel_fixtures import inspect, make


@pytest.fixture
def candidate():
    data = make(
        add={"PIL/nested/deeper/empty.bin": b"", "PIL/nested/deeper/data.bin": b"abc" * 100},
        directories=wheel.REQUIRED_DIRECTORIES | {"PIL/empty"},
        changes={"PIL/Image.py": b"raise RuntimeError('PREPARED_CODE_EXECUTED')"},
    )
    return data, inspect(data)


def prepare(candidate, base):
    return m._prepare_files(*candidate, base)


def test_exact_copy_full_inventory_modes_roles_and_frozen_receipt(candidate, tmp_path):
    base = tmp_path / "private"
    receipt = prepare(candidate, base)
    payload = base / receipt.relative_payload
    inventory = candidate[1]
    assert receipt.archive_sha256 == hashlib.sha256(candidate[0]).hexdigest()
    assert receipt.inventory_digest == inventory.digest
    assert receipt.manifest == m._expected(inventory)
    assert len(receipt.manifest.roles) == 26
    assert receipt.manifest.version == "zelo-browser-artifacts-v1"
    assert receipt.publisher_attribution == "unverified" and receipt.instruction_trust == "none"
    assert stat.S_IMODE(base.stat().st_mode) == 0o700
    assert stat.S_IMODE(payload.parent.stat().st_mode) == 0o700
    assert receipt.root_identity == (payload.stat().st_dev, payload.stat().st_ino)
    for item in inventory.files:
        path = payload / item.path
        assert path.stat().st_nlink == 1 and path.stat().st_uid == os.getuid()
        assert path.stat().st_size == item.size
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256
        assert stat.S_IMODE(path.stat().st_mode) == (
            0o500 if item.path in m.NATIVE_PATHS else 0o400
        )
    for path, mode in receipt.manifest.directories:
        assert stat.S_IMODE((payload / path).stat().st_mode) == mode == 0o500
    assert not any(
        hasattr(receipt, k) for k in ("approved", "success", "cleanup_confirmed", "provenance")
    )
    assert (
        receipt.digest
        == hashlib.sha256(
            json.dumps(
                dataclasses.asdict(receipt), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.relative_payload = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.manifest.files[0].path = "other"
    with pytest.raises(TypeError, match="scanner_required"):
        m.PreparedPillowFiles(**dataclasses.asdict(receipt))
    with pytest.raises(TypeError):
        m.prepare_pillow_files(candidate[0], tmp_path)


@pytest.mark.parametrize(
    "value", [None, True, "wheel", b"", bytearray(b"zip"), memoryview(b"zip"), b"bad pin"]
)
def test_public_rejects_before_filesystem(monkeypatch, value):
    monkeypatch.setattr(m, "_prepare_files", lambda *a: pytest.fail("filesystem reached"))
    with pytest.raises(wheel.MediaArtifactError):
        m.prepare_pillow_files(value)


def test_attempt_bound_retains_exact_four_and_refuses_fifth(candidate, tmp_path):
    base = tmp_path / "private"
    receipts = [prepare(candidate, base) for _ in range(4)]
    assert len({r.relative_payload for r in receipts}) == 4
    before = sorted(base.iterdir())
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, base)
    assert sorted(base.iterdir()) == before


@pytest.mark.parametrize("kind", ["file", "symlink", "public", "unknown_entry", "symlink_entry"])
def test_unsafe_diagnostic_roots_and_retained_entries(candidate, tmp_path, kind):
    base = tmp_path / "private"
    if kind == "file":
        base.write_bytes(b"preserve")
    elif kind == "symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        base.symlink_to(elsewhere, target_is_directory=True)
    else:
        base.mkdir(mode=0o755 if kind == "public" else 0o700)
        if kind == "unknown_entry":
            (base / "keep.txt").write_bytes(b"preserve")
        elif kind == "symlink_entry":
            (base / "attempt-link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(wheel.MediaArtifactError):
        prepare(candidate, base)
    if kind == "file":
        assert base.read_bytes() == b"preserve"
    elif kind == "unknown_entry":
        assert (base / "keep.txt").read_bytes() == b"preserve"


@pytest.mark.parametrize("count", [1, 3, 17, 65536])
def test_short_writes_loop(candidate, tmp_path, monkeypatch, count):
    original = os.write
    monkeypatch.setattr(m.os, "write", lambda fd, block: original(fd, block[:count]))
    assert prepare(candidate, tmp_path / "private").manifest == m._expected(candidate[1])


@pytest.mark.parametrize("value", [0, -1, None, True, "1", 999999])
def test_stalled_or_invalid_write_preserves_failed_attempt(candidate, tmp_path, monkeypatch, value):
    base = tmp_path / "private"
    monkeypatch.setattr(m.os, "write", lambda *a: value)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, base)
    assert len(list(base.iterdir())) == 1
    assert not list(base.rglob("*receipt*"))


@pytest.mark.parametrize(
    "method,nth",
    [
        ("write", 1),
        ("write", 20),
        ("fsync", 1),
        ("fsync", 8),
        ("fsync", 60),
        ("fchmod", 1),
        ("fchmod", 20),
        ("mkdir", 2),
        ("mkdir", 4),
        ("open", 1),
        ("open", 10),
        ("stat", 5),
        ("fstat", 10),
    ],
)
def test_io_failure_never_returns_receipt(candidate, tmp_path, monkeypatch, method, nth):
    original = getattr(m.os, method)
    calls = 0

    def broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == nth:
            raise OSError("fixture_io_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(m.os, method, broken)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")
    assert calls >= nth


@pytest.mark.parametrize("nth", [1, 5, 20, 60, 100])
def test_close_failure_still_attempts_all_other_descriptors(candidate, tmp_path, monkeypatch, nth):
    original_open, original_dup, original_close = os.open, os.dup, os.close
    live = set()
    calls = 0

    def opened(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        live.add(fd)
        return fd

    def duplicated(*args, **kwargs):
        fd = original_dup(*args, **kwargs)
        live.add(fd)
        return fd

    def closed(fd):
        nonlocal calls
        calls += 1
        original_close(fd)
        live.discard(fd)
        if calls == nth:
            raise OSError("fixture_close_failure_after_kernel_close")

    monkeypatch.setattr(m.os, "open", opened)
    monkeypatch.setattr(m.os, "dup", duplicated)
    monkeypatch.setattr(m.os, "close", closed)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")
    assert calls >= nth and not live


@pytest.mark.parametrize("delta", [-1, 1])
def test_second_read_exact_size(candidate, tmp_path, monkeypatch, delta):
    original = zipfile.ZipFile.open
    target = next(item for item in candidate[1].files if item.path == "PIL/Image.py")

    def altered(self, name, *args, **kwargs):
        if name == target.path:
            return io.BytesIO(b"x" * (target.size + delta))
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(m.zipfile.ZipFile, "open", altered)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")


def test_second_read_same_size_wrong_hash(candidate, tmp_path, monkeypatch):
    original = zipfile.ZipFile.open
    target = next(item for item in candidate[1].files if item.path == "PIL/Image.py")

    def altered(self, name, *args, **kwargs):
        if name == target.path:
            return io.BytesIO(b"x" * target.size)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(m.zipfile.ZipFile, "open", altered)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")


def test_second_stream_crc_failure(tmp_path):
    data = make(compression=zipfile.ZIP_STORED)
    inventory = inspect(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        item = archive.getinfo("PIL/Image.py")
    raw = bytearray(data)
    name, extra = struct.unpack_from("<HH", raw, item.header_offset + 26)
    raw[item.header_offset + 30 + name + extra] ^= 1
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        m._prepare_files(bytes(raw), inventory, tmp_path / "private")


@pytest.mark.parametrize(
    "field",
    [
        "files_hash",
        "files_mode",
        "files_size",
        "files_extra",
        "directory_mode",
        "directory_extra",
        "roles",
        "policy",
        "version",
    ],
)
def test_independent_scan_exact_comparison(candidate, tmp_path, monkeypatch, field):
    original = m.capture_manifest

    def mismatched(*args):
        actual = original(*args)
        first = actual.files[0]
        if field.startswith("files_"):
            if field == "files_extra":
                files = actual.files + (dataclasses.replace(first, path="PIL/extra"),)
            else:
                key, value = {
                    "files_hash": ("sha256", "0" * 64),
                    "files_mode": ("mode", 0o600),
                    "files_size": ("size", first.size + 1),
                }[field]
                files = (dataclasses.replace(first, **{key: value}),) + actual.files[1:]
            return dataclasses.replace(actual, files=files)
        if field == "directory_mode":
            return dataclasses.replace(actual, directories=(("", 0o700),) + actual.directories[1:])
        if field == "directory_extra":
            return dataclasses.replace(
                actual, directories=actual.directories + (("PIL/extra", 0o500),)
            )
        if field == "roles":
            return dataclasses.replace(actual, roles=actual.roles[:-1])
        if field == "policy":
            return dataclasses.replace(
                actual, policy=dataclasses.replace(actual.policy, max_depth=7)
            )
        return dataclasses.replace(actual, version="forged")

    monkeypatch.setattr(m, "capture_manifest", mismatched)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")


@pytest.mark.parametrize("stage", ["before", "after"])
def test_root_identity_bound_around_scan(candidate, tmp_path, monkeypatch, stage):
    original = m._match_root
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        value = original(*args)
        return (value[0], value[1] + 1) if calls == (1 if stage == "before" else 2) else value

    monkeypatch.setattr(m, "_match_root", changed)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")


def test_real_root_replacement_after_scan(candidate, tmp_path, monkeypatch):
    original = m.capture_manifest

    def replaced(path, *args):
        actual = original(path, *args)
        path.rename(path.with_name("preserved-payload"))
        path.mkdir(mode=0o500)
        return actual

    monkeypatch.setattr(m, "capture_manifest", replaced)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")


def test_no_production_import_or_pillow_execution():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "app").rglob("*.py"):
        assert "media_preparation" not in path.read_text()
    source = Path(m.__file__).read_text()
    assert "extractall(" not in source and ".extract(" not in source
    assert "import PIL" not in source and "subprocess" not in source


def test_implicit_directory_budget_before_destination(tmp_path):
    data = make(add={f"PIL/p{i}/a/b/c/d/e/data": b"x" for i in range(80)})
    base = tmp_path / "private"
    with pytest.raises(wheel.MediaArtifactError, match="entry_limit"):
        m._prepare_files(data, inspect(data), base)
    assert not base.exists()


def test_exact_entry_budget_including_empty_directories(tmp_path):
    directories = wheel.REQUIRED_DIRECTORIES | {
        f"PIL/empty{i}"
        for i in range(
            wheel.ENTRY_LIMIT - len(wheel.REQUIRED_FILES) - len(wheel.REQUIRED_DIRECTORIES)
        )
    }
    data = make(directories=directories)
    inventory = inspect(data)
    receipt = m._prepare_files(data, inventory, tmp_path / "private")
    assert len(receipt.manifest.files) + len(receipt.manifest.directories) - 1 == 512


def test_symlink_directory_substitution_never_writes_outside(candidate, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    original = os.mkdir
    triggered = False

    def substituted(path, mode=0o777, *, dir_fd=None):
        nonlocal triggered
        original(path, mode, dir_fd=dir_fd)
        if path == "PIL" and not triggered:
            triggered = True
            os.rmdir(path, dir_fd=dir_fd)
            os.symlink(outside, path, dir_fd=dir_fd)

    monkeypatch.setattr(m.os, "mkdir", substituted)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")
    assert triggered and list(outside.iterdir()) == []


def test_hardlink_injected_file_rejected_before_write(candidate, tmp_path, monkeypatch):
    original = os.open
    triggered = False

    def linked(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal triggered
        fd = original(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_CREAT and not triggered:
            triggered = True
            os.link(path, "retained-hardlink", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        return fd

    monkeypatch.setattr(m.os, "open", linked)
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, tmp_path / "private")
    assert triggered


def test_exclusive_file_creation_preserves_existing_bytes(candidate, tmp_path, monkeypatch):
    original = os.open
    triggered = False

    def existing(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal triggered
        if flags & os.O_CREAT and not triggered:
            triggered = True
            fd = original(path, flags, mode, dir_fd=dir_fd)
            try:
                os.write(fd, b"preserve-existing")
            finally:
                os.close(fd)
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(m.os, "open", existing)
    base = tmp_path / "private"
    with pytest.raises(wheel.MediaArtifactError, match="failed"):
        prepare(candidate, base)
    retained = [path for path in base.rglob("*") if path.is_file()]
    assert triggered and len(retained) == 1
    assert retained[0].read_bytes() == b"preserve-existing"


def test_descriptor_high_water_and_bounded_reads(tmp_path, monkeypatch):
    data = make(add={"PIL/a/b/c/d/e/f/data": b"x" * 200000})
    inventory = inspect(data)
    original_open, original_dup, original_close = os.open, os.dup, os.close
    original_stream = zipfile.ZipFile.open
    live = set()
    peak = 0
    sizes = []

    def opened(*args, **kwargs):
        nonlocal peak
        fd = original_open(*args, **kwargs)
        live.add(fd)
        peak = max(peak, len(live))
        return fd

    def duplicated(*args, **kwargs):
        nonlocal peak
        fd = original_dup(*args, **kwargs)
        live.add(fd)
        peak = max(peak, len(live))
        return fd

    def closed(fd):
        original_close(fd)
        live.discard(fd)

    class Stream:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            self.source.__enter__()
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def read(self, size):
            sizes.append(size)
            assert 1 <= size <= 65536
            return self.source.read(size)

    monkeypatch.setattr(m.os, "open", opened)
    monkeypatch.setattr(m.os, "dup", duplicated)
    monkeypatch.setattr(m.os, "close", closed)
    monkeypatch.setattr(m.zipfile.ZipFile, "open", lambda *a, **k: Stream(original_stream(*a, **k)))
    m._prepare_files(data, inventory, tmp_path / "private")
    assert sizes and max(sizes) == 65536 and not live
    assert peak <= 14


def test_normalization_not_immutable_and_scanner_detects_later_change(candidate, tmp_path):
    from qualification.artifacts import verify_manifest

    base = tmp_path / "private"
    receipt = prepare(candidate, base)
    payload = base / receipt.relative_payload
    path = payload / "PIL/Image.py"
    path.chmod(0o600)
    path.write_bytes(b"changed")
    path.chmod(0o400)
    with pytest.raises(ValueError, match="artifact_manifest_mismatch"):
        verify_manifest(payload, m.ROLES, m.SCAN_POLICY, receipt.manifest, receipt.manifest.digest)


def test_policy_vector_closed_and_uncached():
    assert (
        m.PREPARATION_POLICY_DIGEST
        == "8cece7f82aa8b189ffbd2b254e087d5f67a4de3fb97805d16198a4b0092164ec"
    )
    policy = m.preparation_policy()
    policy["files"]["data"] = 0o777
    assert m.preparation_policy()["files"]["data"] == 0o400
    assert m.ROLES == tuple(
        (f"native_{i:02d}", path)
        for i, path in enumerate(sorted(wheel.NATIVE_EXTENSIONS + wheel.BUNDLED_LIBRARIES))
    )


def test_guarded_fresh_process_copy_has_no_package_process_network_or_environment(
    candidate, tmp_path
):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    data, inventory = candidate
    base = tmp_path / "guarded"
    code = f"""import builtins, os, sys
sys.path.insert(0, {str(root)!r})
from pathlib import Path
from qualification import media_preparation as m
from qualification import media_wheel as wheel
data = {data!r}
inventory = wheel._inspect_archive(data, {inventory.archive_sha256!r})
blocked = []
def audit(event,args):
    if (event.startswith(('subprocess.', 'socket.'))
        or event in {{'os.system','os.exec','os.posix_spawn',
                     'os.fork','os.putenv','os.unsetenv'}}):
        blocked.append(event)
        raise RuntimeError('forbidden_side_effect')
sys.addaudithook(audit)
original = builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {{'PIL','app'}}:
        blocked.append('package_import')
        raise RuntimeError('forbidden_import')
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
def forbidden(*args,**kwargs):
    blocked.append('environment')
    raise RuntimeError('forbidden_environment')
os.getenv = forbidden
class Environment(dict):
    __getitem__ = get = __iter__ = keys = items = values = forbidden
os.environ = Environment()
receipt = m._prepare_files(data,inventory,Path({str(base)!r}))
assert receipt.publisher_attribution == 'unverified'
assert not blocked, blocked
assert not any(name == 'PIL' or name.startswith('PIL.') for name in sys.modules)
print('GUARDED_PREPARATION_PASSED')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, timeout=20
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"GUARDED_PREPARATION_PASSED\n"
