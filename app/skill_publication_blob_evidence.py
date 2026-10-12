"""Immutable-commit Git tree and blob proof for a claimed Skill artifact.

All provider operations are GET. Proves exact base/head comparison, single
added file, regular Git mode and SHA-256 bytes without publication authority.
"""

import base64
import binascii
import hashlib
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.auth import OperatorPrincipal
from app.developer_process import DeveloperError
from app.promotion_contracts import MergeRequest
from app.promotion_github import GitHubTransport
from app.skill_learning import CanonicalUuid, Sha256
from app.skill_publication_remote_evidence import SkillPublicationRemoteEvidenceService

_MAX_BYTES = 65_536
_MAX_BASE64 = 90_000
_MAX_TREE_ITEMS = 1024
_OID = re.compile(r"^[0-9a-f]{40}$")


def _oid(value: object) -> bool:
    return type(value) is str and _OID.fullmatch(value) is not None


class SkillPublicationBlobEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-skill-publication-blob-evidence-v1"] = (
        "zelo-skill-publication-blob-evidence-v1"
    )
    claim_id: CanonicalUuid
    claim_fingerprint: Sha256
    merge_request_sha256: Sha256
    repository: str
    pr_number: int = Field(ge=1)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    blob_git_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_path: str
    artifact_sha256: Sha256
    artifact_bytes: int = Field(ge=1, le=_MAX_BYTES)
    verified_at: datetime
    immutable_base_head_compare_verified: Literal[True] = True
    exact_single_added_file: Literal[True] = True
    exact_head_tree_regular_file_mode_verified: Literal[True] = True
    git_blob_oid_and_sha256_verified: Literal[True] = True
    pr_rechecked_after_blob_read: Literal[True] = True
    no_github_write_authority: Literal[True] = True
    no_runtime_skill_authority: Literal[True] = True


