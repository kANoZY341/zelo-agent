"""Hand-built synthetic USTAR streams only; no acquired packages or native tools."""

import ast
import hashlib
import io
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from qualification import media_member_contract as contract
from qualification import media_tar_inventory as m


def binding():
    return contract.ExpectedBinding("a" * 64, 123, "b" * 64, "c" * 64, "d" * 32)


def checksum(block):
    raw = bytearray(block)
    raw[148:156] = b" " * 8
    raw[148:156] = f"{sum(raw):06o}".encode() + b"\0 "
    return bytes(raw)


def header(
    name="file",
    *,
    kind=b"0",
    size=0,
    link="",
    prefix="",
    mode=0o644,
    uid=0,
    gid=0,
    magic=b"ustar\x0000",
):
    raw = bytearray(512)
    for start, end, text in ((0, 100, name), (157, 257, link), (345, 500, prefix)):
        encoded = text.encode("utf-8")
        assert len(encoded) <= end - start
        raw[start : start + len(encoded)] = encoded
    for start, end, number in (
        (100, 108, mode),
        (108, 116, uid),
        (116, 124, gid),
        (124, 136, size),
        (136, 148, 0),
        (329, 337, 0),
        (337, 345, 0),
    ):
        value = f"{number:0{end - start - 1}o}".encode() + b"\0"
        assert len(value) == end - start
        raw[start:end] = value
    raw[156:157] = kind
    raw[257:265] = magic
    return checksum(raw)


def file_record(name="file", data=b"abc", **kwargs):
    return header(name, size=len(data), **kwargs) + data + b"\0" * (-len(data) % 512)


def archive(*records, padding=0):
    return b"".join(records or [file_record()]) + b"\0" * (1024 + padding)


class Source(io.BytesIO):
    def __init__(self, raw, chunk=None):
        super().__init__(raw)
        self.chunk = chunk
        self.requests = []

    def read(self, count):
        assert 0 < count <= m.READ_BYTES
        self.requests.append(count)
        return super().read(min(count, self.chunk) if self.chunk else count)


def parse(raw=None, **kwargs):
    return m.scan_tar(Source(archive() if raw is None else raw), binding(), **kwargs)


def test_real_stdlib_headers_hash_payload_and_complete_stream():
    data = archive(
        header("./", kind=b"5"),
        header("usr/", kind=b"5"),
        file_record("usr/file", b"hello"),
        header("link", kind=b"2", link="/elsewhere"),
        header("hard", kind=b"1", link="usr/file"),
    )
    source = Source(data)
    result = m.scan_tar(source, binding())
    value = json.loads(result.manifest_raw)
    assert result.expanded_bytes == len(data)
    assert result.tar_sha256 == hashlib.sha256(data).hexdigest()
    assert result.member_count == 5 and result.runtime_authority is False
    assert value["members"][2]["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert value["members"][3]["link_target"] == "/elsewhere"
    assert "cleanup_confirmed" not in value and "support_release_confirmed" not in value
    assert not source.closed
    # The test supplies explicitly synthetic owner observations; parser never does.
    observed = contract.OwnerOutcome(0, True, True, True, False, False, False, False, len(data))
    checked = contract.validate_manifest(result.manifest_raw, binding(), observed)
    assert checked.members[2].canonical_name == "usr/file"
    with pytest.raises(FrozenInstanceError):
        result.member_count = 0


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 127, 511, 512, 513, 1024, 65536])
def test_short_reads_are_complete_without_seeking(chunk):
    data = archive()
    source = Source(data, chunk)
    result = m.scan_tar(source, binding())
    assert result.tar_sha256 == hashlib.sha256(data).hexdigest()
    assert result.expanded_bytes == len(data) and max(source.requests) <= 65536


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 65535, 65536, 65537, 131073])
def test_payload_sizes_stream_with_exact_digests(size):
    data = b"x" * size
    result = parse(archive(file_record(data=data)))
    assert (
        json.loads(result.manifest_raw)["members"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    )


@pytest.mark.parametrize("padding", [0, 512, 1024, 8192, 65536])
def test_zero_record_padding_is_hashed(padding):
    data = archive(padding=padding)
    result = parse(data)
    assert result.expanded_bytes == len(data)
    assert result.tar_sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "kind", [b"x", b"g", b"X", b"L", b"K", b"S", b"3", b"4", b"6", b"7", b"Z", b"\0"]
)
def test_extensions_and_special_types_refuse_before_decoder_or_payload(kind):
    source = Source(header(kind=kind, size=2**30) + b"PAYLOAD")
    with pytest.raises(m.Refused, match="^tar_type$"):
        m.scan_tar(
            source, binding(), header_decoder=lambda b: pytest.fail("decoded unsupported type")
        )
    assert source.tell() == 512 and source.requests == [512]


