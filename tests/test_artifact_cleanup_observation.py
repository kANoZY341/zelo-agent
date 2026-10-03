"""Artifact settlement uses harmless temporary bytes and no subprocess."""

import hashlib

import pytest

from app import developer_artifacts as artifacts
from app.developer_process import DeveloperError


@pytest.mark.parametrize("failure", [None, "source", "sealed"])
def test_artifact_attempts_every_close_and_reports_uncertainty(tmp_path, monkeypatch, failure):
    source = tmp_path / "artifact"
    source.write_bytes(b"fixed artifact fixture")
    source.chmod(0o600)
    record = artifacts.ArtifactCleanupObservation()
    original = artifacts.os.close
    closed = []

    def close(fd):
        original(fd)  # Always really close the fixture FD; never leak diagnostics.
        closed.append(fd)
        if failure == ("source" if len(closed) == 1 else "sealed"):
            raise OSError("fixture descriptor-close uncertainty")

    monkeypatch.setattr(artifacts.os, "close", close)
    with artifacts.pin_artifact(
        source,
        approved_path=str(source),
        approved_digest=hashlib.sha256(source.read_bytes()).hexdigest(),
        name="test-artifact",
        cleanup_observation=record,
    ) as pinned:
        pinned.verify()
        assert not record.cleanup_confirmed
    assert len(closed) == 2 and len(set(closed)) == 2
    assert record.cleanup_confirmed is (failure is None)
    with pytest.raises(DeveloperError, match="artifact_observation_reused"):
        with artifacts.pin_artifact(source, name="reuse", cleanup_observation=record):
            pytest.fail("reused record entered")


def test_failed_artifact_identity_still_confirms_only_completed_closes(tmp_path):
    source = tmp_path / "artifact"
    source.write_bytes(b"fixed artifact fixture")
    source.chmod(0o600)
    record = artifacts.ArtifactCleanupObservation()
    with pytest.raises(DeveloperError, match="worker_configuration_changed"):
        with artifacts.pin_artifact(
            source,
            approved_path=str(source),
            approved_digest="0" * 64,
            name="test-artifact",
            cleanup_observation=record,
        ):
            pytest.fail("mismatched artifact yielded")
    assert record.cleanup_confirmed
