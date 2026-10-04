"""Fixed reader bootstrap lock and declarations; no native/ELF parsing or authority."""

import hashlib
import json
import re

LOCK_SHA = "753df1ca08e5be5b29ae8b73d220bf6bfea052b8bed9ffdc0f64ac12091a8e4f"
MANIFEST_SHA = "7c794478d55e159d784086932d64ea0adfc56946cc754e666b14a8e8485da3d4"
ROOT = "runtime/media-package-reader-bootstrap-20261004"
TOTAL_BYTES = 3718288
NAMES = (
    "dpkg-deb",
    "libc.so.6",
    "ld-linux-x86-64.so.2",
    "libz.so.1",
    "libbz2.so.1.0",
    "liblzma.so.5",
    "libzstd.so.1",
    "libmd.so.0",
)
INTERPRETER = "/lib64/ld-linux-x86-64.so.2"
FORBIDDEN_TAGS = frozenset(
    ("DT_RPATH", "DT_RUNPATH", "DT_TEXTREL", "DT_AUDIT", "DT_DEPAUDIT", "DT_FILTER", "DT_AUXILIARY")
)
_FALSE = (
    "runtime_authority",
    "platform_complete",
    "abi_qualified",
    "symbol_content_verified",
    "version_hash_verified",
)


def _require(condition, code):
    if not condition:
        raise ValueError("reader_elf_" + code)


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _name(value):
    _require(
        type(value) is str and re.fullmatch(r"[A-Za-z0-9_+.-]{1,255}", value) is not None, "name"
    )
    return value


def _list(value, bound):
    _require(type(value) is list and len(value) <= bound, "list")
    return value


def load_lock(raw):
    _require(
        type(raw) is bytes and len(raw) <= 16384 and hashlib.sha256(raw).hexdigest() == LOCK_SHA,
        "lock_pin",
    )
    value = json.loads(raw)
    _require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "root",
            "bootstrap_manifest_sha256",
            "aggregate_bytes",
            "files",
            "runtime_authority",
            "independent_binary_provenance",
        },
        "lock_shape",
    )
    _require(
        value["schema"] == "zelo-reader-bootstrap-elf-lock-v1"
        and value["root"] == ROOT
        and value["bootstrap_manifest_sha256"] == MANIFEST_SHA
        and type(value["aggregate_bytes"]) is int
        and value["aggregate_bytes"] == TOTAL_BYTES
        and value["runtime_authority"] is False
        and value["independent_binary_provenance"] is False,
        "lock_identity",
    )
    _require(type(value["files"]) is list and len(value["files"]) == 8, "lock_count")
    for index, (row, name) in enumerate(zip(value["files"], NAMES, strict=True)):
        _require(
            type(row) is dict
            and set(row) == {"name", "relative", "bytes", "sha256", "soname", "role"},
            "lock_row",
        )
        _require(
            row["name"] == name
            and row["relative"] == f"{index:02d}.binary.reference"
            and row["soname"] == (None if index == 0 else name)
            and row["role"] == ("reader" if index == 0 else "loader" if index == 2 else "library")
            and type(row["bytes"]) is int
            and 64 <= row["bytes"] <= 4194304
            and _sha(row["sha256"]),
            "lock_row",
        )
    _require(sum(row["bytes"] for row in value["files"]) == TOTAL_BYTES, "lock_bytes")
    return value