@pytest.mark.parametrize("magic", [b"\0" * 8, b"ustar  \0", b"ustar\x0001", b"not-tar!"])
def test_non_ustar_refuses_before_decoder(magic):
    source = Source(header(magic=magic))
    with pytest.raises(m.Refused, match="^tar_format$"):
        m.scan_tar(source, binding(), header_decoder=lambda b: pytest.fail("decoded non-USTAR"))
    assert source.tell() == 512


@pytest.mark.parametrize("cut", [0, 1, 100, 511, 512, 513, 514, 1023, 1024, 1535, 1536, 2047])
def test_truncated_stream_has_no_inventory(cut):
    with pytest.raises(m.Refused, match="^tar_truncated$"):
        parse(archive()[:cut])


def test_two_zero_blocks_required():
    with pytest.raises(m.Refused, match="^tar_terminator$"):
        parse(file_record() + b"\0" * 512 + header("hidden"))


def test_empty_tar_is_not_an_inventory():
    with pytest.raises(m.Refused, match="^tar_empty_inventory$"):
        parse(b"\0" * 1024)


@pytest.mark.parametrize("tail", [b"X", header("hidden"), archive(), b"\0" * 512 + b"X"])
def test_nonzero_trailing_data_refused(tail):
    with pytest.raises(m.Refused, match="^tar_trailing_data$"):
        parse(archive() + tail)


@pytest.mark.parametrize("size", [1, 2, 511, 513])
def test_partial_zero_trailing_block_refused(size):
    with pytest.raises(m.Refused, match="^tar_trailing_alignment$"):
        parse(archive() + b"\0" * size)


def test_file_padding_must_be_zero():
    raw = bytearray(archive())
    raw[515] = 1
    with pytest.raises(m.Refused, match="^tar_payload_padding$"):
        parse(bytes(raw))


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "/etc/passwd",
        "a//b",
        "a/../b",
        "a/./b",
        "././x",
        "C:x",
        "a\\b",
        "bad\nname",
        "bad\tname",
        "x/",
        "",
        "a\x7fb",
    ],
)
def test_unsafe_names_fail_before_payload(name):
    source = Source(header(name, size=3) + b"abc")
    with pytest.raises(m.Refused, match="^member_(name|text)$"):
        m.scan_tar(source, binding())
    assert source.tell() == 512


@pytest.mark.parametrize("name", ["dir//", "dir///", "././dir/"])
def test_directory_normalization_does_not_hide_unsafe_raw_name(name):
    with pytest.raises(m.Refused, match="^member_name$"):
        parse(archive(header(name, kind=b"5")))


@pytest.mark.parametrize("name", ["./file", "file"])
def test_canonical_duplicates_fail(name):
    with pytest.raises(m.Refused, match="^member_duplicate_name$"):
        parse(archive(file_record(), file_record(name)))


@pytest.mark.parametrize("parent_first", [False, True])
@pytest.mark.parametrize("kind", [b"0", b"1", b"2"])
def test_non_directory_ancestors_refuse_in_either_order(parent_first, kind):
    parent = header("parent", kind=kind, link="elsewhere" if kind in (b"1", b"2") else "")
    child = file_record("parent/child")
    with pytest.raises(m.Refused, match="^member_ancestor$"):
        parse(archive(*(parent, child) if parent_first else (child, parent)))


@pytest.mark.parametrize(
    "name,prefix", [("file", "a/b"), ("é.txt", "资料"), ("f" * 100, "p" * 155)]
)
def test_ustar_prefix_and_utf8_names(name, prefix):
    result = parse(archive(header(name, prefix=prefix)))
    assert json.loads(result.manifest_raw)["members"][0]["name"] == prefix + "/" + name


