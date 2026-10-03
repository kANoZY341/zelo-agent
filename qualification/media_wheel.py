"""Closed Pillow candidate inventory, never an installer or execution approval.

Strict bytes and the independently recorded release pin precede ZIP parsing.
Declared archive permissions are not filesystem grants. Publisher metadata is not
cryptographic attestation verification, ABI compatibility or source/codec audit.
"""

import base64
import csv
import hashlib
import io
import json
import re
import stat
import struct
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from email.parser import BytesParser
from email.policy import default

from qualification.artifacts import ArtifactFile

WHEEL_FILENAME = "pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
WHEEL_SHA256 = "78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91"
DIST_INFO = "pillow-12.3.0.dist-info"
RECORD = f"{DIST_INFO}/RECORD"
LICENSE = f"{DIST_INFO}/licenses/LICENSE"
SBOM = f"{DIST_INFO}/sboms/pillow-12.3.0.cdx.json"
TAGS = ("cp312-cp312-manylinux_2_27_x86_64", "cp312-cp312-manylinux_2_28_x86_64")
ARCHIVE_BYTES = 8 * 1024**2
FILE_BYTES = 16 * 1024**2
TOTAL_BYTES = 64 * 1024**2
METADATA_BYTES = 131072
ENTRY_LIMIT = 512
NATIVE_EXTENSIONS = tuple(
    f"PIL/{name}.cpython-312-x86_64-linux-gnu.so"
    for name in (
        "_avif",
        "_imaging",
        "_imagingcms",
        "_imagingft",
        "_imagingmath",
        "_imagingmorph",
        "_imagingtk",
        "_webp",
    )
)
BUNDLED_LIBRARIES = tuple(
    "pillow.libs/" + name
    for name in (
        "libXau-154567c4.so.6.0.0",
        "libavif-8a7f9d56.so.16.4.2",
        "libbrotlicommon-53534446.so.1.2.0",
        "libbrotlidec-7e5462ba.so.1.2.0",
        "libfreetype-9fc94c80.so.6.20.6",
        "libharfbuzz-172d1f63.so.0.61421.0",
        "libjpeg-31e2ca52.so.62.4.0",
        "liblcms2-dade1fbf.so.2.0.19",
        "liblzma-2be87c3e.so.5.8.3",
        "libopenjp2-b07f72ad.so.2.5.4",
        "libpng16-abb096d5.so.16.58.0",
        "libsharpyuv-0066295b.so.0.1.2",
        "libtiff-fc87e79d.so.6.2.0",
        "libwebp-51b0b3f7.so.7.2.0",
        "libwebpdemux-9fe2abcc.so.2.0.17",
        "libwebpmux-8fb1c9f6.so.3.1.2",
        "libxcb-ad31f5a3.so.1.1.0",
        "libzstd-44be1190.so.1.5.7",
    )
)
REQUIRED_FILES = frozenset(
    NATIVE_EXTENSIONS
    + BUNDLED_LIBRARIES
    + (
        "PIL/__init__.py",
        "PIL/_version.py",
        "PIL/Image.py",
        "PIL/ImageFile.py",
        "PIL/PngImagePlugin.py",
        f"{DIST_INFO}/METADATA",
        f"{DIST_INFO}/WHEEL",
        RECORD,
        f"{DIST_INFO}/top_level.txt",
        f"{DIST_INFO}/zip-safe",
        LICENSE,
        SBOM,
        f"{DIST_INFO}/sboms/auditwheel.cdx.json",
    )
)
REQUIRED_DIRECTORIES = frozenset(
    ("PIL", "pillow.libs", DIST_INFO, f"{DIST_INFO}/licenses", f"{DIST_INFO}/sboms")
)


class MediaArtifactError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    ).hexdigest()


