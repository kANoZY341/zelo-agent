"""Offline framing fixtures; no codec, native guest or registered Media runtime."""

import ast
import binascii
import dataclasses
import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from qualification import media_contracts as m


def chunk(kind, data=b""):
    return (
        struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))
    )


def header(width=1, height=1, depth=8, color=6, compression=0, filtering=0, interlace=0):
    return chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, depth, color, compression, filtering, interlace),
    )


def png(width=1, height=1, color=6, idats=(b"unverified-compressed-data",)):
    return (
        m.PNG_SIGNATURE
        + header(width, height, color=color)
        + b"".join(chunk(b"IDAT", d) for d in idats)
        + chunk(b"IEND")
    )


def request(data=None, width=256, height=256):
    return m.ThumbnailRequest(
        m.PngSource(data if data is not None else png(256, 256)), width, height
    )


_DEFAULT_DATA = object()


def candidate(req, data=_DEFAULT_DATA, **changes):
    values = dict(
        request=req,
        data=png() if data is _DEFAULT_DATA else data,
        source_digest=req.source.sha256,
        request_digest=req.digest,
        policy_digest=req.policy_digest,
    )
    values.update(changes)
    return m.CandidateThumbnail(**values)


@pytest.mark.parametrize("color", [2, 6])
@pytest.mark.parametrize("width,height", [(1, 1), (1024, 1), (1, 1024), (1024, 1024)])
def test_input_framing_boundaries(color, width, height):
    data = png(width, height, color)
    source = m.PngSource(data)
    assert source.data is data
    assert source.envelope == m.PngEnvelope(width, height, color, 1)
    assert source.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "width,height", [(0, 1), (1, 0), (1025, 1), (1, 1025), (0xFFFFFFFF, 1), (1, 0xFFFFFFFF)]
)
def test_invalid_input_dimensions(width, height):
    with pytest.raises(m.MediaContractError, match="media_png_header"):
        m.PngSource(png(width, height))


@pytest.mark.parametrize(
    "field,value",
    [
        ("depth", 0),
        ("depth", 1),
        ("depth", 16),
        ("color", 0),
        ("color", 3),
        ("color", 4),
        ("color", 7),
        ("compression", 1),
        ("filtering", 1),
        ("interlace", 1),
    ],
)
def test_unsupported_headers(field, value):
    data = m.PNG_SIGNATURE + header(**{field: value}) + chunk(b"IDAT", b"x") + chunk(b"IEND")
    with pytest.raises(m.MediaContractError, match="media_png_header"):
        m.inspect_png(data)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        b"",
        "png",
        bytearray(png()),
        memoryview(png()),
        type("Bytes", (bytes,), {})(png()),
    ],
)
def test_strict_immutable_bytes(value):
    with pytest.raises(m.MediaContractError, match="media_png_bytes"):
        m.PngSource(value)


def test_exact_byte_limit_and_adjacent_rejection():
    data = png(idats=(b"x" * (m.PNG_BYTE_LIMIT - 57),))
    assert len(data) == m.PNG_BYTE_LIMIT
    source = m.PngSource(data)
    req = m.ThumbnailRequest(source, 1, 1)
    assert candidate(req, data).sha256 == source.sha256
    oversized = png(idats=(b"x" * (m.PNG_BYTE_LIMIT - 56),))
    for factory in (m.PngSource, lambda d: candidate(req, d)):
        with pytest.raises(m.MediaContractError, match="media_png_bytes"):
            factory(oversized)


@pytest.mark.parametrize("count", [1, 32])
def test_idat_count_boundaries_and_empty_chunks(count):
    data = png(idats=(b"",) * (count - 1) + (b"x",))
    assert m.inspect_png(data).idat_chunks == count
    assert candidate(request(), data).envelope.idat_chunks == count


@pytest.mark.parametrize("idats", [(), (b"",), (b"",) * 32, (b"x",) * 33])
def test_idat_missing_empty_aggregate_or_excess(idats):
    with pytest.raises(m.MediaContractError):
        m.inspect_png(png(idats=idats))


