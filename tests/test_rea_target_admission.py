"""Deterministic inert boundary tests: no REA, Docker, network or Core execution."""

import json
import os
from pathlib import Path

import pytest

from qualification import rea_target_admission as admission
from scripts import admit_rea_target as cli


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    source = tmp_path / "inbox"
    staging = tmp_path / "staging"
    source.mkdir(mode=0o700)
    staging.mkdir(mode=0o700)
    monkeypatch.setattr(admission, "INBOX", source)
    monkeypatch.setattr(admission, "STAGING", staging)
    folder = source / "private-customer-name"
    folder.mkdir(mode=0o700)
    (folder / "index.js").write_bytes(b"export const answer = 42;\n")
    yield folder
    # Read-only snapshots need owner write restored for pytest's own cleanup.
    for root, dirs, _ in os.walk(staging):
        Path(root).chmod(0o700)
        for name in dirs:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(0o700)


def admit(inbox):
    return admission.admit_target(inbox.name, operator_opt_in=True)


def verify(result):
    return admission.verify_target(result["snapshot_id"], result["receipt_sha256"])


def staged(result):
    return admission.STAGING / result["snapshot_id"]


def test_admit_verify_private_inert_snapshot(inbox):
    nested = inbox / "src"
    nested.mkdir()
    for extension in admission.EXTENSIONS:
        (nested / ("input" + extension)).write_text("{}\n")
    original = {p.relative_to(inbox): p.read_bytes() for p in inbox.rglob("*") if p.is_file()}
    result = admit(inbox)
    assert verify(result) == result
    assert inbox.name not in json.dumps(result)
    assert "index.js" not in json.dumps(result)
    assert result["execution_enabled"] is False
    assert result["worker_registered"] is False
    assert result["grants"] == [] and result["core_approval"] is None
    snapshot = staged(result)
    receipt = json.loads((snapshot / "receipt.json").read_bytes())
    assert receipt["limits"] == admission.LIMITS
    assert receipt["provenance"]["selection"] == "operator-fixed-inbox"
    assert receipt["file_count"] == len(original)
    for path in snapshot.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o500 if path.is_dir() else 0o400)
    assert snapshot.stat().st_mode & 0o777 == 0o500
    assert {
        p.relative_to(inbox): p.read_bytes() for p in inbox.rglob("*") if p.is_file()
    } == original
    assert admit(inbox)["snapshot_id"] != result["snapshot_id"]


def test_default_refusal(inbox, monkeypatch, capsys):
    with pytest.raises(admission.AdmissionRefusal, match="explicit_opt_in"):
        admission.admit_target(inbox.name)
    monkeypatch.setattr("sys.argv", ["admit_rea_target", inbox.name])
    assert cli.main() == 2
    assert "explicit_opt_in_required" in capsys.readouterr().out
    assert list(admission.STAGING.iterdir()) == []


