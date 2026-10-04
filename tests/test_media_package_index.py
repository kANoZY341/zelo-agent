"""Synthetic metadata only; no real indexes, decompression, network or processes."""

import copy
import hashlib
import itertools

import pytest

from qualification import media_package_index as m


def stanza(package="libc6", version="2.39-0ubuntu8.9", *, architecture="amd64", source=None):
    lines = [
        f"Package: {package}",
        f"Version: {version}",
        f"Architecture: {architecture}",
        f"Filename: pool/main/g/glibc/{package}_fixed_amd64.deb",
        "Size: 1234",
        "SHA256: " + "a" * 64,
        "Description: synthetic metadata",
        " continuation, never instructions",
    ]
    if source is not None:
        lines.append("Source: " + source)
    return ("\n".join(lines) + "\n\n").encode()


def parsed(raw=None, suite="noble", *, width=65536):
    raw = stanza() if raw is None else raw
    return m.parse_index((raw[i : i + width] for i in range(0, len(raw), width)), suite)


def all_indexes():
    raw = b"".join(stanza(name, version) for name, version in m.TARGETS)
    return [parsed(raw, suite) for suite in m.SUITES]


def test_literal_targets_and_policy_bounds():
    assert m.TARGETS == (
        ("python3.12-minimal", "3.12.3-1ubuntu0.17"),
        ("libpython3.12-minimal", "3.12.3-1ubuntu0.17"),
        ("libpython3.12-stdlib", "3.12.3-1ubuntu0.17"),
        ("libc6", "2.39-0ubuntu8.9"),
        ("libffi8", "3.4.6-1build1"),
        ("libseccomp2", "2.5.5-1ubuntu3.1"),
        ("zlib1g", "1:1.3.dfsg-3.1ubuntu2.2"),
        ("libexpat1", "2.6.1-2ubuntu0.6"),
    )
    assert (m.INDEX_BYTES, m.CHUNK_BYTES, m.STANZA_BYTES, m.STANZAS, m.FIELDS) == (
        134217728,
        65536,
        65536,
        100000,
        256,
    )
    assert m.PACKAGE_BYTES == 33554432


def test_exact_selected_projection_and_content_identity():
    raw = stanza(source="glibc")
    result = parsed(raw)
    assert result == {
        "schema": "zelo-media-fixed-package-index-metadata-v1",
        "suite": "noble",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "packages": [
            {
                "package": "libc6",
                "version": "2.39-0ubuntu8.9",
                "architecture": "amd64",
                "filename": "pool/main/g/glibc/libc6_fixed_amd64.deb",
                "bytes": 1234,
                "sha256": "a" * 64,
                "source": {"name": "glibc", "version": "2.39-0ubuntu8.9"},
            }
        ],
        "metadata_only": True,
        "package_member_origin_verified": False,
        "runtime_authority": False,
    }
    assert b"Description" not in repr(result).encode()


@pytest.mark.parametrize("width", [1, 2, 3, 7, 31, 65536])
def test_chunk_partition_and_utf8_boundaries_do_not_change_result(width):
    raw = stanza().replace(b"synthetic metadata", "Café — 中文".encode())
    assert parsed(raw, width=width) == parsed(raw)


def test_every_single_split_has_same_result():
    raw = stanza().replace(b"synthetic metadata", "Café".encode())
    expected = parsed(raw)
    for point in range(1, len(raw)):
        assert m.parse_index([raw[:point], raw[point:]], "noble") == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_critical_field_order_and_ascii_trimming(reverse):
    lines = stanza().splitlines()[:6]
    if reverse:
        lines.reverse()
    raw = b"\n".join(line.replace(b": ", b":\t ").rstrip() + b"\t " for line in lines) + b"\n"
    result = parsed(raw)["packages"]
    assert result == parsed()["packages"]


@pytest.mark.parametrize(
    "source,expected",
    [
        (None, ("libc6", "2.39-0ubuntu8.9")),
        ("libc6", ("libc6", "2.39-0ubuntu8.9")),
        ("libc6 (2.39-0ubuntu8.9)", ("libc6", "2.39-0ubuntu8.9")),
        ("glibc", ("glibc", "2.39-0ubuntu8.9")),
        ("glibc (1:2.0-1)", ("glibc", "1:2.0-1")),
    ],
)
def test_source_normalization_is_explicit(source, expected):
    assert parsed(stanza(source=source))["packages"][0]["source"] == dict(
        zip(("name", "version"), expected, strict=True)
    )


