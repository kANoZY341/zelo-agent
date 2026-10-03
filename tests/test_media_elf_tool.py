"""Synthetic source-only copies; no optional parser import or ELF execution."""

import dataclasses
import hashlib
import io
import json
import os
import stat
import zipfile

import pytest

from qualification import media_elf_tool as m

REAL_READ_PIN = m._read_pin


@pytest.fixture
def source_candidate(monkeypatch):
    contents = {
        f"elftools/file{index:02d}.py": b"raise RuntimeError('OPTIONAL_CODE_EXECUTED')"
        for index in range(52)
    }
    contents.update({f"dist-info/data{index}": b"metadata" for index in range(6)})
    contents["pyelftools-0.33.data/scripts/readelf.py"] = b"raise RuntimeError('CLI_EXECUTED')"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    raw = stream.getvalue()
    lock = {
        "files": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in contents.items()
        ],
        "expanded_bytes": sum(map(len, contents.values())),
        "notices": [
            {
                "path": "qualification/vendor/pyelftools/LICENSE",
                "sha256": hashlib.sha256(b"notice").hexdigest(),
            }
        ],
    }
    monkeypatch.setattr(m, "WHEEL_BYTES", len(raw))
    monkeypatch.setattr(m, "WHEEL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(
        m,
        "_read_pin",
        lambda path, *args: json.dumps(lock).encode() if path.name.endswith(".json") else b"notice",
    )
    return raw, lock


def test_source_inspection_keeps_only_libraries_and_notices(source_candidate):
    files = m._source_files(source_candidate[0])
    assert len(files) == 54
    assert not any("readelf.py" in name or "dist-info" in name for name, _, _ in files)
    assert sum(name.startswith("lib/elftools/") for name, _, _ in files) == 52
    assert files == tuple(sorted(files))
    assert all(mode == (0o500 if name == "inventory-anchor" else 0o400) for name, _, mode in files)
    assert ("notices/LICENSE", b"notice", 0o400) in files


@pytest.mark.parametrize(
    "value",
    [None, True, 1, "zip", b"", bytearray(b"zip"), memoryview(b"zip"), b"x" * m.WHEEL_BYTES],
)
def test_public_refuses_before_any_disk_copy(monkeypatch, value):
    monkeypatch.setattr(m, "_prepare", lambda *args: pytest.fail("disk reached"))
    with pytest.raises(m.ElfToolError):
        m.prepare_elf_tool(value)


def test_changed_archive_refused_before_parser(source_candidate, monkeypatch):
    raw = source_candidate[0]
    monkeypatch.setattr(
        m.zipfile, "ZipFile", lambda *args: pytest.fail("archive reached before pin")
    )
    with pytest.raises(m.ElfToolError, match="archive_pin"):
        m._source_files(bytes([raw[0] ^ 1]) + raw[1:])


@pytest.mark.parametrize("mutation", ["hash", "size", "missing", "total", "library_count"])
def test_closed_member_inventory_refuses(source_candidate, mutation):
    raw, lock = source_candidate
    if mutation == "hash":
        lock["files"][0]["sha256"] = "0" * 64
    elif mutation == "size":
        lock["files"][0]["bytes"] += 1
    elif mutation == "missing":
        lock["files"].pop()
    elif mutation == "total":
        lock["expanded_bytes"] += 1
    else:
        lock["files"][0]["path"] = "outside/file.py"
    with pytest.raises(m.ElfToolError):
        m._source_files(raw)


@pytest.mark.parametrize("content", [b"", b"changed", b"x" * 33])
def test_source_pin_bounds_and_hash(tmp_path, content):
    path = tmp_path / "source"
    path.write_bytes(content)
    with pytest.raises(m.ElfToolError, match="source_pin"):
        m._read_pin(path, 32, hashlib.sha256(b"expected").hexdigest())


def test_pin_accepts_exact_bytes(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"expected")
    assert m._read_pin(path, 8, hashlib.sha256(b"expected").hexdigest()) == b"expected"


@pytest.fixture
def copy_files():
    return tuple(
        sorted(
            (
                ("inventory-anchor", m.ANCHOR, 0o500),
                ("lib/elftools/__init__.py", b"raise RuntimeError('CODE_EXECUTED')", 0o400),
                ("lib/elftools/nested/empty.py", b"", 0o400),
                ("notices/LICENSE", b"notice", 0o400),
            )
        )
    )


