"""The prospective sixth admission must see old untagged images as resources."""

from pathlib import Path

import pytest

from qualification import media_reader_resource_preflight_v4 as preflight
from scripts import cleanup_media_native_reader_residual_images as cleanup


def _inventory(monkeypatch, *, image_ids=(), container_ids=()):
    commands = []

    def command(_config: Path, *args: str) -> bytes:
        commands.append(args)
        kind = args[0]
        labelled = any(arg == f"label={cleanup.LABEL}" for arg in args)
        rows = () if labelled else (image_ids if kind == "image" else container_ids)
        return ("\n".join(rows) + ("\n" if rows else "")).encode("ascii")

    monkeypatch.setattr(cleanup, "_command", command)
    return commands


def test_sixth_preflight_refuses_untagged_pinned_image_before_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "RETAINED_ROOT", tmp_path)
    commands = _inventory(monkeypatch, image_ids=(cleanup.IDS[0],))
    called = []
    monkeypatch.setattr(preflight.reader, "_docker_readiness", lambda _root: called.append("ready"))

    with pytest.raises(cleanup.ResidualCleanupRefused, match="removed_image_reappeared"):
        preflight.observe_held(tmp_path)

    assert called == []
    assert any(args[:4] == ("image", "ls", "--all", "--no-trunc") for args in commands)
    assert list(tmp_path.iterdir()) == []


def test_sixth_preflight_rechecks_namespace_after_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "RETAINED_ROOT", tmp_path)
    monkeypatch.setattr(preflight.reader, "_docker_readiness", lambda _root: None)
    listed = 0

    def command(_config: Path, *args: str) -> bytes:
        nonlocal listed
        if args[:2] == ("image", "ls") and f"label={cleanup.LABEL}" not in args:
            listed += 1
            if listed == 2:
                return (cleanup.IDS[0] + "\n").encode("ascii")
        return b""

    monkeypatch.setattr(cleanup, "_command", command)
    with pytest.raises(cleanup.ResidualCleanupRefused, match="removed_image_reappeared"):
        preflight.observe_held(tmp_path)
    assert listed == 2
    assert list(tmp_path.iterdir()) == []


def test_sixth_preflight_clean_namespace_and_fixed_root(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "RETAINED_ROOT", tmp_path)
    commands = _inventory(monkeypatch)
    called = []
    monkeypatch.setattr(preflight.reader, "_docker_readiness", lambda root: called.append(root))
    preflight.observe_held(tmp_path)
    assert called == [tmp_path]
    assert len(commands) == 6
    with pytest.raises(preflight.PreflightRefused, match="root"):
        preflight.observe_held(tmp_path / "other")
    assert called == [tmp_path]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("kind", "after_readiness"),
    [("image", False), ("container", True)],
)
def test_sixth_preflight_refuses_labelled_resource_on_either_side_of_readiness(
    monkeypatch, tmp_path, kind, after_readiness
):
    monkeypatch.setattr(preflight, "RETAINED_ROOT", tmp_path)
    readiness = []
    monkeypatch.setattr(preflight.reader, "_docker_readiness", lambda _root: readiness.append(True))

    def command(_config: Path, *args: str) -> bytes:
        if (
            args[0] == kind
            and f"label={cleanup.LABEL}" in args
            and bool(readiness) == after_readiness
        ):
            value = ("sha256:" if kind == "image" else "") + "a" * 64
            return (value + "\n").encode("ascii")
        return b""

    monkeypatch.setattr(cleanup, "_command", command)
    with pytest.raises(cleanup.ResidualCleanupRefused, match="resource_inventory"):
        preflight.observe_held(tmp_path)
    assert readiness == ([True] if after_readiness else [])
    assert list(tmp_path.iterdir()) == []
