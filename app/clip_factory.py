"""Rights-gated contracts for Zelo's autonomous Clip Factory."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CLIP_SOURCE_SCHEMA = "zelo-clip-source-v1"
CLIP_POLICY_SCHEMA = "zelo-clip-policy-v1"
CLIP_JOB_SCHEMA = "zelo-clip-job-v1"

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:@-]+$"),
]
RightsBasis = Literal["owned", "licensed", "creative_commons", "written_permission"]
ClipJobStatus = Literal[
    "discovered",
    "ingested",
    "transcribed",
    "selected",
    "rendered",
    "queued",
    "published",
    "rejected",
    "failed",
]


class ClipContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorizedClipSource(ClipContract):
    """A source that the Managing Director has authorized for automated reuse."""

    schema_version: Literal["zelo-clip-source-v1"] = CLIP_SOURCE_SCHEMA
    platform: Literal["youtube"] = "youtube"
    source_id: Identifier
    display_name: str = Field(min_length=1, max_length=160)
    rights_basis: RightsBasis
    evidence_reference: str = Field(min_length=1, max_length=1024)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_evidence(self):
        if not self.evidence_reference.strip():
            raise ValueError("rights_evidence_required")
        return self


class ClipSegment(ClipContract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _validate_window(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("clip_segment_invalid")
        if self.end_ms - self.start_ms > 180_000:
            raise ValueError("clip_segment_too_long")
        return self


class ClipPolicy(ClipContract):
    """Autonomy limits for one zero-touch clipping campaign."""

    schema_version: Literal["zelo-clip-policy-v1"] = CLIP_POLICY_SCHEMA
    min_duration_ms: int = Field(default=15_000, ge=1_000, le=180_000)
    max_duration_ms: int = Field(default=60_000, ge=1_000, le=180_000)
    max_clips_per_source_video: int = Field(default=3, ge=1, le=20)
    max_posts_per_day: int = Field(default=6, ge=1, le=50)
    target: Literal["youtube_shorts"] = "youtube_shorts"
    auto_publish: bool = True
    require_transformative_edit: bool = True
    require_duplicate_check: bool = True

    @model_validator(mode="after")
    def _validate_limits(self):
        if self.min_duration_ms > self.max_duration_ms:
            raise ValueError("clip_duration_bounds_invalid")
        if not self.auto_publish:
            raise ValueError("clip_factory_requires_auto_publish")
        return self


def clip_fingerprint(
    *,
    source_id: str,
    source_video_id: str,
    segment: ClipSegment,
    target: str,
) -> str:
    payload = {
        "source_id": source_id,
        "source_video_id": source_video_id,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "target": target,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class ClipJob(ClipContract):
    schema_version: Literal["zelo-clip-job-v1"] = CLIP_JOB_SCHEMA
    source_id: Identifier
    source_video_id: Identifier
    segment: ClipSegment
    target: Literal["youtube_shorts"] = "youtube_shorts"
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ClipJobStatus = "discovered"

    @model_validator(mode="after")
    def _validate_fingerprint(self):
        expected = clip_fingerprint(
            source_id=self.source_id,
            source_video_id=self.source_video_id,
            segment=self.segment,
            target=self.target,
        )
        if self.fingerprint != expected:
            raise ValueError("clip_fingerprint_mismatch")
        return self


class ClipFactoryPolicy:
    """Fail-closed policy boundary before any autonomous media work starts."""

    @staticmethod
    def authorize(
        source: AuthorizedClipSource,
        policy: ClipPolicy,
        segment: ClipSegment,
    ) -> None:
        if not source.enabled:
            raise ValueError("clip_source_disabled")
        duration = segment.end_ms - segment.start_ms
        if duration < policy.min_duration_ms:
            raise ValueError("clip_too_short")
        if duration > policy.max_duration_ms:
            raise ValueError("clip_too_long")
        if not source.evidence_reference.strip():
            raise ValueError("rights_evidence_required")


def build_clip_job(
    *,
    source: AuthorizedClipSource,
    source_video_id: str,
    segment: ClipSegment,
    policy: ClipPolicy,
) -> ClipJob:
    ClipFactoryPolicy.authorize(source, policy, segment)
    fingerprint = clip_fingerprint(
        source_id=source.source_id,
        source_video_id=source_video_id,
        segment=segment,
        target=policy.target,
    )
    return ClipJob(
        source_id=source.source_id,
        source_video_id=source_video_id,
        segment=segment,
        target=policy.target,
        fingerprint=fingerprint,
    )
