"""Durable operator-asserted REA target choices, without any analysis authority.

Core accepts only bounded metadata from a separately executed offline preflight.
It does NOT import preflight/admission code, read files, verify the actual target,
activate a Worker or convert a matching hash into an execution permission.
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth import OperatorPrincipal
from app.models import ReaOperatorTargetRevocationRecord, ReaOperatorTargetSelectionRecord
from app.storage import SessionStore

_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ID = re.compile(r"target-[0-9a-f]{32}\Z", re.ASCII)
_SOURCE = "6fee42689ae6e95a0182e0d4d0f55644e73e72be"
_MANIFEST = "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
_CATALOG = "82fb67571758c177d1eecac72efac152772cccc753442a77ef9418edc69be6a0"


def _sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _require_operator(principal: OperatorPrincipal) -> None:
    if type(principal) is not OperatorPrincipal or principal.subject != "ahmad":
        raise PermissionError("rea_operator_required")


def _snapshot_id(value: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("rea_target_identity_invalid")
    return value


def _digest(value: str) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError("rea_target_digest_invalid")
    return value


class ReaOperatorReviewClaim(BaseModel):
    """Operator-submitted offline preview; hash coherence is NOT target proof."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-rea-target-preflight-v1"]
    snapshot_id: str = Field(min_length=39, max_length=39, pattern=r"^target-[0-9a-f]{32}$")
    receipt_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=1, le=128)
    total_bytes: int = Field(ge=0, le=4 * 1024 * 1024)
    source_commit: Literal["6fee42689ae6e95a0182e0d4d0f55644e73e72be"]
    source_manifest_sha256: Literal[
        "6f593ce98506d319cce71acbc975b0c150360d371845f1634966c5dc254a19b9"
    ]
    tool_catalog_sha256: Literal["82fb67571758c177d1eecac72efac152772cccc753442a77ef9418edc69be6a0"]
    tool_name: Literal["analyze_javascript_application"]
    tool_qualification: Literal["fixed_fixture_only_not_target_qualified"]
    manual_operator_opt_in: Literal[True]
    review_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    execution_enabled: Literal[False]
    core_approval: Literal[False]
    runtime_qualified: Literal[False]
    worker_registered: Literal[False]
    tool_grants: tuple[()] = ()

    @field_validator("tool_grants", mode="before")
    @classmethod
    def reject_grants(cls, value):
        # FastAPI parses JSON arrays before model validation. Accept only
        # an explicitly empty list/tuple, never silently drop a grant.
        if type(value) not in (list, tuple) or len(value) != 0:
            raise ValueError("rea_operator_target_grants_forbidden")
        return ()

    @model_validator(mode="after")
    def check_identity(self):
        if self.review_fingerprint != _sha256(
            self.model_dump(mode="json", exclude={"review_fingerprint"})
        ):
            raise ValueError("rea_target_operator_review_fingerprint_invalid")
        return self


class ReaOperatorTargetSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    review: ReaOperatorReviewClaim
    confirm_inert_selection: Literal[True]


