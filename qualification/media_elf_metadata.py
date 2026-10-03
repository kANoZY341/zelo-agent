"""Fixed ELF loader-topology metadata; target inputs are data, never loaders.

Optional parser code runs only in the separately reviewed, authenticated guest.
Known symbol/version/relocation/hash bodies are explicitly uninterpreted here.
"""

import hashlib
import io
import json
import re
import sys

from qualification.artifacts import ArtifactPolicy, capture_manifest

INPUT_BYTES = 16 * 1024**2
OUTPUT_BYTES = 65536
TOOL_ROOT = "/opt/zelo-elf-tools"
TOOL_MANIFEST_DIGEST = "b4561238517c59c6571aafffd096b3130864dbc5c194feefae7e2e233ae070e8"
TOOL_POLICY = ArtifactPolicy(96, 8, 524288, 1048576, 96)
TOOL_ROLES = (("inventory_anchor", "inventory-anchor"),)
GUEST_PATHS = (
    TOOL_ROOT + "/lib",
    "/opt/zelo-elf-source",
    "/usr/lib/python312.zip",
    "/usr/lib/python3.12",
    "/usr/lib/python3.12/lib-dynload",
)
PROGRAM_TYPES = frozenset(
    "PT_NULL PT_LOAD PT_DYNAMIC PT_INTERP PT_NOTE PT_PHDR PT_TLS PT_GNU_EH_FRAME "
    "PT_GNU_STACK PT_GNU_RELRO PT_GNU_PROPERTY".split()
)
SECTION_TYPES = frozenset(
    "SHT_NULL SHT_PROGBITS SHT_SYMTAB SHT_STRTAB SHT_RELA SHT_HASH SHT_DYNAMIC "
    "SHT_NOTE SHT_NOBITS SHT_REL SHT_DYNSYM SHT_INIT_ARRAY SHT_FINI_ARRAY "
    "SHT_PREINIT_ARRAY SHT_GROUP SHT_SYMTAB_SHNDX SHT_RELR SHT_GNU_ATTRIBUTES "
    "SHT_GNU_HASH SHT_GNU_verdef SHT_GNU_verneed SHT_GNU_versym SHT_AMD64_UNWIND".split()
)
DYNAMIC_TAGS = frozenset(
    "DT_NULL DT_NEEDED DT_PLTRELSZ DT_PLTGOT DT_HASH DT_STRTAB DT_SYMTAB DT_RELA "
    "DT_RELASZ DT_RELAENT DT_STRSZ DT_SYMENT DT_INIT DT_FINI DT_SONAME DT_RPATH "
    "DT_SYMBOLIC DT_REL DT_RELSZ DT_RELENT DT_PLTREL DT_DEBUG DT_TEXTREL DT_JMPREL "
    "DT_BIND_NOW DT_INIT_ARRAY DT_FINI_ARRAY DT_INIT_ARRAYSZ DT_FINI_ARRAYSZ "
    "DT_RUNPATH DT_FLAGS DT_PREINIT_ARRAY DT_PREINIT_ARRAYSZ DT_RELRSZ DT_RELR "
    "DT_RELRENT DT_GNU_HASH DT_VERSYM DT_RELACOUNT DT_RELCOUNT DT_FLAGS_1 "
    "DT_VERDEF DT_VERDEFNUM DT_VERNEED DT_VERNEEDNUM "
    "DT_X86_64_PLT DT_X86_64_PLTSZ DT_X86_64_PLTENT".split()
)
# x86-64 psABI commit6d824a52a42d173eb838b879616c1be5870b593e.
# Pinned pyelftools0.33 reports the first as DT_LOPROC, others as raw integers.
X86_64_TAGS = {
    0x70000000: "DT_X86_64_PLT",
    0x70000001: "DT_X86_64_PLTSZ",
    0x70000003: "DT_X86_64_PLTENT",
}
PH_FIELDS = (
    "p_type",
    "p_flags",
    "p_offset",
    "p_vaddr",
    "p_paddr",
    "p_filesz",
    "p_memsz",
    "p_align",
)
SH_FIELDS = (
    "sh_name",
    "sh_type",
    "sh_flags",
    "sh_addr",
    "sh_offset",
    "sh_size",
    "sh_link",
    "sh_info",
    "sh_addralign",
    "sh_entsize",
)


