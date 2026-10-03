"""Offline fake records only: optional pyelftools never imported or executed."""

import copy
import hashlib
import json

import pytest

from qualification import media_elf_metadata as loader
from qualification import media_elf_version_metadata as m
from tests.media_elf_version_fixtures import make


def inspect(candidate):
    raw, _, elf, _, decode, _ = candidate
    bound = json.loads(loader._inspect(raw, elf, decode))
    return m._inspect(raw, bound, elf.structs, decode)


def test_versioned_oracle():
    candidate = make()
    output = inspect(candidate)
    report = json.loads(output)
    assert report["counts"] == dict(
        zip(m.COUNT_FIELDS, (5, 5, 3, 3, 1, 1, 4, 4, 1, 1, 1), strict=True)
    )
    assert [(d["index"], d["name"], d["dependency_names"]) for d in report["definitions"]] == [
        (1, "fixture.so", []),
        (2, "FIXTURE_1.0", []),
        (3, "FIXTURE_2.0", ["FIXTURE_1.0"]),
    ]
    assert report["needs"] == [
        {
            "provider": "libc.so.6",
            "aux_declared": 1,
            "aux_decoded": 1,
            "versions": [{"index": 4, "flags": 0, "hash": 0x56780004, "name": "GLIBC_2.34"}],
        }
    ]
    assert [f["raw"] for f in report["frequencies"]] == [0, 1, 2, 4, 0x8003]
    assert all(f["count"] == 1 for f in report["frequencies"])
    assert all(report[k] is False for k in m.FALSE_CLAIMS)
    assert report["supported_version_graph_structure_verified"] is True
    assert m.validate_report(report, candidate[0]) == output
    assert not any(kind == "Elf_Sym" for kind, _ in candidate[5])


def test_unversioned_is_explicit():
    report = json.loads(inspect(make(False)))
    assert report["versioned"] is False
    assert report["definitions"] == report["needs"] == report["frequencies"] == []
    assert report["counts"]["dynsym"] == 5
    assert all(v == 0 for k, v in report["counts"].items() if k != "dynsym")
    assert all(report["tables"][k] is None for k in ("versym", "verdef", "verneed"))


@pytest.mark.parametrize(
    "kind,relative,key,value",
    [
        ("Elf_Verdef", 0, "vd_version", 2),
        ("Elf_Verneed", 0, "vn_version", 2),
        ("Elf_Verdef", 0, "vd_cnt", 0),
        ("Elf_Verdef", 0, "vd_cnt", 65),
        ("Elf_Verneed", 0, "vn_cnt", 65),
        ("Elf_Verdef", 0, "vd_next", 0),
        ("Elf_Verdef", 0, "vd_next", 4),
        ("Elf_Verdef", 0, "vd_next", 33),
        ("Elf_Verdef", 0, "vd_next", 65536),
        ("Elf_Verdef", 64, "vd_next", 32),
        ("Elf_Verdef", 0, "vd_aux", 4),
        ("Elf_Verdef", 0, "vd_aux", 21),
        ("Elf_Verdef", 0, "vd_aux", 32),
        ("Elf_Verneed", 0, "vn_aux", 65536),
        ("Elf_Verdaux", 84, "vda_next", 0),
        ("Elf_Verdaux", 84, "vda_next", 9),
        ("Elf_Verdaux", 84, "vda_next", 4),
        ("Elf_Verdaux", 84, "vda_next", 16),
        ("Elf_Verdaux", 20, "vda_next", 8),
        ("Elf_Vernaux", 16, "vna_next", 16),
        ("Elf_Verdef", 0, "vd_flags", 4),
        ("Elf_Verdef", 0, "vd_flags", 0),
        ("Elf_Verdef", 32, "vd_flags", 1),
        ("Elf_Vernaux", 16, "vna_flags", 1),
        ("Elf_Verdef", 32, "vd_ndx", 1),
        ("Elf_Verdef", 32, "vd_ndx", 0x8002),
        ("Elf_Vernaux", 16, "vna_other", 2),
        ("Elf_Vernaux", 16, "vna_other", 0x8004),
        ("Elf_Vernaux", 16, "vna_other", 0x7F00),
        ("Elf_Verdef", 0, "vd_hash", True),
        ("Elf_Verneed", 0, "vn_file", 65536),
        ("Elf_Verdaux", 20, "vda_name", 65536),
    ],
)
def test_record_refusals(kind, relative, key, value):
    candidate = make()
    table = ".gnu.version_d" if kind in ("Elf_Verdef", "Elf_Verdaux") else ".gnu.version_r"
    candidate[3][kind, candidate[1]["sections"][table] + relative][key] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize(
    "value",
    [
        True,
        -1,
        65536,
        0x8000,
        0x8001,
        5,
        0x7F00,
        0xFF00,
        0xFF01,
        "VER_NDX_LORESERVE",
        "VER_NDX_ELIMINATE",
        "other",
    ],
)
def test_versym_refusals(value):
    candidate = make()
    candidate[3]["Elf_Versym", candidate[1]["sections"][".gnu.version"] + 8]["ndx"] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize("value", [0, 1, "VER_NDX_LOCAL", "VER_NDX_GLOBAL", 2, 0x8002, 4, 0x8004])
