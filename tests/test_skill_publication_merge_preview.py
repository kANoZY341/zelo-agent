"""Offline exclusive skill-publication merge evidence; never an execution grant."""

import pytest

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.memory import MemoryService
from app.schemas import AgentInstanceTransition
from app.skill_publication_claim import SkillPublicationClaimService
from app.skill_publication_intent import prepare_skill_publication_intent
from app.skill_publication_merge_preview import (
    SkillPublicationMergePreview,
    SkillPublicationMergePreviewService,
)
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_intent import accepted_case

OPERATOR = OperatorPrincipal()


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _qualified(database, proposal_case, tmp_path):
    store, reviewed, agent, revision, _ref = accepted_case(database, proposal_case)
    intent = prepare_skill_publication_intent(store, reviewed.proposal.proposal_id, OPERATOR)
    claims = SkillPublicationClaimService(store)
    claim, created = claims.reserve(intent.proposal_id, intent.intent_fingerprint, OPERATOR)
    assert created
    settings, request, _ci = merge_tests.fixture(tmp_path)
    assert settings.promotion_repository == store.promotion_repository
    artifact = request.promotion.review.candidate_manifest[0].model_copy(
        update={
            "path": intent.artifact_path,
            "operation": "add",
            "object_type": "file",
            "mode": "100644",
            "size": intent.artifact_bytes,
            "sha256": intent.artifact_sha256,
        }
    )
    review = request.promotion.review.model_copy(update={"candidate_manifest": [artifact]})
    request = request.model_copy(
        update={"promotion": request.promotion.model_copy(update={"review": review})}
    )
    return store, agent, intent, claim, SkillPublicationMergePreviewService(claims), request


def test_local_preflight_binds_exact_claim_artifact_and_pr_without_external_actions(
    database, proposal_case, tmp_path
):
    store, agent, intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    result = service.preview(claim.id, request, OPERATOR)
    assert isinstance(result, SkillPublicationMergePreview)
    assert result.proposal_id == claim.proposal_id
    assert result.artifact_path == intent.artifact_path
    assert result.artifact_sha256 == intent.artifact_sha256
    assert result.pr_head_sha == request.publication.pr_head_sha
    assert result.pr_base_sha == request.publication.pr_base_sha
    assert result.no_external_merge_or_execution_authority is True
    assert result.runtime_skill_activation_authority is False
    assert store.get_promotion_link(intent.proposal_id) is None
    assert service.preview(claim.id, request, OPERATOR) == result


def test_wrong_operator_and_unknown_claim_refused(database, proposal_case, tmp_path):
    _store, _agent, _intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    with pytest.raises(PermissionError, match="operator_required"):
        service.preview(claim.id, request, "ahmad")
    with pytest.raises(ValueError, match="claim_not_found"):
        service.preview("00000000-0000-4000-8000-000000000001", request, OPERATOR)


@pytest.mark.parametrize(
    "tamper",
    (
        {"path": "skills/unrelated.json"},
        {"size": 50000},
        {"sha256": "f" * 64},
        {"operation": "delete"},
        {"object_type": "symlink"},
        {"mode": "100755"},
    ),
)
def test_artifact_modifications_refused_at_exact_preflight(
    database, proposal_case, tmp_path, tamper
):
    _store, _agent, _intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    changed = request.promotion.review.candidate_manifest[0].model_copy(update=tamper)
    altered_review = request.promotion.review.model_copy(update={"candidate_manifest": [changed]})
    altered = request.model_copy(
        update={"promotion": request.promotion.model_copy(update={"review": altered_review})}
    )
    with pytest.raises(ValueError, match="artifact_changed"):
        service.preview(claim.id, altered, OPERATOR)


def test_unreviewed_extra_file_or_ignored_artifact_refused(database, proposal_case, tmp_path):
    _store, _agent, _intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    item = request.promotion.review.candidate_manifest[0]
    extra = item.model_copy(update={"path": "src/extra.py"})
    reviewed = request.promotion.review.model_copy(
        update={"candidate_manifest": [item, extra], "changed_file_count": 2}
    )
    changed = request.model_copy(
        update={"promotion": request.promotion.model_copy(update={"review": reviewed})}
    )
    with pytest.raises(ValueError, match="manifest_not_exclusive"):
        service.preview(claim.id, changed, OPERATOR)

    ignored = request.promotion.review.ignored_artifact_summary
    new_ignored = [
        {
            "path": "secrets/untracked",
            "object_type": "file",
            "size": 4,
        }
    ]
    assert not ignored
    reviewed = request.promotion.review.model_copy(update={"ignored_artifact_summary": new_ignored})
    changed = request.model_copy(
        update={"promotion": request.promotion.model_copy(update={"review": reviewed})}
    )
    with pytest.raises(ValueError, match="manifest_not_exclusive"):
        service.preview(claim.id, changed, OPERATOR)


@pytest.mark.parametrize("part", ("repository", "ci_repository", "publisher_repository"))
def test_repository_binding_must_match_claim(database, proposal_case, tmp_path, part):
    _store, _agent, _intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    if part == "repository":
        altered = request.model_copy(
            update={"promotion": request.promotion.model_copy(update={"repository": "other/repo"})}
        )
    elif part == "ci_repository":
        altered = request.model_copy(
            update={"ci": request.ci.model_copy(update={"repository": "other/repo"})}
        )
    else:
        altered_publisher = request.promotion.publisher.model_copy(
            update={"repository": "other/repo"}
        )
        altered = request.model_copy(
            update={
                "promotion": request.promotion.model_copy(update={"publisher": altered_publisher})
            }
        )
    with pytest.raises(ValueError, match="identity_changed"):
        service.preview(claim.id, altered, OPERATOR)


def test_missing_or_duplicate_required_ci_evidence_refused(database, proposal_case, tmp_path):
    _store, _agent, _intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    for modified in (
        request.ci.model_copy(update={"checks": []}),
        request.ci.model_copy(update={"checks": request.ci.checks * 2}),
        request.ci.model_copy(update={"required_checks": ["checks", "checks"]}),
    ):
        altered = request.model_copy(update={"ci": modified})
        with pytest.raises(ValueError, match="ci_evidence_invalid"):
            service.preview(claim.id, altered, OPERATOR)


def test_paused_agent_invalidates_merge_preview(database, settings, proposal_case, tmp_path):
    store, agent, _intent, claim, service, request = _qualified(database, proposal_case, tmp_path)
    AgentInstanceService(database, store.agent_factory, MemoryService(database)).transition(
        agent.id, AgentInstanceTransition(action="pause")
    )
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        service.preview(claim.id, request, OPERATOR)
