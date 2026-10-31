"""Read a short-lived one-use operator decision for the fixed Stage S input."""

import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

DECISION_PATH = Path("/home/zelo/zelo/media-source-snapshot-stage-s-decision.json")
HEADER_DECISION_PATH = Path("/home/zelo/zelo/media-source-snapshot-stage-h-decision.json")
HEADER_MINIMUM_SECONDS = 120
FOLLOWUP_DECISION_PATH = Path("/home/zelo/zelo/media-source-snapshot-stage-s2-decision.json")
FOLLOWUP_MINIMUM_SECONDS = 120
HOST_ROOT = Path("/home/zelo/zelo")
MAX_BYTES = 8192
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class DecisionRefused(ValueError):
    """The input decision is missing, stale, changed or outside the fixed scope."""


def _refuse(code: str) -> None:
    raise DecisionRefused("media_snapshot_decision_" + code) from None


def source_identity(root: Path) -> tuple[str, str]:
    """Hash every checked-out blob, including entries hidden by Git index flags."""
    if not isinstance(root, Path):
        _refuse("root")
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        clean = subprocess.run(
            ["/usr/bin/git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=True,
            env=environment,
        )
        identity = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD", "HEAD^{tree}"],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=True,
            env=environment,
        )
        entries = subprocess.run(
            ["/usr/bin/git", "ls-tree", "-r", "-z", "HEAD"],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=True,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        _refuse("source")
    if (
        clean.stdout
        or clean.stderr
        or identity.stderr
        or entries.stderr
        or len(identity.stdout) != 82
        or not 0 < len(entries.stdout) <= 524288
    ):
        _refuse("source")
    try:
        head, tree, empty = identity.stdout.decode("ascii").split("\n")
    except (UnicodeError, ValueError):
        _refuse("source")
    if empty or not HEX40.fullmatch(head) or not HEX40.fullmatch(tree):
        _refuse("source")
    rows = entries.stdout.split(b"\0")
    if rows[-1] != b"" or not 0 < len(rows) - 1 <= 5000:
        _refuse("source_tree")
    total = 0
    seen = set()
    for row in rows[:-1]:
        try:
            header, name = row.split(b"\t", 1)
            mode, kind, expected = header.decode("ascii").split(" ")
            relative = Path(os.fsdecode(name))
        except (UnicodeError, ValueError):
            _refuse("source_tree")
        if (
            mode not in ("100644", "100755")
            or kind != "blob"
            or not HEX40.fullmatch(expected)
            or relative.is_absolute()
            or not relative.parts
            or any(part in (".", "..") for part in relative.parts)
            or relative in seen
        ):
            _refuse("source_tree")
        seen.add(relative)
        fd = -1
        try:
            fd = os.open(
                root / relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            )
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != (0o755 if mode == "100755" else 0o644)
                or not 0 <= before.st_size <= 4194304
            ):
                _refuse("source_file")
            total += before.st_size
            if total > 67108864:
                _refuse("source_bound")
            digest = hashlib.sha1(f"blob {before.st_size}\0".encode("ascii"))
            remaining = before.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    _refuse("source_read")
                digest.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(fd)
            stable = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if digest.hexdigest() != expected or any(
                getattr(before, field) != getattr(after, field) for field in stable
            ):
                _refuse("source_drift")
        except OSError:
            _refuse("source_file")
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    _refuse("source_close")
    return head, tree


def _read_private_at(path: Path, exact_path: Path) -> bytes:
    if path != exact_path:
        _refuse("path")
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_BYTES
        ):
            _refuse("file")
        raw = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(raw) != before.st_size or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            _refuse("drift")
        return raw
    except OSError:
        _refuse("file")
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                _refuse("close")


def _read_private(path: Path) -> bytes:
    return _read_private_at(path, DECISION_PATH)


def _timestamp(value: object) -> datetime:
    if type(value) is not str or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        _refuse("time")
    try:
        parsed = datetime.strptime(value, TIMESTAMP).replace(tzinfo=UTC)
    except ValueError:
        _refuse("time")
    if parsed.strftime(TIMESTAMP) != value:
        _refuse("time")
    return parsed


