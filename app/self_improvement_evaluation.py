"""Deterministic trusted evaluation of one self-improvement developer candidate."""

import hashlib

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
    worker_id: str,
    worker_implementation: str,
    review_context_bound: bool,
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
        and result.protocol_diagnostic is None
        and result.boundary_error is None
        and result.original_error is None
        and result.head == proposal.base_sha
        and not result.head_changed
    )

    review = result.independent_review
    review_present = review is not None
    findings = review.report.findings if review is not None else ()
    reviewer_high_or_critical = any(
        finding.severity in {"high", "critical"} for finding in findings
    )
    acceptance_criteria_proven = review_present and review_context_bound and len(findings) == 0

    if (
        not result.validation_passed
        or not scope_valid
        or not execution_clean
        or reviewer_high_or_critical
    ):
        status = "rejected_candidate"
    elif not acceptance_criteria_proven:
        status = "inconclusive"
    else:
        status = "accepted_candidate"

    return ImprovementEvaluation(
        proposal_fingerprint=proposal.proposal_fingerprint,
        developer_task_id=developer_task_id,
        developer_attempt_id=developer_attempt_id,
        worker_id=worker_id,
        worker_implementation=worker_implementation,
        changed_files=tuple(sorted(changed_files)),
        final_diff_sha256=hashlib.sha256(result.diff.encode("utf-8")).hexdigest(),
        validation_passed=result.validation_passed,
        scope_valid=scope_valid,
        execution_clean=execution_clean,
        independent_review_present=review_present,
        acceptance_criteria_proven=acceptance_criteria_proven,
        reviewer_finding_count=len(findings),
        reviewer_high_or_critical=reviewer_high_or_critical,
        changed_file_count=len(changed_files),
        status=status,
    )
