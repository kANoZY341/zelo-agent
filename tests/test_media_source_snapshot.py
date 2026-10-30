"""Synthetic source-index fixtures exercise metadata parsing, not archive trust."""

import hashlib
import lzma

import pytest

from qualification import media_source_snapshot as source


def release(index: bytes) -> bytes:
    return (
        "Origin: Ubuntu\nLabel: Ubuntu\nSuite: noble-updates\nCodename: noble\n"
        "Date: Thu, 29 Oct 2026 23:35:03 UTC\n"
        "Valid-Until: Thu, 05 Nov 2026 23:35:03 UTC\n"
        "Architectures: amd64 arm64\nComponents: main restricted universe\nSHA256:\n"
        f" {hashlib.sha256(index).hexdigest()} {len(index)} main/source/Sources.xz\n"
    ).encode()


def stanza(*, package="dpkg", version="1.22.6ubuntu6.6", directory="pool/main/d/dpkg"):
    return (
        f"Package: {package}\nVersion: {version}\nDirectory: {directory}\n"
        "Checksums-Sha256:\n"
        f" {'a' * 64} 1234 dpkg_1.22.6ubuntu6.6.dsc\n"
        " 7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196 "
        "5548716 dpkg_1.22.6ubuntu6.6.tar.xz\n"
        "Checksums-Sha1:\n"
        f" {'c' * 40} 1234 dpkg_1.22.6ubuntu6.6.dsc\n"
        f" {'d' * 40} 5548716 dpkg_1.22.6ubuntu6.6.tar.xz\n"
        "Files:\n"
        f" {'e' * 32} 1234 dpkg_1.22.6ubuntu6.6.dsc\n"
        f" {'f' * 32} 5548716 dpkg_1.22.6ubuntu6.6.tar.xz\n"
        "Maintainer: Example Maintainer\n"
    ).encode()


def select(raw: bytes):
    compressed = lzma.compress(raw)
    declaration = source.select_source_index(release(compressed))
    return source.parse_source_index(compressed, declaration)


def test_selects_only_fixed_source_index_and_exact_version():
    selected = select(stanza() + b"\n" + stanza(package="other", version="1.0"))
    assert selected["metadata_only"] is True
    assert selected["runtime_authority"] is False
    assert selected["signature_verified"] is False
    assert selected["snapshot_binding_verified"] is False
    assert selected["historical_time_policy_verified"] is False
    assert selected["source"] == "dpkg"
    assert selected["version"] == "1.22.6ubuntu6.6"
    assert selected["directory"] == "pool/main/d/dpkg"
    assert selected["descriptor"] == {
        "filename": "dpkg_1.22.6ubuntu6.6.dsc",
        "bytes": 1234,
        "sha256": "a" * 64,
    }
    assert selected["tarball"] == {
        "filename": "dpkg_1.22.6ubuntu6.6.tar.xz",
        "bytes": 5548716,
        "sha256": "7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.replace(b"Suite: noble-updates", b"Suite: noble"),
        lambda b: b.replace(b"Components: main restricted universe", b"Components: universe"),
        lambda b: b.replace(
            b"SHA256:\n", b"SHA256:\n " + b"c" * 64 + b" 1 main/source/Sources.xz\n"
        ),
        lambda b: b.replace(b"main/source/Sources.xz", b"../Sources.xz"),
    ],
)
def test_release_identity_and_index_ambiguity_refuse(mutation):
    compressed = lzma.compress(stanza())
    with pytest.raises(source.SourceSnapshotError):
        source.select_source_index(mutation(release(compressed)))


def test_compressed_bytes_must_match_signed_declaration():
    compressed = lzma.compress(stanza())
    declaration = source.select_source_index(release(compressed))
    with pytest.raises(source.SourceSnapshotError):
        source.parse_source_index(compressed + b"x", declaration)


@pytest.mark.parametrize(
    "raw",
    [
        stanza() + b"\n" + stanza(),
        stanza()
        + b"\n"
        + stanza().replace(b"Version: 1.22.6ubuntu6.6\n", b"Version: 1.22.6ubuntu6.6 \n"),
        stanza().replace(
            b"Version: 1.22.6ubuntu6.6\n", b"Version: 1.22.6ubuntu6.6\nVersion: 1.22.6ubuntu6.6\n"
        ),
        stanza().replace(b"pool/main/d/dpkg", b"../dpkg"),
        stanza().replace(b"dpkg_1.22.6ubuntu6.6.dsc", b"../dpkg_1.22.6ubuntu6.6.dsc"),
        stanza().replace(b"5548716", b"5548716\n " + b"b" * 64 + b" 1 extra.tar.xz"),
        stanza().replace(
            b"7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196", b"b" * 64
        ),
        stanza().replace(
            b" ffffffffffffffffffffffffffffffff 5548716", b" ffffffffffffffffffffffffffffffff 1"
        ),
        stanza().replace(
            b" dddddddddddddddddddddddddddddddddddddddd 5548716",
            b" dddddddddddddddddddddddddddddddddddddddd 1",
        ),
        stanza().replace(b"Files:\n", b"Files:\n " + b"f" * 32 + b" 1 extra.tar.xz\n"),
    ],
)
def test_ambiguous_or_unsafe_target_refuses(raw):
    with pytest.raises(source.SourceSnapshotError):
        select(raw)


def test_xz_trailing_stream_and_expansion_limit_refuse():
    raw = stanza()
    compressed = lzma.compress(raw)
    extended = compressed + lzma.compress(b"extra")
    with pytest.raises(source.SourceSnapshotError):
        source.parse_source_index(extended, source.select_source_index(release(extended)))
    huge = lzma.compress(b"x" * (source.INDEX_BYTES + 1))
    with pytest.raises(source.SourceSnapshotError):
        source.parse_source_index(huge, source.select_source_index(release(huge)))