def test_supported_versym_values(value):
    candidate = make()
    candidate[3]["Elf_Versym", candidate[1]["sections"][".gnu.version"] + 8]["ndx"] = value
    assert json.loads(inspect(candidate))["counts"]["versym"] == 5


def test_max_supported_index():
    candidate = make()
    candidate[3]["Elf_Vernaux", candidate[1]["sections"][".gnu.version_r"] + 16]["vna_other"] = (
        0x7EFF
    )
    candidate[3]["Elf_Versym", candidate[1]["sections"][".gnu.version"] + 8]["ndx"] = 0xFEFF
    assert json.loads(inspect(candidate))["frequencies"][-1]["index"] == 0x7EFF


@pytest.mark.parametrize(
    "field,value",
    [
        ("sh_link", 0),
        ("sh_entsize", 8),
        ("sh_info", 1),
        ("sh_size", 12),
        ("sh_addr", 0),
        ("sh_offset", 240),
    ],
)
def test_versym_table_refusal(field, value):
    candidate = make()
    candidate[3]["Elf_Shdr", candidate[1]["section_table"] + 3 * 64][field] = value
    with pytest.raises(m.ElfMetadataError):
        inspect(candidate)


@pytest.mark.parametrize(
    "tag",
    [
        "DT_SYMTAB",
        "DT_SYMENT",
        "DT_VERSYM",
        "DT_VERDEF",
        "DT_VERDEFNUM",
        "DT_VERNEED",
        "DT_VERNEEDNUM",
    ],
)
def test_missing_companion(tag):
    candidate = make()
    raw, _, elf, _, decode, _ = candidate
    bound = json.loads(loader._inspect(raw, elf, decode))
    bound["dynamic"] = [d for d in bound["dynamic"] if d["tag"] != tag]
    with pytest.raises(m.ElfMetadataError):
        m._inspect(raw, bound, elf.structs, decode)


@pytest.mark.parametrize(
    "tag", ["DT_VERSYM", "DT_VERDEF", "DT_VERDEFNUM", "DT_VERNEED", "DT_VERNEEDNUM"]
)
def test_tag_without_table(tag):
    raw, _, elf, _, decode, _ = make(False)
    bound = json.loads(loader._inspect(raw, elf, decode))
    bound["dynamic"].append({"tag": tag, "value": 1})
    with pytest.raises(m.ElfMetadataError):
        m._inspect(raw, bound, elf.structs, decode)


