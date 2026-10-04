"""Fixed bootstrap metadata and fake native orchestration; no target execution."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qualification import media_reader_bootstrap as m
from scripts import qualify_media_reader_elf as q

REAL_ROOT = q.ROOT


def lock():
    return m.load_lock((REAL_ROOT / q.LOCK_PATH).read_bytes())


def reports(value=None):
    value = lock() if value is None else value
    records = []
    for index, row in enumerate(value["files"]):
        dynamic = []
        if row["soname"]:
            dynamic.append({"tag": "DT_SONAME", "string": row["soname"], "value": 1})
        if index == 0:
            dynamic.append({"tag": "DT_NEEDED", "string": "libc.so.6", "value": 2})
        record = {
            "schema": "zelo-media-elf-version-graph-v1",
            "source_bytes": row["bytes"],
            "source_sha256": row["sha256"],
            "supported_version_graph_structure_verified": True,
            "instruction_trust": "none",
            "definitions": [],
            "needs": [],
            **{name: False for name in m._FALSE},
            "loader_report": {
                "schema": "zelo-media-elf-loader-topology-v1",
                "source_bytes": row["bytes"],
                "source_sha256": row["sha256"],
                "runtime_authority": False,
                "abi_qualified": False,
                "platform_complete": False,
                "interpreter": m.INTERPRETER if index == 0 else None,
                "header": {"e_machine": "EM_X86_64", "e_type": "ET_DYN"},
                "dynamic": dynamic,
            },
        }
        records.append(record)
    records[1]["definitions"] = [{"name": "GLIBC_TEST"}]
    records[0]["needs"] = [
        {"provider": "libc.so.6", "versions": [{"name": "GLIBC_TEST", "flags": 0}]}
    ]
    return records


def test_real_lock_is_exact_pinned_eight_inert_copies():
    value = lock()
    assert [row["name"] for row in value["files"]] == list(m.NAMES)
    assert len(value["files"]) == 8 and sum(row["bytes"] for row in value["files"]) == 3718288
    assert value["runtime_authority"] is False and value["independent_binary_provenance"] is False
    assert all(
        row["relative"] == f"{i:02d}.binary.reference" for i, row in enumerate(value["files"])
    )


@pytest.mark.parametrize("raw", [b"", b"{}", b"\xff", b"x" * 16385, "not bytes", None])
def test_corrupt_lock_refused_before_json_parse(monkeypatch, raw):
    monkeypatch.setattr(m.json, "loads", lambda *a, **k: pytest.fail("parsed unauthenticated lock"))
    with pytest.raises(ValueError, match="^reader_elf_lock_pin$"):
        m.load_lock(raw)


@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "root",
        "manifest",
        "authority",
        "provenance",
        "count",
        "aggregate",
        "name",
        "relative",
        "sha",
        "bytes",
        "soname",
        "role",
        "extra",
    ],
)
def test_lock_semantics_even_after_test_only_pin_override(monkeypatch, change):
    value = lock()
    if change == "schema":
        value["schema"] = "other"
    elif change == "root":
        value["root"] = "../elsewhere"
    elif change == "manifest":
        value["bootstrap_manifest_sha256"] = "0" * 64
    elif change == "authority":
        value["runtime_authority"] = True
    elif change == "provenance":
        value["independent_binary_provenance"] = True
    elif change == "count":
        value["files"].pop()
    elif change == "aggregate":
        value["aggregate_bytes"] = True
    elif change == "name":
        value["files"][0]["name"] = "other"
    elif change == "relative":
        value["files"][0]["relative"] = "../../etc/passwd"
    elif change == "sha":
        value["files"][0]["sha256"] = "bad"
    elif change == "bytes":
        value["files"][0]["bytes"] = True
    elif change == "soname":
        value["files"][0]["soname"] = "unexpected"
    elif change == "role":
        value["files"][0]["role"] = "loader"
    else:
        value["files"][0]["extra"] = True
    raw = json.dumps(value).encode()
    monkeypatch.setattr(m, "LOCK_SHA", hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="^reader_elf_lock_"):
        m.load_lock(raw)


def test_declared_graph_consistency_is_not_abi_or_execution():
    value = m.declarations(lock(), reports())
    assert value["declared_dependency_graph_consistent"] is True and len(value["nodes"]) == 8
    for key in (
        "runtime_authority",
        "abi_qualified",
        "platform_complete",
        "symbol_resolution_verified",
        "independent_binary_provenance",
        "actual_dynamic_load_verified",
    ):
        assert value[key] is False
    assert value["nodes"][0]["needed"] == ["libc.so.6"]
    assert value["nodes"][0]["required_versions"][0]["versions"] == [
        {"name": "GLIBC_TEST", "weak": False}
    ]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("schema", "wrong", "report_schema"),
        ("source_bytes", True, "input_binding"),
        ("source_sha256", "0" * 64, "input_binding"),
        ("runtime_authority", True, "claims"),
        ("platform_complete", True, "claims"),
        ("abi_qualified", True, "claims"),
        ("symbol_content_verified", True, "claims"),
        ("version_hash_verified", True, "claims"),
        ("supported_version_graph_structure_verified", False, "claims"),
        ("instruction_trust", "system", "claims"),
    ],
)
def test_report_identity_and_false_claims(field, value, code):
    rows = reports()
    rows[0][field] = value
    with pytest.raises(ValueError, match="^reader_elf_" + code + "$"):
        m.declarations(lock(), rows)


@pytest.mark.parametrize(
    "change,code",
    [
        ("schema", "loader_schema"),
        ("hash", "loader_binding"),
        ("size", "loader_binding"),
        ("authority", "claims"),
        ("interpreter", "interpreter"),
        ("missing_interpreter", "interpreter"),
        ("machine", "architecture"),
        ("type", "architecture"),
    ],
)
def test_loader_identity(change, code):
    rows = reports()
    loader = rows[0]["loader_report"]
    if change == "schema":
        loader["schema"] = "other"
    elif change == "hash":
        loader["source_sha256"] = "0" * 64
    elif change == "size":
        loader["source_bytes"] = True
    elif change == "authority":
        loader["runtime_authority"] = True
    elif change == "interpreter":
        loader["interpreter"] = "/tmp/loader"
    elif change == "missing_interpreter":
        loader["interpreter"] = None
    elif change == "machine":
        loader["header"]["e_machine"] = "EM_ARM"
    else:
        loader["header"]["e_type"] = "ET_REL"
    with pytest.raises(ValueError, match="^reader_elf_" + code + "$"):
        m.declarations(lock(), rows)


@pytest.mark.parametrize("tag", sorted(m.FORBIDDEN_TAGS))
def test_loader_search_and_special_execution_tags_are_refused(tag):
    rows = reports()
    rows[0]["loader_report"]["dynamic"].append({"tag": tag, "value": 1, "string": "elsewhere"})
    with pytest.raises(ValueError, match="^reader_elf_loader_search$"):
        m.declarations(lock(), rows)


def test_textrel_flag_refused():
    rows = reports()
    rows[0]["loader_report"]["dynamic"].append({"tag": "DT_FLAGS", "value": 4})
    with pytest.raises(ValueError, match="^reader_elf_textrel$"):
        m.declarations(lock(), rows)


@pytest.mark.parametrize(
    "name", ["/etc/lib.so", "../lib.so", "$ORIGIN/x", "lib\n.so", "", "x" * 256]
)
def test_needed_name_cannot_be_a_search_path(name):
    rows = reports()
    rows[0]["loader_report"]["dynamic"][-1]["string"] = name
    with pytest.raises(ValueError, match="^reader_elf_name$"):
        m.declarations(lock(), rows)


def test_missing_provider_and_version_are_distinct():
    rows = reports()
    rows[0]["loader_report"]["dynamic"].append(
        {"tag": "DT_NEEDED", "string": "libmissing.so", "value": 3}
    )
    with pytest.raises(ValueError, match="^reader_elf_missing_provider$"):
        m.declarations(lock(), rows)
    rows = reports()
    rows[1]["definitions"] = []
    with pytest.raises(ValueError, match="^reader_elf_missing_version$"):
        m.declarations(lock(), rows)


@pytest.mark.parametrize(
    "kind,code",
    [
        ("needed", "duplicate_needed"),
        ("soname", "soname"),
        ("definitions", "duplicate_definition"),
        ("needs", "version_provider"),
        ("versions", "duplicate_version"),
    ],
)
def test_duplicate_declarations_refused(kind, code):
    rows = reports()
    if kind == "needed":
        rows[0]["loader_report"]["dynamic"] *= 2
    elif kind == "soname":
        rows[1]["loader_report"]["dynamic"] *= 2
    elif kind == "definitions":
        rows[1]["definitions"] *= 2
    elif kind == "needs":
        rows[0]["needs"] *= 2
    else:
        rows[0]["needs"][0]["versions"] *= 2
    with pytest.raises(ValueError, match="^reader_elf_" + code + "$"):
        m.declarations(lock(), rows)


def test_weak_version_flag_is_retained_not_dropped():
    rows = reports()
    rows[0]["needs"][0]["versions"][0]["flags"] = 2
    graph = m.declarations(lock(), rows)
    assert graph["nodes"][0]["required_versions"][0]["versions"][0]["weak"] is True
    rows[1]["definitions"] = []
    with pytest.raises(ValueError, match="^reader_elf_missing_version$"):
        m.declarations(lock(), rows)


@pytest.mark.parametrize("flag", [True, 1, 3, -1])
def test_invalid_version_flags_refused(flag):
    rows = reports()
    rows[0]["needs"][0]["versions"][0]["flags"] = flag
    with pytest.raises(ValueError, match="^reader_elf_version_flags$"):
        m.declarations(lock(), rows)


def test_dependency_cycle_is_inert_not_recursive_discovery():
    rows = reports()
    rows[1]["loader_report"]["dynamic"].append(
        {"tag": "DT_NEEDED", "string": "ld-linux-x86-64.so.2", "value": 3}
    )
    rows[2]["loader_report"]["dynamic"].append(
        {"tag": "DT_NEEDED", "string": "libc.so.6", "value": 3}
    )
    assert m.declarations(lock(), rows)["declared_dependency_graph_consistent"] is True


@pytest.fixture
def operator(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o700)
    value = lock()
    total = 0
    for index, row in enumerate(value["files"]):
        path = root / m.ROOT / row["relative"]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (f"synthetic-{index}:".encode() + b"x" * 128)[:128]
        path.write_bytes(data)
        path.chmod(0o600)
        row["bytes"] = len(data)
        row["sha256"] = hashlib.sha256(data).hexdigest()
        total += len(data)
    manifest = b"{}\n"
    (root / m.ROOT / "manifest.json").write_bytes(manifest)
    (root / m.ROOT / "manifest.json").chmod(0o600)
    value["aggregate_bytes"] = total
    value["bootstrap_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    raw = json.dumps(value).encode()
    path = root / q.LOCK_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(m, "LOCK_SHA", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(m, "MANIFEST_SHA", hashlib.sha256(manifest).hexdigest())
    monkeypatch.setattr(m, "TOTAL_BYTES", total)
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.old, "ROOT", root)
    monkeypatch.setattr(q.old.shared, "ROOT", root)
    state = q.old._State()
    events = []
    monkeypatch.setattr(q.old, "_State", lambda: state)

    class Lease:
        def finish(self, *, cleanup_confirmed):
            events.append(("finish", cleanup_confirmed))

    class Admission:
        def __init__(self, path):
            assert path == root / q.ADMISSION

        def acquire(self, digest):
            events.append(("acquire", digest))
            return Lease()

    monkeypatch.setattr(q, "AdmissionController", Admission)
    fixture = (REAL_ROOT / "qualification/fixtures/media-elf-version-positive.bin").read_bytes()
    unversioned = (
        REAL_ROOT / "qualification/fixtures/media-elf-version-unversioned.bin"
    ).read_bytes()
    layout = json.loads(
        (REAL_ROOT / "qualification/fixtures/media-elf-version-positive.json").read_bytes()
    )["layout"]
    monkeypatch.setattr(q.old, "SOURCE_PINS", ())
    monkeypatch.setattr(
        q, "_setup", lambda *a: (999, root, [], 998, 997, fixture, unversioned, layout)
    )
    monkeypatch.setattr(
        q.old.m, "capture_manifest", lambda *a: SimpleNamespace(digest=q.old.m.TOOL_MANIFEST_DIGEST)
    )
    expected = reports(value)

    async def native(
        state, store, tool_fd, source_fds, bad_source, bad_tool, case, raw, label, deadline
    ):
        assert not any(event[0] == "finish" for event in events)
        state.observations.append(SimpleNamespace(cleanup_confirmed=True))
        events.append(("case", label))
        metadata = expected[int(label.rsplit("-", 1)[1])] if case == "input" else None
        return {
            "case": case,
            "label": label,
            "passed": True,
            "cleanup_confirmed": True,
            "metadata": metadata,
        }

    monkeypatch.setattr(q.old, "_native", native)
    yield SimpleNamespace(root=root, value=value, state=state, events=events, native=native)
    if state.fds:
        state.close_all()


def test_operator_reuses_32_controls_then_eight_copies(operator):
    receipt, _ = asyncio.run(q._qualify())
    assert len([e for e in operator.events if e[0] == "case"]) == 40
    assert [e[1] for e in operator.events if e[0] == "case"][-8:] == [
        f"reader-input-{i:02d}" for i in range(8)
    ]
    assert operator.events[-1] == ("finish", True) and operator.state.fds == []
    assert (
        receipt["cases"] == 40
        and receipt["metadata_inputs"] == 8
        and receipt["runtime_authority"] is False
    )
    proof = Path(receipt["proof"])
    raw = proof.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt["proof_sha256"]
    assert (
        json.loads(raw)["support_release_confirmed"] is False
        and receipt["support_release_confirmed"] is True
    )


def test_new_profile_consumes_same_old_attempt_scope(operator):
    base = operator.root / q.PROOF_BASE
    previous = [q.old._proof_directory(base) for _ in range(2)]
    asyncio.run(q._qualify())
    assert len(list(base.iterdir())) == 3 and all(path.exists() for path in previous)
    q.old._proof_directory(base)
    with pytest.raises(q.old.QualificationRefused, match="attempt_limit"):
        q.old._proof_directory(base)
    assert len(list(base.iterdir())) == 4


def test_changed_copy_stops_before_its_native_case(operator):
    row = operator.value["files"][0]
    (operator.root / m.ROOT / row["relative"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert len([event for event in operator.events if event[0] == "case"]) == 32
    assert operator.events[-1] == ("finish", False)


def test_copy_symlink_refused_without_following(operator):
    path = operator.root / m.ROOT / operator.value["files"][0]["relative"]
    target = path.with_name("target")
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="^reader_elf_copy_path$"):
        asyncio.run(q._qualify())
    assert len([event for event in operator.events if event[0] == "case"]) == 32


def test_copy_drift_after_native_cases_prevents_receipt(operator, monkeypatch):
    async def changing(*args):
        value = await operator.native(*args)
        if args[-2] == "reader-input-07":
            path = operator.root / m.ROOT / operator.value["files"][0]["relative"]
            raw = path.read_bytes()
            path.write_bytes(b"X" + raw[1:])
        return value

    monkeypatch.setattr(q.old, "_native", changing)
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert operator.events[-1] == ("finish", False)


def test_native_refusal_preserved_not_overwritten(operator, monkeypatch):
    async def failing(state, store, *args):
        store.json("cases.json", {"status": "native-fixed-refusal"})
        raise ValueError("refused")

    monkeypatch.setattr(q.old, "_native", failing)
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    proof = next((operator.root / q.PROOF_BASE).glob("*/cases.json"))
    assert json.loads(proof.read_bytes()) == {"status": "native-fixed-refusal"}


def test_unknown_owned_cleanup_keeps_fence(operator, monkeypatch):
    async def unknown(state, *args):
        state.observations.append(SimpleNamespace(cleanup_confirmed=False))
        raise ValueError("unknown ownership")

    monkeypatch.setattr(q.old, "_native", unknown)
    with pytest.raises(ValueError):
        asyncio.run(q._qualify())
    assert operator.events[-1] == ("finish", False) and operator.state.fds


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_release_failure_is_not_retried(operator, monkeypatch, error):
    original = q.old._release
    calls = []

    def failing(state, lease):
        calls.append(1)
        original(state, lease)
        raise error("late close")

    monkeypatch.setattr(q.old, "_release", failing)
    with pytest.raises(error):
        asyncio.run(q._qualify())
    assert calls == [1]


def test_policy_preserves_old_quota_and_reduces_native_calls():
    current = q.policy()
    old = q.old.policy()
    assert (
        current["proof_scope"]
        == old["proof_scope"]
        == "runtime/media-elf-version-proofs/version-proof-*"
    )
    assert current["attempts"] == old["attempts"] == 4
    assert current["processes"] == 40 < old["processes"] == 44
    assert current["source_pins"] == old["source_pins"]
    assert current["guest_bootstrap_sha256"] == old["guest_bootstrap_sha256"]
    assert current["tool_manifest"] == old["tool_manifest"]
    assert (
        current["independent_binary_provenance"] is False
        and current["actual_dynamic_load_verified"] is False
    )


def test_cli_refusal_does_not_leak_exception(monkeypatch, capsys):
    monkeypatch.setattr(q.sys, "argv", ["qualify", "--run"])

    async def bad():
        raise ValueError("PRIVATE")

    monkeypatch.setattr(q, "_qualify", bad)
    assert q.main() == 1
    value = capsys.readouterr()
    assert value.out == "" and value.err == "reader_elf_refused\n"


@pytest.fixture
def preparer(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "runtime").mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q.old, "ROOT", root)
    monkeypatch.setattr(q.old.shared, "ROOT", root)
    state = q.old._State()
    attempt = q.old._proof_directory(root / q.PROOF_BASE)
    store = q.old._Store(attempt, state)
    tool = root / "runtime/media-elf-tool-preparation/fixed/payload"
    tool.mkdir(parents=True, mode=0o500)
    info = tool.stat()
    receipt = {
        "receipt": {
            "relative_payload": "fixed/payload",
            "root_identity": [info.st_dev, info.st_ino],
        }
    }
    raw = json.dumps(receipt).encode()
    path = root / "runtime/media-elf-tool-real-source-copy.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(q.old, "TOOL_RECEIPT_SHA", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(
        q.old.m, "capture_manifest", lambda *a: SimpleNamespace(digest=q.old.m.TOOL_MANIFEST_DIGEST)
    )
    pins = []
    for index in range(5):
        name = f"qualification/fake-source-{index}.py"
        path = root / name
        path.parent.mkdir(exist_ok=True)
        raw = f"# fake source {index}\n".encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        pins.append((name, len(raw), hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(q.old, "SOURCE_PINS", tuple(pins))
    directory = root / "qualification/fixtures"
    directory.mkdir()
    for name in (
        "media-elf-version-positive.bin",
        "media-elf-version-unversioned.bin",
        "media-elf-version-positive.json",
    ):
        path = directory / name
        path.write_bytes((REAL_ROOT / "qualification/fixtures" / name).read_bytes())
        path.chmod(0o600)
    monkeypatch.setattr(q.old, "_native", lambda *a, **k: pytest.fail("native call during setup"))
    yield SimpleNamespace(root=root, state=state, store=store, tool=tool, pins=pins)
    if state.fds:
        state.close_all()


def test_complete_setup_binds_tool_and_five_source_descriptors(preparer):
    tool_fd, tool_path, sources, bad_source, bad_tool, positive, unversioned, layout = q._setup(
        preparer.state, preparer.store
    )
    assert tool_path == preparer.tool and len(sources) == 5
    assert len(positive) == 1456 and len(unversioned) == 984 and "sections" in layout
    assert all(fd in preparer.state.fds for fd in (tool_fd, *sources, bad_source, bad_tool))
    assert preparer.state.observations == []


@pytest.mark.parametrize("index", range(5))
def test_setup_rejects_each_altered_source_before_native(preparer, index):
    path = preparer.root / preparer.pins[index][0]
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    with pytest.raises(ValueError):
        q._setup(preparer.state, preparer.store)
    assert not preparer.state.observations


@pytest.mark.parametrize(
    "name",
    [
        "media-elf-version-positive.bin",
        "media-elf-version-unversioned.bin",
        "media-elf-version-positive.json",
    ],
)
def test_setup_refuses_fixture_or_layout_drift(preparer, name):
    path = preparer.root / "qualification/fixtures" / name
    raw = path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    with pytest.raises(ValueError):
        q._setup(preparer.state, preparer.store)
    assert not preparer.state.observations


def test_setup_authenticates_tool_receipt_before_json(preparer, monkeypatch):
    path = preparer.root / "runtime/media-elf-tool-real-source-copy.json"
    path.write_bytes(b"{}")
    monkeypatch.setattr(
        q.json, "loads", lambda *a, **k: pytest.fail("unauthenticated receipt parsed")
    )
    with pytest.raises(ValueError):
        q._setup(preparer.state, preparer.store)


def test_tool_manifest_drift_refused_before_fixture_use(preparer, monkeypatch):
    monkeypatch.setattr(q.old.m, "capture_manifest", lambda *a: SimpleNamespace(digest="0" * 64))
    with pytest.raises(ValueError, match="^reader_elf_tool_pin$"):
        q._setup(preparer.state, preparer.store)
    assert not preparer.state.observations


def test_report_shapes_and_bounded_collections():
    with pytest.raises(ValueError, match="^reader_elf_report_count$"):
        m.declarations(lock(), reports()[:-1])
    rows = reports()
    rows[0]["loader_report"]["dynamic"] = [{"tag": "DT_NULL"}] * 1025
    with pytest.raises(ValueError, match="^reader_elf_list$"):
        m.declarations(lock(), rows)
    rows = reports()
    rows[0]["needs"] = [{}] * 129
    with pytest.raises(ValueError, match="^reader_elf_list$"):
        m.declarations(lock(), rows)


def test_old_operator_lock_and_parser_sources_are_unchanged():
    old_raw = (
        REAL_ROOT / "qualification/fixtures/media-platform-elf-target-lock.json"
    ).read_bytes()
    assert hashlib.sha256(old_raw).hexdigest() == q.old.TARGET_LOCK_SHA
    assert len(json.loads(old_raw)["files"]) == 12
    for relative, size, digest in q.old.SOURCE_PINS:
        raw = (REAL_ROOT / relative).read_bytes()
        assert len(raw) == size and hashlib.sha256(raw).hexdigest() == digest