class ElfMetadataError(ValueError):
    pass


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise ElfMetadataError("media_elf_" + code)


def _integer(value: int, ceiling: int = 2**64 - 1) -> int:
    _refuse(type(value) is int and 0 <= value <= ceiling, "integer")
    return value


def _extent(offset: int, size: int, limit: int) -> None:
    _integer(offset)
    _integer(size)
    _refuse(offset <= limit and size <= limit - offset, "extent")


class _Strings:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.examined = 0
        self.emitted = 0

    def read(self, start: int, size: int, offset: int, grammar: str) -> str:
        _extent(start, size, len(self.raw))
        _integer(offset)
        _refuse(offset < size, "string_offset")
        search = min(4096, size - offset)
        # Charge the whole bounded search before examining bytes, including failures.
        self.examined += search
        _refuse(self.examined <= 131072, "string_work")
        begin = start + offset
        end = self.raw.find(b"\0", begin, begin + search)
        _refuse(end >= begin, "string_termination")
        value = self.raw[begin:end]
        try:
            text = value.decode("ascii", "strict")
        except UnicodeError:
            raise ElfMetadataError("media_elf_string_encoding") from None
        _refuse(re.fullmatch(grammar, text) is not None, "string_grammar")
        self.emitted += len(value)
        _refuse(self.emitted <= 32768, "string_output")
        return text