def declarations(lock, reports):
    """Summarize already source-bound, loader/version-validated reports.

    The operator MUST first apply the existing _metadata_result(raw_stdout,
    pinned_input_bytes) to every native result. This graph adds cross-file
    declaration consistency, never replacement validation or runtime authority.
    """
    _require(
        type(lock) is dict and type(lock.get("files")) is list and len(lock["files"]) == 8,
        "lock_count",
    )
    _require(type(reports) in (list, tuple) and len(reports) == 8, "report_count")
    providers = {}
    nodes = []
    for expected, report in zip(lock["files"], reports, strict=True):
        _require(
            type(report) is dict and report.get("schema") == "zelo-media-elf-version-graph-v1",
            "report_schema",
        )
        _require(
            report.get("source_sha256") == expected["sha256"]
            and type(report.get("source_bytes")) is int
            and report["source_bytes"] == expected["bytes"],
            "input_binding",
        )
        _require(
            report.get("supported_version_graph_structure_verified") is True
            and report.get("instruction_trust") == "none"
            and all(report.get(key) is False for key in _FALSE),
            "claims",
        )
        loader = report.get("loader_report")
        _require(
            type(loader) is dict and loader.get("schema") == "zelo-media-elf-loader-topology-v1",
            "loader_schema",
        )
        _require(
            loader.get("source_sha256") == expected["sha256"]
            and type(loader.get("source_bytes")) is int
            and loader["source_bytes"] == expected["bytes"],
            "loader_binding",
        )
        _require(
            loader.get("runtime_authority") is False
            and loader.get("abi_qualified") is False
            and loader.get("platform_complete") is False,
            "claims",
        )
        interpreter = loader.get("interpreter")
        _require(interpreter in (None, INTERPRETER), "interpreter")
        if expected["role"] == "reader":
            _require(interpreter == INTERPRETER, "interpreter")
        header = loader.get("header")
        _require(
            type(header) is dict
            and header.get("e_machine") == "EM_X86_64"
            and header.get("e_type") in ("ET_DYN", "ET_EXEC"),
            "architecture",
        )
        needed = []
        sonames = []
        for tag in _list(loader.get("dynamic"), 1024):
            _require(type(tag) is dict and type(tag.get("tag")) is str, "dynamic")
            _require(tag["tag"] not in FORBIDDEN_TAGS, "loader_search")
            if tag["tag"] == "DT_FLAGS":
                _require(type(tag.get("value")) is int and not tag["value"] & 4, "textrel")
            if tag["tag"] == "DT_NEEDED":
                name = _name(tag.get("string"))
                _require(name not in needed and len(needed) < 128, "duplicate_needed")
                needed.append(name)
            elif tag["tag"] == "DT_SONAME":
                sonames.append(_name(tag.get("string")))
        _require(sonames == ([] if expected["soname"] is None else [expected["soname"]]), "soname")
        definitions = []
        for entry in _list(report.get("definitions"), 128):
            _require(type(entry) is dict, "definition")
            name = _name(entry.get("name"))
            _require(name not in definitions, "duplicate_definition")
            definitions.append(name)
        needs = []
        for entry in _list(report.get("needs"), 128):
            _require(type(entry) is dict, "need")
            provider = _name(entry.get("provider"))
            _require(
                provider in needed and provider not in [r["provider"] for r in needs],
                "version_provider",
            )
            versions = []
            for item in _list(entry.get("versions"), 64):
                _require(type(item) is dict, "version")
                name = _name(item.get("name"))
                _require(name not in [r["name"] for r in versions], "duplicate_version")
                _require(
                    type(item.get("flags")) is int and item["flags"] in (0, 2), "version_flags"
                )
                versions.append({"name": name, "weak": bool(item["flags"] & 2)})
            needs.append({"provider": provider, "versions": versions})
        node = {
            "name": expected["name"],
            "role": expected["role"],
            "sha256": expected["sha256"],
            "bytes": expected["bytes"],
            "soname": expected["soname"],
            "interpreter": interpreter,
            "needed": needed,
            "defined_versions": definitions,
            "required_versions": needs,
        }
        if expected["soname"] is not None:
            _require(expected["soname"] not in providers, "duplicate_provider")
            providers[expected["soname"]] = node
        nodes.append(node)
    for node in nodes:
        for name in node["needed"]:
            _require(name in providers, "missing_provider")
        for need in node["required_versions"]:
            available = providers[need["provider"]]["defined_versions"]
            _require(
                all(version["name"] in available for version in need["versions"]), "missing_version"
            )
    result = {
        "schema": "zelo-reader-elf-declarations-v1",
        "nodes": nodes,
        "declared_dependency_graph_consistent": True,
        "runtime_authority": False,
        "abi_qualified": False,
        "platform_complete": False,
        "symbol_resolution_verified": False,
        "independent_binary_provenance": False,
        "actual_dynamic_load_verified": False,
    }
    raw = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _require(len(raw) <= 65536, "output_budget")
    return result