class ReaOperatorTargetSnapshot(BaseModel):
    """Historical selection only, not verified file admission or runnable lease."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    snapshot_id: str
    receipt_sha256: str
    content_sha256: str
    review_fingerprint: str
    file_count: int
    total_bytes: int
    selected_at: datetime
    currently_selected: bool
    origin: Literal["operator_asserted_unverified"] = "operator_asserted_unverified"
    target_verified_by_core: Literal[False] = False
    core_approval: Literal[False] = False
    execution_authority: Literal[False] = False
    worker_authority: Literal[False] = False
    tool_grants: tuple[()] = ()


class ReaOperatorTargetStore:
    """One selection per snapshot; terminal append-only revocation."""

    def __init__(self, database: SessionStore):
        self.database = database

    @staticmethod
    def _reconstruct(row: ReaOperatorTargetSelectionRecord) -> ReaOperatorReviewClaim:
        """Revalidate stored metadata, including the originally submitted hash."""

        if (
            row.operator_id != "ahmad"
            or row.execution_authority
            or row.worker_authority
            or row.approval_authority
        ):
            raise ValueError("rea_operator_target_record_invalid")
        values = {
            "schema_version": "zelo-rea-target-preflight-v1",
            "snapshot_id": _snapshot_id(row.snapshot_id),
            "receipt_sha256": _digest(row.receipt_sha256),
            "content_sha256": _digest(row.content_sha256),
            "file_count": row.file_count,
            "total_bytes": row.total_bytes,
            "source_commit": _SOURCE,
            "source_manifest_sha256": _MANIFEST,
            "tool_catalog_sha256": _CATALOG,
            "tool_name": "analyze_javascript_application",
            "tool_qualification": "fixed_fixture_only_not_target_qualified",
            "manual_operator_opt_in": True,
            "execution_enabled": False,
            "core_approval": False,
            "runtime_qualified": False,
            "worker_registered": False,
            "tool_grants": [],
            "review_fingerprint": _digest(row.review_fingerprint),
        }
        return ReaOperatorReviewClaim.model_validate_json(json.dumps(values), strict=True)

    @classmethod
    def _snapshot(
        cls, row: ReaOperatorTargetSelectionRecord, *, revoked: bool
    ) -> ReaOperatorTargetSnapshot:
        if type(revoked) is not bool:
            raise ValueError("rea_operator_target_revocation_invalid")
        claim = cls._reconstruct(row)
        return ReaOperatorTargetSnapshot(
            snapshot_id=claim.snapshot_id,
            receipt_sha256=claim.receipt_sha256,
            content_sha256=claim.content_sha256,
            review_fingerprint=claim.review_fingerprint,
            file_count=claim.file_count,
            total_bytes=claim.total_bytes,
            selected_at=row.selected_at,
            currently_selected=not revoked,
        )

    def select(
        self, review: ReaOperatorReviewClaim, *, principal: OperatorPrincipal
    ) -> tuple[ReaOperatorTargetSnapshot, bool]:
        _require_operator(principal)
        if type(review) is not ReaOperatorReviewClaim:
            raise ValueError("rea_operator_target_review_invalid")
        review = ReaOperatorReviewClaim.model_validate_json(
            json.dumps(review.model_dump(mode="json")), strict=True
        )
        with self.database.transaction() as session:
            row = session.get(ReaOperatorTargetSelectionRecord, review.snapshot_id)
            if row is not None:
                if session.get(ReaOperatorTargetRevocationRecord, review.snapshot_id):
                    raise ValueError("rea_operator_target_revoked")
                saved = self._reconstruct(row)
                if saved != review:
                    raise ValueError("rea_operator_target_selection_conflict")
                return self._snapshot(row, revoked=False), False

            row = ReaOperatorTargetSelectionRecord(
                snapshot_id=review.snapshot_id,
                receipt_sha256=review.receipt_sha256,
                content_sha256=review.content_sha256,
                review_fingerprint=review.review_fingerprint,
                file_count=review.file_count,
                total_bytes=review.total_bytes,
                operator_id=principal.subject,
                execution_authority=False,
                worker_authority=False,
                approval_authority=False,
            )
            session.add(row)
            session.flush()
            return self._snapshot(row, revoked=False), True

    def revoke(self, snapshot_id: str, *, principal: OperatorPrincipal) -> bool:
        _require_operator(principal)
        snapshot_id = _snapshot_id(snapshot_id)
        with self.database.transaction() as session:
            row = session.get(ReaOperatorTargetSelectionRecord, snapshot_id)
            if row is None:
                raise LookupError("rea_operator_target_not_selected")
            self._snapshot(row, revoked=False)
            if session.get(ReaOperatorTargetRevocationRecord, snapshot_id):
                return False
            session.add(
                ReaOperatorTargetRevocationRecord(snapshot_id=snapshot_id, operator_id="ahmad")
            )
            session.flush()
            return True

    def get(
        self, snapshot_id: str, *, principal: OperatorPrincipal
    ) -> ReaOperatorTargetSnapshot | None:
        _require_operator(principal)
        snapshot_id = _snapshot_id(snapshot_id)
        with self.database.read() as session:
            row = session.get(ReaOperatorTargetSelectionRecord, snapshot_id)
            if row is None:
                return None
            revocation = session.get(ReaOperatorTargetRevocationRecord, snapshot_id)
            if revocation is not None and revocation.operator_id != "ahmad":
                raise ValueError("rea_operator_target_revocation_invalid")
            return self._snapshot(row, revoked=revocation is not None)