@pytest.mark.parametrize("start,end", m._STRINGS)
def test_data_after_nul_refuses_before_decode(start, end):
    raw = bytearray(header())
    raw[start : start + 3] = b"x\0y"
    with pytest.raises(m.Refused, match="^tar_string_padding$"):
        parse(archive(checksum(raw)))


@pytest.mark.parametrize("start,end", m._NUMBERS)
@pytest.mark.parametrize("first", [b"9", b"-", b"+", b"\x80"])
def test_non_octal_and_base256_fields_refused(start, end, first):
    raw = bytearray(header())
    raw[start : start + 1] = first
    # Do not rewrite checksum when that is the field under test.
    bad = bytes(raw) if start == 148 else checksum(raw)
    with pytest.raises(m.Refused, match="^tar_number_encoding$"):
        parse(archive(bad))


@pytest.mark.parametrize("start,end", m._NUMBERS)
def test_numeric_data_after_nul_refused(start, end):
    raw = bytearray(header())
    raw[start : start + 3] = b"1\x002"
    bad = bytes(raw) if start == 148 else checksum(raw)
    with pytest.raises(m.Refused, match="^tar_number_padding$"):
        parse(archive(bad))


def test_checksum_is_really_verified_by_library():
    raw = bytearray(header())
    raw[0] ^= 1
    with pytest.raises(m.Refused, match="^tar_header_invalid$"):
        parse(archive(bytes(raw)))


@pytest.mark.parametrize("offset", [500, 505, 511])
def test_reserved_tail_refused(offset):
    raw = bytearray(header())
    raw[offset] = 1
    with pytest.raises(m.Refused, match="^tar_reserved$"):
        parse(archive(checksum(raw)))


def test_link_data_is_never_extracted_or_followed(monkeypatch):
    import tarfile

    def forbidden(*a, **k):
        pytest.fail("general archive/extraction path invoked")

    for name in ("open", "extract", "extractall", "extractfile", "getmembers", "next"):
        monkeypatch.setattr(tarfile.TarFile, name, forbidden)
    monkeypatch.setattr(tarfile, "open", forbidden)
    result = parse(archive(header("link", kind=b"2", link="../../outside")))
    assert json.loads(result.manifest_raw)["members"][0]["link_target"] == "../../outside"


@pytest.mark.parametrize("kind", [b"5", b"1", b"2"])
def test_non_regular_payload_size_refused(kind):
    with pytest.raises(m.Refused, match="^member_nonfile$"):
        parse(archive(header(kind=kind, size=1, link="target" if kind != b"5" else "")))


def test_unexpected_link_metadata_refused():
    with pytest.raises(m.Refused, match="^tar_unexpected_link$"):
        parse(archive(header(link="target")))


def test_device_fields_do_not_hide_in_regular_record():
    raw = bytearray(header())
    raw[329:337] = b"0000001\0"
    with pytest.raises(m.Refused, match="^tar_device_metadata$"):
        parse(archive(checksum(raw)))


def test_limits_before_reading_or_materialization(monkeypatch):
    source = Source(archive())
    monkeypatch.setattr(m, "MAX_JSON_BYTES", 128)
    with pytest.raises(m.Refused, match="^tar_json_bound$"):
        m.scan_tar(source, binding())
    assert not source.requests


def test_payload_extent_before_payload_read(monkeypatch):
    monkeypatch.setattr(m, "MAX_STREAM_BYTES", 2048)
    source = Source(header(size=513) + b"not-read")
    with pytest.raises(m.Refused, match="^tar_payload_extent$"):
        m.scan_tar(source, binding())
    assert source.tell() == 512


def test_exact_stream_cap_and_one_byte_overflow(monkeypatch):
    raw = archive()
    monkeypatch.setattr(m, "MAX_STREAM_BYTES", len(raw))
    assert parse(raw).expanded_bytes == len(raw)
    with pytest.raises(m.Refused, match="^tar_stream_bound$"):
        parse(raw + b"\0")


