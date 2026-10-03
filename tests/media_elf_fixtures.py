"""Hand-packed inert ELF test data; no compilation, imports or native execution."""

import struct

BASE = 0x400000


def make():
    """One data-only ELF64 object with explicit bounded loader/version tables."""
    data = bytearray(64 + 3 * 56)
    offsets = {}
    contents = {}

    def add(name, value, alignment=8):
        data.extend(b"\0" * (-len(data) % alignment))
        offsets[name] = len(data)
        contents[name] = value
        data.extend(value)

    strings = bytearray(b"\0")
    names = {}
    for text in ("libc.so.6", "fixture.so", "getpid", "fixture_api", "GLIBC_2.34", "FIXTURE_1.0"):
        names[text] = len(strings)
        strings.extend(text.encode("ascii") + b"\0")
    add(".dynstr", bytes(strings))
    add(
        ".dynsym",
        bytes(24)
        + struct.pack("<IBBHQQ", names["getpid"], 0x12, 0, 0, 0, 0)
        + struct.pack("<IBBHQQ", names["fixture_api"], 0x12, 0, 0xFFF1, BASE, 0),
    )
    add(".gnu.version", struct.pack("<HHH", 0, 3, 2), 2)
    add(
        ".gnu.version_r",
        struct.pack("<HHIII", 1, 1, names["libc.so.6"], 16, 0)
        + struct.pack("<IHHII", 0, 0, 3, names["GLIBC_2.34"], 0),
    )
    add(
        ".gnu.version_d",
        struct.pack("<HHHHIII", 1, 0, 2, 1, 0, 20, 0) + struct.pack("<II", names["FIXTURE_1.0"], 0),
    )
    tags = (
        (1, names["libc.so.6"]),
        (14, names["fixture.so"]),
        (5, BASE + offsets[".dynstr"]),
        (10, len(strings)),
        (6, BASE + offsets[".dynsym"]),
        (11, 24),
        (0x6FFFFFF0, BASE + offsets[".gnu.version"]),
        (0x6FFFFFFE, BASE + offsets[".gnu.version_r"]),
        (0x6FFFFFFF, 1),
        (0x6FFFFFFC, BASE + offsets[".gnu.version_d"]),
        (0x6FFFFFFD, 1),
        (0, 0),
    )
    add(".dynamic", b"".join(struct.pack("<qQ", tag, value) for tag, value in tags))
    add(".interp", b"/lib64/ld-linux-x86-64.so.2\0", 1)
    section_names = bytearray(b"\0")
    section_offsets = {}
    for name in (
        ".dynstr",
        ".dynsym",
        ".gnu.version",
        ".gnu.version_r",
        ".gnu.version_d",
        ".dynamic",
        ".interp",
        ".shstrtab",
    ):
        section_offsets[name] = len(section_names)
        section_names.extend(name.encode() + b"\0")
    add(".shstrtab", bytes(section_names), 1)
    data.extend(b"\0" * (-len(data) % 8))
    shoff = len(data)
    sections = (
        (".dynstr", 3, 0, 0, 1, 0),
        (".dynsym", 11, 1, 1, 8, 24),
        (".gnu.version", 0x6FFFFFFF, 2, 0, 2, 2),
        (".gnu.version_r", 0x6FFFFFFE, 1, 1, 8, 0),
        (".gnu.version_d", 0x6FFFFFFD, 1, 1, 8, 0),
        (".dynamic", 6, 1, 0, 8, 16),
        (".interp", 1, 0, 0, 1, 0),
        (".shstrtab", 3, 0, 0, 1, 0),
    )
    data.extend(bytes(64))
    for name, section_type, link, info, alignment, width in sections:
        flags = 0 if name == ".shstrtab" else 2
        address = 0 if name == ".shstrtab" else BASE + offsets[name]
        data.extend(
            struct.pack(
                "<IIQQQQIIQQ",
                section_offsets[name],
                section_type,
                flags,
                address,
                offsets[name],
                len(contents[name]),
                link,
                info,
                alignment,
                width,
            )
        )
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    data[:64] = struct.pack(
        "<16sHHIQQQIHHHHHH", ident, 3, 62, 1, 0, 64, shoff, 0, 64, 56, 3, 64, 9, 8
    )
    program_headers = (
        (1, 4, 0, BASE, BASE, len(data), len(data), 4096),
        (
            2,
            4,
            offsets[".dynamic"],
            BASE + offsets[".dynamic"],
            BASE + offsets[".dynamic"],
            len(contents[".dynamic"]),
            len(contents[".dynamic"]),
            8,
        ),
        (
            3,
            4,
            offsets[".interp"],
            BASE + offsets[".interp"],
            BASE + offsets[".interp"],
            len(contents[".interp"]),
            len(contents[".interp"]),
            1,
        ),
    )
    for index, header in enumerate(program_headers):
        data[64 + index * 56 : 64 + (index + 1) * 56] = struct.pack("<IIQQQQQQ", *header)
    return bytes(data), {
        "sections": offsets,
        "section_table": shoff,
        "dynamic_tags": tags,
        "strings": names,
    }


def changed(raw, offset, format_string, *values):
    """Explicit mutation seam; inert bytes, never a target executable."""
    data = bytearray(raw)
    struct.pack_into(format_string, data, offset, *values)
    return bytes(data)
