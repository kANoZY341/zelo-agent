"""Synthetic metadata only: no package/archive/native parser is executed."""

import ast
import hashlib
import json
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest

from qualification import media_member_contract as m

EMPTY = hashlib.sha256(b"").hexdigest()
DATA = hashlib.sha256(b"abc").hexdigest()


def binding(index=0):
    return m.ExpectedBinding(f"{index + 1:064x}", 16, "a" * 64, "b" * 64, "c" * 32)


def member(name="usr/example", kind="regular", size=3, **changes):
    row = {
        "ordinal": 0,
        "name": name,
        "kind": kind,
        "mode": 420,
        "uid": 0,
        "gid": 0,
        "size": size if kind == "regular" else 0,
        "sha256": (EMPTY if size == 0 else DATA) if kind == "regular" else None,
        "link_target": "../target" if kind in ("symlink", "hardlink") else None,
    }
    row.update(changes)
    return row


def document(rows=None, expected=None, expanded=None):
    rows = [member()] if rows is None else rows
    expected = binding() if expected is None else expected
    rows = [dict(row, ordinal=i) for i, row in enumerate(rows)]
    if expanded is None:
        expanded = 1024 + sum(512 + ((row["size"] + 511) // 512) * 512 for row in rows)
    return {
        "schema": m.SCHEMA,
        "binding": asdict(expected),
        "expanded_bytes": expanded,
        "members": rows,
    }


def encode(doc):
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def outcome(expanded=2048, **changes):
    values = dict(
        exit_code=0,
        eof=True,
        parser_complete=True,
        cleanup_confirmed=True,
        cancelled=False,
        timed_out=False,
        stdout_saturated=False,
        stderr_saturated=False,
        expanded_bytes=expanded,
    )
    values.update(changes)
    return m.OwnerOutcome(**values)


def validate(doc):
    return m.validate_manifest(encode(doc), binding(), outcome(doc["expanded_bytes"]))


def test_valid_metadata_retains_identity_without_authority():
    doc = document([member("./usr/", "directory"), member("./usr/example")])
    result = validate(doc)
    assert result.binding == binding()
    assert result.members[0].name == "./usr/"
    assert result.members[0].canonical_name == "usr"
    assert result.members[1].canonical_name == "usr/example"
    assert result.raw_sha256 == hashlib.sha256(encode(doc)).hexdigest()
    assert result.runtime_authority is False
    assert isinstance(result.members, tuple)
    with pytest.raises(FrozenInstanceError):
        result.runtime_authority = True
    with pytest.raises(FrozenInstanceError):
        result.members[0].name = "changed"
    doc["members"][0]["name"] = "mutated"
    assert result.members[0].name == "./usr/"


@pytest.mark.parametrize(
    "field,value",
    [
        ("archive_sha256", "d" * 64),
        ("archive_bytes", 17),
        ("source_proof_sha256", "d" * 64),
        ("reader_policy_sha256", "d" * 64),
        ("run_id", "d" * 32),
        ("action", "ctrl-tar"),
        ("archive_bytes", True),
    ],
)
def test_exact_binding_refuses_replay_or_substitution(field, value):
    doc = document()
    doc["binding"][field] = value
    with pytest.raises(m.Refused, match="^member_binding$"):
        validate(doc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("archive_sha256", "A" * 64),
        ("archive_sha256", []),
        ("archive_sha256", "a" * 63),
        ("archive_bytes", True),
        ("archive_bytes", 0),
        ("archive_bytes", m.MAX_ARCHIVE_BYTES + 1),
        ("source_proof_sha256", "g" * 64),
        ("reader_policy_sha256", None),
        ("run_id", "a" * 31),
        ("run_id", 3),
        ("action", "extract"),
    ],
)
def test_bad_caller_binding_is_rejected_before_decode(monkeypatch, field, value):
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("decoded invalid identity"))
    with pytest.raises(m.Refused, match="^member_expected_binding$"):
        m.validate_manifest(b"{}", replace(binding(), **{field: value}), outcome())


