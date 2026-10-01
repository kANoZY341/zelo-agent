import pytest
from pydantic import ValidationError

from app.clip_factory import (
    AuthorizedClipSource,
    ClipFactoryPolicy,
    ClipPolicy,
    ClipSegment,
    build_clip_job,
    clip_fingerprint,
)


def source(**changes):
    values = {
        "source_id": "approved-channel",
        "display_name": "Approved Channel",
        "rights_basis": "written_permission",
        "evidence_reference": "contract://creator-license-001",
    }
    values.update(changes)
    return AuthorizedClipSource(**values)


def test_source_requires_rights_evidence():
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        source(evidence_reference="")


def test_segment_rejects_invalid_window():
    with pytest.raises(ValidationError, match="clip_segment_invalid"):
        ClipSegment(start_ms=20_000, end_ms=10_000)


def test_segment_rejects_over_three_minutes():
    with pytest.raises(ValidationError, match="clip_segment_too_long"):
        ClipSegment(start_ms=0, end_ms=180_001)


def test_disabled_source_fails_closed():
    with pytest.raises(ValueError, match="clip_source_disabled"):
        ClipFactoryPolicy.authorize(
            source(enabled=False),
            ClipPolicy(),
            ClipSegment(start_ms=0, end_ms=30_000),
        )


def test_policy_enforces_duration_bounds():
    policy = ClipPolicy(min_duration_ms=20_000, max_duration_ms=45_000)
    with pytest.raises(ValueError, match="clip_too_short"):
        ClipFactoryPolicy.authorize(
            source(),
            policy,
            ClipSegment(start_ms=0, end_ms=15_000),
        )
    with pytest.raises(ValueError, match="clip_too_long"):
        ClipFactoryPolicy.authorize(
            source(),
            policy,
            ClipSegment(start_ms=0, end_ms=50_000),
        )


def test_build_job_binds_deterministic_duplicate_fingerprint():
    segment = ClipSegment(start_ms=15_000, end_ms=60_000)
    policy = ClipPolicy()
    job = build_clip_job(
        source=source(),
        source_video_id="video-123",
        segment=segment,
        policy=policy,
    )
    assert job.fingerprint == clip_fingerprint(
        source_id="approved-channel",
        source_video_id="video-123",
        segment=segment,
        target="youtube_shorts",
    )
    assert job.status == "discovered"


def test_fingerprint_changes_when_segment_changes():
    first = ClipSegment(start_ms=0, end_ms=30_000)
    second = ClipSegment(start_ms=1_000, end_ms=31_000)
    assert clip_fingerprint(
        source_id="approved-channel",
        source_video_id="video-123",
        segment=first,
        target="youtube_shorts",
    ) != clip_fingerprint(
        source_id="approved-channel",
        source_video_id="video-123",
        segment=second,
        target="youtube_shorts",
    )


def test_policy_is_zero_touch_by_contract():
    with pytest.raises(ValidationError, match="clip_factory_requires_auto_publish"):
        ClipPolicy(auto_publish=False)