def test_exact_normalized_copy_frozen_diagnostic_only(copy_files, tmp_path):
    base = tmp_path / "private"
    receipt = m._prepare(copy_files, base)
    payload = base / receipt.relative_payload
    assert receipt.manifest == m._expected(copy_files)
    assert receipt.root_identity == (payload.stat().st_dev, payload.stat().st_ino)
    for path, data, mode in copy_files:
        target = payload / path
        assert target.read_bytes() == data
        assert target.stat().st_uid == os.getuid() and target.stat().st_nlink == 1
        assert stat.S_IMODE(target.stat().st_mode) == mode
    for path, mode in receipt.manifest.directories:
        assert stat.S_IMODE((payload / path).stat().st_mode) == mode == 0o500
    assert not receipt.optional_tool_executed and not receipt.immutable_delivery
    assert not receipt.platform_complete and not receipt.runtime_authority
    assert receipt.instruction_trust == "none"
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.platform_complete = True
    with pytest.raises(TypeError):
        m.PreparedElfTool(**dataclasses.asdict(receipt))


def test_retained_attempt_cap(copy_files, tmp_path):
    base = tmp_path / "private"
    receipts = [m._prepare(copy_files, base) for _ in range(4)]
    assert len({r.relative_payload for r in receipts}) == 4
    before = sorted(base.iterdir())
    with pytest.raises(ValueError, match="attempt_limit"):
        m._prepare(copy_files, base)
    assert sorted(base.iterdir()) == before


@pytest.mark.parametrize("kind", ["symlink", "file", "public", "unknown", "symlink_attempt"])
def test_unsafe_roots_and_attempts_refused(copy_files, tmp_path, kind):
    base = tmp_path / "private"
    if kind == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        base.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        base.write_bytes(b"sentinel")
    else:
        base.mkdir(mode=0o700)
        if kind == "public":
            base.chmod(0o755)
        elif kind == "unknown":
            (base / "unknown").mkdir(mode=0o700)
        else:
            (base / "attempt-fake").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        m._prepare(copy_files, base)


def test_partial_write_failure_retained_and_descriptors_closed(copy_files, tmp_path, monkeypatch):
    real_write = m._write_all
    observed = []

    def fail(fd, data):
        observed.append(fd)
        real_write(fd, data[:1])
        raise OSError("write failed")

    monkeypatch.setattr(m, "_write_all", fail)
    base = tmp_path / "private"
    with pytest.raises(OSError):
        m._prepare(copy_files, base)
    assert len(list(base.iterdir())) == 1
    assert observed
    for fd in observed:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_manifest_mismatch_never_returns_receipt(copy_files, tmp_path, monkeypatch):
    real_scan = m.capture_manifest

    def drift(*args):
        manifest = real_scan(*args)
        return dataclasses.replace(manifest, files=manifest.files[:-1])

    monkeypatch.setattr(m, "capture_manifest", drift)
    with pytest.raises(m.ElfToolError, match="manifest"):
        m._prepare(copy_files, tmp_path / "private")


def test_real_lock_and_notice_identity():
    lock_raw = (m.ROOT / "qualification/fixtures/media-elf-tool-lock.json").read_bytes()
    assert len(lock_raw) <= 16384 and hashlib.sha256(lock_raw).hexdigest() == m.LOCK_SHA256
    lock = json.loads(lock_raw)
    assert len(lock["files"]) == 59 and lock["library_source_count"] == 52
    assert lock["wheel_bytes"] == m.WHEEL_BYTES and lock["wheel_sha256"] == m.WHEEL_SHA256
    assert not lock["optional_tool_executed"] and not lock["independent_release_signature_verified"]
    for notice in lock["notices"]:
        assert m._read_pin(m.ROOT / notice["path"], 65536, notice["sha256"])


@pytest.mark.parametrize(
    "kind",
    ["duplicate", "directory", "encrypted", "compression", "symlink", "oversize", "traversal"],
)
def test_adversarial_archive_metadata_before_copy(source_candidate, monkeypatch, kind):
    real_zip = m.zipfile.ZipFile

    class ChangedZip(real_zip):
        def infolist(self):
            entries = super().infolist()
            if kind == "duplicate":
                entries[-1].filename = entries[0].filename
            elif kind == "directory":
                entries[0].filename += "/"
            elif kind == "encrypted":
                entries[0].flag_bits |= 1
            elif kind == "compression":
                entries[0].compress_type = zipfile.ZIP_BZIP2
            elif kind == "symlink":
                entries[0].external_attr = (stat.S_IFLNK | 0o777) << 16
            elif kind == "oversize":
                entries[0].file_size = m.POLICY.max_file_bytes + 1
            else:
                entries[0].filename = "../outside.py"
            return entries

    monkeypatch.setattr(m.zipfile, "ZipFile", ChangedZip)
    with pytest.raises(m.ElfToolError, match="inventory"):
        m._source_files(source_candidate[0])