def wheel_policy() -> dict:
    return {
        "schema": "zelo-media-pillow-wheel-policy-v1",
        "wheel": WHEEL_FILENAME,
        "closed_sha256": WHEEL_SHA256,
        "archive_bytes": ARCHIVE_BYTES,
        "file_bytes": FILE_BYTES,
        "total_bytes": TOTAL_BYTES,
        "metadata_bytes": METADATA_BYTES,
        "entries": ENTRY_LIMIT,
        "depth": 8,
        "path_bytes": 512,
        "compression": [0, 8],
        "flags": 0,
        "member_extra_bytes": 0,
        "extract_versions": [10, 20],
        "required_files": sorted(REQUIRED_FILES),
        "required_directories": sorted(REQUIRED_DIRECTORIES),
        "tags": TAGS,
        "record_hash": "canonical-sha256-urlsafe-no-padding",
        "record_self": "one-empty-hash-and-size-row",
        "mode_semantics": "declared-archive-permissions-not-filesystem-grants",
        "record_0664_exception": RECORD,
        "untyped_0600_exception": SBOM,
        "instruction_trust": "none",
        "import_install_extract": False,
    }


WHEEL_POLICY_DIGEST = _canonical_digest(wheel_policy())


def _path(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 512
        or re.fullmatch(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", value) is None
        or len(value.split("/")) > 8
        or any(part in (".", "..") for part in value.split("/"))
        or value.split("/")[0] not in {"PIL", "pillow.libs", DIST_INFO}
    ):
        raise MediaArtifactError("media_wheel_path")
    return value


def _file_mode(path: str, mode: int) -> bool:
    return (
        mode in (0o644, 0o755)
        or (path == RECORD and mode == 0o664)
        or (path == SBOM and mode == 0o600)
    )


@dataclass(frozen=True, init=False)
class PillowWheelInventory:
    """Scanner-derived candidate metadata; no approval or runtime-success fields."""

    archive_sha256: str
    archive_bytes: int
    uncompressed_bytes: int
    files: tuple[ArtifactFile, ...]
    directories: tuple[tuple[str, int], ...]
    licenses: tuple[str, ...] = field(default=(LICENSE,), init=False)
    tags: tuple[str, ...] = field(default=TAGS, init=False)
    schema: str = field(default="zelo-media-pillow-wheel-candidate-v1", init=False)
    policy_digest: str = field(default=WHEEL_POLICY_DIGEST, init=False)
    instruction_trust: str = field(default="none", init=False)

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("media_wheel_scanner_required")

    def __post_init__(self) -> None:
        if (
            type(self.archive_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.archive_sha256) is None
            or type(self.archive_bytes) is not int
            or not 1 <= self.archive_bytes <= ARCHIVE_BYTES
            or type(self.uncompressed_bytes) is not int
            or not 0 <= self.uncompressed_bytes <= TOTAL_BYTES
            or type(self.files) is not tuple
            or type(self.directories) is not tuple
            or not 1 <= len(self.files) + len(self.directories) <= ENTRY_LIMIT
        ):
            raise MediaArtifactError("media_wheel_inventory")
        names = set()
        for item in self.files:
            if (
                type(item) is not ArtifactFile
                or type(item.size) is not int
                or not 0 <= item.size <= FILE_BYTES
                or type(item.mode) is not int
                or not _file_mode(item.path, item.mode)
                or type(item.sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", item.sha256) is None
            ):
                raise MediaArtifactError("media_wheel_inventory")
            name = _path(item.path)
            if name in names:
                raise MediaArtifactError("media_wheel_inventory")
            names.add(name)
        if self.files != tuple(sorted(self.files, key=lambda item: item.path)):
            raise MediaArtifactError("media_wheel_inventory")
        directories = set()
        for item in self.directories:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[1]) is not int
                or item[1] != 0o755
            ):
                raise MediaArtifactError("media_wheel_inventory")
            name = _path(item[0])
            if name in directories or name in names:
                raise MediaArtifactError("media_wheel_inventory")
            directories.add(name)
        if (
            self.directories != tuple(sorted(self.directories))
            or not REQUIRED_FILES <= names
            or not REQUIRED_DIRECTORIES <= directories
            or sum(item.size for item in self.files) != self.uncompressed_bytes
        ):
            raise MediaArtifactError("media_wheel_inventory")
        for name in names | directories:
            if any("/".join(name.split("/")[:i]) in names for i in range(1, len(name.split("/")))):
                raise MediaArtifactError("media_wheel_inventory")

    @property
    def digest(self) -> str:
        return _canonical_digest(asdict(self))