@pytest.mark.parametrize(
    "field,value",
    [
        ("cancelled", True),
        ("timed_out", True),
        ("stdout_saturated", True),
        ("stderr_saturated", True),
        ("cleanup_confirmed", False),
        ("parser_complete", False),
        ("eof", False),
        ("exit_code", None),
        ("exit_code", 1),
        ("exit_code", -13),
        ("exit_code", False),
        ("eof", 1),
        ("cancelled", 0),
        ("cleanup_confirmed", "true"),
    ],
)
def test_poisoned_owner_cannot_be_overridden_by_exit_zero(monkeypatch, field, value):
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("parsed after owner failure"))
    with pytest.raises(m.Refused, match="^member_owner_outcome$"):
        m.validate_manifest(encode(document()), binding(), outcome(**{field: value}))


@pytest.mark.parametrize("value", [True, 0, 1023, 1537, m.MAX_EXPANDED_BYTES + 512])
def test_invalid_owner_extent_precedes_json(monkeypatch, value):
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("parsed invalid extent"))
    with pytest.raises(m.Refused, match="^member_owner_extent$"):
        m.validate_manifest(b"{}", binding(), outcome(value))


@pytest.mark.parametrize("raw", [b"", "{}", bytearray(b"{}"), memoryview(b"{}"), None])
def test_raw_input_requires_exact_bounded_bytes(raw):
    with pytest.raises(m.Refused, match="^member_json_bound$"):
        m.validate_manifest(raw, binding(), outcome())


def test_oversize_is_refused_before_json_and_hashing(monkeypatch):
    monkeypatch.setattr(m, "MAX_JSON_BYTES", 2)
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("decoded oversize"))
    monkeypatch.setattr(m.hashlib, "sha256", lambda raw: pytest.fail("hashed oversize"))
    with pytest.raises(m.Refused, match="^member_json_bound$"):
        m.validate_manifest(b"123", binding(), outcome())


def test_exact_json_byte_ceiling_is_accepted():
    raw = encode(document())
    raw += b" " * (m.MAX_JSON_BYTES - len(raw))
    assert len(raw) == m.MAX_JSON_BYTES
    assert m.validate_manifest(raw, binding(), outcome()).runtime_authority is False


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"{", "member_json_invalid"),
        (b"\xff", "member_json_invalid"),
        (b"{} {}", "member_json_invalid"),
        (b"[]", "member_manifest_shape"),
        (b"NaN", "member_json_constant"),
        (b"Infinity", "member_json_constant"),
        (b"-Infinity", "member_json_constant"),
        (b'{"a":1,"a":2}', "member_json_duplicate"),
        pytest.param(
            b"[" * 64 + b"0" + b"]" * 64, "member_manifest_shape", id="nested-nonmanifest"
        ),
    ],
)
def test_malformed_json_has_only_fixed_diagnostic(raw, code):
    with pytest.raises(m.Refused) as found:
        m.validate_manifest(raw, binding(), outcome())
    assert str(found.value) == code


@pytest.mark.parametrize(
    "where,code",
    [
        ("schema", "member_json_duplicate"),
        ("run_id", "member_json_duplicate"),
        ("name", "member_json_object"),
    ],
)
def test_duplicate_keys_at_every_schema_level(where, code):
    raw = encode(document())
    token = ('"' + where + '":').encode()
    raw = raw.replace(token, token + b'"DUPLICATE",' + token, 1)
    with pytest.raises(m.Refused, match="^" + code + "$"):
        m.validate_manifest(raw, binding(), outcome())


