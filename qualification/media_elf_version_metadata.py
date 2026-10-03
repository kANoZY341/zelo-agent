"""Bounded GNU version graph structure, never symbol or ABI qualification.

Optional fixed-record parsing is guest-only. The pure validator checks output
binding and consistency, not the correctness of the guest's ELF decoding.
"""

import hashlib
import io
import json
import re

from qualification import media_elf_metadata as loader

INPUT_BYTES = loader.INPUT_BYTES
OUTPUT_BYTES = loader.OUTPUT_BYTES
ElfMetadataError = loader.ElfMetadataError
GRAMMAR = r"[A-Za-z0-9_+.-]{1,255}"
TABLE_FIELDS = "section_index offset bytes address entry_bytes link info content_sha256".split()
COUNT_FIELDS = (
    "dynsym versym definitions_declared definitions_decoded needs_declared needs_decoded "
    "definition_aux_declared definition_aux_decoded need_aux_declared need_aux_decoded "
    "inheritance_edges"
).split()
FALSE_CLAIMS = (
    "symbol_content_verified version_hash_verified abi_qualified "
    "platform_complete runtime_authority"
).split()
TOP_FIELDS = (
    "schema source_bytes source_sha256 loader_report loader_report_sha256 versioned tables counts "
    "definitions needs frequencies supported_version_graph_structure_verified instruction_trust"
).split() + FALSE_CLAIMS
LIMITS = {
    "decode_count": 34048,
    "decode_bytes": 98304,
    "intervals": 1280,
    "comparisons": 4096,
    "disjoint": 32,
    "padding": 131072,
    "graph": 4096,
    "frequency": 32768,
    "string_work": 4194304,
    "string_output": 32768,
}


def _require(condition, code):
    if not condition:
        raise ElfMetadataError("media_elf_version_" + code)


def _int(value, low=0, high=2**64 - 1, code="integer"):
    _require(type(value) is int and low <= value <= high, code)
    return value


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys), "schema")


def _list(value, maximum):
    _require(type(value) is list and len(value) <= maximum, "count")
    return value


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "digest")


class _Budget:
    def __init__(self):
        self.used = dict.fromkeys(LIMITS, 0)

    def charge(self, kind, amount=1):
        _int(amount)
        _require(amount <= LIMITS[kind] - self.used[kind], kind)
        self.used[kind] += amount


def canonical(value):
    """Incremental output cap includes the newline the guest adds."""
    chunks = bytearray()
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    for chunk in encoder.iterencode(value):
        encoded = chunk.encode("ascii")
        _require(len(encoded) + len(chunks) + 1 <= OUTPUT_BYTES, "output")
        chunks.extend(encoded)
    return bytes(chunks)


def _name(value, budget):
    _require(type(value) is str and re.fullmatch(GRAMMAR, value), "string")
    budget.charge("string_output", len(value))
    return value


def _mapped(raw, programs, address, size):
    _int(address)
    _int(size)
    _require(address + size <= 2**64 - 1, "extent")
    matches = []
    for item in programs:
        if item["p_type"] == "PT_LOAD":
            start, extent = _int(item["p_vaddr"]), _int(item["p_filesz"])
            offset = _int(item["p_offset"])
            if start <= address and size <= extent - (address - start):
                matches.append(offset + address - start)
    _require(len(matches) == 1, "mapping")
    _require(matches[0] <= len(raw) and size <= len(raw) - matches[0], "extent")
    return matches[0]