class SkillPublicationBlobEvidenceService:
    """Attest immutable head tree and exact source bytes; never issue a PUT."""

    def __init__(
        self,
        remote: SkillPublicationRemoteEvidenceService,
        transport: GitHubTransport | None = None,
    ) -> None:
        self.remote = remote
        self.transport = transport if transport is not None else remote.transport

    async def _tree_blob_oid(self, repo: str, head_sha: str, relative_path: str) -> str:
        commit = await self.transport.request("GET", f"/repos/{repo}/git/commits/{head_sha}")
        if type(commit) is not dict or commit.get("sha") != head_sha:
            raise DeveloperError("skill_publication_blob_commit_changed")
        root = commit.get("tree")
        if type(root) is not dict or not _oid(root.get("sha")):
            raise DeveloperError("skill_publication_blob_commit_changed")
        current_tree = root["sha"]
        segments = relative_path.split("/")
        if not 2 <= len(segments) <= 8 or any(
            not segment or segment in (".", "..") for segment in segments
        ):
            raise DeveloperError("skill_publication_blob_path_invalid")

        for i, segment in enumerate(segments):
            response = await self.transport.request(
                "GET", f"/repos/{repo}/git/trees/{current_tree}"
            )
            if (
                type(response) is not dict
                or response.get("sha") != current_tree
                or response.get("truncated") is not False
                or type(response.get("tree")) is not list
                or len(response["tree"]) > _MAX_TREE_ITEMS
            ):
                raise DeveloperError("skill_publication_blob_tree_invalid")
            matching = [
                item
                for item in response["tree"]
                if type(item) is dict and item.get("path") == segment
            ]
            if len(matching) != 1 or not _oid(matching[0].get("sha")):
                raise DeveloperError("skill_publication_blob_tree_path_invalid")
            item = matching[0]
            if i != len(segments) - 1:
                if item.get("type") != "tree" or item.get("mode") != "040000":
                    raise DeveloperError("skill_publication_blob_tree_mode_invalid")
                current_tree = item["sha"]
            else:
                if item.get("type") != "blob" or item.get("mode") != "100644":
                    raise DeveloperError("skill_publication_blob_tree_mode_invalid")
                return item["sha"]
        raise DeveloperError("skill_publication_blob_tree_path_invalid")

    async def verify(
        self, claim_id: str, request: MergeRequest, principal: OperatorPrincipal
    ) -> SkillPublicationBlobEvidence:
        fresh = await self.remote.verify(claim_id, request, principal)
        preview = self.remote.previews.preview(claim_id, request, principal)
        if fresh.merge_request_sha256 != preview.merge_request_sha256:
            raise DeveloperError("skill_publication_blob_local_evidence_changed")

        base = f"/repos/{fresh.repository}"
        # Compare immutable Git SHAs, NOT /pulls/{id}/files (a mutable head).
        compare = await self.transport.request(
            "GET",
            base + f"/compare/{fresh.pr_base_sha}...{fresh.pr_head_sha}",
        )
        if type(compare) is not dict or compare.get("status") != "ahead":
            raise DeveloperError("skill_publication_blob_compare_invalid")
        comparison_base = compare.get("base_commit")
        merge_base = compare.get("merge_base_commit")
        files = compare.get("files")
        if (
            type(comparison_base) is not dict
            or comparison_base.get("sha") != fresh.pr_base_sha
            or type(merge_base) is not dict
            or merge_base.get("sha") != fresh.pr_base_sha
            or type(files) is not list
            or len(files) != 1
            or type(files[0]) is not dict
        ):
            raise DeveloperError("skill_publication_blob_files_not_exclusive")
        entry = files[0]
        oid = entry.get("sha")
        if (
            entry.get("filename") != preview.artifact_path
            or entry.get("status") != "added"
            or not _oid(oid)
            or entry.get("previous_filename") is not None
        ):
            raise DeveloperError("skill_publication_blob_pr_file_changed")

        # Walk the Git tree rooted at the EXACT immutable reviewed head,
        # rejecting symlinks, submodules, directories and wrong blob IDs.
        tree_oid = await self._tree_blob_oid(
            fresh.repository, fresh.pr_head_sha, preview.artifact_path
        )
        if tree_oid != oid:
            raise DeveloperError("skill_publication_blob_tree_blob_changed")

        blob = await self.transport.request("GET", base + f"/git/blobs/{oid}")
        if type(blob) is not dict:
            raise DeveloperError("skill_publication_blob_response_invalid")
        size = blob.get("size")
        raw_content = blob.get("content")
        if (
            blob.get("encoding") != "base64"
            or blob.get("sha") != oid
            or type(size) is not int
            or size != preview.artifact_bytes
            or not 1 <= size <= _MAX_BYTES
            or type(raw_content) is not str
            or len(raw_content) > _MAX_BASE64
        ):
            raise DeveloperError("skill_publication_blob_response_invalid")
        encoded = raw_content.replace("\n", "")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise DeveloperError("skill_publication_blob_encoding_invalid") from None
        if len(data) != size or hashlib.sha256(data).hexdigest() != preview.artifact_sha256:
            raise DeveloperError("skill_publication_blob_content_changed")

        # Git protocol uses SHA-1 for blob IDs. SHA-256 above is the actual
        # security digest; the Git OID check links bytes to tree object IDs.
        header = f"blob {len(data)}\0".encode()
        if hashlib.sha1(header + data, usedforsecurity=False).hexdigest() != oid:
            raise DeveloperError("skill_publication_blob_git_oid_invalid")

        current = await self.transport.request("GET", base + f"/pulls/{fresh.pr_number}")
        self.remote._verify_pr_again(request, current)
        after = self.remote.previews.preview(claim_id, request, principal)
        if after != preview:
            raise DeveloperError("skill_publication_blob_local_evidence_changed")
        return SkillPublicationBlobEvidence(
            claim_id=preview.claim_id,
            claim_fingerprint=preview.claim_fingerprint,
            merge_request_sha256=preview.merge_request_sha256,
            repository=fresh.repository,
            pr_number=fresh.pr_number,
            head_sha=fresh.pr_head_sha,
            blob_git_oid=oid,
            artifact_path=preview.artifact_path,
            artifact_sha256=preview.artifact_sha256,
            artifact_bytes=size,
            verified_at=datetime.now(UTC),
        )