@pytest.mark.parametrize(
    "target,key,code",
    [
        ("top", "members", "member_manifest_shape"),
        ("binding", "run_id", "member_binding"),
        ("row", "name", "member_record_shape"),
    ],
)
@pytest.mark.parametrize("operation", ["missing", "extra"])
def test_strict_shapes(target, key, code, operation):
    doc = document()
    obj = doc if target == "top" else doc["binding"] if target == "binding" else doc["members"][0]
    if operation == "missing":
        obj.pop(key)
    else:
        obj["untrusted_extra"] = "not authority"
    with pytest.raises(m.Refused) as found:
        validate(doc)
    assert str(found.value) == (
        "member_json_object" if target == "row" and operation == "extra" else code
    )


@pytest.mark.parametrize(
    "name",
    [
        "/absolute",
        "../escape",
        "a/../b",
        "./../b",
        "a//b",
        "a/./b",
        "././a",
        "a/",
        "C:/file",
        "a\\b",
        "./",
        ".",
        "a/" * 64 + "b",
    ],
)
def test_bad_member_names(name):
    with pytest.raises(m.Refused, match="^member_(name|root)$"):
        validate(document([member(name)]))


@pytest.mark.parametrize(
    "name",
    ["", "a\x00b", "a\nb", "a\rb", "a\tb", "a\x7fb", "\ud800", "a\u202eb", "a" * 1025, "é" * 513],
)
def test_bad_or_oversize_text(name):
    with pytest.raises(m.Refused, match="^member_text$"):
        validate(document([member(name)]))


@pytest.mark.parametrize("name", ["a" * 1024, "é" * 512, "/".join(["a"] * 64), "Name", "name"])
def test_name_exact_limits_and_case_are_preserved(name):
    assert validate(document([member(name)])).members[0].canonical_name == name


@pytest.mark.parametrize("name", [".", "./"])
def test_optional_root_directory_at_first_ordinal_only(name):
    result = validate(document([member(name, "directory"), member("a", size=0)]))
    assert result.members[0].canonical_name == "."
    with pytest.raises(m.Refused, match="^member_root$"):
        validate(document([member("a", size=0), member(name, "directory")]))


@pytest.mark.parametrize(
    "left,right,kind", [("a", "./a", "regular"), ("./a/", "a", "directory"), ("a", "a", "symlink")]
)
def test_normalized_duplicate_names_are_refused(left, right, kind):
    with pytest.raises(m.Refused, match="^member_duplicate_name$"):
        validate(document([member(left, kind), member(right, kind)]))


@pytest.mark.parametrize("kind", ["regular", "symlink", "hardlink"])
@pytest.mark.parametrize("parent_first", [False, True])
def test_non_directory_ancestors_are_rejected_in_both_orders(kind, parent_first):
    parent, child = member("usr", kind), member("usr/lib/example")
    rows = [parent, child] if parent_first else [child, parent]
    with pytest.raises(m.Refused, match="^member_ancestor$"):
        validate(document(rows))


@pytest.mark.parametrize("parent_first", [False, True])
def test_directory_ancestors_allow_both_orders(parent_first):
    parent, child = member("usr", "directory"), member("usr/lib/example")
    rows = [parent, child] if parent_first else [child, parent]
    assert len(validate(document(rows)).members) == 2


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("target", ["../lib/example", "/usr/lib/example", "literal target"])
def test_links_remain_inert_unresolved_metadata(kind, target):
    result = validate(document([member("link", kind, link_target=target)]))
    assert result.members[0].link_target == target
    assert result.members[0].sha256 is None
    assert result.members[0].size == 0
    assert result.runtime_authority is False


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("ordinal", True, "member_ordinal"),
        ("ordinal", 1, "member_ordinal"),
        ("kind", "device", "member_kind"),
        ("kind", "fifo", "member_kind"),
        ("kind", "sparse", "member_kind"),
        ("kind", "pax", "member_kind"),
        ("kind", [], "member_kind"),
        ("mode", True, "member_mode"),
        ("mode", -1, "member_mode"),
        ("mode", 4096, "member_mode"),
        ("uid", -1, "member_owner_id"),
        ("gid", 2**32, "member_owner_id"),
        ("uid", False, "member_owner_id"),
        ("size", -1, "member_size"),
        ("size", True, "member_size"),
        ("size", 3.0, "member_size"),
        ("size", m.MAX_EXPANDED_BYTES + 1, "member_size"),
        ("sha256", "A" * 64, "member_digest"),
        ("sha256", None, "member_digest"),
        ("sha256", "a" * 65, "member_digest"),
        ("link_target", "other", "member_regular"),
    ],
)
def test_field_types_and_bounds(field, value, code):
    doc = document()
    doc["members"][0][field] = value
    with pytest.raises(m.Refused, match="^" + code + "$"):
        validate(doc)