@pytest.mark.parametrize(
    "source",
    [
        "x",
        "Bad",
        "glibc  (1.0)",
        "glibc\t(1.0)",
        "glibc ()",
        "glibc (v1)",
        "glibc (1.0) extra",
        "glibc/other",
        "a" * 129,
        "glibc (" + "1" * 129 + ")",
    ],
)
def test_malformed_selected_source_refuses(source):
    with pytest.raises(m.PackageIndexError, match="source"):
        parsed(stanza(source=source))


@pytest.mark.parametrize(
    "architecture,version",
    [("all", "2.39-0ubuntu8.9"), ("arm64", "2.39-0ubuntu8.9"), ("amd64", "2.39-0ubuntu8.8")],
)
def test_other_architectures_and_versions_do_not_satisfy_selection(architecture, version):
    assert parsed(stanza(version=version, architecture=architecture))["packages"] == []


def test_zlib_epoch_is_literal_not_version_ordering():
    assert parsed(stanza("zlib1g", "1:1.3.dfsg-3.1ubuntu2.2"))["packages"][0]["version"].startswith(
        "1:"
    )
    assert parsed(stanza("zlib1g", "1.3.dfsg-3.1ubuntu2.2"))["packages"] == []


@pytest.mark.parametrize(
    "field", ["Package", "Version", "Architecture", "Filename", "Size", "SHA256", "Source"]
)
def test_critical_fields_cannot_fold_or_be_empty(field):
    raw = stanza(source="glibc")
    lines = raw.splitlines()
    line = next(line for line in lines if line.startswith(field.encode() + b":"))
    for replacement, code in (
        (line + b"\n continuation", "simple_continuation"),
        (field.encode() + b": \t", "empty_simple"),
    ):
        with pytest.raises(m.PackageIndexError, match=code):
            parsed(raw.replace(line, replacement))


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "empty"),
        (b"\n\n", "empty"),
        (b" \n", "whitespace_line"),
        (b"\t\n", "whitespace_line"),
        (b" continuation\n", "orphan_continuation"),
        (b"# comment\n", "field"),
        (b"-Bad: value\n", "field"),
        (b"Bad Field: value\n", "field"),
        (b"Package : libc6\n", "field"),
        (b"NoColon\n", "field"),
        (b": value\n", "field"),
        (b"x" * 65 + b": value\n", "field"),
        (b"Package: libc6\r\n", "control"),
        (b"Description: \xff\n", "utf8"),
        ("Description: \u0085\n".encode(), "control"),
        (b"Description: \x7f\n", "control"),
        (b"Description: \x00\n", "control"),
    ],
)
def test_invalid_framing_and_control_data_refuse(raw, code):
    with pytest.raises(m.PackageIndexError, match=code):
        parsed(raw)


@pytest.mark.parametrize("tail", [b"bad", b"Package: libc6\n", b"\xff\n", b"bad\n"])
def test_invalid_trailing_data_prevents_any_partial_result(tail):
    with pytest.raises(m.PackageIndexError):
        parsed(stanza() + tail)


def test_final_lf_required_but_extra_stanza_separator_optional():
    assert parsed(stanza()[:-1])["packages"] == parsed()["packages"]
    with pytest.raises(m.PackageIndexError, match="final_lf"):
        parsed(stanza()[:-2])
    assert parsed(b"\n\n" + stanza() + b"\n\n")["packages"] == parsed()["packages"]


@pytest.mark.parametrize("key", [b"Package", b"pAcKaGe", b"Description", b"dEsCrIpTiOn"])
def test_duplicate_fields_refuse_even_when_unselected(key):
    raw = stanza("unselected", "1.0")
    extra = key + b": same\n"
    with pytest.raises(m.PackageIndexError, match="duplicate_field"):
        parsed(raw[:-1] + extra + b"\n")


def test_unused_simple_values_are_bounded_text_without_numeric_or_path_authority():
    raw = stanza("unselected", "1.0").replace(b"Size: 1234", b"Size: not a number")
    raw = raw.replace(b"pool/main/g/glibc/unselected_fixed_amd64.deb", b"../../unused")
    raw = raw.replace(b"a" * 64, b"not a hash")
    assert parsed(raw)["packages"] == []


