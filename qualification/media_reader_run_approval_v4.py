"""Read-only evidence contract for a possible sixth native Reader decision.

This validates a fresh issue comment against the immutable five-refusal ledger.
It neither creates an admission lease nor grants or runs another attempt.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from qualification import media_reader_run_approval_v3 as previous
from qualification import media_reader_sixth_ledger as ledger

SCHEMA = "zelo-media-native-reader-sixth-run-approval-v1"
DECISION = "authorize-one-sixth-native-media-reader-proof"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ApprovalRefused(ValueError):
    """The proposed sixth decision is absent, stale, changed, or unbound."""


def _refuse(reason: str) -> None:
    raise ApprovalRefused("reader_native_v4_approval_" + reason) from None


def _time(value: object) -> datetime:
    try:
        return previous._time(value)
    except previous.ApprovalRefused:
        _refuse("time")


def _history() -> list[dict[str, str]]:
    """Construct the literal five-entry history from the reviewed pins."""
    pins = ledger.FILE_PINS
    return [
        {
            "attempt": name,
            "policy_sha256": pins[f"media-native-reader/{name}/policy.json"][0],
            "proof_sha256": pins[f"media-native-reader/{name}/cases.json"][0],
            "source_head": head,
            "source_tree": tree,
        }
        for name, head, tree in (
            (ledger.first.ATTEMPT_NAME, ledger.first.REFUSAL_HEAD, ledger.first.REFUSAL_TREE),
            (ledger.v1.SECOND, ledger.v2.SECOND_SOURCE_HEAD, ledger.v2.SECOND_SOURCE_TREE),
            (ledger.v2.THIRD, ledger.v2.THIRD_SOURCE_HEAD, ledger.v2.THIRD_SOURCE_TREE),
            (ledger.v3.FOURTH, ledger.v3.FOURTH_SOURCE_HEAD, ledger.v3.FOURTH_SOURCE_TREE),
            (ledger.fifth.FIFTH, ledger.fifth.SOURCE_HEAD, ledger.fifth.SOURCE_TREE),
        )
    ]


def _verified_ledger(value: object) -> list[dict[str, str]]:
    history = _history()
    expected = {
        "status": "verified-five-refusals-and-reconciled-fence",
        "proof_history": tuple(history),
        "attempts_used": 5,
        "attempt_limit": 5,
        "marker_cleared": True,
        "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
        "reconciliation_sha256": ledger.RECONCILIATION_SHA256,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }
    if (
        type(value) is not dict
        or set(value) != set(expected)
        or any(
            type(value[key]) is not type(item) or value[key] != item
            for key, item in expected.items()
        )
    ):
        _refuse("ledger")
    return history


def validate_snapshot(
    *,
    repository: dict,
    issue: dict,
    comment: dict,
    expected_comment_id: int,
    expected_challenge: str,
    challenge_issued_at: datetime,
    expected_root: Path,
    source_head: str,
    source_tree: str,
    audited_ledger: dict,
    now: datetime,
) -> dict:
    """Parse a new, exact MD comment; returned evidence has no run authority."""
    history = _verified_ledger(audited_ledger)
    if not isinstance(expected_root, Path) or expected_root != ledger.RETAINED_ROOT:
        _refuse("root")
    if type(expected_comment_id) is not int or expected_comment_id <= 0:
        _refuse("comment_id")
    if type(expected_challenge) is not str or HEX64.fullmatch(expected_challenge) is None:
        _refuse("challenge")
    if (
        type(source_head) is not str
        or HEX40.fullmatch(source_head) is None
        or type(source_tree) is not str
        or HEX40.fullmatch(source_tree) is None
    ):
        _refuse("source")
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        _refuse("clock")
    if (
        type(challenge_issued_at) is not datetime
        or challenge_issued_at.tzinfo is None
        or challenge_issued_at.utcoffset() != UTC.utcoffset(challenge_issued_at)
        or challenge_issued_at > now
    ):
        _refuse("challenge_time")
    if (
        type(repository) is not dict
        or repository.get("id") != previous.REPOSITORY_ID
        or repository.get("full_name") != "kANoZY341/zelo-agent"
        or repository.get("url") != previous.API_ROOT
        or type(issue) is not dict
        or issue.get("id") != previous.ISSUE_ID
        or issue.get("number") != previous.ISSUE_NUMBER
        or issue.get("repository_url") != previous.API_ROOT
        or type(comment) is not dict
        or type(comment.get("id")) is not int
        or comment["id"] != expected_comment_id
        or comment.get("issue_url") != f"{previous.API_ROOT}/issues/{previous.ISSUE_NUMBER}"
        or comment.get("url") != f"{previous.API_ROOT}/issues/comments/{expected_comment_id}"
        or type(comment.get("user")) is not dict
        or type(comment["user"].get("id")) is not int
        or comment["user"]["id"] != previous.MD_ACCOUNT_ID
    ):
        _refuse("identity")
    created = _time(comment.get("created_at"))
    updated = _time(comment.get("updated_at"))
    if (
        created != updated
        or created > now
        or created <= challenge_issued_at.replace(microsecond=0)
        or now - created > previous.MAX_LIFETIME
    ):
        _refuse("comment_time")
    body = comment.get("body")
    if type(body) is not str or not 0 < len(body.encode("utf-8")) <= previous.MAX_BODY_BYTES:
        _refuse("body_bound")
    try:
        parsed = json.loads(body, object_pairs_hook=previous._unique_pairs)
    except (ValueError, UnicodeError, RecursionError, previous.ApprovalRefused):
        _refuse("body_json")
    expected = {
        "schema": SCHEMA,
        "decision": DECISION,
        "challenge": expected_challenge,
        "runtime_root": str(expected_root),
        "source_head": source_head,
        "source_tree": source_tree,
        "proof_history": history,
        "proof_history_sha256": previous._history_digest(history),
        "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
        "reconciliation_sha256": ledger.RECONCILIATION_SHA256,
        "attempts_used_before": 5,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
    }
    if type(parsed) is not dict or set(parsed) != set(expected) | {"expires_utc"}:
        _refuse("body_shape")
    if any(
        type(parsed[key]) is not type(item) or parsed[key] != item for key, item in expected.items()
    ):
        _refuse("body_binding")
    expiry = _time(parsed["expires_utc"])
    if not created < expiry <= created + previous.MAX_LIFETIME or now >= expiry:
        _refuse("expired")
    return {
        "schema": "zelo-media-native-reader-v4-approval-snapshot-v1",
        "repository_id": previous.REPOSITORY_ID,
        "issue_id": previous.ISSUE_ID,
        "comment_id": expected_comment_id,
        "challenge_sha256": hashlib.sha256(expected_challenge.encode("ascii")).hexdigest(),
        "challenge_issued_utc": challenge_issued_at.isoformat(timespec="microseconds"),
        "md_account_id": previous.MD_ACCOUNT_ID,
        "comment_created_utc": comment["created_at"],
        "comment_updated_utc": comment["updated_at"],
        "comment_body": body,
        "comment_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "checked_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_root": str(expected_root),
        "source_head": source_head,
        "source_tree": source_tree,
        "proof_history_sha256": expected["proof_history_sha256"],
        "cleanup_completion_sha256": ledger.CLEANUP_SHA256,
        "reconciliation_sha256": ledger.RECONCILIATION_SHA256,
    }


def fetch_validated_snapshot(
    *,
    get: Callable[[str], dict],
    expected_comment_id: int,
    expected_challenge: str,
    challenge_issued_at: datetime,
    source_head: str,
    source_tree: str,
    clock: Callable[[], datetime],
) -> dict:
    """Read fixed GitHub endpoints and the retained ledger, without a grant."""
    if type(expected_comment_id) is not int or expected_comment_id <= 0:
        _refuse("comment_id")
    if type(expected_challenge) is not str or HEX64.fullmatch(expected_challenge) is None:
        _refuse("challenge")
    try:
        before = ledger.audit(ledger.RETAINED_ROOT)
    except (OSError, ValueError, RuntimeError):
        _refuse("ledger")
    base = "/repos/kANoZY341/zelo-agent"
    endpoints = (
        base,
        f"{base}/issues/{previous.ISSUE_NUMBER}",
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
    try:
        after = ledger.audit(ledger.RETAINED_ROOT)
    except (OSError, ValueError, RuntimeError):
        _refuse("ledger")
    if before != after:
        _refuse("ledger_drift")
    return validate_snapshot(
        repository=responses[0],
        issue=responses[1],
        comment=responses[2],
        expected_comment_id=expected_comment_id,
        expected_challenge=expected_challenge,
        challenge_issued_at=challenge_issued_at,
        expected_root=ledger.RETAINED_ROOT,
        source_head=source_head,
        source_tree=source_tree,
        audited_ledger=after,
        now=clock(),
    )


def recheck_snapshot(*, original: dict, **binding: object) -> dict:
    """Refuse a changed comment or ledger between two authenticated reads."""
    fresh = fetch_validated_snapshot(**binding)
    if (
        type(original) is not dict
        or set(original) != set(fresh)
        or any(original[key] != item for key, item in fresh.items() if key != "checked_utc")
    ):
        _refuse("comment_drift")
    return fresh
