"""Fixed snapshot candidate metadata checks; no network or signature authority."""

import hashlib
import lzma

import pytest

from qualification import media_source_snapshot as source
from tests.test_media_source_snapshot import release, stanza


def metadata() -> bytes:
    return release(lzma.compress(stanza())).replace(
        b"Valid-Until: Thu, 05 Nov 2026 23:35:03 UTC\n", b""
    )


def test_candidate_pins_one_snapshot_and_bounded_release_date():
    result = source.candidate_snapshot_release(metadata())
    assert result["snapshot_id"] == "20261030T000000Z"
    assert result["inrelease_url"] == (
        "https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/dists/noble-updates/InRelease"
    )
    assert result["sources_url"] == (
        "https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/dists/"
        "noble-updates/main/source/Sources.xz"
    )
    assert result["release_date"] == "Thu, 29 Oct 2026 23:35:03 UTC"
    assert result["plaintext_sha256"] == hashlib.sha256(metadata()).hexdigest()
    assert result["signature_verified"] is False
    assert result["snapshot_binding_verified"] is False
    assert result["source_acquired"] is False
    assert result["runtime_authority"] is False


@pytest.mark.parametrize(
    "date",
    [
        b"Thu, 29 Oct 2026 22:59:59 UTC",
        b"Fri, 30 Oct 2026 00:00:01 UTC",
        b"Thu, 29 Oct 2026 23:35:03 +0100",
    ],
)
def test_stale_future_or_offset_release_refuses(date):
    with pytest.raises(source.SourceSnapshotError):
        source.candidate_snapshot_release(
            metadata().replace(b"Thu, 29 Oct 2026 23:35:03 UTC", date)
        )


def test_expired_optional_valid_until_refuses():
    with pytest.raises(source.SourceSnapshotError):
        source.candidate_snapshot_release(
            metadata().replace(
                b"Architectures:", b"Valid-Until: Thu, 29 Oct 2026 23:59:59 UTC\nArchitectures:"
            )
        )


@pytest.mark.parametrize(
    "changed",
    [
        b"Thu, 29 Oct 2026 23:35:03 UTC garbage",
        b"Fri, 29 Oct 2026 23:35:03 UTC",
        b"Thu, 29 Oct 2026 23:35:03 +0000",
    ],
)
def test_ambiguous_or_noncanonical_signed_date_refuses(changed):
    with pytest.raises(source.SourceSnapshotError):
        source.candidate_snapshot_release(
            metadata().replace(b"Thu, 29 Oct 2026 23:35:03 UTC", changed)
        )


def test_ambiguous_valid_until_refuses():
    with pytest.raises(source.SourceSnapshotError):
        source.candidate_snapshot_release(
            metadata().replace(
                b"Architectures:",
                b"Valid-Until: Thu, 05 Nov 2026 23:35:03 UTC garbage\nArchitectures:",
            )
        )