@pytest.mark.parametrize(
    "old,new,code",
    [
        (b"Package: libc6", b"Package: Aaa", "package"),
        (b"Package: libc6", b"Package: x", "package"),
        (b"Version: 2.39-0ubuntu8.9", b"Version: v1", "version"),
        (b"Architecture: amd64", b"Architecture: AMD64", "architecture"),
        (b"Size: 1234", b"Size: 0", "size"),
        (b"Size: 1234", b"Size: 01234", "size"),
        (b"Size: 1234", b"Size: +1234", "size"),
        (b"Size: 1234", b"Size: -1234", "size"),
        (b"Size: 1234", b"Size: 33554433", "size"),
        (b"Size: 1234", "Size: １２３４".encode(), "size"),
        (b"a" * 64, b"A" * 64, "sha256"),
        (b"a" * 64, b"a" * 63, "sha256"),
    ],
)
def test_selected_lexical_and_resource_refusals(old, new, code):
    with pytest.raises(m.PackageIndexError, match=code):
        parsed(stanza().replace(old, new))


def test_selected_size_exact_ceiling_accepts():
    assert (
        parsed(stanza().replace(b"Size: 1234", b"Size: 33554432"))["packages"][0]["bytes"]
        == 33554432
    )


@pytest.mark.parametrize(
    "path",
    [
        "/pool/main/x.deb",
        "https://user@host/x.deb",
        "pool/main/../x.deb",
        "pool/main/./x.deb",
        "pool/main//x.deb",
        "pool/main/%2e%2e/x.deb",
        "pool/main/x.deb?x",
        "pool/main/x.deb#x",
        "pool/main\\x.deb",
        "pool/universe/x.deb",
        "pool/main/x.tar",
        "pool/main/x:1.deb",
        "pool/main/" + "/".join(["x"] * 6) + "/x.deb",
        "pool/main/" + "x" * 125 + ".deb",
        "pool/main/" + "/".join(["x" * 128] * 4) + ".deb",
    ],
)
def test_selected_paths_never_gain_uri_or_traversal_semantics(path):
    raw = stanza().replace(b"pool/main/g/glibc/libc6_fixed_amd64.deb", path.encode())
    with pytest.raises(m.PackageIndexError, match="filename"):
        parsed(raw)


def test_literal_at_in_selected_relative_filename_has_no_uri_meaning():
    raw = stanza().replace(b"libc6_fixed_amd64.deb", b"libc6@literal.deb")
    assert parsed(raw)["packages"][0]["filename"] == "pool/main/g/glibc/libc6@literal.deb"


@pytest.mark.parametrize(
    "missing", ["Package", "Version", "Architecture", "Filename", "Size", "SHA256"]
)
def test_required_selected_fields_cannot_be_missing(missing):
    raw = (
        b"\n".join(
            line for line in stanza().splitlines() if not line.startswith(missing.encode() + b":")
        )
        + b"\n"
    )
    with pytest.raises(m.PackageIndexError):
        parsed(raw)


def test_per_index_selected_duplicate_refuses_identical_or_conflicting_metadata():
    for other in (stanza(), stanza().replace(b"Size: 1234", b"Size: 1235")):
        with pytest.raises(m.PackageIndexError, match="duplicate_package"):
            parsed(stanza() + other)


def padded_stanza(size):
    base = stanza()[:-1]
    prefix = b"Extra: "
    padding = size - len(base) - len(prefix) - 1
    assert padding >= 0
    return base + prefix + b"x" * padding + b"\n"


def test_real_stanza_boundary_and_terminating_separator_accounting():
    raw = padded_stanza(65536)
    assert len(raw) == 65536
    assert parsed(raw)["packages"]
    for bad in (raw + b"\n", padded_stanza(65537)):
        with pytest.raises(m.PackageIndexError, match="stanza_bytes"):
            parsed(bad)
    assert parsed(padded_stanza(65535) + b"\n")["packages"]