@pytest.mark.parametrize(
    "kind,changes,code",
    [
        ("regular", {"size": 0, "sha256": DATA}, "member_regular"),
        ("directory", {"size": 1}, "member_nonfile"),
        ("symlink", {"sha256": EMPTY}, "member_nonfile"),
        ("directory", {"link_target": "a"}, "member_directory"),
        ("symlink", {"link_target": None}, "member_text"),
        ("hardlink", {"link_target": "a\n"}, "member_text"),
    ],
)
def test_kind_specific_invariants(kind, changes, code):
    doc = document([member("a", kind)])
    doc["members"][0].update(changes)
    with pytest.raises(m.Refused, match="^" + code + "$"):
        validate(doc)


def test_empty_file_and_inert_permission_boundaries():
    row = member("a", size=0, mode=4095, uid=2**32 - 1, gid=2**32 - 1)
    result = validate(document([row]))
    assert result.members[0].sha256 == EMPTY
    assert result.members[0].mode == 4095  # Recorded, never applied to a filesystem.


@pytest.mark.parametrize("value", [True, "2048", 2048.0, 1536, m.MAX_EXPANDED_BYTES + 512])
def test_manifest_extent_must_match_independent_outcome(value):
    doc = document()
    doc["expanded_bytes"] = value
    with pytest.raises(m.Refused, match="^member_extent$"):
        m.validate_manifest(encode(doc), binding(), outcome())


def test_impossible_payload_extent_is_rejected():
    doc = document()
    doc["members"][0]["size"] = 1024
    with pytest.raises(m.Refused, match="^member_extent$"):
        validate(doc)


@pytest.mark.parametrize("rows", [[], {}, None, "members"])
def test_member_collection_shape(rows):
    doc = document()
    doc["members"] = rows
    with pytest.raises(m.Refused, match="^member_count$"):
        validate(doc)


def test_exact_member_limit_and_one_above():
    rows = [member(f"f{i}", size=0) for i in range(m.MAX_MEMBERS)]
    assert len(validate(document(rows)).members) == m.MAX_MEMBERS
    rows.append(member("over", size=0))
    with pytest.raises(m.Refused, match="^member_count$"):
        validate(document(rows))


def batch(member_count=1, expanded=None):
    expected = tuple(binding(i) for i in range(8))
    docs = tuple(
        document([member(f"f{n}", size=0) for n in range(member_count)], b, expanded)
        for b in expected
    )
    return tuple(map(encode, docs)), expected, tuple(outcome(d["expanded_bytes"]) for d in docs)


def test_fixed_batch_accepts_consistent_metadata_only():
    result = m.validate_batch(*batch())
    assert type(result) is tuple and len(result) == 8
    assert all(x.runtime_authority is False for x in result)


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("operation", ["short", "long", "list"])
def test_batch_requires_exact_tuple_shape(position, operation):
    args = list(batch())
    original = args[position]
    args[position] = (
        original[:-1]
        if operation == "short"
        else original + original[:1]
        if operation == "long"
        else list(original)
    )
    with pytest.raises(m.Refused, match="^member_batch_shape$"):
        m.validate_batch(*args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "d" * 32),
        ("source_proof_sha256", "d" * 64),
        ("reader_policy_sha256", "d" * 64),
        ("archive_sha256", "0" * 63 + "1"),
    ],
)
def test_batch_identity_mismatch_precedes_decoding(monkeypatch, field, value):
    raws, expected, outcomes = batch()
    expected = expected[:-1] + (replace(expected[-1], **{field: value}),)
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("decoded mismatched batch"))
    with pytest.raises(m.Refused, match="^member_batch_binding$"):
        m.validate_batch(raws, expected, outcomes)