def _inspect(raw: bytes, elf, decode) -> bytes:
    """Private fixed-record seam; fake records permit tests without optional imports."""
    _refuse(type(raw) is bytes and 64 <= len(raw) <= INPUT_BYTES, "input")
    header = elf.header
    ident = header["e_ident"]
    _refuse(
        elf.elfclass == 64
        and elf.little_endian is True
        and header["e_machine"] == "EM_X86_64"
        and header["e_type"] in ("ET_EXEC", "ET_DYN")
        and header["e_version"] == ident["EI_VERSION"] == "EV_CURRENT"
        and ident["EI_OSABI"] in ("ELFOSABI_SYSV", "ELFOSABI_LINUX")
        and ident["EI_ABIVERSION"] == 0
        and header["e_flags"] == 0
        and raw[9:16] == bytes(7),
        "abi",
    )
    count_p = _integer(header["e_phnum"])
    count_s = _integer(header["e_shnum"])
    _refuse(1 <= count_p <= 64 and 2 <= count_s <= 512, "counts")
    _refuse(
        header["e_ehsize"] == 64 and header["e_phentsize"] == 56 and header["e_shentsize"] == 64,
        "width",
    )
    _extent(header["e_phoff"], count_p * 56, len(raw))
    _extent(header["e_shoff"], count_s * 64, len(raw))
    _refuse(header["e_phoff"] >= 64 and header["e_shoff"] >= 64, "header_overlap")
    _refuse(
        header["e_phoff"] + count_p * 56 <= header["e_shoff"]
        or header["e_shoff"] + count_s * 64 <= header["e_phoff"],
        "header_tables_overlap",
    )
    _refuse(
        type(header["e_shstrndx"]) is int and 0 < header["e_shstrndx"] < count_s, "section_names"
    )

    def record(kind, offset, width):
        structure = getattr(elf.structs, kind)
        _refuse(structure.sizeof() == width, "structure_width")
        _extent(offset, width, len(raw))
        return decode(structure, offset)

    programs = []
    for index in range(count_p):
        item = record("Elf_Phdr", header["e_phoff"] + index * 56, 56)
        _refuse(item["p_type"] in PROGRAM_TYPES, "program_type")
        for key in PH_FIELDS[1:]:
            _integer(item[key])
        _refuse(item["p_flags"] <= 7, "program_flags")
        _extent(item["p_offset"], item["p_filesz"], len(raw))
        _extent(item["p_vaddr"], item["p_memsz"], 2**64 - 1)
        alignment = item["p_align"]
        _refuse(alignment in (0, 1) or alignment & (alignment - 1) == 0, "alignment")
        if item["p_type"] == "PT_LOAD":
            _refuse(item["p_filesz"] <= item["p_memsz"], "load_size")
            _refuse(
                alignment <= 1 or item["p_offset"] % alignment == item["p_vaddr"] % alignment,
                "load_alignment",
            )
        programs.append({key: item[key] for key in PH_FIELDS})
    loads = [item for item in programs if item["p_type"] == "PT_LOAD"]
    _refuse(bool(loads), "loads")

    def mapped(address, size):
        _integer(address)
        _integer(size)
        _extent(address, size, 2**64 - 1)
        candidates = [
            item["p_offset"] + address - item["p_vaddr"]
            for item in loads
            if item["p_vaddr"] <= address and size <= item["p_filesz"] - (address - item["p_vaddr"])
        ]
        _refuse(len(candidates) == 1, "load_mapping")
        _extent(candidates[0], size, len(raw))
        return candidates[0]

    sections = []
    for index in range(count_s):
        item = record("Elf_Shdr", header["e_shoff"] + index * 64, 64)
        _refuse(item["sh_type"] in SECTION_TYPES, "section_type")
        for key in SH_FIELDS:
            if key != "sh_type":
                _integer(item[key])
        _refuse(
            item["sh_link"] < count_s and not item["sh_flags"] & 0x800,
            "section_link_or_compression",
        )
        if item["sh_type"] == "SHT_NOBITS":
            _extent(item["sh_offset"], 0, len(raw))
        else:
            _extent(item["sh_offset"], item["sh_size"], len(raw))
        sections.append({key: item[key] for key in SH_FIELDS})
    _refuse(
        sections[0]["sh_type"] == "SHT_NULL"
        and all(sections[0][key] == 0 for key in SH_FIELDS if key != "sh_type"),
        "null_section",
    )
    names = sections[header["e_shstrndx"]]
    _refuse(names["sh_type"] == "SHT_STRTAB", "section_names")
    strings = _Strings(raw)
    for item in sections:
        item["name"] = strings.read(
            names["sh_offset"], names["sh_size"], item["sh_name"], r"[A-Za-z0-9_+.-]{0,255}"
        )
        if item["sh_type"] != "SHT_NOBITS":
            item["uninterpreted_content_sha256"] = hashlib.sha256(
                raw[item["sh_offset"] : item["sh_offset"] + item["sh_size"]]
            ).hexdigest()

    interpreters = [item for item in programs if item["p_type"] == "PT_INTERP"]
    _refuse(len(interpreters) <= 1, "interpreter_count")
    interpreter = None
    if interpreters:
        item = interpreters[0]
        _refuse(2 <= item["p_filesz"] <= 4096, "interpreter_extent")
        interpreter = strings.read(
            item["p_offset"], item["p_filesz"], 0, r"/(?:[A-Za-z0-9_+.-]+/)*[A-Za-z0-9_+.-]+"
        )
        _refuse(
            all(part not in (".", "..") for part in interpreter.split("/")[1:]), "interpreter_path"
        )
        tail = raw[item["p_offset"] + len(interpreter) : item["p_offset"] + item["p_filesz"]]
        _refuse(tail and not any(tail), "interpreter_trailing")
    segments = [item for item in programs if item["p_type"] == "PT_DYNAMIC"]
    tables = [item for item in sections if item["sh_type"] == "SHT_DYNAMIC"]
    _refuse(len(segments) == len(tables) == 1, "dynamic_count")
    segment, table = segments[0], tables[0]
    _refuse(
        segment["p_offset"] == table["sh_offset"] and segment["p_filesz"] == table["sh_size"],
        "dynamic_extent",
    )
    _refuse(
        mapped(segment["p_vaddr"], segment["p_filesz"]) == segment["p_offset"]
        and table["sh_addr"] == segment["p_vaddr"],
        "dynamic_address_mapping",
    )
    _refuse(
        table["sh_entsize"] == 16
        and table["sh_size"] % 16 == 0
        and 1 <= table["sh_size"] // 16 <= 1024,
        "dynamic_width_count",
    )
    dynstr = sections[table["sh_link"]]
    _refuse(dynstr["sh_type"] == "SHT_STRTAB", "dynamic_strings")
    tags = []
    seen = {}
    terminated = False
    for index in range(table["sh_size"] // 16):
        item = record("Elf_Dyn", table["sh_offset"] + index * 16, 16)
        tag, value = item["d_tag"], _integer(item["d_val"])
        if tag == "DT_LOPROC":
            tag = X86_64_TAGS[0x70000000]
        elif type(tag) is int and tag in X86_64_TAGS:
            tag = X86_64_TAGS[tag]
        # Already required EM_X86_64; no other processor tags are normalized.

        if tag not in DYNAMIC_TAGS:
            # Bounded pinned-enum label/integer diagnostic; never accept this tag.
            label = (
                tag
                if type(tag) is str and re.fullmatch(r"DT_[A-Z0-9_]{1,64}", tag)
                else str(tag)
                if type(tag) is int and -(2**63) <= tag < 2**63
                else "unrecognized"
            )
            raise ElfMetadataError("media_elf_dynamic_tag:" + label)
        if terminated:
            _refuse(tag == "DT_NULL" and value == 0, "dynamic_padding")
            continue
        if tag == "DT_NULL":
            _refuse(value == 0, "dynamic_null")
            terminated = True
            continue
        _refuse(tag == "DT_NEEDED" or tag not in seen, "dynamic_duplicate")
        seen[tag] = value
        tags.append({"tag": tag, "value": value})
    _refuse(terminated and "DT_STRTAB" in seen and "DT_STRSZ" in seen, "dynamic_required")
    _refuse(
        seen["DT_STRSZ"] == dynstr["sh_size"]
        and mapped(seen["DT_STRTAB"], seen["DT_STRSZ"]) == dynstr["sh_offset"],
        "dynamic_string_mapping",
    )
    for item in tags:
        if item["tag"] in ("DT_NEEDED", "DT_SONAME", "DT_RPATH", "DT_RUNPATH"):
            grammar = (
                r"[A-Za-z0-9_+.-]{1,255}"
                if item["tag"] in ("DT_NEEDED", "DT_SONAME")
                else r"[A-Za-z0-9_+./:$-]{0,4095}"
            )
            item["string"] = strings.read(
                dynstr["sh_offset"], dynstr["sh_size"], item["value"], grammar
            )
    result = {
        "schema": "zelo-media-elf-loader-topology-v1",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
        "header": {
            key: header[key]
            for key in (
                "e_type",
                "e_machine",
                "e_entry",
                "e_phoff",
                "e_phnum",
                "e_shoff",
                "e_shnum",
                "e_shstrndx",
            )
        },
        "osabi": ident["EI_OSABI"],
        "interpreter": interpreter,
        "programs": programs,
        "sections": sections,
        "dynamic": tags,
        "uninterpreted_bodies": "symbol-version-hash-relocation-note-and-other-section-content",
        "symbol_version_content_verified": False,
        "abi_qualified": False,
        "platform_complete": False,
        "runtime_authority": False,
        "instruction_trust": "none",
    }
    output = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )
    _refuse(len(output) + 1 <= OUTPUT_BYTES, "output")
    return output