def test_cli_admission_and_verification(inbox, monkeypatch, capsys):
    args = ["admit_rea_target", "--allow-offline-target-admission"]
    monkeypatch.setattr("sys.argv", args + [inbox.name])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)["receipt"]
    monkeypatch.setattr(
        "sys.argv",
        args + ["--verify", result["snapshot_id"], "--receipt-sha256", result["receipt_sha256"]],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified_inert"


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".hidden",
        "..",
        "../escape",
        "/etc",
        "a/b",
        "a\\b",
        "name\n",
        "with space",
        "é",
        "x" * 81,
        None,
    ],
)
def test_selection_is_one_safe_name(inbox, name):
    with pytest.raises(admission.AdmissionRefusal):
        admission.admit_target(name, operator_opt_in=True)


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "dir_symlink",
        "hardlink",
        "fifo",
        "socket",
        "device",
        "hidden",
        "binary",
        "nul",
        "archive",
        "empty_dir",
        "unsafe_name",
        "writable_file",
        "writable_dir",
    ],
)
def test_unsafe_entries(inbox, kind):
    path = inbox / "bad.js"
    if kind == "symlink":
        path.symlink_to(inbox / "index.js")
    elif kind == "dir_symlink":
        path.symlink_to(inbox, target_is_directory=True)
    elif kind == "hardlink":
        os.link(inbox / "index.js", path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind in {"socket", "device"}:
        # Mode-based fixture avoids requiring a socket capability in the sandbox.
        original = admission.os.stat

        def fake_stat(name, *args, **kwargs):
            info = original(name, *args, **kwargs)
            if name == "bad.js":
                values = list(info)
                values[0] = 0o140600 if kind == "socket" else 0o020600
                return os.stat_result(values)
            return info

        path.write_bytes(b"x")
        # A device is rejected by the same pre-open nonregular check.
        from unittest.mock import patch

        with patch.object(admission.os, "stat", side_effect=fake_stat):
            with pytest.raises(admission.AdmissionRefusal):
                admit(inbox)
        return
    elif kind == "hidden":
        (inbox / ".env").write_bytes(b"secret")
    elif kind == "binary":
        path.write_bytes(b"\xff\xfe")
    elif kind == "nul":
        path.write_bytes(b"\x00ELF")
    elif kind == "archive":
        (inbox / "archive.zip").write_bytes(b"PK")
    elif kind == "empty_dir":
        path.mkdir()
    elif kind == "unsafe_name":
        (inbox / "bad name.js").write_bytes(b"x")
    elif kind == "writable_file":
        path.write_bytes(b"x")
        path.chmod(0o666)
    else:
        path.mkdir(mode=0o777)
        path.chmod(0o777)
    with pytest.raises(admission.AdmissionRefusal):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == []


@pytest.mark.parametrize("which", ["inbox", "staging", "source", "ancestor"])
def test_symlink_roots_and_ancestors(inbox, tmp_path, monkeypatch, which):
    if which == "source":
        (inbox.parent / "link").symlink_to(inbox, target_is_directory=True)
        with pytest.raises(admission.AdmissionRefusal):
            admission.admit_target("link", operator_opt_in=True)
        return
    target = admission.INBOX if which == "inbox" else admission.STAGING
    link = tmp_path / "link"
    if which == "ancestor":
        link.symlink_to(tmp_path, target_is_directory=True)
        monkeypatch.setattr(admission, "INBOX", link / "inbox")
    else:
        link.symlink_to(target, target_is_directory=True)
        monkeypatch.setattr(admission, which.upper(), link)
    with pytest.raises(admission.AdmissionRefusal):
        admit(inbox)


@pytest.mark.parametrize("limit", ["file", "total", "count", "depth", "entries", "empty"])
def test_limits(inbox, limit):
    if limit == "file":
        (inbox / "index.js").write_bytes(b"a" * (512 * 1024 + 1))
    elif limit == "total":
        for i in range(8):
            (inbox / f"f{i}.js").write_bytes(b"a" * (512 * 1024))
    elif limit == "count":
        for i in range(128):
            (inbox / f"f{i}.js").write_bytes(b"x")
    elif limit == "depth":
        path = inbox
        for _ in range(5):
            path /= "dir"
            path.mkdir()
        (path / "f.js").write_bytes(b"x")
    elif limit == "entries":
        for i in range(256):
            (inbox / f"d{i}").mkdir()
    else:
        (inbox / "index.js").unlink()
    with pytest.raises(admission.AdmissionRefusal):
        admit(inbox)


def test_exact_limits(inbox):
    (inbox / "index.js").unlink()
    for i in range(8):
        (inbox / f"large{i}.js").write_bytes(b"a" * (512 * 1024))
    path = inbox
    for _ in range(4):
        path /= "dir"
        path.mkdir()
    for i in range(120):
        (path / f"empty{i}.js").write_bytes(b"")
    result = admit(inbox)
    assert result["file_count"] == 128
    assert result["total_bytes"] == 4 * 1024 * 1024
    verify(result)


@pytest.mark.parametrize(
    "kind",
    [
        "changed",
        "extra",
        "missing",
        "symlink",
        "hardlink",
        "receipt",
        "receipt_extra",
        "oversized",
        "fifo",
        "mode",
        "coherent_rewrite",
    ],
)
def test_verifier_tampering(inbox, kind):
    result = admit(inbox)
    root = staged(result)
    files = root / "files"
    file = files / "index.js"
    root.chmod(0o700)
    files.chmod(0o700)
    file.chmod(0o600)
    if kind in {"changed", "coherent_rewrite"}:
        file.write_bytes(b"changed")
    elif kind == "extra":
        (files / "extra.js").write_bytes(b"x")
    elif kind == "missing":
        file.unlink()
    elif kind == "symlink":
        file.unlink()
        file.symlink_to(inbox / "index.js")
    elif kind == "hardlink":
        os.link(file, inbox / "other.js")
    elif kind == "receipt":
        (root / "receipt.json").chmod(0o600)
        (root / "receipt.json").write_bytes(b"{}")
    elif kind == "receipt_extra":
        (root / "extra.json").write_bytes(b"{}")
    elif kind == "oversized":
        file.write_bytes(b"x" * (512 * 1024 + 1))
    elif kind == "fifo":
        file.unlink()
        os.mkfifo(file)
    if kind == "coherent_rewrite":
        receipt = admission._json(
            admission._receipt(result["snapshot_id"], {"index.js": b"changed"})
        )
        (root / "receipt.json").chmod(0o600)
        (root / "receipt.json").write_bytes(receipt)
    if kind not in {"missing", "symlink", "fifo", "mode"}:
        file.chmod(0o400)
    (root / "receipt.json").chmod(0o400)
    files.chmod(0o500)
    root.chmod(0o500)
    with pytest.raises(admission.AdmissionRefusal):
        verify(result)


def test_partial_write_cleanup_preserves_other_data(inbox, monkeypatch):
    existing = admission.STAGING / "unrelated"
    existing.mkdir()
    (existing / "keep").write_text("keep")
    original = admission._write

    def broken(parent, name, data):
        original(parent, name, data)
        raise OSError("simulated full disk")

    monkeypatch.setattr(admission, "_write", broken)
    with pytest.raises(admission.AdmissionRefusal, match="filesystem_refusal"):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == [existing]
    assert (existing / "keep").read_text() == "keep"
    assert (inbox / "index.js").read_bytes().startswith(b"export")


def test_change_during_read(inbox, monkeypatch):
    original = admission.os.read
    changed = False

    def racing_read(fd, count):
        nonlocal changed
        data = original(fd, count)
        if not changed:
            changed = True
            (inbox / "index.js").write_bytes(b"tampered")
        return data

    monkeypatch.setattr(admission.os, "read", racing_read)
    with pytest.raises(admission.AdmissionRefusal, match="changed_read"):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == []


def test_change_during_copy_cleanup(inbox, monkeypatch):
    original = admission._populate

    def racing_copy(fd, files):
        original(fd, files)
        (inbox / "index.js").write_bytes(b"tampered")

    monkeypatch.setattr(admission, "_populate", racing_copy)
    with pytest.raises(admission.AdmissionRefusal, match="source_changed"):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == []


def test_no_runtime_import_or_binding():
    import ast

    root = Path(__file__).absolute().parents[1]
    for path in (root / "app").glob("*.py"):
        assert "rea_target_admission" not in path.read_text()
    tree = ast.parse(Path(admission.__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported <= {
        "hashlib",
        "json",
        "os",
        "re",
        "secrets",
        "stat",
        "contextlib",
        "pathlib",
        "collections.abc",
    }


@pytest.mark.parametrize("which", ["INBOX", "STAGING", "ancestor"])
def test_unsafe_root_modes(inbox, monkeypatch, tmp_path, which):
    path = tmp_path if which == "ancestor" else getattr(admission, which)
    original = path.stat().st_mode & 0o777
    path.chmod(0o777)
    try:
        with pytest.raises(admission.AdmissionRefusal):
            admit(inbox)
    finally:
        path.chmod(original)


@pytest.mark.parametrize("phase", ["receipt", "seal", "source_replacement"])
def test_late_failure_cleanup(inbox, monkeypatch, phase):
    if phase == "receipt":
        original = admission._write

        def fail(parent, name, data):
            original(parent, name, data)
            if name == "receipt.json":
                raise OSError("receipt failure")

        monkeypatch.setattr(admission, "_write", fail)
    else:
        original = admission._seal
        replaced = False

        def fail(fd):
            nonlocal replaced
            original(fd)
            if phase == "seal":
                raise OSError("seal failure")
            if not replaced:
                replaced = True
                inbox.rename(inbox.with_name("moved"))
                inbox.mkdir(mode=0o700)

        monkeypatch.setattr(admission, "_seal", fail)
    with pytest.raises(admission.AdmissionRefusal):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == []


def test_replacement_between_stat_and_open(inbox, monkeypatch):
    original = admission.os.open

    def racing_open(name, flags, *args, **kwargs):
        if name == "index.js":
            (inbox / "index.js").unlink()
            (inbox / "index.js").symlink_to(inbox.parent / "outside")
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(admission.os, "open", racing_open)
    with pytest.raises(admission.AdmissionRefusal):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == []


@pytest.mark.parametrize(
    "identity,digest", [("../bad", "0" * 64), ("target-" + "0" * 32, "bad"), (None, None)]
)
def test_verifier_arguments(identity, digest):
    with pytest.raises(admission.AdmissionRefusal):
        admission.verify_target(identity, digest)


def test_identity_collision_preserves_existing_snapshot(inbox, monkeypatch):
    monkeypatch.setattr(admission.secrets, "token_hex", lambda _: "a" * 32)
    first = admit(inbox)
    with pytest.raises(admission.AdmissionRefusal, match="filesystem_refusal"):
        admit(inbox)
    assert verify(first) == first
    assert len(list(admission.STAGING.iterdir())) == 1


def test_admission_preserves_core_registries(inbox):
    from app.agent_factory import BUILTIN_AGENT_TASK_KINDS, default_agent_registry
    from app.capabilities import default_capability_registry
    from app.rea_candidate import rea_candidate_snapshot
    from app.skills import default_skill_registry
    from app.worker_registry import (
        WorkerRegistry,
        descriptor_for_api_worker,
        descriptor_for_browser_worker,
        descriptor_for_computer_navigate_worker,
        descriptor_for_computer_worker,
        descriptor_for_python_worker,
        descriptor_for_research_worker,
        descriptor_for_shell_worker,
    )

    def versions():
        # Real trusted descriptors/authority profiles, inert stand-in backends;
        # no runtime, application lifespan, tool or network operation is started.
        descriptors = (
            descriptor_for_api_worker,
            descriptor_for_browser_worker,
            descriptor_for_computer_navigate_worker,
            descriptor_for_computer_worker,
            descriptor_for_python_worker,
            descriptor_for_research_worker,
            descriptor_for_shell_worker,
        )
        workers = WorkerRegistry((factory(), object()) for factory in descriptors)
        return (
            default_agent_registry().version,
            default_capability_registry().version,
            default_skill_registry().version,
            BUILTIN_AGENT_TASK_KINDS,
            workers.version,
        )

    before = versions()
    verify(admit(inbox))
    assert versions() == before
    candidate = rea_candidate_snapshot()
    assert candidate.tools_available == 139
    assert candidate.tool_grants == ()
    assert candidate.execution_enabled is False


def test_scratch_open_failure_cleanup(inbox, monkeypatch):
    original = admission.os.open

    def fail(name, flags, *args, **kwargs):
        if str(name).startswith("target-"):
            raise OSError("cannot open scratch")
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(admission.os, "open", fail)
    with pytest.raises(admission.AdmissionRefusal, match="filesystem_refusal"):
        admit(inbox)
    assert list(admission.STAGING.iterdir()) == []
