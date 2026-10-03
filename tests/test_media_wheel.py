"""Synthetic pinned ZIP fixtures only; no Pillow import/install/extraction/guest."""

import ast
import base64
import csv
import dataclasses
import hashlib
import io
import json
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from qualification import media_wheel as m

METADATA = (
    b"Metadata-Version: 2.4\nName: pillow\nVersion: 12.3.0\n"
    b"License-Expression: MIT-CMU\nLicense-File: LICENSE\n\nfixture description\n"
)
WHEEL = (
    b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\n"
    b"Tag: cp312-cp312-manylinux_2_27_x86_64\n"
    b"Tag: cp312-cp312-manylinux_2_28_x86_64\n\n"
)


def members():
    result = {name: b"fixture-file:" + name.encode() for name in m.REQUIRED_FILES}
    result[f"{m.DIST_INFO}/METADATA"] = METADATA
    result[f"{m.DIST_INFO}/WHEEL"] = WHEEL
    result[m.RECORD] = b""
    return result


def make(
    *,
    add=None,
    remove=(),
    changes=None,
    record_change=None,
    info_change=None,
    compression=zipfile.ZIP_DEFLATED,
    directories=None,
    comment=b"",
):
    files = members()
    files.update(add or {})
    files.update(changes or {})
    for name in remove:
        files.pop(name, None)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, data in sorted(files.items()):
        if name == m.RECORD:
            writer.writerow((name, "", ""))
        else:
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            writer.writerow((name, "sha256=" + digest, str(len(data))))
    record = output.getvalue().encode()
    if m.RECORD in files:
        files[m.RECORD] = record_change(record) if record_change else record
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", allowZip64=False) as archive:
        archive.comment = comment
        for name in sorted(m.REQUIRED_DIRECTORIES if directories is None else directories):
            info = zipfile.ZipInfo(name + "/", (2026, 7, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o40755 << 16 | 0x10
            archive.writestr(info, b"", compress_type=zipfile.ZIP_STORED)
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 7, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o100755 if name in m.NATIVE_EXTENSIONS + m.BUNDLED_LIBRARIES else 0o100644
            if name == m.RECORD:
                mode = 0o100664
            elif name == m.SBOM:
                mode = 0o600
            info.external_attr = mode << 16
            if info_change:
                info_change(info)
            archive.writestr(info, data, compress_type=compression)
    return target.getvalue()


def inspect(data):
    # Private trusted fixture pin; never an alternate public candidate selector.
    return m._inspect_archive(data, hashlib.sha256(data).hexdigest())


def patch_central(data, name, offset, value, fmt="I"):
    result = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        position = archive.start_dir
    while result[position : position + 4] == b"PK\x01\x02":
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", result, position + 28)
        raw = bytes(result[position + 46 : position + 46 + name_len]).decode()
        if raw == name:
            struct.pack_into("<" + fmt, result, position + offset, value)
            return bytes(result)
        position += 46 + name_len + extra_len + comment_len
    raise AssertionError("fixture member missing")


def test_fixture_inventory_determinism_frozen_and_candidate_only():
    data = make()
    result = inspect(data)
    assert result == inspect(data)
    assert result.archive_sha256 == hashlib.sha256(data).hexdigest()
    assert result.archive_bytes == len(data)
    assert result.uncompressed_bytes == sum(f.size for f in result.files)
    assert {f.path for f in result.files} == m.REQUIRED_FILES
    assert {p for p, _ in result.directories} == m.REQUIRED_DIRECTORIES
    assert result.tags == m.TAGS and result.licenses == (m.LICENSE,)
    assert result.instruction_trust == "none"
    assert not any(
        hasattr(result, k) for k in ("approved", "success", "cleanup_confirmed", "provenance")
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.files = []
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.files[0].path = "other"
    for kwargs in ({}, {"instruction_trust": "trusted"}, {"success": True}, {"files": []}):
        with pytest.raises(TypeError, match="scanner_required"):
            m.PillowWheelInventory(**kwargs)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        "wheel",
        b"",
        bytearray(b"zip"),
        memoryview(b"zip"),
        type("Bytes", (bytes,), {})(b"zip"),
    ],
)
def test_strict_bytes_before_parser(monkeypatch, value):
    monkeypatch.setattr(m.zipfile, "ZipFile", lambda *a, **k: pytest.fail("parser reached"))
    with pytest.raises(m.MediaArtifactError, match="media_wheel_bytes"):
        m.inspect_pillow_wheel(value)


def test_closed_public_pin_before_zip_parser(monkeypatch):
    data = make()
    monkeypatch.setattr(m.zipfile, "ZipFile", lambda *a, **k: pytest.fail("parser reached"))
    for digest in (m.WHEEL_SHA256, None, True, b"0" * 64):
        with pytest.raises(m.MediaArtifactError, match="media_wheel_pin"):
            m._inspect_archive(data, digest)
    with pytest.raises(m.MediaArtifactError, match="media_wheel_pin"):
        m.inspect_pillow_wheel(data)
    with pytest.raises(TypeError):
        m.inspect_pillow_wheel(data, hashlib.sha256(data).hexdigest())


def test_archive_exact_byte_boundary_and_adjacent_rejection(monkeypatch):
    initial = make(add={"PIL/pad.bin": b"x"}, compression=zipfile.ZIP_STORED)
    count = m.ARCHIVE_BYTES - len(initial) + 1
    count -= len(str(count)) - 1
    data = make(add={"PIL/pad.bin": b"x" * count}, compression=zipfile.ZIP_STORED)
    assert len(data) == m.ARCHIVE_BYTES
    assert inspect(data).archive_bytes == m.ARCHIVE_BYTES
    monkeypatch.setattr(m.zipfile, "ZipFile", lambda *a, **k: pytest.fail("parser reached"))
    with pytest.raises(m.MediaArtifactError, match="media_wheel_bytes"):
        m.inspect_pillow_wheel(data + b"x")


def test_count_boundary_and_adjacent_rejection():
    extra_count = m.ENTRY_LIMIT - len(m.REQUIRED_FILES) - len(m.REQUIRED_DIRECTORIES)
    extras = {f"PIL/fixture_{i}.py": b"x" for i in range(extra_count)}
    assert len(inspect(make(add=extras)).files) + len(m.REQUIRED_DIRECTORIES) == m.ENTRY_LIMIT
    extras["PIL/too_many.py"] = b"x"
    with pytest.raises(m.MediaArtifactError, match="media_wheel_entries"):
        inspect(make(add=extras))


@pytest.mark.parametrize(
    "path",
    [
        "../PIL/x",
        "/PIL/x",
        "PIL/../x",
        "PIL/./x",
        "PIL//x",
        "PIL/x\\y",
        "PIL/x:y",
        "PIL/\x01x",
        "PIL/caf\u00e9.py",
        "other/x",
        "PIL/" + "x" * 509,
        "PIL/a/b/c/d/e/f/g/x",
    ],
)
def test_invalid_paths(path):
    with pytest.raises(m.MediaArtifactError):
        inspect(make(add={path: b"x"}))


@pytest.mark.parametrize("path", ["PIL/" + "x" * 508, "PIL/a/b/c/d/e/f/x"])
def test_path_depth_and_length_boundaries(path):
    assert path in {f.path for f in inspect(make(add={path: b"x"})).files}


def test_nul_original_name_rejected_before_normalization():
    data = make(add={"PIL/nul.py": b"x"})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        start = archive.start_dir
    data = data[:start] + data[start:].replace(b"PIL/nul.py", b"PIL/\0ul.py", 1)
    with pytest.raises(m.MediaArtifactError, match="media_wheel_path"):
        inspect(data)


def test_duplicate_and_file_prefix_collision_before_member_reads(monkeypatch):
    # A duplicate file/directory canonical path is encoded by an added root file.
    for data, code in [
        (make(add={"PIL": b"x"}), "duplicate"),
        (make(add={"PIL/parent": b"x", "PIL/parent/child": b"x"}), "collision"),
    ]:
        monkeypatch.setattr(
            m.zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("member read reached")
        )
        with pytest.raises(m.MediaArtifactError, match="media_wheel_" + code):
            inspect(data)
        monkeypatch.undo()


@pytest.mark.parametrize(
    "mode",
    [0o120777, 0o20644, 0o10644, 0o140644, 0o104755, 0o102644, 0o100666, 0o600, 0o100664, 0o40755],
)
def test_nonregular_privileged_or_unsupported_file_modes(mode):
    def change(info):
        if info.filename == "PIL/Image.py":
            info.external_attr = mode << 16

    with pytest.raises(m.MediaArtifactError, match="media_wheel_entry"):
        inspect(make(info_change=change))


def test_archive_mode_exceptions_are_fixed_metadata_only():
    result = inspect(make())
    modes = {f.path: f.mode for f in result.files}
    assert modes[m.RECORD] == 0o664 and modes[m.SBOM] == 0o600
    assert (
        m.wheel_policy()["mode_semantics"] == "declared-archive-permissions-not-filesystem-grants"
    )


def test_directory_marker_type_and_payload_agreement():
    with pytest.raises(m.MediaArtifactError, match="media_wheel_entry"):
        inspect(make(add={"PIL/forged/": b"x"}))
    data = make()
    data = patch_central(data, "PIL/", 38, 0o100644 << 16)
    with pytest.raises(m.MediaArtifactError, match="media_wheel_entry"):
        inspect(data)


@pytest.mark.parametrize("kind", ["creator", "extra", "version", "member_comment"])
def test_unsupported_entry_metadata(kind):
    def change(info):
        if info.filename == "PIL/Image.py":
            if kind == "creator":
                info.create_system = 0
            elif kind == "extra":
                info.extra = b"\xfe\xff\0\0"
            elif kind == "version":
                info.extract_version = 45
            else:
                info.comment = b"x"

    with pytest.raises(m.MediaArtifactError, match="media_wheel_entry"):
        inspect(make(info_change=change))


@pytest.mark.parametrize(
    "offset,value,fmt",
    [(8, 1, "H"), (8, 8, "H"), (8, 2048, "H"), (10, 93, "H"), (24, m.FILE_BYTES + 1, "I")],
)
def test_flags_compression_or_declared_size_rejected_before_reads(monkeypatch, offset, value, fmt):
    data = patch_central(make(), "PIL/Image.py", offset, value, fmt)
    monkeypatch.setattr(
        m.zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("member read reached")
    )
    with pytest.raises(m.MediaArtifactError, match="media_wheel_entry"):
        inspect(data)


def test_aggregate_declared_size_rejected_before_reads(monkeypatch):
    data = make()
    for name in sorted(m.NATIVE_EXTENSIONS)[:5]:
        data = patch_central(data, name, 24, m.FILE_BYTES)
    monkeypatch.setattr(
        m.zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("member read reached")
    )
    with pytest.raises(m.MediaArtifactError, match="media_wheel_size"):
        inspect(data)


@pytest.mark.parametrize("field", ["METADATA", "WHEEL", "RECORD"])
def test_declared_metadata_budget_before_reads(monkeypatch, field):
    data = patch_central(make(), f"{m.DIST_INFO}/{field}", 24, m.METADATA_BYTES + 1)
    monkeypatch.setattr(
        m.zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("member read reached")
    )
    with pytest.raises(m.MediaArtifactError, match="media_wheel_metadata_size"):
        inspect(data)


@pytest.mark.parametrize("data", [b"not zip", make()[:20], make()[:-10]])
def test_truncated_or_invalid_archive_sanitized(data):
    with pytest.raises(m.MediaArtifactError, match="media_wheel_archive"):
        inspect(data)


def test_actual_crc_and_local_header_mismatch_sanitized():
    data = patch_central(make(), "PIL/Image.py", 16, 0)
    with pytest.raises(m.MediaArtifactError, match="media_wheel_archive"):
        inspect(data)
    data = make()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        offset = archive.getinfo("PIL/Image.py").header_offset + 30
    data = data[:offset] + b"X" + data[offset + 1 :]
    with pytest.raises(m.MediaArtifactError, match="media_wheel_archive"):
        inspect(data)


def test_archive_comment_and_signature_members_refused():
    with pytest.raises(m.MediaArtifactError, match="media_wheel_entries"):
        inspect(make(comment=b"x"))
    for name in (m.RECORD + ".jws", m.RECORD + ".p7s"):
        with pytest.raises(m.MediaArtifactError, match="media_wheel_signature_member"):
            inspect(make(add={name: b"x"}))


@pytest.mark.parametrize("name", sorted(m.REQUIRED_FILES))
def test_required_package_native_notice_metadata_member_missing(name):
    with pytest.raises(m.MediaArtifactError, match="media_wheel_inventory"):
        inspect(make(remove=(name,)))


@pytest.mark.parametrize("name", sorted(m.REQUIRED_DIRECTORIES))
def test_required_directory_missing(name):
    with pytest.raises(m.MediaArtifactError, match="media_wheel_inventory"):
        inspect(make(directories=m.REQUIRED_DIRECTORIES - {name}))


@pytest.mark.parametrize(
    "old,new",
    [
        (b"Name: pillow", b"Name: other"),
        (b"Version: 12.3.0", b"Version: 12.3.1"),
        (b"Metadata-Version: 2.4", b"Metadata-Version: 2.3"),
        (b"License-Expression: MIT-CMU", b"License-Expression: other"),
        (b"License-File: LICENSE", b"License-File: missing"),
        (b"Name: pillow", b"Name: pillow\nName: pillow"),
        (b"Name: pillow", b"Name: pillow\nname: other"),
    ],
)
def test_package_metadata_identity_and_duplicate_rejected(old, new):
    with pytest.raises(m.MediaArtifactError, match="media_wheel_metadata"):
        inspect(make(changes={f"{m.DIST_INFO}/METADATA": METADATA.replace(old, new)}))


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.replace(b"1.0", b"2.0"),
        lambda b: b.replace(b"false", b"true"),
        lambda b: b.replace(b"cp312", b"cp311"),
        lambda b: b.replace(b"manylinux_2_28_x86_64", b"manylinux_2_27_x86_64"),
        lambda b: b + b"body",
        lambda b: b.replace(
            b"Root-Is-Purelib: false", b"Root-Is-Purelib: false\nRoot-Is-Purelib: false"
        ),
    ],
)
def test_wheel_metadata_exact_tag_set_and_singleton_fields(change):
    with pytest.raises(m.MediaArtifactError, match="media_wheel_metadata"):
        inspect(make(changes={f"{m.DIST_INFO}/WHEEL": change(WHEEL)}))


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate",
        "extra",
        "wrong_hash",
        "padding",
        "algorithm",
        "size",
        "leading_zero",
        "self_hash",
        "self_size",
        "columns",
        "directory",
        "unicode",
        "malformed_csv",
    ],
)
def test_record_complete_canonical_binding(kind):
    def change(data):
        rows = list(csv.reader(io.StringIO(data.decode())))
        row = next(r for r in rows if r[0] == "PIL/Image.py")
        own = next(r for r in rows if r[0] == m.RECORD)
        if kind == "missing":
            rows.remove(row)
        elif kind == "duplicate":
            rows.append(row)
        elif kind == "extra":
            rows.append(["PIL/missing", "sha256=x", "1"])
        elif kind == "wrong_hash":
            row[1] = "sha256=" + "0" * 43
        elif kind == "padding":
            row[1] += "="
        elif kind == "algorithm":
            row[1] = row[1].replace("sha256", "sha512")
        elif kind == "size":
            row[2] = str(int(row[2]) + 1)
        elif kind == "leading_zero":
            row[2] = "0" + row[2]
        elif kind == "self_hash":
            own[1] = row[1]
        elif kind == "self_size":
            own[2] = "0"
        elif kind == "columns":
            row.append("extra")
        elif kind == "directory":
            rows.append(["PIL", "", ""])
        elif kind == "unicode":
            return b"\xff"
        elif kind == "malformed_csv":
            return b'"unterminated'
        out = io.StringIO(newline="")
        csv.writer(out, lineterminator="\n").writerows(rows)
        return out.getvalue().encode()

    with pytest.raises(m.MediaArtifactError):
        inspect(make(record_change=change))