@pytest.mark.parametrize(
    "kind",
    [
        b"tEXt",
        b"zTXt",
        b"iTXt",
        b"iCCP",
        b"acTL",
        b"fcTL",
        b"fdAT",
        b"PLTE",
        b"eXIf",
        b"sRGB",
        b"gAMA",
        b"cHRM",
        b"pHYs",
        b"ABCD",
        b"ihdr",
    ],
)
def test_metadata_animation_palette_and_unknown_rejected(kind):
    data = m.PNG_SIGNATURE + header() + chunk(kind, b"x") + chunk(b"IDAT", b"x") + chunk(b"IEND")
    with pytest.raises(m.MediaContractError, match="media_png_forbidden_chunk"):
        m.inspect_png(data)


@pytest.mark.parametrize(
    "parts",
    [
        (chunk(b"IDAT", b"x"), header(), chunk(b"IEND")),
        (chunk(b"IEND"),),
        (header(), header(), chunk(b"IDAT", b"x"), chunk(b"IEND")),
        (header(), chunk(b"IDAT", b"x")),
        (header(), chunk(b"IEND")),
        (chunk(b"IDAT", b"x"), chunk(b"IEND")),
        (header(), chunk(b"IDAT", b"x"), chunk(b"IEND", b"x")),
        (header(), chunk(b"IDAT", b"x"), chunk(b"IEND"), chunk(b"IEND")),
        (header(), chunk(b"IDAT", b"x"), chunk(b"IEND"), chunk(b"IDAT", b"x")),
    ],
)
def test_chunk_order_duplicates_missing_and_trailing(parts):
    with pytest.raises(m.MediaContractError):
        m.inspect_png(m.PNG_SIGNATURE + b"".join(parts))


@pytest.mark.parametrize("length", [0, 12, 14])
def test_exact_ihdr_length(length):
    with pytest.raises(m.MediaContractError, match="media_png_header"):
        m.inspect_png(
            m.PNG_SIGNATURE + chunk(b"IHDR", b"x" * length) + chunk(b"IDAT", b"x") + chunk(b"IEND")
        )


@pytest.mark.parametrize("cut", range(len(png())))
def test_every_truncation_is_rejected(cut):
    with pytest.raises(m.MediaContractError):
        m.inspect_png(png()[:cut])


@pytest.mark.parametrize("index", range(8))
def test_every_signature_byte_is_checked(index):
    data = bytearray(png())
    data[index] ^= 1
    with pytest.raises(m.MediaContractError, match="media_png_signature"):
        m.inspect_png(bytes(data))


@pytest.mark.parametrize("index", [29, 33 + 8, -1])
def test_crc_covers_header_data_idat_data_and_empty_iend(index):
    data = bytearray(png())
    data[index] ^= 1
    with pytest.raises(m.MediaContractError, match="media_png_crc"):
        m.inspect_png(bytes(data))


@pytest.mark.parametrize("length", [0x7FFFFFFF, 0xFFFFFFFF])
def test_declared_length_checked_before_crc_or_slice(monkeypatch, length):
    def forbidden(*args):
        pytest.fail("truncated chunk reached checksum")

    monkeypatch.setattr(m.binascii, "crc32", forbidden)
    with pytest.raises(m.MediaContractError, match="media_png_chunk_bounds"):
        m.inspect_png(m.PNG_SIGNATURE + struct.pack(">I", length) + b"IDAT" + b"\0" * 4)


@pytest.mark.parametrize("suffix", [b"x", b"\0", png()])
def test_exact_framing_end(suffix):
    with pytest.raises(m.MediaContractError, match="media_png_end"):
        m.inspect_png(png() + suffix)


@pytest.mark.parametrize("field", ["box_width", "box_height"])
@pytest.mark.parametrize(
    "value", [None, False, True, 0, 257, -1, 1.0, "1", float("nan"), float("inf")]
)
def test_strict_request_box(field, value):
    values = dict(source=m.PngSource(png()), box_width=1, box_height=1)
    values[field] = value
    with pytest.raises(m.MediaContractError, match="media_request_box"):
        m.ThumbnailRequest(**values)


@pytest.mark.parametrize("width,height", [(1, 1), (1, 256), (256, 1), (256, 256)])
def test_request_box_boundaries(width, height):
    req = request(width=width, height=height)
    assert (req.box_width, req.box_height) == (width, height)