def test_read_call_budget(monkeypatch):
    monkeypatch.setattr(m, "MAX_READ_CALLS", 2)
    source = Source(archive(), chunk=1)
    with pytest.raises(m.Refused, match="^tar_read_calls$"):
        m.scan_tar(source, binding())
    assert source.requests == [512, 511]


def test_member_budget_before_decoder(monkeypatch):
    monkeypatch.setattr(m, "MAX_MEMBERS", 1)
    seen = []

    def decoder(block):
        seen.append(1)
        return m.decode_header(block)

    with pytest.raises(m.Refused, match="^tar_member_count$"):
        parse(archive(file_record("one"), file_record("two")), header_decoder=decoder)
    assert seen == [1]


@pytest.mark.parametrize("raw", [None, "bytes", bytearray(b"x"), memoryview(b"x"), b"x" * 513])
def test_invalid_stream_return_sanitized(raw):
    class Bad:
        def read(self, count):
            return raw

    with pytest.raises(m.Refused, match="^tar_read_result$"):
        m.scan_tar(Bad(), binding())


@pytest.mark.parametrize("error", [OSError, ValueError, RuntimeError])
def test_stream_exception_has_no_peer_diagnostic(error):
    class Bad:
        def read(self, count):
            raise error("PEER SECRET")

    with pytest.raises(m.Refused) as caught:
        m.scan_tar(Bad(), binding())
    assert str(caught.value) == "tar_read" and caught.value.__cause__ is None


def test_interrupt_is_not_converted_to_a_result():
    class Bad:
        def read(self, count):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        m.scan_tar(Bad(), binding())


@pytest.mark.parametrize("value", [True, 1, None])
def test_cancellation_before_first_read(value):
    source = Source(archive())
    with pytest.raises(m.Refused, match="^tar_cancel"):
        m.scan_tar(source, binding(), cancelled=lambda: value)
    assert not source.requests


def test_cancellation_after_read_invalidates_result():
    source = Source(archive())
    with pytest.raises(m.Refused, match="^tar_cancelled$"):
        m.scan_tar(source, binding(), cancelled=lambda: source.tell() >= 512)
    assert source.tell() == 512


def test_cooperative_deadline_after_read():
    source = Source(archive())
    with pytest.raises(m.Refused, match="^tar_deadline$"):
        m.scan_tar(source, binding(), clock=lambda: 61.0 if source.tell() else 0.0)
    assert source.tell() == 512


@pytest.mark.parametrize(
    "clock", [lambda: float("nan"), lambda: float("inf"), lambda: True, lambda: "0"]
)
def test_invalid_clock_refused(clock):
    with pytest.raises(m.Refused, match="^tar_clock$"):
        parse(clock=clock)


def test_invalid_binding_precedes_all_reads():
    source = Source(archive())
    with pytest.raises(m.Refused, match="^member_expected_binding$"):
        m.scan_tar(source, replace(binding(), archive_bytes=True))
    assert not source.requests


@pytest.mark.parametrize("value", [None, {}, (), "header"])
def test_decoder_result_must_have_exact_type(value):
    with pytest.raises(m.Refused, match="^tar_decoder_result$"):
        parse(header_decoder=lambda block: value)


def test_decoder_failure_sanitized():
    def bad(block):
        raise ValueError("PEER SECRET")

    with pytest.raises(m.Refused, match="^tar_header_invalid$"):
        parse(header_decoder=bad)


def test_decoder_kind_cannot_override_wire_type():
    def wrong(block):
        return replace(m.decode_header(block), kind="directory", size=0)

    with pytest.raises(m.Refused, match="^tar_decoder_kind$"):
        parse(header_decoder=wrong)


@pytest.mark.parametrize(
    "field,value",
    [("size", True), ("size", -1), ("mode", 4096), ("uid", -1), ("gid", 2**32), ("name", "../x")],
)
def test_decoded_metadata_is_checked_before_payload(field, value):
    source = Source(archive())

    def bad(block):
        return replace(m.decode_header(block), **{field: value})

    with pytest.raises(m.Refused, match="^member_"):
        m.scan_tar(source, binding(), header_decoder=bad)
    assert source.tell() == 512