def test_partial_stanza_precharge_refuses_before_append():
    parser = m._Parser()
    base = stanza()[:-1]
    parser.feed(base)
    available = 65536 - len(base)
    parser.feed(b"X" * available)
    before = bytes(parser.partial)
    with pytest.raises(m.PackageIndexError, match="stanza_bytes"):
        parser.feed(b"Y")
    assert bytes(parser.partial) == before and parser.stanza_bytes == 65536


def test_exact_field_count_and_plus_one():
    base = b"Package: unselected\nVersion: 1\nArchitecture: all\n"
    raw = base + b"".join(f"X-{i}: value\n".encode() for i in range(253)) + b"\n"
    assert parsed(raw)["packages"] == []
    with pytest.raises(m.PackageIndexError, match="fields"):
        parsed(raw[:-1] + b"Last: value\n\n")


def test_cumulative_budget_precharge_at_literal_limit():
    # Isolate the final-byte budget transition without processing 128MiB of fixtures.
    parser = m._Parser()
    parser.feed(stanza())
    parser.total = 134217727
    parser.feed(b"\n")
    assert parser.total == 134217728
    digest = parser.hasher.hexdigest()
    with pytest.raises(m.PackageIndexError, match="index_bytes"):
        parser.feed(b"\n")
    assert parser.total == 134217728 and parser.hasher.hexdigest() == digest


def test_stanza_count_precharge_at_literal_limit():
    parser = m._Parser()
    parser.stanzas = 99999
    parser.feed(stanza("unselected", "1"))
    assert parser.stanzas == 100000
    with pytest.raises(m.PackageIndexError, match="stanzas"):
        parser.feed(b"P")
    assert not parser.partial and parser.stanzas == 100000


@pytest.mark.parametrize("chunk", [b"", b"x" * 65537, "text", bytearray(b"x"), None, True])
def test_chunk_type_size_and_no_progress_refuse(chunk):
    with pytest.raises(m.PackageIndexError, match="chunk"):
        m.parse_index([chunk], "noble")


def test_unknown_suite_refuses_before_trusted_iterator_is_touched():
    def chunks():
        pytest.fail("must reject suite before iterator")
        yield b"x"

    with pytest.raises(m.PackageIndexError, match="suite"):
        m.parse_index(chunks(), "noble-backports")


def test_iterator_failure_produces_no_result():
    def chunks():
        yield stanza()
        raise RuntimeError("synthetic iterator failure")

    with pytest.raises(RuntimeError, match="synthetic iterator"):
        m.parse_index(chunks(), "noble")


def test_full_merge_coalesces_only_normalized_metadata_and_preserves_fixed_order():
    results = all_indexes()
    for order in itertools.permutations(results):
        merged = m.merge_fixed_indexes(order)
        assert [item["package"] for item in merged["packages"]] == [name for name, _ in m.TARGETS]
        assert all(item["suites"] == list(m.SUITES) for item in merged["packages"])
        assert [item["suite"] for item in merged["indexes"]] == list(m.SUITES)
        assert merged["metadata_only"] is True
        assert merged["package_member_origin_verified"] is False
        assert merged["runtime_authority"] is False


def test_source_equivalence_and_ignored_descriptions_do_not_create_conflicts():
    results = all_indexes()
    raw = b"".join(
        stanza(name, version, source=f"{name} ({version})") for name, version in m.TARGETS
    )
    raw = raw.replace(b"synthetic metadata", b"changed description")
    results[1] = parsed(raw, "noble-updates")
    assert len(m.merge_fixed_indexes(results)["packages"]) == 8


@pytest.mark.parametrize(
    "field,new",
    [
        ("filename", "pool/main/other.deb"),
        ("bytes", 1235),
        ("sha256", "b" * 64),
        ("source", {"name": "other", "version": "1.0"}),
    ],
)
def test_cross_pocket_declared_metadata_conflict_refuses(field, new):
    results = all_indexes()
    results[1]["packages"][0][field] = new
    with pytest.raises(m.PackageIndexError, match="conflict"):
        m.merge_fixed_indexes(results)


