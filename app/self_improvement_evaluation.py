"""Deterministic trusted evaluation of one self-improvement developer candidate."""

from app.schemas import DeveloperRunResult
from app.self_improvement import (
    ImprovementEvaluation,
    ImprovementProposal,
    SelfImprovementError,
)


def evaluate_improvement_candidate(
    proposal: ImprovementProposal,
    *,
    developer_task_id: str,
    developer_attempt_id: str,
    result: DeveloperRunResult,
) -> ImprovementEvaluation:
    """Evaluate only trusted developer/reviewer evidence; never ask a model to self-grade."""

    if result.base_sha != proposal.base_sha:
        raise SelfImprovementError("improvement_evaluation_binding_invalid")

    changed_files = tuple(result.changed_files)
    scope = set(proposal.draft.scope_paths)
    scope_valid = bool(changed_files) and all(path in scope for path in changed_files)

    execution_clean = (
        result.worker_exit_code == 0
        and not result.cancelled
        and result.error is None
        and result.boundary_error is None
        and not result.head_changed
    )

    review = result.independent_review
    review_present = review is not None
    reviewer_high_or_critical = bool(
        review
        and any(finding.severity in {"high", "critical"} for finding in review.report.findings)
    )

    if (
        not result.validation_passed
        or not scope_valid
        or not execution_clean
        or reviewer_high_or_critical
    ):
        status = "rejected_candidate"
    elif not review_present:
        status = "inconclusive"
    else:
        status = "accepted_candidate"

    return ImprovementEvaluation(
        proposal_fingerprint=proposal.proposal_fingerprint,
        developer_task_id=developer_task_id,
        developer_attempt_id=developer_attempt_id,
        validation_passed=result.validation_passed,
        scope_valid=scope_valid,
        execution_clean=execution_clean,
        independent_review_present=review_present,
        reviewer_high_or_critical=reviewer_high_or_critical,
        changed_file_count=len(changed_files),
        status=status,
    )
