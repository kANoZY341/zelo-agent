"""Verify source-owned, inert community reference snapshots.

Untrusted upstream Markdown and code are DATA. This verifier never imports,
executes, registers or returns their source contents to a model/Worker.
"""

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "integrations" / "community" / "sources"
MANIFEST = ROOT / "integrations" / "community" / "sources.json"
EXPECTED_MANIFEST_SHA256 = "c59c13db3539448a00f88dab4b189b0c23fe8bf17b3f045ce4f387c89b6b4457"
EXPECTED_SOURCES = {
    "open_code_review": ("alibaba/open-code-review", "Apache-2.0"),
    "ecc": ("affaan-m/ECC", "MIT"),
    "harness": ("revfactory/harness", "Apache-2.0"),
    "archify": ("tt-a1i/archify", "MIT"),
    "opendots": ("CopilotKit/OpenDots", "MIT"),
}
EXPECTED_FILES = 19
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\Z")
MAX_FILE_BYTES = 65536
MAX_MANIFEST_BYTES = 16384


class CommunitySourceRefusal(ValueError):
    """Fail closed if any borrowed file, license or provenance differs."""


@dataclass(frozen=True, slots=True)
class CuratedSource:
    name: str
    repository: str
    commit: str
    license: str
    file_count: int
    source_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    worker_registered: Literal[False] = False
    tools_granted: tuple[()] = ()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    # Reading updates atime on some filesystems; do not mistake it for tampering.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_regular(path: Path, maximum: int) -> bytes:
    """No symlinks, hardlinks or mutable/oversize evidence."""

    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise CommunitySourceRefusal("community_source_not_regular")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            if _stamp(opened) != _stamp(before):
                raise CommunitySourceRefusal("community_source_changed")
            data = bytearray()
            while len(data) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != before.st_size or _stamp(os.fstat(fd)) != _stamp(before):
                raise CommunitySourceRefusal("community_source_changed")
        finally:
            os.close(fd)
        if _stamp(path.lstat()) != _stamp(before):
            raise CommunitySourceRefusal("community_source_changed")
        return bytes(data)
    except OSError:
        raise CommunitySourceRefusal("community_source_unavailable") from None


