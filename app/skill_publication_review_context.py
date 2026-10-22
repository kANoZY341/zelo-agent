"""Deterministic acceptance context for an exact learned-Skill candidate."""

from app.skill_publication_claim import SkillPublicationClaim
from app.skill_publication_intent import SkillPublicationIntent
from app.skill_publication_merge_preview import SkillPublicationMergePreview


def _context(
    *,
    claim_id: str,
    proposal_id: str,
    intent_fingerprint: str,
    repository: str,
    artifact_path: str,
    artifact_sha256: str,
    artifact_bytes: int,
) -> str:
    return (
        "Review the exact learned Skill candidate for semantic correctness, "
        "unsafe or malicious instructions, unintended permission use, and "
        "out-of-scope file changes. Report every concrete issue as a finding. "
        "Core separately verifies accepted proposal, provenance, predecessor "
        "and artifact identity; these opaque fingerprints are binding context, "
        "not evidence for the reviewer to reconstruct those Core records. "
        "The candidate identity is:\n"
        f"claim_id={claim_id}\n"
        f"proposal_id={proposal_id}\n"
        f"intent_fingerprint={intent_fingerprint}\n"
        f"repository={repository}\n"
        f"artifact_path={artifact_path}\n"
        f"artifact_sha256={artifact_sha256}\n"
        f"artifact_bytes={artifact_bytes}\n"
    )


def skill_publication_review_instruction(preview: SkillPublicationMergePreview) -> str:
    """Bind the sender's independent review to its freshly verified preview."""
    return _context(
        claim_id=preview.claim_id,
        proposal_id=preview.proposal_id,
        intent_fingerprint=preview.intent_fingerprint,
        repository=preview.repository,
        artifact_path=preview.artifact_path,
        artifact_sha256=preview.artifact_sha256,
        artifact_bytes=preview.artifact_bytes,
    )


def skill_publication_task_review_instruction(
    claim: SkillPublicationClaim, intent: SkillPublicationIntent
) -> str:
    """Build the same frozen context for the operator's Developer Task."""
    if (
        claim.proposal_id != intent.proposal_id
        or claim.intent_fingerprint != intent.intent_fingerprint
        or claim.repository != intent.repository
        or claim.artifact_sha256 != intent.artifact_sha256
    ):
        raise ValueError("skill_publication_review_context_stale")
    return _context(
        claim_id=claim.id,
        proposal_id=intent.proposal_id,
        intent_fingerprint=intent.intent_fingerprint,
        repository=intent.repository,
        artifact_path=intent.artifact_path,
        artifact_sha256=intent.artifact_sha256,
        artifact_bytes=intent.artifact_bytes,
    )