@pytest.mark.parametrize("source", [png(), None, {}, type("Source", (), {})()])
def test_request_requires_validated_source(source):
    with pytest.raises(m.MediaContractError, match="media_request_source"):
        m.ThumbnailRequest(source, 1, 1)


def test_digest_derivation_and_fresh_policy_copy():
    source = m.PngSource(png())
    req = m.ThumbnailRequest(source, 1, 2)
    policy = m.thumbnail_policy()
    expected_policy = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert req.policy_digest == expected_policy
    payload = {
        "schema": "zelo-media-thumbnail-request-v1",
        "source_sha256": hashlib.sha256(source.data).hexdigest(),
        "box_width": 1,
        "box_height": 2,
        "policy_digest": expected_policy,
    }
    assert (
        req.digest
        == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    policy["output_side"] = 1024
    assert m.thumbnail_policy()["output_side"] == 256
    assert m.THUMBNAIL_POLICY_DIGEST == expected_policy
    assert req.digest != m.ThumbnailRequest(source, 2, 2).digest
    assert req.digest != m.ThumbnailRequest(source, 1, 1).digest
    assert req.digest != m.ThumbnailRequest(m.PngSource(png(idats=(b"other",))), 1, 2).digest


@pytest.mark.parametrize("field", ["source_digest", "request_digest", "policy_digest"])
@pytest.mark.parametrize("value", [None, True, "bad", "0" * 64, b"0" * 64])
def test_candidate_forged_binding_rejected(field, value):
    with pytest.raises(m.MediaContractError, match="media_candidate_binding"):
        candidate(request(), **{field: value})


def test_stale_or_swapped_valid_request_rejected():
    old = request()
    for new in (request(png(255, 256)), request(width=255), request(height=255)):
        with pytest.raises(m.MediaContractError, match="media_candidate_binding"):
            candidate(new, source_digest=old.source.sha256, request_digest=old.digest)


@pytest.mark.parametrize("value", [None, {}, png()])
def test_candidate_request_is_strict(value):
    with pytest.raises(m.MediaContractError, match="media_candidate_request"):
        m.CandidateThumbnail(value, png(), "0" * 64, "0" * 64, "0" * 64)


@pytest.mark.parametrize("data", [png(257, 1), png(1, 257), png(color=2), png(0, 1)])
def test_output_is_bounded_rgba_only(data):
    with pytest.raises(m.MediaContractError, match="media_png_header"):
        candidate(request(), data)


@pytest.mark.parametrize(
    "source,box,output",
    [
        (png(1, 256), (256, 256), png(2, 1)),
        (png(256, 1), (256, 256), png(1, 2)),
        (png(256, 256), (1, 256), png(2, 1)),
        (png(256, 256), (256, 1), png(1, 2)),
    ],
)
def test_candidate_box_and_no_upscale_dimensions(source, box, output):
    with pytest.raises(m.MediaContractError, match="media_candidate_dimensions"):
        candidate(request(source, *box), output)


@pytest.mark.parametrize("width,height", [(1, 1), (256, 1), (1, 256), (256, 256)])
def test_candidate_dimension_boundaries(width, height):
    item = candidate(request(), png(width, height))
    assert (item.envelope.width, item.envelope.height) == (width, height)
    assert item.instruction_trust == "none"
    assert item.sha256 == hashlib.sha256(item.data).hexdigest()
    assert item.digest != candidate(item.request, png(width, height, idats=(b"changed",))).digest


def test_objects_are_frozen_and_unknown_or_acceptance_fields_refused():
    req = request()
    item = candidate(req)
    for obj, field, value in [
        (req.source, "data", png()),
        (req.source.envelope, "width", 2),
        (req, "box_width", 2),
        (item, "instruction_trust", "trusted"),
    ]:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, value)
    for fields in (
        {"instruction_trust": "trusted"},
        {"cleanup_confirmed": True},
        {"success": True},
        {"provenance": "approved"},
        {"path": "/tmp/image"},
    ):
        with pytest.raises(TypeError):
            candidate(req, **fields)
    with pytest.raises(TypeError):
        m.PngSource(png(), envelope=m.PngEnvelope(1, 1, 6, 1))
    with pytest.raises(TypeError):
        m.ThumbnailRequest(req.source, 1, 1, policy_digest="0" * 64)
    assert "unverified-compressed-data" not in repr(item)