def verify_community_sources() -> tuple[CuratedSource, ...]:
    """Verify exact bytes and source inventory; no execution/import occurs."""

    encoded = _read_regular(MANIFEST, MAX_MANIFEST_BYTES)
    if _digest(encoded) != EXPECTED_MANIFEST_SHA256:
        raise CommunitySourceRefusal("community_manifest_digest_changed")
    try:
        manifest = json.loads(encoded)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise CommunitySourceRefusal("community_manifest_invalid") from None
    if (
        type(manifest) is not dict
        or manifest.get("schema") != "zelo-curated-community-sources-v1"
        or manifest.get("auto_activation") is not False
        or manifest.get("source_evidence_only") is not True
        or manifest.get("granted_workers") != []
        or manifest.get("granted_tools") != []
        or set(manifest.get("sources", ())) != set(EXPECTED_SOURCES)
    ):
        raise CommunitySourceRefusal("community_manifest_authority_changed")
    context = manifest.get("context_mode")
    if (
        type(context) is not dict
        or context.get("repository") != "mksglu/context-mode"
        or context.get("license") != "Elastic-2.0"
        or context.get("copied_files") != []
        or not isinstance(context.get("commit"), str)
        or COMMIT.fullmatch(context["commit"]) is None
    ):
        raise CommunitySourceRefusal("context_mode_source_copy_forbidden")
    summary = []
    paths = set()
    for name in sorted(EXPECTED_SOURCES):
        item = manifest["sources"][name]
        expected_repo, expected_license = EXPECTED_SOURCES[name]
        if (
            type(item) is not dict
            or item.get("repository") != expected_repo
            or item.get("license") != expected_license
            or not isinstance(item.get("commit"), str)
            or COMMIT.fullmatch(item["commit"]) is None
            or item.get("execution_enabled") is not False
            or item.get("tool_grants") != []
            or not isinstance(item.get("files"), list)
            or not item["files"]
        ):
            raise CommunitySourceRefusal("community_source_identity_invalid")
        seen = set()
        for entry in item["files"]:
            if type(entry) is not dict or set(entry) != {
                "path",
                "sha256",
                "bytes",
                "upstream_git_blob",
            }:
                raise CommunitySourceRefusal("community_file_entry_invalid")
            rel = entry["path"]
            if (
                type(rel) is not str
                or len(rel) > 240
                or any(not NAME.fullmatch(part) or part in (".", "..") for part in rel.split("/"))
                or rel.startswith("/")
                or rel in seen
                or not isinstance(entry["sha256"], str)
                or HEX.fullmatch(entry["sha256"]) is None
                or type(entry["bytes"]) is not int
                or not 1 <= entry["bytes"] <= MAX_FILE_BYTES
                or not isinstance(entry["upstream_git_blob"], str)
                or COMMIT.fullmatch(entry["upstream_git_blob"]) is None
            ):
                raise CommunitySourceRefusal("community_file_identity_invalid")
            seen.add(rel)
            parts = (name, *rel.split("/"))
            path = SOURCE_ROOT.joinpath(*parts)
            for ancestor in path.parents:
                if ancestor == SOURCE_ROOT.parent:
                    break
                if ancestor.is_symlink():
                    raise CommunitySourceRefusal("community_source_symlink_ancestor")
            content = _read_regular(path, MAX_FILE_BYTES)
            if len(content) != entry["bytes"] or _digest(content) != entry["sha256"]:
                raise CommunitySourceRefusal("community_source_bytes_changed")
            # Git blob object identity independently corroborates captured provenance.
            blob_identity = hashlib.sha1(
                b"blob " + str(len(content)).encode("ascii") + b"\0" + content,
                usedforsecurity=False,
            ).hexdigest()
            if blob_identity != entry["upstream_git_blob"]:
                raise CommunitySourceRefusal("community_upstream_blob_identity_changed")
            paths.add(path.relative_to(SOURCE_ROOT).as_posix())
        if "LICENSE" not in seen:
            raise CommunitySourceRefusal("community_license_missing")
        summary.append(
            CuratedSource(name, expected_repo, item["commit"], expected_license, len(seen))
        )
    if len(paths) != EXPECTED_FILES:
        raise CommunitySourceRefusal("community_source_file_count_invalid")
    try:
        present = set()
        for path in SOURCE_ROOT.rglob("*"):
            if path.is_symlink():
                raise CommunitySourceRefusal("community_unexpected_symlink")
            if path.is_file():
                present.add(path.relative_to(SOURCE_ROOT).as_posix())
            elif not path.is_dir():
                raise CommunitySourceRefusal("community_unexpected_special_file")
        if present != paths:
            raise CommunitySourceRefusal("community_unexpected_source_file")
    except OSError:
        raise CommunitySourceRefusal("community_source_inventory_unavailable") from None
    return tuple(summary)


def read_pinned_reference(name: str, relative_path: str) -> bytes:
    """Return verified *source data* only, never instructions or executable code.

    Repeat exact digest checks on the bytes actually consumed so a change after
    an inventory verification cannot substitute an unreviewed schema/content.
    """

    verify_community_sources()
    encoded = _read_regular(MANIFEST, MAX_MANIFEST_BYTES)
    if _digest(encoded) != EXPECTED_MANIFEST_SHA256:
        raise CommunitySourceRefusal("community_manifest_digest_changed")
    try:
        manifest = json.loads(encoded)
        entries = manifest["sources"][name]["files"]
        matches = [item for item in entries if item["path"] == relative_path]
        if len(matches) != 1:
            raise CommunitySourceRefusal("community_source_path_not_curated")
        entry = matches[0]
        target = SOURCE_ROOT / name / relative_path
        if any(part in (".", "..", "") for part in relative_path.split("/")):
            raise CommunitySourceRefusal("community_source_path_not_curated")
        for ancestor in target.parents:
            if ancestor == SOURCE_ROOT.parent:
                break
            if ancestor.is_symlink():
                raise CommunitySourceRefusal("community_source_symlink_ancestor")
        content = _read_regular(target, MAX_FILE_BYTES)
        if len(content) != entry["bytes"] or _digest(content) != entry["sha256"]:
            raise CommunitySourceRefusal("community_source_bytes_changed")
        blob_identity = hashlib.sha1(
            b"blob " + str(len(content)).encode("ascii") + b"\0" + content,
            usedforsecurity=False,
        ).hexdigest()
        if blob_identity != entry["upstream_git_blob"]:
            raise CommunitySourceRefusal("community_upstream_blob_identity_changed")
        return content
    except (KeyError, TypeError, RecursionError):
        raise CommunitySourceRefusal("community_source_path_not_curated") from None