def test_merge_requires_all_eight_after_all_three_inputs():
    results = all_indexes()
    for result in results:
        result["packages"].pop()
    with pytest.raises(m.PackageIndexError, match="missing_packages"):
        m.merge_fixed_indexes(results)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.pop(),
        lambda r: r.append(r[0]),
        lambda r: r.__setitem__(1, copy.deepcopy(r[0])),
        lambda r: r[0].update(suite="noble-backports"),
        lambda r: r[0].update(extra="field"),
        lambda r: r[0].pop("schema"),
        lambda r: r[0].update(schema="other"),
        lambda r: r[0].update(bytes=True),
        lambda r: r[0].update(bytes=0),
        lambda r: r[0].update(bytes=134217729),
        lambda r: r[0].update(sha256="a" * 63),
        lambda r: r[0].update(metadata_only=1),
        lambda r: r[0].update(package_member_origin_verified=True),
        lambda r: r[0].update(runtime_authority=True),
        lambda r: r[0].update(packages=tuple(r[0]["packages"])),
        lambda r: r[0]["packages"].append(r[0]["packages"][0]),
        lambda r: r[0]["packages"].__setitem__(1, copy.deepcopy(r[0]["packages"][0])),
        lambda r: r[0]["packages"][0].update(extra=1),
        lambda r: r[0]["packages"][0].update(package="unselected"),
        lambda r: r[0]["packages"][0].update(version="9.9"),
        lambda r: r[0]["packages"][0].update(architecture="all"),
        lambda r: r[0]["packages"][0].update(bytes=True),
        lambda r: r[0]["packages"][0].update(bytes=33554433),
        lambda r: r[0]["packages"][0].update(filename="../escape.deb"),
        lambda r: r[0]["packages"][0].update(sha256="A" * 64),
        lambda r: r[0]["packages"][0].update(source={"name": "glibc"}),
        lambda r: r[0]["packages"][0].update(source={"name": "Bad", "version": "1"}),
        lambda r: r[0]["packages"][0].update(source={"name": "glibc", "version": "v1"}),
    ],
)
def test_forged_merge_structures_never_gain_authority(mutation):
    results = all_indexes()
    mutation(results)
    with pytest.raises(m.PackageIndexError):
        m.merge_fixed_indexes(results)


@pytest.mark.parametrize("value", [None, {}, iter([]), "text", True])
def test_merge_rejects_other_container_types(value):
    with pytest.raises(m.PackageIndexError, match="index_set"):
        m.merge_fixed_indexes(value)


@pytest.mark.parametrize("kind,maximum", [("package", 128), ("version", 128), ("architecture", 32)])
def test_unselected_identity_lexical_length_boundaries(kind, maximum):
    kwargs = dict(package="unselected", version="1", architecture="all")
    kwargs[kind] = ("1" if kind == "version" else "a") + "a" * (maximum - 1)
    assert parsed(stanza(**kwargs))["packages"] == []
    kwargs[kind] += "a"
    with pytest.raises(m.PackageIndexError, match=kind):
        parsed(stanza(**kwargs))


def test_source_exact_name_and_version_lengths_accept():
    source = "a" * 128 + " (" + "1" + "x" * 127 + ")"
    assert len(source) == 259
    row = parsed(stanza(source=source))["packages"][0]
    assert len(row["source"]["name"]) == 128 and len(row["source"]["version"]) == 128


@pytest.mark.parametrize(
    "path",
    [
        "pool/main/" + "/".join(["x"] * 5 + ["x.deb"]),
        "pool/main/" + "x" * 124 + ".deb",
        "pool/main/" + "/".join(["x" * 128] * 3 + ["y" * 111 + ".deb"]),
    ],
)
def test_selected_path_exact_depth_component_and_total_limits_accept(path):
    assert len(path) <= 512
    raw = stanza().replace(b"pool/main/g/glibc/libc6_fixed_amd64.deb", path.encode())
    assert parsed(raw)["packages"][0]["filename"] == path


@pytest.mark.parametrize(
    "field,value",
    [
        ("Package", "\u00a0libc6"),
        ("Version", "2.39-0ubuntu8.9\u00a0"),
        ("Architecture", "\u00a0amd64"),
        ("Size", "\u00a01234"),
    ],
)
def test_unicode_whitespace_is_not_normalized_into_identity(field, value):
    lines = stanza().splitlines()
    old = next(line for line in lines if line.startswith(field.encode() + b":"))
    with pytest.raises(m.PackageIndexError):
        parsed(stanza().replace(old, (field + ": " + value).encode()))