def _identity_metadata(content: bytes, *, wheel: bool) -> None:
    try:
        content.decode("utf-8", "strict")
        message = BytesParser(policy=default).parsebytes(content)
    except (UnicodeError, ValueError):
        raise MediaArtifactError("media_wheel_metadata") from None
    expected = (
        {"Wheel-Version": "1.0", "Root-Is-Purelib": "false"}
        if wheel
        else {
            "Metadata-Version": "2.4",
            "Name": "pillow",
            "Version": "12.3.0",
            "License-Expression": "MIT-CMU",
        }
    )
    if message.defects or any(
        [str(v) for v in message.get_all(k, [])] != [value] for k, value in expected.items()
    ):
        raise MediaArtifactError("media_wheel_metadata")
    if wheel:
        if (
            tuple(sorted(str(v) for v in message.get_all("Tag", []))) != TAGS
            or message.get_payload().strip()
        ):
            raise MediaArtifactError("media_wheel_metadata")
    elif [str(v) for v in message.get_all("License-File", [])] != ["LICENSE"]:
        raise MediaArtifactError("media_wheel_metadata")


def _record(content: bytes, files: tuple[ArtifactFile, ...]) -> None:
    inventory = {item.path: item for item in files}
    seen = set()
    try:
        rows = csv.reader(io.StringIO(content.decode("utf-8", "strict"), newline=""), strict=True)
        for row in rows:
            if len(row) != 3 or len(seen) >= ENTRY_LIMIT:
                raise MediaArtifactError("media_wheel_record")
            name = _path(row[0])
            if name not in inventory or name in seen:
                raise MediaArtifactError("media_wheel_record")
            seen.add(name)
            if name == RECORD:
                if row[1:] != ["", ""]:
                    raise MediaArtifactError("media_wheel_record")
            else:
                item = inventory[name]
                value = (
                    base64.urlsafe_b64encode(bytes.fromhex(item.sha256)).decode("ascii").rstrip("=")
                )
                if row[1] != "sha256=" + value or row[2] != str(item.size):
                    raise MediaArtifactError("media_wheel_record")
        if seen != set(inventory):
            raise MediaArtifactError("media_wheel_record")
    except (UnicodeError, csv.Error):
        raise MediaArtifactError("media_wheel_record") from None


