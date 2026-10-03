"""Independent hand-packed version graph fixture and explicit fake-record oracle."""

import copy
import struct
from types import SimpleNamespace

from qualification import media_elf_metadata as loader

BASE = 0x400000
WIDTHS = {
    "Elf_Phdr": 56,
    "Elf_Shdr": 64,
    "Elf_Dyn": 16,
    "Elf_Verdef": 20,
    "Elf_Verdaux": 8,
    "Elf_Verneed": 16,
    "Elf_Vernaux": 16,
    "Elf_Versym": 2,
}


def make(versioned=True):
    data = bytearray(232)
    offsets, contents, records = {}, {}, {}
    strings = bytearray(b"\0")
    names = {}
    for name in ("libc.so.6", "fixture.so", "FIXTURE_1.0", "FIXTURE_2.0", "GLIBC_2.34"):
        names[name] = len(strings)
        strings.extend(name.encode() + b"\0")

    def add(name, value, alignment=8):
        data.extend(bytes(-len(data) % alignment))
        offsets[name] = len(data)
        contents[name] = value
        data.extend(value)

    def rec(kind, offset, values):
        records[(kind, offset)] = values

    add(".dynstr", bytes(strings))
    add(".dynsym", bytes(5 * 24))
    if versioned:
        add(".gnu.version", struct.pack("<5H", 0, 1, 2, 0x8003, 4), 2)
        for i, index in enumerate(("VER_NDX_LOCAL", "VER_NDX_GLOBAL", 2, 0x8003, 4)):
            rec("Elf_Versym", offsets[".gnu.version"] + 2 * i, {"ndx": index})
        definitions = bytearray()
        for i, (index, name, deps) in enumerate(
            (
                (1, "fixture.so", ()),
                (2, "FIXTURE_1.0", ()),
                (3, "FIXTURE_2.0", ("FIXTURE_1.0",)),
            )
        ):
            values = (
                1,
                1 if index == 1 else 0,
                index,
                1 + len(deps),
                0x12340000 + index,
                20,
                32 if i < 2 else 0,
            )
            definitions.extend(struct.pack("<HHHHIII", *values))
            for j, text in enumerate((name, *deps)):
                definitions.extend(struct.pack("<II", names[text], 8 if j < len(deps) else 0))
            if i < 2:
                definitions.extend(bytes(4))
        add(".gnu.version_d", bytes(definitions))
        for i, (index, name, deps) in enumerate(
            (
                (1, "fixture.so", ()),
                (2, "FIXTURE_1.0", ()),
                (3, "FIXTURE_2.0", ("FIXTURE_1.0",)),
            )
        ):
            pos = offsets[".gnu.version_d"] + i * 32
            rec(
                "Elf_Verdef",
                pos,
                dict(
                    zip(
                        (
                            "vd_version",
                            "vd_flags",
                            "vd_ndx",
                            "vd_cnt",
                            "vd_hash",
                            "vd_aux",
                            "vd_next",
                        ),
                        (
                            1,
                            1 if index == 1 else 0,
                            index,
                            1 + len(deps),
                            0x12340000 + index,
                            20,
                            32 if i < 2 else 0,
                        ),
                        strict=True,
                    )
                ),
            )
            for j, text in enumerate((name, *deps)):
                rec(
                    "Elf_Verdaux",
                    pos + 20 + j * 8,
                    {"vda_name": names[text], "vda_next": 8 if j < len(deps) else 0},
                )
        add(
            ".gnu.version_r",
            struct.pack("<HHIII", 1, 1, names["libc.so.6"], 16, 0)
            + struct.pack("<IHHII", 0x56780004, 0, 4, names["GLIBC_2.34"], 0),
        )
        rec(
            "Elf_Verneed",
            offsets[".gnu.version_r"],
            {
                "vn_version": 1,
                "vn_cnt": 1,
                "vn_file": names["libc.so.6"],
                "vn_aux": 16,
                "vn_next": 0,
            },
        )
        rec(
            "Elf_Vernaux",
            offsets[".gnu.version_r"] + 16,
            {
                "vna_hash": 0x56780004,
                "vna_flags": 0,
                "vna_other": 4,
                "vna_name": names["GLIBC_2.34"],
                "vna_next": 0,
            },
        )
    tags = [
        (1, names["libc.so.6"], "DT_NEEDED"),
        (14, names["fixture.so"], "DT_SONAME"),
        (5, BASE + offsets[".dynstr"], "DT_STRTAB"),
        (10, len(strings), "DT_STRSZ"),
        (6, BASE + offsets[".dynsym"], "DT_SYMTAB"),
        (11, 24, "DT_SYMENT"),
    ]
    if versioned:
        tags += [
            (0x6FFFFFF0, BASE + offsets[".gnu.version"], "DT_VERSYM"),
            (0x6FFFFFFC, BASE + offsets[".gnu.version_d"], "DT_VERDEF"),
            (0x6FFFFFFD, 3, "DT_VERDEFNUM"),
            (0x6FFFFFFE, BASE + offsets[".gnu.version_r"], "DT_VERNEED"),
            (0x6FFFFFFF, 1, "DT_VERNEEDNUM"),
        ]
    tags += [(0, 0, "DT_NULL")]
    add(".dynamic", b"".join(struct.pack("<qQ", tag, val) for tag, val, _ in tags))
    for i, (_, value, name) in enumerate(tags):
        rec("Elf_Dyn", offsets[".dynamic"] + i * 16, {"d_tag": name, "d_val": value})
    add(".interp", b"/lib64/ld-linux-x86-64.so.2\0", 1)
    specs = [
        (".dynstr", 3, "SHT_STRTAB", 0, 0, 1, 0),
        (".dynsym", 11, "SHT_DYNSYM", 1, 1, 8, 24),
    ]
    if versioned:
        specs += [
            (".gnu.version", 0x6FFFFFFF, "SHT_GNU_versym", 2, 0, 2, 2),
            (".gnu.version_d", 0x6FFFFFFD, "SHT_GNU_verdef", 1, 3, 8, 0),
            (".gnu.version_r", 0x6FFFFFFE, "SHT_GNU_verneed", 1, 1, 8, 0),
        ]
    specs += [
        (".dynamic", 6, "SHT_DYNAMIC", 1, 0, 8, 16),
        (".interp", 1, "SHT_PROGBITS", 0, 0, 1, 0),
        (".shstrtab", 3, "SHT_STRTAB", 0, 0, 1, 0),
    ]
    section_names = bytearray(b"\0")
    section_offsets = {}
    for name, *_ in specs:
        section_offsets[name] = len(section_names)
        section_names.extend(name.encode() + b"\0")
    add(".shstrtab", bytes(section_names), 1)
    data.extend(bytes(-len(data) % 8))
    shoff = len(data)
    data.extend(bytes(64))
    rec("Elf_Shdr", shoff, {k: "SHT_NULL" if k == "sh_type" else 0 for k in loader.SH_FIELDS})
    for i, (name, numeric, kind, link, info, align, width) in enumerate(specs, 1):
        values = (
            section_offsets[name],
            numeric,
            0 if name == ".shstrtab" else 2,
            0 if name == ".shstrtab" else BASE + offsets[name],
            offsets[name],
            len(contents[name]),
            link,
            info,
            align,
            width,
        )
        data.extend(struct.pack("<IIQQQQIIQQ", *values))
        record = dict(zip(loader.SH_FIELDS, values, strict=True))
        record["sh_type"] = kind
        rec("Elf_Shdr", shoff + i * 64, record)
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    data[:64] = struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident,
        3,
        62,
        1,
        0,
        64,
        shoff,
        0,
        64,
        56,
        3,
        64,
        len(specs) + 1,
        len(specs),
    )
    programs = [
        (1, "PT_LOAD", 0, len(data), 4096),
        (2, "PT_DYNAMIC", offsets[".dynamic"], len(contents[".dynamic"]), 8),
        (3, "PT_INTERP", offsets[".interp"], len(contents[".interp"]), 1),
    ]
    for i, (numeric, kind, offset, size, align) in enumerate(programs):
        values = (numeric, 4, offset, BASE + offset, BASE + offset, size, size, align)
        data[64 + i * 56 : 64 + (i + 1) * 56] = struct.pack("<IIQQQQQQ", *values)
        record = dict(zip(loader.PH_FIELDS, values, strict=True))
        record["p_type"] = kind
        rec("Elf_Phdr", 64 + i * 56, record)
    header = {
        "e_ident": {"EI_VERSION": "EV_CURRENT", "EI_OSABI": "ELFOSABI_SYSV", "EI_ABIVERSION": 0},
        "e_machine": "EM_X86_64",
        "e_type": "ET_DYN",
        "e_version": "EV_CURRENT",
        "e_flags": 0,
        "e_ehsize": 64,
        "e_phoff": 64,
        "e_phentsize": 56,
        "e_phnum": 3,
        "e_shoff": shoff,
        "e_shentsize": 64,
        "e_shnum": len(specs) + 1,
        "e_shstrndx": len(specs),
        "e_entry": 0,
    }
    elf = SimpleNamespace(
        header=header,
        elfclass=64,
        little_endian=True,
        structs=SimpleNamespace(
            **{
                kind: SimpleNamespace(name=kind, sizeof=lambda n=width: n)
                for kind, width in WIDTHS.items()
            }
        ),
    )
    calls = []

    def decode(structure, offset):
        calls.append((structure.name, offset))
        return copy.deepcopy(records[(structure.name, offset)])

    layout = {
        "sections": offsets,
        "section_table": shoff,
        "strings": names,
        "dynamic_tags": [(tag, val) for tag, val, _ in tags],
    }
    return bytes(data), layout, elf, records, decode, calls
