"""Bounded Stage S sequencing; the operator owns I/O and the real gpgv invocation."""

import hashlib
from collections.abc import Callable

from qualification import media_origin as origin
from qualification import media_source_snapshot as source

BASE_PATH = f"/ubuntu/{source.SNAPSHOT_ID}/dists/noble-updates/"
RELEASE_PATH = BASE_PATH + "InRelease"
SOURCES_PATH = BASE_PATH + source.INDEX_PATH


def assess_snapshot(
    fetch: Callable[[str, int, dict | None], bytes],
    verify: Callable[[bytes], tuple[bytes, bytes, int, bool]],
) -> dict:
    """Sequence exactly two operator-bound inputs; synthetic callbacks grant no trust."""
    raw = fetch(RELEASE_PATH, origin.RELEASE_BYTES, None)
    origin.validate_envelope(raw)
    outcome = verify(raw)
    if type(outcome) is not tuple or len(outcome) != 4:
        raise origin.OriginError("media_origin_invocation_unsettled")
    plaintext, status, exit_code, cleanup_confirmed = outcome
    accepted = origin.accept_release(
        raw,
        plaintext,
        status,
        suite="noble-updates",
        exit_code=exit_code,
        cleanup_confirmed=cleanup_confirmed,
    )
    candidate = source.candidate_snapshot_release(plaintext)
    declaration = candidate["index"]
    expected = {"bytes": declaration["bytes"], "sha256": declaration["sha256"]}
    compressed = fetch(SOURCES_PATH, source.COMPRESSED_BYTES, expected)
    selected = source.parse_source_index(compressed, declaration)
    return {
        "schema": "zelo-media-source-snapshot-stage-s-conditional-v1",
        "snapshot_id": source.SNAPSHOT_ID,
        "inrelease": {
            "path": RELEASE_PATH,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "plaintext_sha256": accepted["plaintext_sha256"],
            "status_sha256": accepted["status_sha256"],
            "signer": accepted["signature"]["signer"],
            "date": candidate["release_date"],
        },
        "sources_path": SOURCES_PATH,
        "source": selected,
        "trust": "conditional-on-reviewed-fixed-operator",
        "uploader_identity_verified": False,
        "installed_binary_provenance_verified": False,
        "decoder_qualified": False,
        "runtime_authority": False,
    }