def test_invalid_compressed_pixels_remain_candidate_only_without_codec(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("framing invoked a decoder")

    monkeypatch.setattr(zlib, "decompress", forbidden)
    data = png(idats=(b"not a zlib stream",))
    req = request(data, 1, 1)
    item = candidate(req, data)
    assert item.instruction_trust == "none"
    assert not any(
        hasattr(item, name)
        for name in ("cleanup_confirmed", "success", "provenance", "pixel_validity_verified")
    )
    assert m.thumbnail_policy()["pixel_validity_verified"] is False
    assert m.thumbnail_policy()["transformation_verified"] is False


def test_small_standard_png_framing_fixture():
    data = png(idats=(zlib.compress(b"\0\xff\0\0\xff"),))
    req = request(data, 1, 1)
    assert candidate(req, data).envelope == m.PngEnvelope(1, 1, 6, 1)


def test_module_dependencies_and_guarded_validation_have_no_side_effects():
    path = Path(m.__file__)
    source = path.read_text()
    dependencies = {
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [None])
    }
    assert dependencies == {"binascii", "hashlib", "json", "struct", "dataclasses"}
    code = f"""import binascii, hashlib, json, struct, dataclasses, sys, os, builtins
source = {source!r}
data = {png()!r}
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {{'PIL', 'app', 'qualification', 'socket', 'subprocess'}}:
        raise RuntimeError('forbidden_import')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def forbidden(*args, **kwargs):
    raise RuntimeError('environment_access')
os.getenv = forbidden
class ForbiddenEnvironment(dict):
    __getitem__ = get = __iter__ = keys = items = values = forbidden
os.environ = ForbiddenEnvironment()
def audit(event, args):
    if event in {{'open', 'os.listdir', 'os.scandir', 'subprocess.Popen',
                 'socket.__new__', 'os.putenv', 'os.unsetenv', 'os.system'}}:
        raise RuntimeError('forbidden_side_effect')
sys.addaudithook(audit)
namespace = {{'__name__': 'guarded_media'}}
import types
module = types.ModuleType('guarded_media')
sys.modules['guarded_media'] = module
exec(compile(source, '<media-contracts>', 'exec'), module.__dict__)
req = module.ThumbnailRequest(module.PngSource(data), 1, 1)
item = module.CandidateThumbnail(req, data, req.source.sha256, req.digest, req.policy_digest)
assert item.instruction_trust == 'none'
print('GUARDED_CONTRACT_PASSED')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"GUARDED_CONTRACT_PASSED\n"


def test_fixed_digest_vectors():
    source = m.PngSource(png())
    req = m.ThumbnailRequest(source, 1, 2)
    item = candidate(req, source.data)
    assert (
        m.THUMBNAIL_POLICY_DIGEST
        == "f6b9abdb94df422d02662e033d7418543e5dc752cb25dbecf2b00171589cde23"
    )
    assert source.sha256 == "f52b7af6a67574445271f2d5e8f8081dd37a2414b0c914d47681de1ea078a39d"
    assert req.digest == "9e770cce4d8ed5a7eabb2400f6fb02319eda3ecd76892cb4c91acafa1ebc02bd"
    assert item.digest == "72150b79cf6a086e0a80f744200b2f5acaa6ecee2fa68438c2ec54a346d62e4c"


@pytest.mark.parametrize("value", [None, True, "png", bytearray(png()), memoryview(png())])
def test_candidate_bytes_are_strict(value):
    with pytest.raises(m.MediaContractError, match="media_png_bytes"):
        candidate(request(), value)


def test_no_production_media_import_or_registration():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "app").rglob("*.py"):
        assert "media_contracts" not in path.read_text()


def test_changed_policy_has_different_identity():
    policy = m.thumbnail_policy()
    policy["upscale"] = True
    changed = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert changed != m.THUMBNAIL_POLICY_DIGEST
    with pytest.raises(m.MediaContractError, match="media_candidate_binding"):
        candidate(request(), policy_digest=changed)