@pytest.mark.parametrize("relative,value", [(28, 1), (60, 1)])
def test_padding(relative, value):
    candidate = list(make())
    raw = bytearray(candidate[0])
    raw[candidate[1]["sections"][".gnu.version_d"] + relative] = value
    candidate[0] = bytes(raw)
    with pytest.raises(m.ElfMetadataError, match="padding"):
        inspect(candidate)


@pytest.mark.parametrize("value", [b"\xff", b"/", b"\0"])
def test_string_encoding_grammar(value):
    candidate = list(make())
    raw = bytearray(candidate[0])
    start = candidate[1]["sections"][".dynstr"] + candidate[1]["strings"]["FIXTURE_1.0"]
    raw[start : start + 1] = value
    candidate[0] = bytes(raw)
    with pytest.raises(m.ElfMetadataError, match="string"):
        inspect(candidate)


@pytest.mark.parametrize(
    "path,value",
    [
        (["schema"], "other"),
        (["source_bytes"], True),
        (["source_bytes"], 64),
        (["source_sha256"], "0" * 64),
        (["loader_report_sha256"], "0" * 64),
        (["versioned"], 1),
        (["versioned"], False),
        (["runtime_authority"], True),
        (["supported_version_graph_structure_verified"], 1),
        (["instruction_trust"], "trusted"),
        (["tables", "dynsym", "bytes"], True),
        (["tables", "dynsym", "content_sha256"], "0" * 64),
        (["tables", "verdef", "info"], 2),
        (["tables", "versym"], None),
        (["counts", "dynsym"], True),
        (["counts", "versym"], 0),
        (["counts", "definition_aux_declared"], 5),
        (["counts", "inheritance_edges"], 2),
        (["definitions", 0, "index"], True),
        (["definitions", 0, "flags"], 0),
        (["definitions", 0, "name"], "other.so"),
        (["definitions", 1, "index"], 1),
        (["definitions", 1, "name"], "fixture.so"),
        (["definitions", 1, "hash"], 2**32),
        (["definitions", 1, "aux_decoded"], 2),
        (["definitions", 2, "dependency_names"], ["MISSING"]),
        (["definitions", 2, "dependency_names"], ["FIXTURE_2.0"]),
        (["needs", 0, "provider"], "other.so"),
        (["needs", 0, "versions", 0, "index"], 3),
        (["needs", 0, "versions", 0, "flags"], 1),
        (["needs", 0, "versions", 0, "hash"], -1),
        (["frequencies", 0, "raw"], 0x8000),
        (["frequencies", 0, "hidden"], 0),
        (["frequencies", 0, "count"], True),
        (["frequencies", 0, "count"], 2),
        (["frequencies", 1, "raw"], 0),
    ],
)
def test_nested_host_tamper(path, value):
    candidate = make()
    report = json.loads(inspect(candidate))
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(m.ElfMetadataError):
        m.validate_report(report, candidate[0])


@pytest.mark.parametrize(
    "path",
    [
        [],
        ["tables"],
        ["tables", "dynsym"],
        ["counts"],
        ["definitions", 0],
        ["needs", 0],
        ["needs", 0, "versions", 0],
        ["frequencies", 0],
    ],
)
@pytest.mark.parametrize("mode", ["extra", "missing"])
def test_exact_nested_keys(path, mode):
    candidate = make()
    report = json.loads(inspect(candidate))
    target = report
    for key in path:
        target = target[key]
    if mode == "extra":
        target["extra"] = False
    else:
        del target[next(iter(target))]
    with pytest.raises(m.ElfMetadataError):
        m.validate_report(report, candidate[0])


def test_graph_cycle_and_missing_reference():
    budget = m._Budget()
    with pytest.raises(m.ElfMetadataError, match="graph_cycle"):
        m._graph(
            [{"name": "a", "dependency_names": ["b"]}, {"name": "b", "dependency_names": ["a"]}],
            budget,
        )
    with pytest.raises(m.ElfMetadataError, match="reference"):
        m._graph([{"name": "a", "dependency_names": ["missing"]}], m._Budget())


