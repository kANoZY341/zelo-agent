"""Synthetic wheel bytes shared by offline inventory and copy fixtures."""

import base64
import csv
import hashlib
import io
import struct
import zipfile

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
