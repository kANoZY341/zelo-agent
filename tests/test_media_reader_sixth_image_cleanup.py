"""Disposable sixth-image cleanup gates; no test invokes a Docker daemon."""

import json
import sys

import pytest

from scripts import cleanup_media_native_reader_sixth_image as cleanup


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    admission = root / "media-elf-metadata-admission"
    admission.mkdir(mode=0o700)
    (admission / "admission.lock").touch(mode=0o600)
    (admission / "active.marker").touch(mode=0o600)
    audits = []

    def audit(path):
        assert path == root
        audits.append(True)
        return {"proof_sha256": "pinned-proof", "grant_sha256": "pinned-grant"}

    monkeypatch.setattr(cleanup, "_audit", audit)
    monkeypatch.setattr(cleanup, "ROOT", root)
    return root, audits


def test_one_use_receipts_and_exact_image_removal(retained, monkeypatch):
    root, audits = retained
    events = []

    def snapshot(_config, present):
        events.append(("snapshot", present))

    def remove(_config):
        events.append(("remove", cleanup.IMAGE_ID))
        return b"exact image removed"

    result = cleanup._cleanup(
        root,
        snapshot=snapshot,
        remove=remove,
        quiet_window=lambda _: ("2026-10-26T14:00:00Z", "2026-10-26T14:00:02Z"),
    )
    assert result["status"] == "exact-sixth-image-cleaned"
    assert events == [
        ("snapshot", True),
        ("snapshot", True),
        ("snapshot", True),
        ("remove", cleanup.IMAGE_ID),
        ("snapshot", False),
    ]
    assert len(audits) >= 3
    receipt = json.loads(
        (
            root / cleanup.BASE / next((root / cleanup.BASE).iterdir()).name / "completed.json"
        ).read_text()
    )
    assert receipt["pending_sha256"] == cleanup._digest(
        (
            root / cleanup.BASE / next((root / cleanup.BASE).iterdir()).name / "pending.json"
        ).read_bytes()
    )
    assert receipt["image_id"] == cleanup.IMAGE_ID
    assert receipt["cleanup_confirmed"] is True
    assert receipt["another_attempt_authorized"] is False
    assert (root / "media-elf-metadata-admission/active.marker").read_bytes() == b""
    monkeypatch.setattr(
        cleanup, "_snapshot", lambda _config, *, image_present: snapshot(_config, image_present)
    )
    assert cleanup.verify_completed()["completion_sha256"] == result["completion_sha256"]
    assert events[-1] == ("snapshot", False)
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="prior_consumption"):
        cleanup._cleanup(root, snapshot=snapshot, remove=remove)
    assert events.count(("remove", cleanup.IMAGE_ID)) == 1


def test_completion_postcheck_refuses_modified_receipt_and_remaining_image(retained, monkeypatch):
    root, _ = retained
    cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        remove=lambda _: b"removed",
        quiet_window=lambda _: ("2026-10-26T14:00:00Z", "2026-10-26T14:00:02Z"),
    )
    monkeypatch.setattr(
        cleanup, "_snapshot", lambda *_args, **_kwargs: cleanup._refuse("image_remaining")
    )
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="image_remaining"):
        cleanup.verify_completed()
    evidence = next((root / cleanup.BASE).iterdir())
    completion = evidence / "completed.json"
    value = json.loads(completion.read_text())
    value["runtime_authority"] = True
    completion.write_text(json.dumps(value))
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="completion_binding"):
        cleanup.verify_completed()


def test_default_removal_is_exact_and_does_not_prune(retained, monkeypatch):
    root, _ = retained
    calls = []

    def command(_config, *args):
        calls.append(args)
        return b"removed"

    monkeypatch.setattr(cleanup.docker, "_command", command)
    cleanup._cleanup(
        root,
        snapshot=lambda *_: None,
        quiet_window=lambda _: ("2026-10-26T14:00:00Z", "2026-10-26T14:00:02Z"),
    )
    assert calls == [("image", "rm", "--no-prune", cleanup.IMAGE_ID)]


