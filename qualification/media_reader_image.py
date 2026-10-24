"""Deterministic inert Media reader image manifest; no Docker action on import."""

import hashlib
import json
from dataclasses import dataclass

from qualification import media_reader_bootstrap as bootstrap
from qualification import media_reader_container as container

DESTINATIONS = (
    "reader/dpkg-deb",
    "reader/lib/libc.so.6",
    "reader/ld.so",
    "reader/lib/libz.so.1",
    "reader/lib/libbz2.so.1.0",
    "reader/lib/liblzma.so.5",
    "reader/lib/libzstd.so.1",
    "reader/lib/libmd.so.0",
)
MODES = (0o555, 0o444, 0o555, 0o444, 0o444, 0o444, 0o444, 0o444)
DOCKERFILE_BYTES = 8192


class Refused(ValueError):
    """Fixed image-manifest refusal."""


def _require(condition, code):
    if not condition:
        raise Refused("reader_image_" + code)


@dataclass(frozen=True)
class ImageFile:
    source: str
    destination: str
    bytes: int
    mode: int
    sha256: str


@dataclass(frozen=True)
class ImageManifest:
    files: tuple[ImageFile, ...]
    digest: str


def load_lock(raw: bytes):
    try:
        return bootstrap.load_lock(raw)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise Refused("reader_image_lock") from None


def manifest(lock) -> ImageManifest:
    _require(type(lock) is dict and type(lock.get("files")) is list, "lock_shape")
    _require(len(lock["files"]) == len(DESTINATIONS) == len(MODES) == 8, "lock_count")
    rows = []
    for index, (item, destination, mode) in enumerate(
        zip(lock["files"], DESTINATIONS, MODES, strict=True)
    ):
        _require(type(item) is dict, "lock_row")
        _require(item.get("relative") == f"{index:02d}.binary.reference", "lock_order")
        _require(
            type(item.get("bytes")) is int
            and 0 < item["bytes"] <= 4194304
            and type(item.get("sha256")) is str
            and len(item["sha256"]) == 64,
            "lock_identity",
        )
        rows.append(
            ImageFile(
                source=item["relative"],
                destination=destination,
                bytes=item["bytes"],
                mode=mode,
                sha256=item["sha256"],
            )
        )
    canonical = [
        {
            "source": row.source,
            "destination": row.destination,
            "bytes": row.bytes,
            "mode": row.mode,
            "sha256": row.sha256,
        }
        for row in rows
    ]
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return ImageManifest(tuple(rows), digest)


def dockerfile(value: ImageManifest) -> bytes:
    _require(type(value) is ImageManifest and len(value.files) == 8, "manifest")
    lines = ["FROM scratch"]
    # The context files carry the pinned modes for legacy Docker COPY.
    for row in value.files:
        lines.append(f"COPY --chown=0:0 {row.source} /{row.destination}")
    # Explicitly replace Docker scratch image default PATH with an unusable path.
    lines.append("ENV PATH=/nonexistent")
    lines.append(f'LABEL {container.BUNDLE_LABEL}="{value.digest}"')
    raw = ("\n".join(lines) + "\n").encode()
    _require(len(raw) <= DOCKERFILE_BYTES, "dockerfile")
    return raw


def expected_export(value: ImageManifest) -> dict[str, tuple[int, int, str]]:
    _require(type(value) is ImageManifest and len(value.files) == 8, "manifest")
    result = {}
    for row in value.files:
        _require(row.destination not in result, "duplicate")
        result[row.destination] = (row.bytes, row.mode, row.sha256)
    return result


def contract(image_identity: str, value: ImageManifest) -> container.ReaderContainerContract:
    _require(type(value) is ImageManifest, "manifest")
    result = container.ReaderContainerContract(image_identity, value.digest)
    try:
        result.validate()
    except container.Refused as exc:
        raise Refused("reader_image_contract") from exc
    return result
