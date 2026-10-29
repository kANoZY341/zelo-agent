"""A new ninth decision must bind all eight terminal refusals exactly."""

import copy
import json
from datetime import UTC, datetime

import pytest

from qualification import media_reader_ninth_ledger as ledger
from qualification import media_reader_run_approval_v3 as previous
from qualification import media_reader_run_approval_v7 as approval

NOW = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
ISSUED = datetime(2026, 10, 28, 17, 29, tzinfo=UTC)
CHALLENGE = "c" * 64
COMMENT_ID = 6443000000
HEAD, TREE = "a" * 40, "b" * 40
EIGHTH_PROOF = "9d11825abaa2b707e6d7d928155c958d39f5460a123694c5039a1f7fa69c0bc7"


def audited():
    return {
        "status": "verified-eight-terminal-refusals",
        "proof_history": tuple(approval._history()),
        "attempts_used": 8,
        "attempt_limit": 8,
        "marker_cleared": True,
        "docker_resources_checked": False,
        "another_attempt_authorized": False,
        "runtime_authority": False,
    }


def evidence():
    history = approval._history()
    body = {
        "schema": approval.SCHEMA,
        "decision": approval.DECISION,
        "challenge": CHALLENGE,
        "runtime_root": str(ledger.RETAINED_ROOT),
        "source_head": HEAD,
        "source_tree": TREE,
        "proof_history": history,
        "proof_history_sha256": previous._history_digest(history),
        "eighth_proof_sha256": EIGHTH_PROOF,
        "attempts_used_before": 8,
        "additional_attempts": 1,
        "network": "none",
        "runtime_authority": False,
        "expires_utc": "2026-10-28T19:00:00Z",
    }
    repo = {
        "id": previous.REPOSITORY_ID,
        "full_name": "kANoZY341/zelo-agent",
        "url": previous.API_ROOT,
    }
    issue = {
        "id": previous.ISSUE_ID,
        "number": previous.ISSUE_NUMBER,
        "repository_url": previous.API_ROOT,
    }
    comment = {
        "id": COMMENT_ID,
        "issue_url": f"{previous.API_ROOT}/issues/{previous.ISSUE_NUMBER}",
        "url": f"{previous.API_ROOT}/issues/comments/{COMMENT_ID}",
        "user": {"id": previous.MD_ACCOUNT_ID},
        "created_at": "2026-10-28T17:30:00Z",
        "updated_at": "2026-10-28T17:30:00Z",
        "body": json.dumps(body, sort_keys=True),
    }
    return repo, issue, comment


def validate(repo, issue, comment, *, audited_ledger=None):
    return approval.validate_snapshot(
        repository=repo,
        issue=issue,
        comment=comment,
        expected_comment_id=COMMENT_ID,
        expected_challenge=CHALLENGE,
        challenge_issued_at=ISSUED,
        expected_root=ledger.RETAINED_ROOT,
        source_head=HEAD,
        source_tree=TREE,
        audited_ledger=audited() if audited_ledger is None else audited_ledger,
        now=NOW,
    )


def test_exact_eight_refusal_comment_is_only_evidence():
    snapshot = validate(*evidence())
    assert snapshot["schema"] == "zelo-media-native-reader-v7-approval-snapshot-v1"
    assert snapshot["eighth_proof_sha256"] == EIGHTH_PROOF
    assert "run_authorized" not in snapshot


@pytest.mark.parametrize(
    "change",
    [
        "old_decision",
        "seven_proofs",
        "bad_proof",
        "extra_budget",
        "wrong_author",
        "authority",
        "ledger_grant",
    ],
)
def test_old_or_widened_decision_refuses(change):
    repo, issue, comment = copy.deepcopy(evidence())
    body = json.loads(comment["body"])
    if change == "old_decision":
        body["decision"] = "authorize-one-eighth-native-media-reader-proof"
    elif change == "seven_proofs":
        body["proof_history"] = body["proof_history"][:7]
    elif change == "bad_proof":
        body["eighth_proof_sha256"] = "0" * 64
    elif change == "extra_budget":
        body["additional_attempts"] = 2
    elif change == "wrong_author":
        comment["user"]["id"] += 1
    elif change == "authority":
        body["runtime_authority"] = True
    elif change == "ledger_grant":
        bad = audited()
        bad["another_attempt_authorized"] = True
        with pytest.raises(approval.ApprovalRefused):
            validate(repo, issue, comment, audited_ledger=bad)
        return
    comment["body"] = json.dumps(body, sort_keys=True)
    with pytest.raises(approval.ApprovalRefused):
        validate(repo, issue, comment)