def inspect_elf_metadata(raw: bytes) -> bytes:
    """Guest-only optional parser; no host import or target-selected executable."""
    _refuse(
        sys.flags.isolated
        and sys.flags.no_site
        and sys.dont_write_bytecode
        and tuple(sys.path) == GUEST_PATHS,
        "guest_required",
    )
    _refuse(type(raw) is bytes and 64 <= len(raw) <= INPUT_BYTES, "input")
    manifest = capture_manifest(TOOL_ROOT, TOOL_ROLES, TOOL_POLICY)
    _refuse(manifest.digest == TOOL_MANIFEST_DIGEST, "tool_manifest")
    from elftools.common.exceptions import ELFError
    from elftools.common.utils import struct_parse
    from elftools.construct import ConstructError
    from elftools.elf.elffile import ELFFile

    try:
        stream = io.BytesIO(raw)
        elf = ELFFile(stream, stream_loader=None)
        return _inspect(
            raw, elf, lambda structure, offset: struct_parse(structure, stream, stream_pos=offset)
        )
    except ElfMetadataError:
        raise  # Preserve only our fixed refusal codes, never parser exception text.
    except (ELFError, ConstructError, ValueError, KeyError, TypeError, EOFError, OverflowError):
        raise ElfMetadataError("media_elf_metadata_refused") from None