@pytest.mark.parametrize("kind", list(m.LIMITS))
def test_budget_precharge(kind):
    budget = m._Budget()
    budget.charge(kind, m.LIMITS[kind])
    with pytest.raises(m.ElfMetadataError, match=kind):
        budget.charge(kind)
    assert budget.used[kind] == m.LIMITS[kind]


def test_canonical_overflow_and_exact_boundary():
    assert len(m.canonical("a" * 65533)) == 65535
    with pytest.raises(m.ElfMetadataError, match="output"):
        m.canonical("a" * 65534)


def test_host_entry_refuses_before_optional_import():
    with pytest.raises(m.ElfMetadataError, match="guest_required"):
        m.inspect_elf_metadata(make()[0])


def test_input_identity():
    candidate = make()
    report = json.loads(inspect(candidate))
    changed = candidate[0][:-1] + b"\x01"
    assert hashlib.sha256(changed).hexdigest() != report["source_sha256"]
    with pytest.raises(m.ElfMetadataError, match="binding"):
        m.validate_report(copy.deepcopy(report), changed)


@pytest.mark.parametrize("kind", list(m.LIMITS))
def test_each_real_path_charges_budget_before_work(monkeypatch, kind):
    monkeypatch.setitem(m.LIMITS, kind, 0)
    with pytest.raises(m.ElfMetadataError, match=kind):
        inspect(make())


def test_record_not_decoded_outside_parent_span():
    candidate = make()
    offset = candidate[1]["sections"][".gnu.version_d"]
    candidate[3]["Elf_Verdef", offset]["vd_aux"] = 32
    with pytest.raises(m.ElfMetadataError, match="extent"):
        inspect(candidate)
    assert ("Elf_Verdaux", offset + 32) not in candidate[5]


def test_string_termination_and_search_accounting():
    raw = b"x" * 5000
    budget = m._Budget()
    reader = m._Reader(raw, None, None, {"offset": 0, "bytes": len(raw)}, budget)
    with pytest.raises(m.ElfMetadataError, match="string"):
        reader.name(0)
    assert budget.used["string_work"] == 4096
    raw = b"a" * 255 + b"\0"
    reader = m._Reader(raw, None, None, {"offset": 0, "bytes": len(raw)}, budget)
    assert reader.name(0) == "a" * 255
    assert reader.name(0) == "a" * 255
    assert budget.used["string_output"] == 510


def test_wrong_struct_width_before_decode():
    candidate = make()
    candidate[2].structs.Elf_Verdef.sizeof = lambda: 19
    with pytest.raises(m.ElfMetadataError, match="width"):
        inspect(candidate)
    assert not any(kind == "Elf_Verdef" for kind, _ in candidate[5])


def test_duplicate_need_provider_is_not_collapsed():
    candidate = make()
    report = json.loads(inspect(candidate))
    report["needs"].append(copy.deepcopy(report["needs"][0]))
    with pytest.raises(m.ElfMetadataError, match="provider"):
        m.validate_report(report, candidate[0])


def test_duplicate_requirement_name_is_not_collapsed():
    candidate = make()
    report = json.loads(inspect(candidate))
    need = report["needs"][0]
    extra = dict(need["versions"][0], index=5)
    need["versions"].append(extra)
    need["aux_declared"] = need["aux_decoded"] = 2
    with pytest.raises(m.ElfMetadataError, match="duplicate"):
        m.validate_report(report, candidate[0])


def test_duplicate_dependency_is_not_collapsed():
    candidate = make()
    report = json.loads(inspect(candidate))
    report["definitions"][2]["dependency_names"] *= 2
    with pytest.raises(m.ElfMetadataError, match="duplicate"):
        m.validate_report(report, candidate[0])