def test_last_failed_owner_prevents_parsing_any_batch_item(monkeypatch):
    raws, expected, outcomes = batch()
    outcomes = outcomes[:-1] + (replace(outcomes[-1], cancelled=True),)
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("partial batch parsed"))
    with pytest.raises(m.Refused, match="^member_owner_outcome$"):
        m.validate_batch(raws, expected, outcomes)


def test_batch_raw_budget_precedes_parsing(monkeypatch):
    args = batch()
    monkeypatch.setattr(m, "MAX_BATCH_JSON_BYTES", sum(map(len, args[0])) - 1)
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("decoded over raw budget"))
    with pytest.raises(m.Refused, match="^member_batch_budget$"):
        m.validate_batch(*args)


def test_batch_expanded_limit_exact_and_over(monkeypatch):
    args = batch(expanded=m.MAX_BATCH_EXPANDED_BYTES // 8)
    assert len(m.validate_batch(*args)) == 8
    outcomes = args[2][:-1] + (
        replace(args[2][-1], expanded_bytes=args[2][-1].expanded_bytes + 512),
    )
    monkeypatch.setattr(m, "_decode", lambda raw: pytest.fail("decoded over expanded budget"))
    with pytest.raises(m.Refused, match="^member_batch_budget$"):
        m.validate_batch(args[0], args[1], outcomes)


def test_batch_member_limit_exact_and_over():
    args = batch(member_count=m.MAX_BATCH_MEMBERS // 8)
    assert sum(len(x.members) for x in m.validate_batch(*args)) == m.MAX_BATCH_MEMBERS
    doc = document(
        [member(f"f{n}", size=0) for n in range(m.MAX_BATCH_MEMBERS // 8 + 1)], args[1][-1]
    )
    raws = args[0][:-1] + (encode(doc),)
    outcomes = args[2][:-1] + (outcome(doc["expanded_bytes"]),)
    with pytest.raises(m.Refused, match="^member_batch_budget$"):
        m.validate_batch(raws, args[1], outcomes)


def test_no_archive_or_io_imports_and_no_execution_entrypoint():
    tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports == {"dataclasses", "hashlib", "json"}
    assert not hasattr(m, "main")
    assert not hasattr(m, "extract")


def test_decoder_recursion_failure_is_sanitized(monkeypatch):
    def exhausted(*args, **kwargs):
        raise RecursionError("UNTRUSTED decoder diagnostic")

    monkeypatch.setattr(m.json, "loads", exhausted)
    with pytest.raises(m.Refused) as found:
        m.validate_manifest(encode(document()), binding(), outcome())
    assert str(found.value) == "member_json_invalid"


@pytest.mark.parametrize("value", [None, True, 3, [], {}, "other-schema"])
def test_exact_schema_identity(value):
    doc = document()
    doc["schema"] = value
    with pytest.raises(m.Refused, match="^member_schema$"):
        validate(doc)


@pytest.mark.parametrize("value", [None, [], "binding", True])
def test_binding_shape_has_no_coercion(value):
    doc = document()
    doc["binding"] = value
    with pytest.raises(m.Refused, match="^member_binding$"):
        validate(doc)


def test_child_cannot_supply_owner_cleanup_or_success():
    doc = document()
    doc["cleanup_confirmed"] = True
    with pytest.raises(m.Refused, match="^member_manifest_shape$"):
        validate(doc)


def test_names_differing_only_in_case_remain_distinct():
    result = validate(document([member("A"), member("a")]))
    assert [x.canonical_name for x in result.members] == ["A", "a"]