def test_close_failure_prevents_receipt_and_closes_remaining_descriptors(
    copy_files, tmp_path, monkeypatch
):
    from contextlib import contextmanager

    real_descriptor = m._descriptor
    depth = 0
    recorded = []

    @contextmanager
    def close_failure(fd):
        nonlocal depth
        depth += 1
        recorded.append(fd)
        top = depth == 1
        try:
            with real_descriptor(fd) as opened:
                yield opened
        finally:
            depth -= 1
            if top:
                raise OSError("reported final close failure")

    monkeypatch.setattr(m, "_descriptor", close_failure)
    with pytest.raises(OSError, match="final close failure"):
        m._prepare(copy_files, tmp_path / "private")
    for fd in recorded:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_prepared_claims_are_fixed_false(copy_files, tmp_path):
    receipt = m._prepare(copy_files, tmp_path / "private")
    for name in (
        "optional_tool_executed",
        "immutable_delivery",
        "platform_complete",
        "runtime_authority",
    ):
        assert getattr(receipt, name) is False
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(receipt, name, True)
    with pytest.raises(TypeError):
        m.prepare_elf_tool(b"x", tmp_path)


def test_fresh_stdlib_only_import_and_invalid_input_audit():
    import subprocess
    import sys

    script = r"""
import sys
root = sys.argv[1]
sys.path.insert(0, root)
blocked = ('PIL', 'elftools', 'ctypes', '_ctypes', 'sigstore',
           'pypi_attestations', 'app', 'subprocess')
def audit(event, args):
    if event == 'import' and args[0].split('.')[0] in blocked:
        raise RuntimeError('optional import forbidden')
    if (event.startswith(('subprocess.', 'socket.', 'ctypes.'))
        or event in ('os.system', 'os.posix_spawn', 'os.fork', 'os.exec')):
        raise RuntimeError('execution forbidden')
sys.addaudithook(audit)
from qualification import media_elf_tool as m

REAL_READ_PIN = m._read_pin
try:
    m.prepare_elf_tool(b'bad')
except m.ElfToolError:
    pass
else:
    raise RuntimeError('invalid input accepted')
assert not any(name.split('.')[0] in blocked for name in sys.modules)
print('stdlib_source_import_only')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(m.ROOT)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"stdlib_source_import_only\n"


@pytest.mark.parametrize("kind", ["corrupt", "missing", "oversize"])
def test_bad_lock_refused_before_json_or_zip(source_candidate, tmp_path, monkeypatch, kind):
    raw, _ = source_candidate
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "_read_pin", REAL_READ_PIN)
    fixture = tmp_path / "qualification/fixtures/media-elf-tool-lock.json"
    fixture.parent.mkdir(parents=True)
    if kind != "missing":
        fixture.write_bytes(b"corrupt" if kind == "corrupt" else b"x" * 16385)
    with monkeypatch.context() as isolated:
        isolated.setattr(
            m.json, "loads", lambda *args: pytest.fail("JSON before lock authentication")
        )
        isolated.setattr(
            m.zipfile, "ZipFile", lambda *args: pytest.fail("ZIP before lock authentication")
        )
        with pytest.raises((m.ElfToolError, OSError)):
            m._source_files(raw)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("kind", ["corrupt", "missing", "oversize"])
def test_each_notice_authenticated_before_disk_copy(
    source_candidate, tmp_path, monkeypatch, index, kind
):
    raw, lock = source_candidate
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "_read_pin", REAL_READ_PIN)
    lock["notices"] = [
        {
            "path": "qualification/vendor/pyelftools/" + name,
            "sha256": hashlib.sha256(b"notice").hexdigest(),
        }
        for name in ("LICENSE", "Construct-LICENSE")
    ]
    for current, notice in enumerate(lock["notices"]):
        path = tmp_path / notice["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if current == index and kind == "missing":
            continue
        path.write_bytes(
            (b"corrupt" if kind == "corrupt" else b"x" * 65537) if current == index else b"notice"
        )
    lock_raw = json.dumps(lock).encode()
    fixture = tmp_path / "qualification/fixtures/media-elf-tool-lock.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(lock_raw)
    monkeypatch.setattr(m, "LOCK_SHA256", hashlib.sha256(lock_raw).hexdigest())
    monkeypatch.setattr(
        m, "_prepare", lambda *args: pytest.fail("disk copy before notice authentication")
    )
    with pytest.raises(m.ElfToolError):
        m.prepare_elf_tool(raw)
    assert not (tmp_path / "runtime").exists()


def test_repeated_short_writes_complete_exact_copy(copy_files, tmp_path, monkeypatch):
    real_write = os.write
    calls = []

    def short_write(fd, data):
        calls.append(len(data))
        return real_write(fd, data[:1])

    monkeypatch.setattr(os, "write", short_write)
    base = tmp_path / "private"
    receipt = m._prepare(copy_files, base)
    assert receipt.manifest == m._expected(copy_files)
    assert len(calls) == sum(len(data) for _, data, _ in copy_files)
    assert any(size > 1 for size in calls)
