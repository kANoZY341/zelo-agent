"""Controlled local fixtures only; no installed browser or runtime activation."""

import dataclasses
import os
import shutil
import socket
import stat

import pytest

from qualification import artifacts as a

ROLES = (("browser", "bin/browser"), ("controller", "bin/controller"))
POLICY = a.ArtifactPolicy()


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    (root / "bin").mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    for name in ("browser", "controller"):
        path = root / "bin" / name
        path.write_bytes(b"fixture executable\n")
        path.chmod(0o755)
    (root / "data").write_bytes(b"asset")
    (root / "data").chmod(0o644)
    return root


def capture(bundle, roles=ROLES, policy=POLICY):
    return a.capture_manifest(bundle, roles, policy)


def test_determinism_relocation_and_immutable(bundle, tmp_path):
    expected = capture(bundle)
    copy = tmp_path / "copy"
    shutil.copytree(bundle, copy)
    os.utime(copy / "data", ns=(123, 456))
    assert capture(copy, tuple(reversed(ROLES))) == expected
    assert capture(copy).digest == expected.digest
    assert a.verify_manifest(copy, ROLES, POLICY, expected, expected.digest) == expected
    assert tuple(f.path for f in expected.files) == ("bin/browser", "bin/controller", "data")
    with pytest.raises(dataclasses.FrozenInstanceError):
        expected.version = "modified"


@pytest.mark.parametrize(
    "change", ["bytes", "extra", "missing", "rename", "mode", "exec", "dir", "empty"]
)
def test_exact_mismatch(bundle, change):
    expected = capture(bundle)
    path = bundle / "data"
    if change == "bytes":
        path.write_bytes(b"other")
    elif change == "extra":
        (bundle / "extra").write_bytes(b"extra")
    elif change == "missing":
        path.unlink()
    elif change == "rename":
        path.rename(bundle / "renamed")
    elif change == "mode":
        path.chmod(0o600)
    elif change == "exec":
        (bundle / "bin/browser").chmod(0o744)
    elif change == "dir":
        (bundle / "bin").chmod(0o700)
    else:
        (bundle / "empty").mkdir()
    with pytest.raises(ValueError, match="mismatch"):
        a.verify_manifest(bundle, ROLES, POLICY, expected, expected.digest)


def test_policy_role_and_expected_identity(bundle):
    expected = capture(bundle)
    policy = dataclasses.replace(POLICY, max_files=10)
    roles = (("browser", "bin/controller"), ("controller", "bin/browser"))
    for changed_roles, changed_policy in [(roles, POLICY), (ROLES, policy)]:
        assert capture(bundle, changed_roles, changed_policy).digest != expected.digest
        with pytest.raises(ValueError, match="mismatch"):
            a.verify_manifest(bundle, changed_roles, changed_policy, expected, expected.digest)
    for digest in ["0" * 64, "bad", True]:
        with pytest.raises(ValueError):
            a.verify_manifest(bundle, ROLES, POLICY, expected, digest)


@pytest.mark.parametrize(
    "path",
    [
        "/bin/browser",
        "../browser",
        "bin/../browser",
        "bin//browser",
        "./bin/browser",
        "",
        "bin/",
        "bin\\browser",
        "bin/\x00browser",
    ],
)
def test_invalid_role_paths(bundle, path):
    with pytest.raises(ValueError):
        capture(bundle, (("browser", path),))


@pytest.mark.parametrize(
    "roles",
    [
        (),
        (("Browser", "data"),),
        (("1role", "data"),),
        (("a.b", "data"),),
        (("a", "data"), ("a", "data")),
        (("a",),),
        ((True, "data"),),
        (("a", "data"),) * 33,
        [("a", "data")],
    ],
)
def test_invalid_roles(bundle, roles):
    with pytest.raises(ValueError):
        capture(bundle, roles)


@pytest.mark.parametrize("path", ["absent", "bin", "data"])
def test_required_executable(bundle, path):
    with pytest.raises(ValueError, match="required_executable"):
        capture(bundle, (("browser", path),))


@pytest.mark.parametrize(
    "kind",
    [
        "root",
        "file",
        "directory",
        "hardlink",
        "fifo",
        "socket",
        "writable_file",
        "writable_dir",
        "writable_root",
    ],
)
def test_unsafe_objects(bundle, tmp_path, kind):
    sock = None
    root = bundle
    if kind == "root":
        root = tmp_path / "alias"
        root.symlink_to(bundle, target_is_directory=True)
    elif kind in ("file", "directory"):
        (bundle / "alias").symlink_to(bundle / ("data" if kind == "file" else "bin"))
    elif kind == "hardlink":
        os.link(bundle / "data", bundle / "alias")
    elif kind == "fifo":
        os.mkfifo(bundle / "pipe")
    elif kind == "socket":
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(bundle / "socket"))
        except PermissionError:
            sock.close()
            pytest.skip("environment forbids controlled Unix socket bind")
    else:
        target = {
            "writable_file": bundle / "data",
            "writable_dir": bundle / "bin",
            "writable_root": bundle,
        }[kind]
        target.chmod(0o777)
    try:
        with pytest.raises(ValueError):
            capture(root)
    finally:
        if sock:
            sock.close()


