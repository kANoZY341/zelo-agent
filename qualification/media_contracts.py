"""Bounded PNG framing and candidate binding, never decoding or execution authority.

CRC detects corruption, not authenticity. Correctly framed invalid compressed data
is intentionally still a candidate. No codec, filesystem or runtime is accessed.
"""

import binascii
import hashlib
import json
import struct
from dataclasses import dataclass, field

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_BYTE_LIMIT = 262144
INPUT_SIDE_LIMIT = 1024
OUTPUT_SIDE_LIMIT = 256
IDAT_LIMIT = 32


class MediaContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    ).hexdigest()


def thumbnail_policy() -> dict:
    """Return requested semantics; no decoder/resource enforcement is certified."""
    return {
        "schema": "zelo-media-png-thumbnail-policy-v1",
        "framing": "rgb-rgba8-ihdr-idat-iend-crc-no-interlace-v1",
        "input_bytes": PNG_BYTE_LIMIT,
        "input_side": INPUT_SIDE_LIMIT,
        "input_pixels": INPUT_SIDE_LIMIT**2,
        "output_bytes": PNG_BYTE_LIMIT,
        "output_side": OUTPUT_SIDE_LIMIT,
        "output_pixels": OUTPUT_SIDE_LIMIT**2,
        "idat_chunks": IDAT_LIMIT,
        "empty_idat": "allowed-with-nonempty-aggregate",
        "operation": "thumbnail-fit",
        "resampling": "LANCZOS",
        "output_mode": "RGBA8",
        "upscale": False,
        "ancillary_chunks": False,
        "instruction_trust": "none",
        "pixel_validity_verified": False,
        "transformation_verified": False,
    }


THUMBNAIL_POLICY_DIGEST = _digest(thumbnail_policy())


@dataclass(frozen=True)
class PngEnvelope:
    """Observed framing fields, not decoded image or trusted metadata."""

    width: int
    height: int
    color_type: int
    idat_chunks: int


def _inspect_png(data: bytes, side_limit: int, rgba_only: bool) -> PngEnvelope:
    if type(data) is not bytes or not 1 <= len(data) <= PNG_BYTE_LIMIT:
        raise MediaContractError("media_png_bytes")
    if not data.startswith(PNG_SIGNATURE):
        raise MediaContractError("media_png_signature")
    view = memoryview(data)
    offset = len(PNG_SIGNATURE)
    width = height = color_type = idats = idat_bytes = 0
    header = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise MediaContractError("media_png_chunk_bounds")
        length = struct.unpack_from(">I", view, offset)[0]
        if length > len(data) - offset - 12:
            raise MediaContractError("media_png_chunk_bounds")
        kind = data[offset + 4 : offset + 8]
        end = offset + 8 + length
        crc = struct.unpack_from(">I", view, end)[0]
        if binascii.crc32(view[offset + 4 : end]) & 0xFFFFFFFF != crc:
            raise MediaContractError("media_png_crc")
        if kind == b"IHDR":
            if header or offset != 8 or length != 13:
                raise MediaContractError("media_png_header")
            width, height, depth, color_type, compression, filtering, interlace = (
                struct.unpack_from(">IIBBBBB", view, offset + 8)
            )
            if (
                not 1 <= width <= side_limit
                or not 1 <= height <= side_limit
                or width * height > side_limit**2
                or depth != 8
                or color_type not in ((6,) if rgba_only else (2, 6))
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise MediaContractError("media_png_header")
            header = True
        elif kind == b"IDAT":
            if not header or idats >= IDAT_LIMIT:
                raise MediaContractError("media_png_idat")
            idats += 1
            idat_bytes += length
        elif kind == b"IEND":
            if not header or not idats or not idat_bytes or length != 0 or end + 4 != len(data):
                raise MediaContractError("media_png_end")
            return PngEnvelope(width, height, color_type, idats)
        else:
            raise MediaContractError("media_png_forbidden_chunk")
        offset = end + 4
    raise MediaContractError("media_png_end")


def inspect_png(data: bytes) -> PngEnvelope:
    """Validate only the fixed bounded input envelope; never decompress IDAT."""
    return _inspect_png(data, INPUT_SIDE_LIMIT, False)


@dataclass(frozen=True)
class PngSource:
    data: bytes = field(repr=False)
    envelope: PngEnvelope = field(init=False)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "envelope", inspect_png(self.data))
        object.__setattr__(self, "sha256", hashlib.sha256(self.data).hexdigest())


@dataclass(frozen=True)
class ThumbnailRequest:
    source: PngSource
    box_width: int
    box_height: int
    schema: str = field(default="zelo-media-thumbnail-request-v1", init=False)
    policy_digest: str = field(default=THUMBNAIL_POLICY_DIGEST, init=False)

    def __post_init__(self) -> None:
        if type(self.source) is not PngSource:
            raise MediaContractError("media_request_source")
        for value in (self.box_width, self.box_height):
            if type(value) is not int or not 1 <= value <= OUTPUT_SIDE_LIMIT:
                raise MediaContractError("media_request_box")

    @property
    def digest(self) -> str:
        return _digest(
            {
                "schema": self.schema,
                "source_sha256": self.source.sha256,
                "box_width": self.box_width,
                "box_height": self.box_height,
                "policy_digest": self.policy_digest,
            }
        )


def _matching_digest(value: str, expected: str) -> bool:
    return type(value) is str and value == expected


@dataclass(frozen=True)
class CandidateThumbnail:
    """A bound untrusted candidate, not proof of pixels, transformation or cleanup.

    The caller must supply hashes to compare against the validated request. Hashes
    and dimensions do not establish that these output bytes came from its source.
    No successful-runtime result type or acceptance flags are exposed here.
    """

    request: ThumbnailRequest
    data: bytes = field(repr=False)
    source_digest: str
    request_digest: str
    policy_digest: str
    envelope: PngEnvelope = field(init=False)
    sha256: str = field(init=False)
    schema: str = field(default="zelo-media-thumbnail-candidate-v1", init=False)
    instruction_trust: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        request = self.request
        if type(request) is not ThumbnailRequest:
            raise MediaContractError("media_candidate_request")
        if not all(
            _matching_digest(value, expected)
            for value, expected in (
                (self.source_digest, request.source.sha256),
                (self.request_digest, request.digest),
                (self.policy_digest, request.policy_digest),
            )
        ):
            raise MediaContractError("media_candidate_binding")
        envelope = _inspect_png(self.data, OUTPUT_SIDE_LIMIT, True)
        if envelope.width > min(
            request.box_width, request.source.envelope.width
        ) or envelope.height > min(request.box_height, request.source.envelope.height):
            raise MediaContractError("media_candidate_dimensions")
        object.__setattr__(self, "envelope", envelope)
        object.__setattr__(self, "sha256", hashlib.sha256(self.data).hexdigest())

    @property
    def digest(self) -> str:
        return _digest(
            {
                "schema": self.schema,
                "request_digest": self.request_digest,
                "source_digest": self.source_digest,
                "policy_digest": self.policy_digest,
                "output_sha256": self.sha256,
                "instruction_trust": self.instruction_trust,
            }
        )