def _inspect_archive(data: bytes, expected_digest: str) -> PillowWheelInventory:
    """Private trusted-pin seam for offline fixtures; public pin cannot be selected."""
    if type(data) is not bytes or not 1 <= len(data) <= ARCHIVE_BYTES:
        raise MediaArtifactError("media_wheel_bytes")
    actual_digest = hashlib.sha256(data).hexdigest()
    if type(expected_digest) is not str or actual_digest != expected_digest:
        raise MediaArtifactError("media_wheel_pin")
    files = []
    directories = []
    buffered = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()  # Central directory parsed after bounded closed hash.
            if not 1 <= len(infos) <= ENTRY_LIMIT or archive.comment:
                raise MediaArtifactError("media_wheel_entries")
            names = set()
            declared = 0
            for info in infos:
                original = info.orig_filename
                if original != info.filename or type(original) is not str:
                    raise MediaArtifactError("media_wheel_path")
                directory = original.endswith("/")
                name = _path(original[:-1] if directory else original)
                if name in names:
                    raise MediaArtifactError("media_wheel_duplicate")
                names.add(name)
                if name in {RECORD + ".jws", RECORD + ".p7s"}:
                    raise MediaArtifactError("media_wheel_signature_member")
                mode = info.external_attr >> 16
                if (
                    info.create_system != 3
                    or info.flag_bits != 0
                    or info.extra
                    or info.comment
                    or info.extract_version not in (10, 20)
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or not 0 <= info.file_size <= FILE_BYTES
                    or not 0 <= info.compress_size <= ARCHIVE_BYTES
                ):
                    raise MediaArtifactError("media_wheel_entry")
                if directory:
                    if (
                        mode != 0o40755
                        or info.file_size
                        or info.compress_size
                        or info.CRC
                        or info.compress_type != zipfile.ZIP_STORED
                    ):
                        raise MediaArtifactError("media_wheel_entry")
                    directories.append((name, 0o755))
                else:
                    if not (
                        mode in (0o100644, 0o100755)
                        or (name == RECORD and mode == 0o100664)
                        or (name == SBOM and mode == 0o600)
                    ):
                        raise MediaArtifactError("media_wheel_entry")
                    declared += info.file_size
                    if declared > TOTAL_BYTES:
                        raise MediaArtifactError("media_wheel_size")
                    if (
                        name in {RECORD, f"{DIST_INFO}/METADATA", f"{DIST_INFO}/WHEEL"}
                        and info.file_size > METADATA_BYTES
                    ):
                        raise MediaArtifactError("media_wheel_metadata_size")
            file_names = {info.filename for info in infos if not info.is_dir()}
            for name in names:
                if any(
                    "/".join(name.split("/")[:i]) in file_names
                    for i in range(1, len(name.split("/")))
                ):
                    raise MediaArtifactError("media_wheel_collision")
            actual_total = 0
            for info in infos:
                if info.is_dir():
                    continue
                name = info.filename
                total = 0
                digest = hashlib.sha256()
                parts = (
                    [] if name in {RECORD, f"{DIST_INFO}/METADATA", f"{DIST_INFO}/WHEEL"} else None
                )
                with archive.open(info, "r") as source:
                    while block := source.read(min(65536, info.file_size - total + 1)):
                        total += len(block)
                        actual_total += len(block)
                        if (
                            total > info.file_size
                            or total > FILE_BYTES
                            or actual_total > TOTAL_BYTES
                        ):
                            raise MediaArtifactError("media_wheel_size")
                        digest.update(block)
                        if parts is not None:
                            parts.append(block)
                if total != info.file_size:
                    raise MediaArtifactError("media_wheel_size")
                files.append(
                    ArtifactFile(
                        name, total, stat.S_IMODE(info.external_attr >> 16), digest.hexdigest()
                    )
                )
                if parts is not None:
                    buffered[name] = b"".join(parts)
            result = object.__new__(PillowWheelInventory)
            for key, value in (
                ("archive_sha256", actual_digest),
                ("archive_bytes", len(data)),
                ("uncompressed_bytes", actual_total),
                ("files", tuple(sorted(files, key=lambda f: f.path))),
                ("directories", tuple(sorted(directories))),
            ):
                object.__setattr__(result, key, value)
            result.__post_init__()
            _identity_metadata(buffered[f"{DIST_INFO}/METADATA"], wheel=False)
            _identity_metadata(buffered[f"{DIST_INFO}/WHEEL"], wheel=True)
            _record(buffered[RECORD], result.files)
            return result
    except MediaArtifactError:
        raise
    except (
        zipfile.BadZipFile,
        NotImplementedError,
        UnicodeError,
        EOFError,
        struct.error,
        zlib.error,
        ValueError,
    ):
        raise MediaArtifactError("media_wheel_archive") from None


def inspect_pillow_wheel(data: bytes) -> PillowWheelInventory:
    """Inspect only the closed release candidate; never import/install/extract it."""
    return _inspect_archive(data, WHEEL_SHA256)