def test_preflight_or_marker_drift_prevents_removal(retained):
    root, _ = retained
    removed = []
    marker = root / "media-elf-metadata-admission/active.marker"
    marker.write_bytes(b"lease")
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="marker"):
        cleanup._cleanup(root, snapshot=lambda *_: None, remove=lambda *_: removed.append(True))
    marker.write_bytes(b"")
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="inventory"):
        cleanup._cleanup(
            root,
            snapshot=lambda *_: cleanup._refuse("resource_inventory"),
            remove=lambda *_: removed.append(True),
        )
    assert removed == []
    assert not (root / cleanup.BASE).exists()


def test_failure_after_pending_cannot_repeat(retained):
    root, _ = retained
    calls = []

    def failed_remove(_config):
        calls.append(True)
        raise RuntimeError("simulated failure")

    with pytest.raises(RuntimeError, match="simulated failure"):
        cleanup._cleanup(
            root,
            snapshot=lambda *_: None,
            remove=failed_remove,
            quiet_window=lambda _: ("2026-10-26T14:00:00Z", "2026-10-26T14:00:02Z"),
        )
    evidence = next((root / cleanup.BASE).iterdir())
    assert {path.name for path in evidence.iterdir()} == {"pending.json"}
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="prior_consumption"):
        cleanup._cleanup(root, snapshot=lambda *_: None, remove=failed_remove)
    assert calls == [True]


def test_snapshot_sees_untagged_image_and_unlabelled_dependents(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    calls = []

    def ids(_config, kind, *, label=True, ancestor=None):
        calls.append((kind, label, ancestor))
        if kind == "image":
            return (cleanup.IMAGE_ID,)
        return ()

    monkeypatch.setattr(cleanup.docker, "_ids", ids)
    monkeypatch.setattr(
        cleanup.docker,
        "_inspect",
        lambda *_: {
            "Id": cleanup.IMAGE_ID,
            "Config": {
                "Labels": {
                    cleanup.docker.LABEL: cleanup.LEASE,
                    cleanup.docker.BUNDLE_LABEL: cleanup.docker.BUNDLE,
                },
                "Env": ["PATH=/nonexistent"],
            },
            "Architecture": "amd64",
            "Os": "linux",
            "Size": 1735854,
            "RepoTags": [],
            "RepoDigests": [],
            "RootFS": {"Type": "layers", "Layers": ["layer"] * 8},
        },
    )
    monkeypatch.setattr(
        cleanup,
        "IMAGE_INSPECT_SHA256",
        cleanup._digest(
            json.dumps(
                {
                    "id": cleanup.IMAGE_ID,
                    "labels": {
                        cleanup.docker.LABEL: cleanup.LEASE,
                        cleanup.docker.BUNDLE_LABEL: cleanup.docker.BUNDLE,
                    },
                    "env": ["PATH=/nonexistent"],
                    "rootfs": {"Type": "layers", "Layers": ["layer"] * 8},
                    "size": 1735854,
                    "repo_tags": [],
                    "repo_digests": [],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ),
    )
    cleanup._snapshot(config, image_present=True)
    assert ("image", True, None) in calls
    assert ("image", False, None) in calls
    assert ("container", False, cleanup.IMAGE_ID) in calls

    def with_dependent(_config, kind, *, label=True, ancestor=None):
        if kind == "container" and ancestor is not None:
            return ("a" * 64,)
        return ids(_config, kind, label=label, ancestor=ancestor)

    monkeypatch.setattr(cleanup.docker, "_ids", with_dependent)
    with pytest.raises(cleanup.SixthImageCleanupRefused, match="dependent"):
        cleanup._snapshot(config, image_present=True)


def test_read_only_inventory_does_not_call_cleanup(retained, monkeypatch, capsys):
    root, _ = retained
    snapshots = []
    monkeypatch.setattr(
        cleanup, "_snapshot", lambda _config, *, image_present: snapshots.append(image_present)
    )
    monkeypatch.setattr(cleanup, "cleanup", lambda: pytest.fail("cleanup called"))
    monkeypatch.setattr(sys, "argv", ["cleanup_media_native_reader_sixth_image.py", "--inventory"])
    assert cleanup.main() == 0
    assert snapshots == [True]
    assert json.loads(capsys.readouterr().out)["image_id"] == cleanup.IMAGE_ID
    assert not (root / cleanup.BASE).exists()