@pytest.mark.parametrize(
    "role,size",
    [
        ("dynsym", 24 * 32769),
        ("versym", 65538),
        ("verdef", 65537),
        ("verneed", 65537),
    ],
)
def test_table_bounds_precede_any_content_access(monkeypatch, role, size):
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    kinds = {
        "dynsym": "SHT_DYNSYM",
        "versym": "SHT_GNU_versym",
        "verdef": "SHT_GNU_verdef",
        "verneed": "SHT_GNU_verneed",
    }
    selected = next(s for s in bound["sections"] if s["sh_type"] == kinds[role])
    selected["sh_size"] = size
    bound["programs"][0]["p_filesz"] = 1048576

    class NoContentAccess:
        def __len__(self):
            return 1048576

        def __getitem__(self, item):
            pytest.fail("content accessed before table limit")

    monkeypatch.setattr(m.hashlib, "sha256", lambda *args: pytest.fail("hash before table limit"))
    with pytest.raises(m.ElfMetadataError, match="count|extent"):
        m._topology(NoContentAccess(), bound, m._Budget())


@pytest.mark.parametrize("has_def,has_need", [(False, False), (True, False), (False, True)])
def test_supported_partial_version_tables(has_def, has_need):
    candidate = make()
    raw, _, elf, records, decode, _ = candidate
    bound = json.loads(loader._inspect(raw, elf, decode))
    remove_types, remove_tags = set(), set()
    if not has_def:
        remove_types.add("SHT_GNU_verdef")
        remove_tags.update(("DT_VERDEF", "DT_VERDEFNUM"))
    if not has_need:
        remove_types.add("SHT_GNU_verneed")
        remove_tags.update(("DT_VERNEED", "DT_VERNEEDNUM"))
    for section in bound["sections"]:
        if section["sh_type"] in remove_types:
            section["sh_type"] = "SHT_PROGBITS"
    bound["dynamic"] = [d for d in bound["dynamic"] if d["tag"] not in remove_tags]
    for (kind, _), record in records.items():
        if kind == "Elf_Versym":
            record["ndx"] = "VER_NDX_GLOBAL"
    output = json.loads(m._inspect(raw, bound, elf.structs, decode))
    assert output["versioned"] is True
    assert bool(output["definitions"]) is has_def
    assert bool(output["needs"]) is has_need
    assert output["frequencies"] == [{"raw": 1, "index": 1, "hidden": False, "count": 5}]
    if not has_def:
        assert output["tables"]["verdef"] is None
        assert output["counts"]["definitions_declared"] == 0
    if not has_need:
        assert output["tables"]["verneed"] is None
        assert output["counts"]["needs_declared"] == 0


@pytest.mark.parametrize("remove", ["DT_VERDEF", "DT_VERDEFNUM", "DT_VERNEED", "DT_VERNEEDNUM"])
def test_partial_table_absence_requires_both_tags_absent(remove):
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    kind = "SHT_GNU_verdef" if remove.startswith("DT_VERDEF") else "SHT_GNU_verneed"
    for section in bound["sections"]:
        if section["sh_type"] == kind:
            section["sh_type"] = "SHT_PROGBITS"
    bound["dynamic"] = [d for d in bound["dynamic"] if d["tag"] != remove]
    with pytest.raises(m.ElfMetadataError, match="companion"):
        m._inspect(raw, bound, elf.structs, decode)


def test_version_parents_without_versym_refused():
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    for section in bound["sections"]:
        if section["sh_type"] == "SHT_GNU_versym":
            section["sh_type"] = "SHT_PROGBITS"
    bound["dynamic"] = [d for d in bound["dynamic"] if d["tag"] != "DT_VERSYM"]
    with pytest.raises(m.ElfMetadataError, match="companion"):
        m._inspect(raw, bound, elf.structs, decode)