def validate(
    *,
    path: Path,
    expected_sha256: str,
    root: Path,
    policy_digest: str,
    urls: tuple[str, str],
    caps: tuple[int, int],
    now: datetime | None = None,
) -> bytes:
    """Return exact decision bytes for durable retention before any network use."""
    if type(expected_sha256) is not str or not HEX64.fullmatch(expected_sha256):
        _refuse("digest")
    if type(policy_digest) is not str or not HEX64.fullmatch(policy_digest):
        _refuse("policy")
    if (
        not isinstance(urls, tuple)
        or len(urls) != 2
        or not isinstance(caps, tuple)
        or len(caps) != 2
    ):
        _refuse("scope")
    head, tree = source_identity(root)
    raw = _read_private(path)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _refuse("digest")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _refuse("json")
    expected = {
        "schema": "zelo-media-source-snapshot-stage-s-decision-v1",
        "decision": "authorize-one-stage-s-source-snapshot-input",
        "source_head": head,
        "source_tree": tree,
        "policy_digest": policy_digest,
        "host_root": str(HOST_ROOT),
        "urls": list(urls),
        "body_caps": list(caps),
        "additional_attempts": 1,
        "runtime_authority": False,
    }
    if type(parsed) is not dict or set(parsed) != set(expected) | {"issued_utc", "expires_utc"}:
        _refuse("shape")
    if any(
        type(parsed[key]) is not type(value) or parsed[key] != value
        for key, value in expected.items()
    ):
        _refuse("binding")
    issued = _timestamp(parsed["issued_utc"])
    expiry = _timestamp(parsed["expires_utc"])
    clock = datetime.now(UTC) if now is None else now
    if (
        type(clock) is not datetime
        or clock.tzinfo is None
        or clock.utcoffset() != timedelta(0)
        or not issued <= clock < expiry <= issued + timedelta(minutes=10)
    ):
        _refuse("expired")
    return raw


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse("duplicate")
        result[key] = value
    return result


def validate_header_diagnostic(
    *,
    path: Path,
    expected_sha256: str,
    root: Path,
    policy_digest: str,
    url: str,
    now: datetime | None = None,
) -> bytes:
    """Return a private one-use header-only decision, without Stage S authority."""
    if type(expected_sha256) is not str or not HEX64.fullmatch(expected_sha256):
        _refuse("digest")
    if type(policy_digest) is not str or not HEX64.fullmatch(policy_digest):
        _refuse("policy")
    if type(url) is not str or not url.startswith("https://snapshot.ubuntu.com/"):
        _refuse("scope")
    head, tree = source_identity(root)
    raw = _read_private_at(path, HEADER_DECISION_PATH)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _refuse("digest")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _refuse("json")
    expected = {
        "schema": "zelo-media-source-header-diagnostic-decision-v1",
        "decision": "authorize-one-header-only-snapshot-diagnostic",
        "source_head": head,
        "source_tree": tree,
        "policy_digest": policy_digest,
        "host_root": str(HOST_ROOT),
        "url": url,
        "additional_attempts": 1,
        "body_bytes": 0,
        "runtime_authority": False,
    }
    if type(parsed) is not dict or set(parsed) != set(expected) | {"issued_utc", "expires_utc"}:
        _refuse("shape")
    if any(
        type(parsed[key]) is not type(value) or parsed[key] != value
        for key, value in expected.items()
    ):
        _refuse("binding")
    issued = _timestamp(parsed["issued_utc"])
    expiry = _timestamp(parsed["expires_utc"])
    clock = datetime.now(UTC) if now is None else now
    if (
        type(clock) is not datetime
        or clock.tzinfo is None
        or clock.utcoffset() != timedelta(0)
        or not issued <= clock < expiry <= issued + timedelta(minutes=10)
        or clock + timedelta(seconds=HEADER_MINIMUM_SECONDS) > expiry
    ):
        _refuse("expired")
    return raw


def validate_followup(
    *,
    path: Path,
    expected_sha256: str,
    root: Path,
    policy_digest: str,
    urls: tuple[str, str],
    caps: tuple[int, int],
    now: datetime | None = None,
) -> bytes:
    """Bind a new, one-use Stage S2 input to its own private decision path."""
    if type(expected_sha256) is not str or not HEX64.fullmatch(expected_sha256):
        _refuse("digest")
    if type(policy_digest) is not str or not HEX64.fullmatch(policy_digest):
        _refuse("policy")
    if type(urls) is not tuple or len(urls) != 2 or type(caps) is not tuple or len(caps) != 2:
        _refuse("scope")
    head, tree = source_identity(root)
    raw = _read_private_at(path, FOLLOWUP_DECISION_PATH)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _refuse("digest")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _refuse("json")
    expected = {
        "schema": "zelo-media-source-snapshot-stage-s2-decision-v1",
        "decision": "authorize-one-stage-s2-source-snapshot-input",
        "source_head": head,
        "source_tree": tree,
        "policy_digest": policy_digest,
        "host_root": str(HOST_ROOT),
        "urls": list(urls),
        "body_caps": list(caps),
        "additional_attempts": 1,
        "runtime_authority": False,
    }
    if type(parsed) is not dict or set(parsed) != set(expected) | {"issued_utc", "expires_utc"}:
        _refuse("shape")
    if any(
        type(parsed[key]) is not type(value) or parsed[key] != value
        for key, value in expected.items()
    ):
        _refuse("binding")
    issued = _timestamp(parsed["issued_utc"])
    expiry = _timestamp(parsed["expires_utc"])
    clock = datetime.now(UTC) if now is None else now
    if (
        type(clock) is not datetime
        or clock.tzinfo is None
        or clock.utcoffset() != timedelta(0)
        or not issued <= clock < expiry <= issued + timedelta(minutes=10)
        or clock + timedelta(seconds=FOLLOWUP_MINIMUM_SECONDS) > expiry
    ):
        _refuse("expired")
    return raw