def test_policy_inventory_digest_changes_and_no_mutable_cache():
    original = inspect(make())
    changed = inspect(make(changes={"PIL/Image.py": b"other"}))
    assert changed.digest != original.digest and changed.archive_sha256 != original.archive_sha256
    altered = m.wheel_policy()
    altered["required_files"].clear()
    assert m.wheel_policy()["required_files"] == sorted(m.REQUIRED_FILES)
    assert (
        m.WHEEL_POLICY_DIGEST
        == hashlib.sha256(
            json.dumps(
                m.wheel_policy(), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )
    assert (
        m.WHEEL_POLICY_DIGEST == "5a85e8e50782dd22759334dd2d01349ceee9c3bed680dd8fb469ce3db05e1a0f"
    )


def test_no_package_or_production_import_and_guarded_validation():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "app").rglob("*.py"):
        assert "media_wheel" not in path.read_text()
    tree = ast.parse(Path(m.__file__).read_text())
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith(("PIL", "app")) for name in imported)
    data = make(changes={"PIL/Image.py": b"raise RuntimeError('PACKAGE_EXECUTED')"})
    bad_record = make(record_change=lambda data: b"unhashed")
    code = f"""import builtins, os, sys
sys.path.insert(0, {str(root)!r})
from qualification import media_wheel as m
data = {data!r}
pin = {hashlib.sha256(data).hexdigest()!r}
m._inspect_archive(data, pin)  # Prime standard-library parser imports before guards.
blocked = []
def audit(event, args):
    if event in {{'open','os.listdir','os.scandir','subprocess.Popen',
                 'socket.__new__','os.putenv','os.unsetenv','os.system'}}:
        blocked.append(event)
        raise RuntimeError('forbidden_side_effect')
sys.addaudithook(audit)
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {{'PIL', 'app'}}:
        blocked.append('package_import')
        raise RuntimeError('forbidden_import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
def forbidden(*args, **kwargs):
    blocked.append('environment')
    raise RuntimeError('forbidden_environment')
os.getenv = forbidden
class Environment(dict):
    __getitem__ = get = __iter__ = keys = items = values = forbidden
os.environ = Environment()
value = m._inspect_archive(data, pin)
assert value.instruction_trust == 'none'
bad_record = {bad_record!r}
for payload, digest in ((b'bad', pin),
                        (b'not zip', m.hashlib.sha256(b'not zip').hexdigest()),
                        (bad_record, m.hashlib.sha256(bad_record).hexdigest())):
    try:
        m._inspect_archive(payload, digest)
    except m.MediaArtifactError:
        pass
    else:
        raise RuntimeError('missing_rejection')
assert not blocked, blocked
print('GUARDED_WHEEL_PASSED')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"GUARDED_WHEEL_PASSED\n"


@pytest.mark.parametrize("delta", [-1, 1])
def test_actual_read_size_must_match_declared(monkeypatch, delta):
    data = make()
    original = m.zipfile.ZipFile.open

    def altered(self, info, *args, **kwargs):
        if info.filename == "PIL/Image.py":
            return io.BytesIO(b"x" * (info.file_size + delta))
        return original(self, info, *args, **kwargs)

    monkeypatch.setattr(m.zipfile.ZipFile, "open", altered)
    with pytest.raises(m.MediaArtifactError, match="media_wheel_size"):
        inspect(data)


def test_exact_file_size_boundary():
    data = make(add={"PIL/large.bin": b"x" * m.FILE_BYTES})
    assert next(f for f in inspect(data).files if f.path == "PIL/large.bin").size == m.FILE_BYTES


@pytest.mark.parametrize("field", ["METADATA", "WHEEL"])
def test_exact_metadata_buffer_boundary(field):
    content = METADATA if field == "METADATA" else WHEEL
    data = make(
        changes={f"{m.DIST_INFO}/{field}": content + b" " * (m.METADATA_BYTES - len(content))}
    )
    assert (
        next(f for f in inspect(data).files if f.path == f"{m.DIST_INFO}/{field}").size
        == m.METADATA_BYTES
    )


def test_actual_total_exact_boundary():
    block = b"x" * m.FILE_BYTES
    extras = {f"PIL/large_{i}.bin": block for i in range(3)}
    extras["PIL/large_3.bin"] = b"x"
    first = inspect(make(add=extras)).uncompressed_bytes
    remaining = m.TOTAL_BYTES - first + 1
    remaining -= len(str(remaining)) - 1
    extras["PIL/large_3.bin"] = b"x" * remaining
    result = inspect(make(add=extras))
    assert result.uncompressed_bytes == m.TOTAL_BYTES
