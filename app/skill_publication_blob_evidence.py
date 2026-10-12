"""Bounded, read-only SHA-256 attestation of a claimed Skill PR's Git blob.

This checks the provider's exact file bytes, not Git tree mode, and never
authorizes GitHub merge or runtime Skill loading.
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
_OID = re.compile(r"^[0-9a-f]{40}$")


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
    exact_single_added_file: Literal[True] = True
    git_blob_oid_and_sha256_verified: Literal[True] = True
    pr_rechecked_after_blob_read: Literal[True] = True
    git_tree_mode_not_verified: Literal[True] = True
    no_github_write_authority: Literal[True] = True
    no_runtime_skill_authority: Literal[True] = True


class SkillPublicationBlobEvidenceService:
    """Use bounded GitHub GETs only, retaining no raw source data."""

    def __init__(
        self,
        remote: SkillPublicationRemoteEvidenceService,
        transport: GitHubTransport | None = None,
    ) -> None:
        self.remote = remote
        self.transport = transport if transport is not None else remote.transport

    async def verify(
        self, claim_id: str, request: MergeRequest, principal: OperatorPrincipal
    ) -> SkillPublicationBlobEvidence:
        fresh = await self.remote.verify(claim_id, request, principal)
        preview = self.remote.previews.preview(claim_id, request, principal)
        if fresh.merge_request_sha256 != preview.merge_request_sha256:
            raise DeveloperError("skill_publication_blob_local_evidence_changed")

        base = f"/repos/{fresh.repository}"
        path = f"/pulls/{fresh.pr_number}/files?per_page=100&page=1"
        files = await self.transport.request("GET", base + path)
        if type(files) is not list or len(files) != 1 or type(files[0]) is not dict:
            raise DeveloperError("skill_publication_blob_files_not_exclusive")
        entry = files[0]
        oid = entry.get("sha")
        if (
            entry.get("filename") != preview.artifact_path
            or entry.get("status") != "added"
            or type(oid) is not str
            or _OID.fullmatch(oid) is None
        ):
            raise DeveloperError("skill_publication_blob_pr_file_changed")

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

        # The Git object ID is SHA-1 by Git protocol, NOT our security digest.
        # SHA-256 comparison above is the trusted content-integrity check.
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