def _topology(raw, report, budget):
    """Use the bound loader's records; no optional imports or symbol decoding."""
    sections = _list(report["sections"], 512)
    programs = _list(report["programs"], 64)
    _require(2 <= len(sections) and 1 <= len(programs), "count")
    tags = {}
    needed = []
    soname = None
    for item in _list(report["dynamic"], 1024):
        tag = item["tag"]
        _require(type(tag) is str, "schema")
        value = _int(item["value"])
        _require(tag == "DT_NEEDED" or tag not in tags, "duplicate")
        tags[tag] = value
        if tag == "DT_NEEDED":
            needed.append(item["string"])
        elif tag == "DT_SONAME":
            soname = item["string"]

    def section(kind, required):
        found = [i for i, s in enumerate(sections) if s["sh_type"] == kind]
        _require(len(found) == 1 if required else len(found) <= 1, "table")
        return found[0] if found else None

    dynamic_index = section("SHT_DYNAMIC", True)
    string_index = _int(sections[dynamic_index]["sh_link"], 0, len(sections) - 1)
    _require(sections[string_index]["sh_type"] == "SHT_STRTAB", "link")
    indices = {
        "dynsym": section("SHT_DYNSYM", True),
        "dynstr": string_index,
        "versym": section("SHT_GNU_versym", False),
        "verdef": section("SHT_GNU_verdef", False),
        "verneed": section("SHT_GNU_verneed", False),
    }
    tables = {}
    for role, index in indices.items():
        if index is None:
            tables[role] = None
            continue
        s = sections[index]
        offset, size = _int(s["sh_offset"]), _int(s["sh_size"])
        address = _int(s["sh_addr"])
        _require(offset <= len(raw) and size <= len(raw) - offset, "extent")
        flags = _int(s["sh_flags"])
        _require(not flags & 0x800, "table")
        if flags & 2:
            _require(_mapped(raw, programs, address, size) == offset, "mapping")
        digest = s["uninterpreted_content_sha256"]
        _sha(digest)
        tables[role] = dict(
            zip(
                TABLE_FIELDS,
                (
                    index,
                    offset,
                    size,
                    address,
                    _int(s["sh_entsize"]),
                    _int(s["sh_link"], 0, len(sections) - 1),
                    _int(s["sh_info"]),
                    digest,
                ),
                strict=True,
            )
        )
    sym, strings = tables["dynsym"], tables["dynstr"]
    _require(
        sym["entry_bytes"] == 24 and sym["bytes"] % 24 == 0 and sym["link"] == string_index,
        "dynsym",
    )
    count = _int(sym["bytes"] // 24, 1, 32768, "count")
    _int(sym["info"], 0, count)
    _require(strings["info"] == 0 and strings["entry_bytes"] == 0, "table")
    _require(tags.get("DT_SYMENT") == 24 and "DT_SYMTAB" in tags, "companion")
    _require(
        _mapped(raw, programs, tags["DT_SYMTAB"], sym["bytes"]) == sym["offset"]
        and tags["DT_SYMTAB"] == sym["address"],
        "mapping",
    )
    _require(tags.get("DT_STRSZ") == strings["bytes"] and "DT_STRTAB" in tags, "companion")
    _require(
        _mapped(raw, programs, tags["DT_STRTAB"], strings["bytes"]) == strings["offset"], "mapping"
    )
    parent_counts = {}
    for role, pointer, number in (
        ("verdef", "DT_VERDEF", "DT_VERDEFNUM"),
        ("verneed", "DT_VERNEED", "DT_VERNEEDNUM"),
        ("versym", "DT_VERSYM", None),
    ):
        table = tables[role]
        if table is None:
            _require(pointer not in tags and (number is None or number not in tags), "companion")
            if number:
                parent_counts[role] = 0
            continue
        _require(pointer in tags and (number is None or number in tags), "companion")
        _require(0 < table["bytes"] <= 65536, "extent")
        _require(table["entry_bytes"] == (2 if role == "versym" else 0), "width")
        _require(table["link"] == (indices["dynsym"] if role == "versym" else string_index), "link")
        _require(
            _mapped(raw, programs, tags[pointer], table["bytes"]) == table["offset"]
            and tags[pointer] == table["address"],
            "mapping",
        )
        if number:
            parent_counts[role] = _int(tags[number], 1, 128, "count")
            _require(table["info"] == tags[number], "count")
        else:
            _require(table["bytes"] == 2 * count and table["info"] == 0, "count")
    _require(
        tables["versym"] is not None or (tables["verdef"] is None and tables["verneed"] is None),
        "companion",
    )
    occupied = [t for t in tables.values() if t is not None]
    for i, a in enumerate(occupied):
        for b in occupied[i + 1 :]:
            budget.charge("disjoint")
            _require(
                a["offset"] + a["bytes"] <= b["offset"] or b["offset"] + b["bytes"] <= a["offset"],
                "overlap",
            )
    # All role widths/counts/extents, companions, maps and disjointness precede
    # content access. memoryview avoids an input-sized temporary slice.
    for table in occupied:
        view = memoryview(raw)[table["offset"] : table["offset"] + table["bytes"]]
        _require(hashlib.sha256(view).hexdigest() == table["content_sha256"], "digest")
    return tables, count, parent_counts, needed, soname


def _graph(definitions, budget):
    by_name = {d["name"]: d["dependency_names"] for d in definitions}
    colors = {}
    for root in by_name:
        if colors.get(root) == 2:
            continue
        stack = [(root, False)]
        while stack:
            budget.charge("graph")
            name, leaving = stack.pop()
            if leaving:
                colors[name] = 2
                continue
            _require(colors.get(name) != 1, "graph_cycle")
            if colors.get(name) == 2:
                continue
            colors[name] = 1
            stack.append((name, True))
            for dependency in reversed(by_name[name]):
                budget.charge("graph")
                _require(dependency in by_name, "reference")
                stack.append((dependency, False))


def validate_report(report, raw):
    """Pure host consistency validation; caller also applies old loader acceptance."""
    _require(type(raw) is bytes and 64 <= len(raw) <= INPUT_BYTES, "input")
    _keys(report, TOP_FIELDS)
    _require(report["schema"] == "zelo-media-elf-version-graph-v1", "schema")
    _int(report["source_bytes"], 64, INPUT_BYTES)
    _sha(report["source_sha256"])
    _require(
        report["source_bytes"] == len(raw)
        and report["source_sha256"] == hashlib.sha256(raw).hexdigest(),
        "binding",
    )
    embedded = report["loader_report"]
    _require(type(embedded) is dict, "schema")
    _require(
        embedded["source_bytes"] == len(raw)
        and embedded["source_sha256"] == report["source_sha256"],
        "binding",
    )
    _sha(report["loader_report_sha256"])
    _require(
        hashlib.sha256(canonical(embedded)).hexdigest() == report["loader_report_sha256"], "binding"
    )
    _require(type(report["versioned"]) is bool, "schema")
    _require(
        report["supported_version_graph_structure_verified"] is True
        and all(report[k] is False for k in FALSE_CLAIMS)
        and report["instruction_trust"] == "none",
        "claims",
    )
    budget = _Budget()
    tables, count, parents, needed, soname = _topology(raw, embedded, budget)
    _keys(report["tables"], tables)
    for role, expected in tables.items():
        actual = report["tables"][role]
        if expected is None:
            _require(actual is None, "table")
        else:
            _keys(actual, TABLE_FIELDS)
            for key in TABLE_FIELDS[:-1]:
                _int(actual[key])
            _sha(actual["content_sha256"])
            _require(actual == expected, "table")
    versioned = tables["versym"] is not None
    _require(report["versioned"] is versioned, "table")
    counts = report["counts"]
    _keys(counts, COUNT_FIELDS)
    for key in COUNT_FIELDS:
        _int(counts[key], 0, 32768 if key in ("dynsym", "versym") else 1024)
    definitions = _list(report["definitions"], 128)
    needs = _list(report["needs"], 128)
    indices, names = set(), set()
    last = 0
    bases = 0
    edges = 0
    def_aux = 0
    for item in definitions:
        _keys(item, "index flags hash name dependency_names aux_declared aux_decoded".split())
        index = _int(item["index"], 1, 0x7EFF)
        _require(index > last and index not in indices, "index")
        last = index
        indices.add(index)
        flags = _int(item["flags"], 0, 65535)
        _require(flags & ~3 == 0, "flags")
        _require(bool(flags & 1) == (index == 1), "base")
        bases += bool(flags & 1)
        _int(item["hash"], 0, 2**32 - 1)
        name = _name(item["name"], budget)
        _require(name not in names, "duplicate")
        names.add(name)
        if index == 1:
            _require(name == soname, "base")
        deps = _list(item["dependency_names"], 63)
        seen = set()
        for dependency in deps:
            _name(dependency, budget)
            _require(dependency != name and dependency not in seen, "duplicate")
            seen.add(dependency)
        aux = _int(item["aux_declared"], 1, 64)
        _int(item["aux_decoded"], 1, 64)
        _require(aux == item["aux_decoded"] == 1 + len(deps), "count")
        def_aux += aux
        edges += len(deps)
    _require(bases == (1 if tables["verdef"] is not None else 0), "base")
    _graph(definitions, budget)
    providers = set()
    need_aux = 0
    for item in needs:
        _keys(item, "provider aux_declared aux_decoded versions".split())
        provider = _name(item["provider"], budget)
        _require(provider not in providers and needed.count(provider) == 1, "provider")
        providers.add(provider)
        versions = _list(item["versions"], 64)
        aux = _int(item["aux_declared"], 1, 64)
        _int(item["aux_decoded"], 1, 64)
        _require(aux == item["aux_decoded"] == len(versions), "count")
        need_aux += aux
        version_names = set()
        for version in versions:
            _keys(version, "index flags hash name".split())
            index = _int(version["index"], 2, 0x7EFF)
            _require(index not in indices, "index")
            indices.add(index)
            flags = _int(version["flags"], 0, 2)
            _require(flags & ~2 == 0, "flags")
            _int(version["hash"], 0, 2**32 - 1)
            name = _name(version["name"], budget)
            _require(name not in version_names, "duplicate")
            version_names.add(name)
    _require(def_aux + need_aux <= 1024, "count")
    expected = dict(
        zip(
            COUNT_FIELDS,
            (
                count,
                count if versioned else 0,
                parents["verdef"],
                len(definitions),
                parents["verneed"],
                len(needs),
                def_aux,
                def_aux,
                need_aux,
                need_aux,
                edges,
            ),
            strict=True,
        )
    )
    _require(
        expected["definitions_declared"] == expected["definitions_decoded"]
        and expected["needs_declared"] == expected["needs_decoded"]
        and counts == expected,
        "count",
    )
    total, last = 0, -1
    for item in _list(report["frequencies"], 2050):
        _keys(item, "raw index hidden count".split())
        raw_index = _int(item["raw"], 0, 65535)
        index = _int(item["index"], 0, 0x7EFF, "index")
        _require(type(item["hidden"]) is bool, "schema")
        _require(
            raw_index > last
            and index == raw_index & 0x7FFF
            and item["hidden"] is bool(raw_index & 0x8000),
            "index",
        )
        _require(raw_index in (0, 1) or (index > 1 and index in indices), "reference")
        last = raw_index
        amount = _int(item["count"], 1, 32768)
        budget.charge("frequency", amount)
        total += amount
    _require(total == (count if versioned else 0), "count")
    return canonical(report)


class _Reader:
    def __init__(self, raw, structs, decode, strings, budget):
        self.raw, self.structs, self.decode = raw, structs, decode
        self.strings, self.budget = strings, budget
        self.aux_total = 0

    def record(self, kind, offset, width, begin, end, intervals=None):
        if intervals is not None:
            self.budget.charge("comparisons")
            _require(begin <= offset and width <= end - offset, "extent")
        # VERSYM offsets are derived from the already checked exact table width.
        self.budget.charge("decode_count")
        self.budget.charge("decode_bytes", width)
        structure = getattr(self.structs, kind)
        _require(structure.sizeof() == width, "width")
        if intervals is not None:
            self.budget.charge("intervals")
            intervals.append((offset, offset + width))
        return self.decode(structure, offset)

    def name(self, relative):
        _int(relative)
        table = self.strings
        _require(relative < table["bytes"], "string")
        allowance = min(4096, table["bytes"] - relative)
        self.budget.charge("string_work", allowance)
        start = table["offset"] + relative
        end = self.raw.find(b"\0", start, start + allowance)
        _require(end >= start, "string")
        try:
            value = self.raw[start:end].decode("ascii")
        except UnicodeError:
            raise ElfMetadataError("media_elf_version_string") from None
        return _name(value, self.budget)

    def chain(self, table, count, definition):
        if table is None:
            return []
        parent_kind, aux_kind, parent_width, aux_width, prefix = (
            ("Elf_Verdef", "Elf_Verdaux", 20, 8, "vd")
            if definition
            else ("Elf_Verneed", "Elf_Vernaux", 16, 16, "vn")
        )
        begin, end = table["offset"], table["offset"] + table["bytes"]
        position = begin
        intervals, result = [], []
        for number in range(count):
            parent = self.record(parent_kind, position, parent_width, begin, end, intervals)
            _require(_int(parent[prefix + "_version"], 0, 65535) == 1, "revision")
            aux_count = _int(parent[prefix + "_cnt"], 1, 64, "count")
            self.aux_total += aux_count
            _require(self.aux_total <= 1024, "count")
            step = _int(parent[prefix + "_next"], 0, 2**32 - 1)
            if number + 1 < count:
                _require(step >= parent_width and step % 4 == 0, "chain")
                boundary = position + step
                self.budget.charge("comparisons")
                _require(parent_width <= end - boundary, "extent")
            else:
                _require(step == 0, "chain")
                boundary = end
            aux_step = _int(parent[prefix + "_aux"], 0, 2**32 - 1)
            _require(aux_step >= parent_width and aux_step % 4 == 0, "chain")
            auxiliary = position + aux_step
            decoded = []
            for aux_number in range(aux_count):
                record = self.record(
                    aux_kind, auxiliary, aux_width, position + parent_width, boundary, intervals
                )
                if definition:
                    decoded.append(self.name(record["vda_name"]))
                    next_aux = _int(record["vda_next"], 0, 2**32 - 1)
                else:
                    decoded.append(
                        {
                            "index": _int(record["vna_other"], 2, 0x7EFF),
                            "flags": _int(record["vna_flags"], 0, 65535),
                            "hash": _int(record["vna_hash"], 0, 2**32 - 1),
                            "name": self.name(record["vna_name"]),
                        }
                    )
                    next_aux = _int(record["vna_next"], 0, 2**32 - 1)
                if aux_number + 1 < aux_count:
                    _require(next_aux >= aux_width and next_aux % 4 == 0, "chain")
                    auxiliary += next_aux
                else:
                    _require(next_aux == 0, "chain")
            if definition:
                result.append(
                    {
                        "index": _int(parent["vd_ndx"], 1, 0x7EFF),
                        "flags": _int(parent["vd_flags"], 0, 65535),
                        "hash": _int(parent["vd_hash"], 0, 2**32 - 1),
                        "name": decoded[0],
                        "dependency_names": decoded[1:],
                        "aux_declared": aux_count,
                        "aux_decoded": len(decoded),
                    }
                )
            else:
                result.append(
                    {
                        "provider": self.name(parent["vn_file"]),
                        "versions": decoded,
                        "aux_declared": aux_count,
                        "aux_decoded": len(decoded),
                    }
                )
            position = boundary
        cursor = begin
        for start, stop in sorted(intervals):
            self.budget.charge("comparisons")
            _require(cursor <= start, "overlap")
            self.budget.charge("padding", start - cursor)
            _require(not any(self.raw[cursor:start]), "padding")
            cursor = stop
        self.budget.charge("padding", end - cursor)
        _require(not any(self.raw[cursor:end]), "padding")
        return result


def _inspect(raw, report, structs, decode):
    """Fake fixed-record seam for offline tests; caller supplies validated loader."""
    budget = _Budget()
    tables, count, parents, _, _ = _topology(raw, report, budget)
    reader = _Reader(raw, structs, decode, tables["dynstr"], budget)
    definitions = reader.chain(tables["verdef"], parents["verdef"], True)
    needs = reader.chain(tables["verneed"], parents["verneed"], False)
    definitions.sort(key=lambda item: item["index"])
    frequencies = {}
    table = tables["versym"]
    if table is not None:
        begin, end = table["offset"], table["offset"] + table["bytes"]
        for i in range(count):
            # VERSYM isn't a parent/aux interval and uses a separate finite range.
            item = reader.record("Elf_Versym", begin + 2 * i, 2, begin, end)
            value = item["ndx"]
            if type(value) is str:
                _require(value in ("VER_NDX_LOCAL", "VER_NDX_GLOBAL"), "index")
                value = 0 if value == "VER_NDX_LOCAL" else 1
            _int(value, 0, 65535)
            budget.charge("frequency")
            if value not in frequencies:
                _require(len(frequencies) < 2050, "frequency")
                frequencies[value] = 0
            frequencies[value] += 1
    def_aux = sum(d["aux_decoded"] for d in definitions)
    need_aux = sum(n["aux_decoded"] for n in needs)
    result = {
        "schema": "zelo-media-elf-version-graph-v1",
        "source_bytes": len(raw),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "loader_report": report,
        "loader_report_sha256": hashlib.sha256(canonical(report)).hexdigest(),
        "versioned": table is not None,
        "tables": tables,
        "counts": dict(
            zip(
                COUNT_FIELDS,
                (
                    count,
                    count if table else 0,
                    parents["verdef"],
                    len(definitions),
                    parents["verneed"],
                    len(needs),
                    def_aux,
                    def_aux,
                    need_aux,
                    need_aux,
                    sum(len(d["dependency_names"]) for d in definitions),
                ),
                strict=True,
            )
        ),
        "definitions": definitions,
        "needs": needs,
        "frequencies": [
            {
                "raw": value,
                "index": value & 0x7FFF,
                "hidden": bool(value & 0x8000),
                "count": frequencies[value],
            }
            for value in sorted(frequencies)
        ],
        "supported_version_graph_structure_verified": True,
        "instruction_trust": "none",
        **dict.fromkeys(FALSE_CLAIMS, False),
    }
    return validate_report(result, raw)


def inspect_elf_metadata(raw):
    """Authenticate/validate with unchanged guest loader before optional imports."""
    bound = loader.inspect_elf_metadata(raw)
    from elftools.common.exceptions import ELFError
    from elftools.common.utils import struct_parse
    from elftools.construct import ConstructError
    from elftools.elf.elffile import ELFFile

    try:
        stream = io.BytesIO(raw)
        elf = ELFFile(stream, stream_loader=None)
        return _inspect(
            raw,
            json.loads(bound),
            elf.structs,
            lambda structure, offset: struct_parse(structure, stream, stream_pos=offset),
        )
    except ElfMetadataError:
        raise
    except (ELFError, ConstructError, ValueError, KeyError, TypeError, EOFError, OverflowError):
        raise ElfMetadataError("media_elf_version_refused") from None
