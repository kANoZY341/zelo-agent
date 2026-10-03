"""Fake fixed records only; no pyelftools import, ELF parse or native execution."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from qualification import media_elf_metadata as m


@pytest.fixture
def candidate():
    raw = bytearray(2048)
    names = b"\0.dynstr\0.dynamic\0.shstrtab\0"
    dynstr = b"\0libc.so.6\0fixture.so\0"
    interpreter = b"/lib64/ld-linux-x86-64.so.2\0"
    raw[700 : 700 + len(names)] = names
    raw[400 : 400 + len(dynstr)] = dynstr
    raw[280 : 280 + len(interpreter)] = interpreter
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
        "e_shoff": 1024,
        "e_shentsize": 64,
        "e_shnum": 4,
        "e_shstrndx": 3,
        "e_entry": 0,
    }
    structures = {
        name: SimpleNamespace(name=name, sizeof=lambda size=size: size)
        for name, size in (("Elf_Phdr", 56), ("Elf_Shdr", 64), ("Elf_Dyn", 16))
    }
    elf = SimpleNamespace(
        header=header, elfclass=64, little_endian=True, structs=SimpleNamespace(**structures)
    )

    def ph(kind, offset, size, alignment):
        return {
            "p_type": kind,
            "p_flags": 4,
            "p_offset": offset,
            "p_vaddr": 0x400000 + offset,
            "p_paddr": 0x400000 + offset,
            "p_filesz": size,
            "p_memsz": size,
            "p_align": alignment,
        }

    def sh(kind, offset, size, name=0, link=0, width=0):
        return {
            "sh_type": kind,
            "sh_name": name,
            "sh_flags": 0,
            "sh_addr": 0x400000 + offset
            if kind in ("SHT_DYNAMIC", "SHT_STRTAB") and offset != 700
            else 0,
            "sh_offset": offset,
            "sh_size": size,
            "sh_link": link,
            "sh_info": 0,
            "sh_addralign": 0,
            "sh_entsize": width,
        }

    programs = [
        ph("PT_LOAD", 0, len(raw), 4096),
        ph("PT_DYNAMIC", 500, 80, 8),
        ph("PT_INTERP", 280, len(interpreter), 1),
    ]
    sections = [
        sh("SHT_NULL", 0, 0),
        sh("SHT_STRTAB", 400, len(dynstr), 1),
        sh("SHT_DYNAMIC", 500, 80, 9, 1, 16),
        sh("SHT_STRTAB", 700, len(names), 18),
    ]
    dynamic = [
        {"d_tag": name, "d_val": value}
        for name, value in (
            ("DT_NEEDED", 1),
            ("DT_STRTAB", 0x400000 + 400),
            ("DT_STRSZ", len(dynstr)),
            ("DT_SONAME", 11),
            ("DT_NULL", 0),
        )
    ]
    records = {("Elf_Phdr", 64 + index * 56): item for index, item in enumerate(programs)}
    records.update({("Elf_Shdr", 1024 + index * 64): item for index, item in enumerate(sections)})
    records.update({("Elf_Dyn", 500 + index * 16): item for index, item in enumerate(dynamic)})
    calls = []

    def decode(structure, offset):
        calls.append((structure.name, offset))
        return copy.deepcopy(records[(structure.name, offset)])

    return raw, elf, decode, records, calls


def inspect(candidate):
    raw, elf, decode, _, _ = candidate
    return m._inspect(bytes(raw), elf, decode)


def test_exact_closed_loader_topology_and_false_claims(candidate):
    raw = inspect(candidate)
    report = json.loads(raw)
    assert (
        raw == json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )
    assert report["interpreter"] == "/lib64/ld-linux-x86-64.so.2"
    assert report["dynamic"][0] == {"tag": "DT_NEEDED", "value": 1, "string": "libc.so.6"}
    assert report["source_sha256"] == hashlib.sha256(candidate[0]).hexdigest()
    assert report["instruction_trust"] == "none"
    for key in (
        "symbol_version_content_verified",
        "abi_qualified",
        "platform_complete",
        "runtime_authority",
    ):
        assert report[key] is False
    assert len(report["sections"]) == 4 and len(report["programs"]) == 3
    assert all("uninterpreted_content_sha256" in s for s in report["sections"])
    assert len(raw) <= m.OUTPUT_BYTES


@pytest.mark.parametrize(
    "key,value",
    [
        ("e_phnum", 0),
        ("e_phnum", 65),
        ("e_phnum", True),
        ("e_shnum", 0),
        ("e_shnum", 513),
        ("e_shstrndx", 0xFFFF),
        ("e_machine", "EM_386"),
        ("e_type", "ET_REL"),
        ("e_flags", 1),
        ("e_ehsize", 63),
        ("e_phentsize", 57),
        ("e_shentsize", 63),
        ("e_phoff", 2**64),
        ("e_shoff", 2048),
    ],
)
def test_header_refused_before_any_record_decode(candidate, key, value):
    candidate[1].header[key] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)
    assert not candidate[4]


@pytest.mark.parametrize(
    "key,value", [("EI_VERSION", "EV_NONE"), ("EI_OSABI", "ELFOSABI_SOLARIS"), ("EI_ABIVERSION", 1)]
)
def test_abi_refusal_before_records(candidate, key, value):
    candidate[1].header["e_ident"][key] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)
    assert not candidate[4]


@pytest.mark.parametrize(
    "key,value",
    [
        ("p_type", "PT_SHLIB"),
        ("p_type", 123),
        ("p_flags", 8),
        ("p_filesz", 2049),
        ("p_memsz", 1),
        ("p_vaddr", 2**64 - 1),
        ("p_align", 3),
        ("p_vaddr", 0x400001),
    ],
)
def test_program_range_type_alignment_refusal(candidate, key, value):
    candidate[3][("Elf_Phdr", 64)][key] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize(
    "key,value",
    [
        ("sh_type", 123),
        ("sh_type", "SHT_GNU_LIBLIST"),
        ("sh_link", 4),
        ("sh_flags", 0x800),
        ("sh_offset", 2049),
        ("sh_size", 2049),
        ("sh_name", 9999),
    ],
)
def test_section_metadata_refusal(candidate, key, value):
    candidate[3][("Elf_Shdr", 1088)][key] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize(
    "tag",
    [
        "DT_AUDIT",
        "DT_FILTER",
        "DT_AUXILIARY",
        "DT_CONFIG",
        "DT_DEPAUDIT",
        "DT_ANDROID_REL",
        "DT_MIPS_FLAGS",
        123456,
    ],
)
def test_unrecognized_or_external_loader_feature_rejected(candidate, tag):
    candidate[3][("Elf_Dyn", 500)]["d_tag"] = tag
    with pytest.raises(m.ElfMetadataError, match="dynamic_tag"):
        inspect(candidate)


@pytest.mark.parametrize(
    "kind",
    [
        "missing_null",
        "null_value",
        "duplicate",
        "wrong_mapping",
        "wrong_size",
        "bad_string_offset",
        "nonascii",
        "unterminated",
        "dependency_slash",
        "ambiguous_load",
        "dynamic_extent",
        "dynamic_width",
        "dynamic_count",
        "dynamic_link",
    ],
)
def test_dynamic_extent_mapping_and_string_refusals(candidate, kind):
    raw, _, _, records, _ = candidate
    if kind == "missing_null":
        records[("Elf_Dyn", 564)] = {"d_tag": "DT_FLAGS", "d_val": 0}
    elif kind == "null_value":
        records[("Elf_Dyn", 564)]["d_val"] = 1
    elif kind == "duplicate":
        records[("Elf_Dyn", 548)] = copy.deepcopy(records[("Elf_Dyn", 516)])
    elif kind == "wrong_mapping":
        records[("Elf_Dyn", 516)]["d_val"] += 1
    elif kind == "wrong_size":
        records[("Elf_Dyn", 532)]["d_val"] += 1
    elif kind == "bad_string_offset":
        records[("Elf_Dyn", 500)]["d_val"] = 9999
    elif kind == "nonascii":
        raw[401] = 0xFF
    elif kind == "unterminated":
        end = 400 + records[("Elf_Shdr", 1088)]["sh_size"]
        raw[401:end] = b"x" * (end - 401)
    elif kind == "dependency_slash":
        raw[401] = ord("/")
    elif kind == "ambiguous_load":
        records[("Elf_Phdr", 176)] = copy.deepcopy(records[("Elf_Phdr", 64)])
    elif kind == "dynamic_extent":
        records[("Elf_Phdr", 120)]["p_filesz"] = 79
    elif kind == "dynamic_width":
        records[("Elf_Shdr", 1152)]["sh_entsize"] = 8
    elif kind == "dynamic_count":
        records[("Elf_Shdr", 1152)]["sh_type"] = "SHT_PROGBITS"
    else:
        records[("Elf_Shdr", 1152)]["sh_link"] = 2
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize(
    "kind", ["nonascii", "relative", "dotdot", "trailing", "empty", "too_big", "duplicate"]
)
def test_interpreter_closed_language(candidate, kind):
    raw, _, _, records, _ = candidate
    item = records[("Elf_Phdr", 176)]
    if kind == "nonascii":
        raw[281] = 0xFF
    elif kind == "relative":
        raw[280] = ord("x")
    elif kind == "dotdot":
        raw[280:288] = b"/../lib\0"
        item["p_filesz"] = 8
    elif kind == "trailing":
        item["p_filesz"] += 1
        raw[280 + item["p_filesz"] - 1] = 1
    elif kind == "empty":
        item["p_filesz"] = 0
    elif kind == "too_big":
        item["p_filesz"] = 4097
    else:
        records[("Elf_Phdr", 120)] = copy.deepcopy(item)
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize(
    "raw",
    [None, True, "ELF", bytearray(64), memoryview(bytes(64)), bytes(63), bytes(m.INPUT_BYTES + 1)],
)
def test_private_input_strictness_before_decode(raw, candidate):
    with pytest.raises(m.ElfMetadataError):
        m._inspect(raw, candidate[1], candidate[2])
    assert not candidate[4]


def test_output_limit_refuses_whole_result(candidate, monkeypatch):
    monkeypatch.setattr(m, "OUTPUT_BYTES", 1)
    with pytest.raises(m.ElfMetadataError, match="output"):
        inspect(candidate)


def test_string_search_work_charged_before_scan():
    strings = m._Strings(b"x" * 4096)
    strings.examined = 131072 - 4095
    with pytest.raises(m.ElfMetadataError, match="string_work"):
        strings.read(0, 4096, 0, r"x*")
    assert strings.examined == 131073


def test_host_refused_before_optional_import_or_manifest(monkeypatch):
    monkeypatch.setattr(m, "capture_manifest", lambda *args: pytest.fail("host reached tools"))
    with pytest.raises(m.ElfMetadataError, match="guest_required"):
        m.inspect_elf_metadata(bytes(64))


@pytest.mark.parametrize("owned", [True, False])
def test_only_owned_fixed_refusal_survives_parser_wrapper(monkeypatch, owned):
    # Entire optional import closure is fake; no pyelftools code is imported/executed.
    import sys
    from types import ModuleType, SimpleNamespace

    monkeypatch.setattr(
        m,
        "sys",
        SimpleNamespace(
            flags=SimpleNamespace(isolated=True, no_site=True),
            dont_write_bytecode=True,
            path=m.GUEST_PATHS,
        ),
    )
    monkeypatch.setattr(
        m, "capture_manifest", lambda *args: SimpleNamespace(digest=m.TOOL_MANIFEST_DIGEST)
    )
    failure = (
        m.ElfMetadataError("media_elf_string_work")
        if owned
        else ValueError("PRIVATE_PARSER_DETAIL")
    )

    def fake_elf(*args, **kwargs):
        raise failure

    exports = {
        "elftools.common.exceptions": {"ELFError": type("FakeELFError", (Exception,), {})},
        "elftools.common.utils": {"struct_parse": lambda *args, **kwargs: None},
        "elftools.construct": {"ConstructError": type("FakeConstructError", (Exception,), {})},
        "elftools.elf.elffile": {"ELFFile": fake_elf},
    }
    for name, values in exports.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(m.ElfMetadataError) as result:
        m.inspect_elf_metadata(bytes(64))
    assert str(result.value) == ("media_elf_string_work" if owned else "media_elf_metadata_refused")
