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
    """Require an unmodified tracked tree at the exact Git commit used to run."""
    if not isinstance(root, Path):
        _refuse("root")
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(HOST_ROOT),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        clean = subprocess.run(
            ["/usr/bin/git", "status", "--porcelain", "--untracked-files=no"],
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
    except (OSError, subprocess.SubprocessError):
        _refuse("source")
    if clean.stdout or clean.stderr or identity.stderr or len(identity.stdout) != 82:
        _refuse("source")
    try:
        head, tree, empty = identity.stdout.decode("ascii").split("\n")
    except (UnicodeError, ValueError):
        _refuse("source")
    if empty or not HEX40.fullmatch(head) or not HEX40.fullmatch(tree):
        _refuse("source")
    return head, tree


def _read_private(path: Path) -> bytes:
    if path != DECISION_PATH:
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
        if (
            len(raw) != before.st_size
            or any(getattr(before, field) != getattr(after, field) for field in stable)
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