def test_no_general_parser_or_system_actions_in_adapter():
    tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
    blocked = {
        "extract",
        "extractall",
        "extractfile",
        "fromtarfile",
        "_fromtarfile",
        "_proc_pax",
        "_proc_gnulong",
        "_proc_sparse",
        "TarFile",
        "getmembers",
        "seek",
        "open",
        "system",
        "Popen",
        "chmod",
        "chown",
        "symlink",
        "mkdir",
        "exec",
        "eval",
        "compile",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            assert name not in blocked
    assert not any(
        isinstance(n, ast.Import) and any(a.name == "tarfile" for a in n.names) for n in tree.body
    )


def test_signed_legacy_checksum_is_not_accepted_as_posix():
    raw = bytearray(header("é.txt"))
    raw[148:156] = b" " * 8
    signed = sum(value if value < 128 else value - 256 for value in raw)
    raw[148:156] = f"{signed:06o}".encode() + b"\0 "
    with pytest.raises(m.Refused, match="^tar_checksum_unsigned$"):
        parse(archive(bytes(raw)))


def test_root_only_directory_is_valid():
    result = parse(archive(header("./", kind=b"5")))
    assert result.member_count == 1 and result.expanded_bytes == 1536


def test_root_directory_must_be_first():
    with pytest.raises(m.Refused, match="^member_root$"):
        parse(archive(file_record(), header("./", kind=b"5")))


@pytest.mark.parametrize("prefix", ["..", "/absolute", "a//b", "a/../b"])
def test_bad_prefix_cannot_hide_unsafe_path(prefix):
    with pytest.raises(m.Refused, match="^member_name$"):
        parse(archive(header("file", prefix=prefix)))


def test_json_reservation_precedes_payload_read(monkeypatch):
    source = Source(archive())
    wanted = contract._binding(binding())
    row = m._row(m.decode_header(header(size=3)), 0)
    minimum = len(m._prefix(wanted, m.MAX_STREAM_BYTES)) + 2 + len(m._json(row))
    monkeypatch.setattr(m, "MAX_JSON_BYTES", minimum - 1)
    with pytest.raises(m.Refused, match="^tar_json_bound$"):
        m.scan_tar(source, binding())
    assert source.tell() == 512


def test_json_exact_reservation_is_sufficient(monkeypatch):
    row = m._row(m.decode_header(header(size=3)), 0)
    minimum = (
        len(m._prefix(contract._binding(binding()), m.MAX_STREAM_BYTES)) + 2 + len(m._json(row))
    )
    monkeypatch.setattr(m, "MAX_JSON_BYTES", minimum)
    result = parse()
    assert len(result.manifest_raw) <= minimum


def test_cancellation_at_eof_does_not_return_partial_success():
    class AtEOF(Source):
        eof_seen = False

        def read(self, count):
            raw = super().read(count)
            if not raw:
                self.eof_seen = True
            return raw

    source = AtEOF(archive())
    with pytest.raises(m.Refused, match="^tar_cancelled$"):
        m.scan_tar(source, binding(), cancelled=lambda: source.eof_seen)
    assert source.eof_seen


@pytest.mark.parametrize("which", ["clock", "cancelled"])
def test_failing_control_callback_is_sanitized(which):
    def bad():
        raise OSError("PRIVATE DETAIL")

    code = "tar_clock" if which == "clock" else "tar_cancel_check"
    with pytest.raises(m.Refused) as caught:
        parse(**{which: bad})
    assert str(caught.value) == code


@pytest.mark.parametrize("raw", [b"", b"x" * 511, b"x" * 513, bytearray(512), None])
def test_fixed_header_decoder_refuses_wrong_width_or_type(raw):
    with pytest.raises(m.Refused, match="^tar_header_width$"):
        m.decode_header(raw)


def test_actual_header_decoder_cannot_call_filesystem(monkeypatch):
    import builtins
    import os
    import tarfile  # Trusted stdlib initialized before installing test-only guards.

    assert tarfile.TarInfo

    def denied(*a, **k):
        pytest.fail("filesystem action in fixed header decoder")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", denied)
        for name in ("open", "readlink", "symlink", "mkdir", "chmod", "chown"):
            patch.setattr(os, name, denied)
        result = parse(archive(header("link", kind=b"2", link="/etc/passwd")))
        assert result.member_count == 1