@pytest.mark.parametrize("step", [0, 4, 17, 20])
def test_nonfinal_vernaux_next_before_second_decode(step):
    candidate = make()
    offset = candidate[1]["sections"][".gnu.version_r"]
    candidate[3]["Elf_Verneed", offset]["vn_cnt"] = 2
    candidate[3]["Elf_Vernaux", offset + 16]["vna_next"] = step
    with pytest.raises(m.ElfMetadataError, match="chain|extent"):
        inspect(candidate)
    assert ("Elf_Vernaux", offset + 16 + step) not in candidate[5][
        candidate[5].index(("Elf_Vernaux", offset + 16)) + 1 :
    ]


def test_unknown_flags_with_valid_base_are_independently_refused():
    candidate = make()
    candidate[3]["Elf_Verdef", candidate[1]["sections"][".gnu.version_d"]]["vd_flags"] = 5
    with pytest.raises(m.ElfMetadataError, match="flags"):
        inspect(candidate)


@pytest.mark.parametrize(
    "role,field,value",
    [
        ("SHT_DYNSYM", "sh_link", 0),
        ("SHT_DYNSYM", "sh_entsize", 16),
        ("SHT_DYNSYM", "sh_size", 121),
        ("SHT_DYNSYM", "sh_info", 6),
        ("SHT_GNU_verdef", "sh_link", 2),
        ("SHT_GNU_verneed", "sh_link", 2),
        ("SHT_GNU_verdef", "sh_entsize", 20),
        ("SHT_GNU_verneed", "sh_entsize", 16),
        ("SHT_GNU_verdef", "sh_info", 2),
        ("SHT_GNU_verneed", "sh_info", 0),
        ("SHT_GNU_verdef", "sh_addr", 0),
        ("SHT_GNU_verneed", "sh_addr", 0),
    ],
)
def test_all_role_links_widths_counts_maps(role, field, value):
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    next(s for s in bound["sections"] if s["sh_type"] == role)[field] = value
    with pytest.raises(m.ElfMetadataError):
        m._inspect(raw, bound, elf.structs, decode)


@pytest.mark.parametrize(
    "role", ["SHT_DYNSYM", "SHT_GNU_versym", "SHT_GNU_verdef", "SHT_GNU_verneed"]
)
def test_duplicate_role_table(role):
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    bound["sections"].append(
        copy.deepcopy(next(s for s in bound["sections"] if s["sh_type"] == role))
    )
    with pytest.raises(m.ElfMetadataError, match="table"):
        m._inspect(raw, bound, elf.structs, decode)


def test_ambiguous_full_range_load_mapping():
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    bound["programs"].append(copy.deepcopy(bound["programs"][0]))
    with pytest.raises(m.ElfMetadataError, match="mapping"):
        m._inspect(raw, bound, elf.structs, decode)


def test_overlap_refused_before_hash(monkeypatch):
    raw, _, elf, _, decode, _ = make()
    bound = json.loads(loader._inspect(raw, elf, decode))
    definition = next(s for s in bound["sections"] if s["sh_type"] == "SHT_GNU_verdef")
    symbols = next(s for s in bound["sections"] if s["sh_type"] == "SHT_DYNSYM")
    definition["sh_offset"] = symbols["sh_offset"]
    definition["sh_addr"] = symbols["sh_addr"]
    next(d for d in bound["dynamic"] if d["tag"] == "DT_VERDEF")["value"] = symbols["sh_addr"]
    monkeypatch.setattr(m.hashlib, "sha256", lambda *args: pytest.fail("hash before overlap"))
    with pytest.raises(m.ElfMetadataError, match="overlap"):
        m._inspect(raw, bound, elf.structs, decode)


def test_full_report_multinode_inheritance_cycle():
    candidate = make()
    report = json.loads(inspect(candidate))
    report["definitions"][1]["dependency_names"] = ["FIXTURE_2.0"]
    report["definitions"][1]["aux_declared"] = report["definitions"][1]["aux_decoded"] = 2
    with pytest.raises(m.ElfMetadataError, match="graph_cycle"):
        m.validate_report(report, candidate[0])