@pytest.mark.parametrize("field", list(a.ArtifactPolicy.__dataclass_fields__))
@pytest.mark.parametrize(
    "value", [True, False, 0, -1, 1.5, float("inf"), float("nan"), None, 2**50]
)
def test_policy_invalid(field, value):
    with pytest.raises(ValueError):
        a.ArtifactPolicy(**{field: value})


@pytest.mark.parametrize(
    "policy",
    [
        a.ArtifactPolicy(max_files=2),
        a.ArtifactPolicy(max_depth=1),
        a.ArtifactPolicy(max_file_bytes=4),
        a.ArtifactPolicy(max_total_bytes=20),
        a.ArtifactPolicy(max_entries=3),
    ],
)
def test_bounds(bundle, policy):
    with pytest.raises(ValueError, match="limit"):
        capture(bundle, policy=policy)


def test_empty_directory_inventory_bound(bundle):
    for index in range(5):
        (bundle / str(index)).mkdir()
    with pytest.raises(ValueError, match="entry_limit"):
        capture(bundle, policy=a.ArtifactPolicy(max_entries=5))


@pytest.mark.parametrize(
    "mutation", ["bytes", "replace", "add", "remove", "chmod", "late_bytes", "root_replace"]
)
def test_real_mutation_during_hash(bundle, monkeypatch, mutation):
    original = os.read
    fired = False
    reads = 0

    def read(fd, size):
        nonlocal fired, reads
        block = original(fd, size)
        reads += 1
        # Third read occurs after the first file has passed its local checks.
        if block and not fired and (mutation != "late_bytes" or reads >= 3):
            fired = True
            path = bundle / "bin/browser"
            if mutation in ("bytes", "late_bytes"):
                path.write_bytes(b"changed executable\n")
            elif mutation == "replace":
                replacement = bundle / "replacement"
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o755)
                replacement.replace(path)
            elif mutation == "add":
                (bundle / "new").write_bytes(b"new")
            elif mutation == "remove":
                (bundle / "data").unlink()
            elif mutation == "chmod":
                path.chmod(0o700)
            else:
                bundle.rename(bundle.with_name("old"))
                bundle.mkdir()
        return block

    monkeypatch.setattr(a.os, "read", read)
    with pytest.raises(ValueError):
        capture(bundle)
    assert fired


def test_descriptors_closed_on_failure(bundle, monkeypatch):
    # Track descriptors acquired by this module, without reading /proc or system dirs.
    opened = set()
    real_open, real_close = os.open, os.close

    def tracked_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.add(fd)
        return fd

    def tracked_close(fd):
        real_close(fd)
        opened.remove(fd)

    monkeypatch.setattr(a.os, "open", tracked_open)
    monkeypatch.setattr(a.os, "close", tracked_close)
    for _ in range(3):
        with pytest.raises(ValueError):
            capture(bundle, policy=a.ArtifactPolicy(max_file_bytes=1))
        assert not opened

    def failed_read(*args):
        raise OSError("controlled read failure")

    monkeypatch.setattr(a.os, "read", failed_read)
    with pytest.raises(ValueError, match="unavailable"):
        capture(bundle)
    assert not opened


@pytest.mark.parametrize("mode", [stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_nonregular_metadata_rejected_before_open(bundle, monkeypatch, mode):
    real_stat, real_open = os.stat, os.open

    def metadata(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == "data" and kwargs.get("dir_fd") is not None:
            fields = list(result)
            fields[0] = mode | 0o600
            return os.stat_result(fields)
        return result

    def guarded_open(path, *args, **kwargs):
        assert path != "data", "nonregular artifact must never be opened"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(a.os, "stat", metadata)
    monkeypatch.setattr(a.os, "open", guarded_open)
    with pytest.raises(ValueError, match="unsafe"):
        capture(bundle)


def test_open_flags_and_preopen_replacement(bundle, monkeypatch):
    real_open = os.open
    changed = False

    def replaced_open(path, flags, *args, **kwargs):
        nonlocal changed
        for flag in (os.O_NOFOLLOW, os.O_NONBLOCK, os.O_CLOEXEC):
            assert flags & flag
        if path == "browser":
            changed = True
            (bundle / "bin/browser").unlink()
            os.mkfifo(bundle / "bin/browser")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(a.os, "open", replaced_open)
    with pytest.raises(ValueError, match="changed"):
        capture(bundle)
    assert changed


def test_directory_mutation_after_its_local_scan(bundle, monkeypatch):
    real_read = os.read

    def mutate(fd, size):
        block = real_read(fd, size)
        if block == b"asset":
            (bundle / "bin/late").write_bytes(b"late")
        return block

    monkeypatch.setattr(a.os, "read", mutate)
    with pytest.raises(ValueError, match="changed"):
        capture(bundle)


@pytest.mark.parametrize("suffix", ["/", "/.", "/../alias"])
def test_symlink_root_spelling(bundle, tmp_path, suffix):
    alias = tmp_path / "alias"
    alias.symlink_to(bundle, target_is_directory=True)
    with pytest.raises(ValueError):
        capture(str(alias) + suffix)
