"""Offline validation of a proposed exact Media run-approval snapshot.

This parser does not fetch GitHub, create a grant, or authorize qualification.
A future caller must obtain fresh authenticated API responses. The confirmed
Managing Director account ID is pinned here, but no run is authorized.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPOSITORY_ID = 1373657980
ISSUE_ID = 5806214593
ISSUE_NUMBER = 166
MD_ACCOUNT_ID = 64309771
API_ROOT = "https://api.github.com/repos/kANoZY341/zelo-agent"
SCHEMA = "zelo-media-native-reader-fifth-run-approval-v1"
DECISION = "authorize-one-fifth-native-media-reader-proof"
MAX_BODY_BYTES = 4096
MAX_LIFETIME = timedelta(hours=24)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ApprovalRefused(ValueError):
    """A proposed approval snapshot is absent, changed, or not exact."""


def _refuse(reason: str) -> None:
    raise ApprovalRefused("reader_native_v3_approval_" + reason)


def _time(value: Any) -> datetime:
    if type(value) is not str or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is None:
        _refuse("time")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _refuse("time")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _refuse("duplicate_key")
        result[key] = value
    return result


def _history_digest(history: list[dict]) -> str:
    raw = json.dumps(history, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_snapshot(
    *,
    repository: dict,
    issue: dict,
    comment: dict,
    expected_comment_id: int,
    md_account_id: int | None,
    expected_root: Path,
    source_head: str,
    source_tree: str,
    proof_history: list[dict],
    now: datetime,
) -> dict:
    """Validate strict API snapshots; returned data is evidence, never a run permit."""
    if type(md_account_id) is not int or md_account_id != MD_ACCOUNT_ID:
        _refuse("md_mapping_missing")
    if type(expected_comment_id) is not int or expected_comment_id <= 0:
        _refuse("comment_id")
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
        _refuse("clock")
    if (
        type(repository) is not dict
        or repository.get("id") != REPOSITORY_ID
        or repository.get("full_name") != "kANoZY341/zelo-agent"
        or repository.get("url") != API_ROOT
        or type(issue) is not dict
        or issue.get("id") != ISSUE_ID
        or issue.get("number") != ISSUE_NUMBER
        or issue.get("repository_url") != API_ROOT
        or type(comment) is not dict
        or type(comment.get("id")) is not int
        or comment["id"] != expected_comment_id
        or comment.get("issue_url") != f"{API_ROOT}/issues/{ISSUE_NUMBER}"
        or comment.get("url") != f"{API_ROOT}/issues/comments/{expected_comment_id}"
        or type(comment.get("user")) is not dict
        or type(comment["user"].get("id")) is not int
        or comment["user"]["id"] != md_account_id
    ):
        _refuse("identity")
    created = _time(comment.get("created_at"))
    updated = _time(comment.get("updated_at"))
    if created != updated or created > now or now - created > MAX_LIFETIME:
        _refuse("comment_time")
    root = str(expected_root)
    if (
        not isinstance(expected_root, Path)
        or not expected_root.is_absolute()
        or expected_root.resolve() != expected_root
        or type(source_head) is not str
        or not HEX40.fullmatch(source_head)
        or type(source_tree) is not str
        or not HEX40.fullmatch(source_tree)
        or type(proof_history) is not list
        or len(proof_history) != 4
        or any(
            type(entry) is not dict
            or set(entry)
            != {"attempt", "policy_sha256", "proof_sha256", "source_head", "source_tree"}
            or type(entry["attempt"]) is not str
            or not re.fullmatch(r"native-reader-proof-[a-z0-9-]{1,48}", entry["attempt"])
            or any(
                type(entry[key]) is not str or not HEX64.fullmatch(entry[key])
                for key in ("policy_sha256", "proof_sha256")
            )
            or any(
                type(entry[key]) is not str or not HEX40.fullmatch(entry[key])
                for key in ("source_head", "source_tree")
            )
            for entry in proof_history
        )
        or len({entry["attempt"] for entry in proof_history}) != 4
    ):
        _refuse("expected_binding")
    body = comment.get("body")
    if type(body) is not str or not 0 < len(body.encode("utf-8")) <= MAX_BODY_BYTES:
        _refuse("body_bound")
    try:
        parsed = json.loads(body, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _refuse("body_json")
    expected = {
        "schema": SCHEMA,
        "decision": DECISION,
        "runtime_root": root,
        "source_head": source_head,
        "source_tree": source_tree,
        "proof_history": proof_history,
        "proof_history_sha256": _history_digest(proof_history),
        "attempts_used_before": 4,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
    }
    if type(parsed) is not dict or set(parsed) != set(expected) | {"expires_utc"}:
        _refuse("body_shape")
    if any(
        type(parsed[key]) is not type(value) or parsed[key] != value
        for key, value in expected.items()
    ):
        _refuse("body_binding")
    expiry = _time(parsed["expires_utc"])
    if not created < expiry <= created + MAX_LIFETIME or now >= expiry:
        _refuse("expired")
    return {
        "schema": "zelo-media-native-reader-v3-approval-snapshot-v1",
        "repository_id": REPOSITORY_ID,
        "issue_id": ISSUE_ID,
        "comment_id": expected_comment_id,
        "md_account_id": md_account_id,
        "comment_created_utc": comment["created_at"],
        "comment_updated_utc": comment["updated_at"],
        "comment_body": body,
        "comment_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "checked_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_root": root,
        "source_head": source_head,
        "source_tree": source_tree,
        "proof_history_sha256": expected["proof_history_sha256"],
    }


def fetch_validated_snapshot(
    *,
    get: Callable[[str], dict],
    expected_comment_id: int,
    md_account_id: int | None,
    expected_root: Path,
    source_head: str,
    source_tree: str,
    proof_history: list[dict],
    clock: Callable[[], datetime],
) -> dict:
    """Read fixed GitHub resources and validate evidence, without granting a run."""
    if type(expected_comment_id) is not int or expected_comment_id <= 0:
        _refuse("comment_id")
    base = "/repos/kANoZY341/zelo-agent"
    endpoints = (
        base,
        f"{base}/issues/{ISSUE_NUMBER}",
        f"{base}/issues/comments/{expected_comment_id}",
    )
    responses = []
    for endpoint in endpoints:
        try:
            response = get(endpoint)
        except Exception:
            _refuse("fetch_failed")
        if type(response) is not dict:
            _refuse("fetch_shape")
        responses.append(response)
    return validate_snapshot(
        repository=responses[0],
        issue=responses[1],
        comment=responses[2],
        expected_comment_id=expected_comment_id,
        md_account_id=md_account_id,
        expected_root=expected_root,
        source_head=source_head,
        source_tree=source_tree,
        proof_history=proof_history,
        now=clock(),
    )


def recheck_snapshot(
    *,
    get: Callable[[str], dict],
    original: dict,
    expected_comment_id: int,
    md_account_id: int | None,
    expected_root: Path,
    source_head: str,
    source_tree: str,
    proof_history: list[dict],
    clock: Callable[[], datetime],
) -> dict:
    """Revalidate a fresh snapshot against prior evidence; this grants nothing."""
    fresh = fetch_validated_snapshot(
        get=get,
        expected_comment_id=expected_comment_id,
        md_account_id=md_account_id,
        expected_root=expected_root,
        source_head=source_head,
        source_tree=source_tree,
        proof_history=proof_history,
        clock=clock,
    )
    if (
        type(original) is not dict
        or set(original) != set(fresh)
        or any(original[key] != value for key, value in fresh.items() if key != "checked_utc")
    ):
        _refuse("comment_drift")
    return fresh


def gh_api_get(endpoint: str) -> dict:
    """Read a pinned GitHub resource with existing CLI credentials, without writing."""
    base = "/repos/kANoZY341/zelo-agent"
    if type(endpoint) is not str or (
        endpoint not in (base, f"{base}/issues/{ISSUE_NUMBER}")
        and re.fullmatch(rf"{base}/issues/comments/[1-9][0-9]*", endpoint) is None
    ):
        _refuse("endpoint")
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        _refuse("fetch_failed")
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 131072:
        _refuse("fetch_failed")
    try:
        response = json.loads(result.stdout, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _refuse("fetch_shape")
    if type(response) is not dict:
        _refuse("fetch_shape")
    return response
