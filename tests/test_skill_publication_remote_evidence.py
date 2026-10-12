"""Fresh, read-only remote PR and CI observation for claimed learned skills."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from app.agent_instances import AgentInstanceService
from app.auth import OperatorPrincipal
from app.config import ROOT, Settings
from app.developer_process import DeveloperError
from app.memory import MemoryService
from app.schemas import AgentInstanceTransition
from app.skill_publication_remote_evidence import (
    SkillPublicationRemoteEvidence,
    SkillPublicationRemoteEvidenceService,
)
from tests import test_promotion_ci as ci_tests
from tests import test_promotion_merge as merge_tests
from tests import test_skill_learning_store as learning_store_tests
from tests.test_skill_publication_merge_preview import _qualified

OPERATOR = OperatorPrincipal()


@pytest.fixture
def proposal_case(database, settings):
    return learning_store_tests.proposal_case.__wrapped__(database, settings)


def _remote_fixture(database, proposal_case, tmp_path, *, checks=None, final_pr=None):
    store, agent, _intent, claim, previews, request = _qualified(database, proposal_case, tmp_path)
    # Reuse the pre-created fixture publisher identity and known local
    # executable. No real GitHub requests or external token is used here.
    settings = Settings(
        _env_file=None,
        runtime_dir=ROOT / "runtime",
        promotion_repository=request.promotion.repository,
        promotion_gh_executable=Path("/usr/bin/true"),
        promotion_gh_config_dir=tmp_path / "gh",
        promotion_required_checks=("checks",),
    )
    repo = request.promotion.repository
    first_pr = merge_tests.open_pr(request)
    final = deepcopy(first_pr)
    final["draft"] = False
    if final_pr is not None:
        final.update(final_pr)
    selected_checks = [ci_tests.check()] if checks is None else checks
    transport = merge_tests.ScriptedTransport(
        [
            ("GET", f"/repos/{repo}/pulls/{request.publication.pr_number}", first_pr),
            (
                "GET",
                f"/repos/{repo}/commits/{request.publication.pr_head_sha}/check-runs?filter=latest&per_page=100",
                {
                    "total_count": len(selected_checks),
                    "check_runs": selected_checks,
                },
            ),
            ("GET", f"/repos/{repo}/pulls/{request.publication.pr_number}", final),
        ]
    )
    service = SkillPublicationRemoteEvidenceService(previews, settings, transport=transport)
    return store, agent, claim, request, transport, service


def test_exact_remote_green_ci_and_two_pr_reads_are_inert(database, proposal_case, tmp_path):
    store, _agent, claim, request, transport, service = _remote_fixture(
        database, proposal_case, tmp_path
    )
    evidence = asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert isinstance(evidence, SkillPublicationRemoteEvidence)
    assert evidence.claim_fingerprint == claim.claim_fingerprint
    assert evidence.merge_request_sha256
    assert evidence.pr_head_sha == request.publication.pr_head_sha
    assert evidence.pr_base_sha == request.publication.pr_base_sha
    assert evidence.current_required_ci_checks_green
    assert evidence.no_write_ever_issued_to_github is True
    assert evidence.current_observation_not_merge_authority is True
    assert evidence.runtime_skill_activation_authority is False
    assert len(transport.calls) == 3
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert transport.responses == []
    assert store.get_promotion_link(claim.proposal_id) is None


@pytest.mark.parametrize(
    ("part", "value"),
    [
        ("state", "closed"),
        ("draft", True),
        ("merged", True),
        ("number", 999),
    ],
)
def test_second_remote_pr_observation_catches_status_and_identity_drift(
    database, proposal_case, tmp_path, part, value
):
    _store, _agent, claim, request, transport, service = _remote_fixture(
        database, proposal_case, tmp_path, final_pr={part: value}
    )
    with pytest.raises(DeveloperError, match="skill_publication_remote_pr_changed"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 3
    assert all(method == "GET" for method, _, _ in transport.calls)


@pytest.mark.parametrize(
    ("location", "value"),
    [
        ("head_sha", "8" * 40),
        ("head_ref", "zelo/promotion/other"),
        ("base_sha", "7" * 40),
        ("base_ref", "other"),
        ("head_repo", "attacker/repository"),
    ],
)
def test_second_remote_pr_observation_denies_mutated_branches(
    database, proposal_case, tmp_path, location, value
):
    _store, _agent, claim, request, _transport, service = _remote_fixture(
        database, proposal_case, tmp_path
    )
    transport = service.transport
    final = transport.responses[2][2]
    if location == "head_sha":
        final["head"]["sha"] = value
    elif location == "head_ref":
        final["head"]["ref"] = value
    elif location == "base_sha":
        final["base"]["sha"] = value
    elif location == "base_ref":
        final["base"]["ref"] = value
    else:
        final["head"]["repo"]["full_name"] = value
    with pytest.raises(DeveloperError, match="skill_publication_remote_pr_changed"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert all(method == "GET" for method, _, _ in transport.calls)


@pytest.mark.parametrize(
    ("check", "error"),
    [
        (ci_tests.check(status="in_progress", conclusion=None), "promotion_ci_not_green"),
        (ci_tests.check(conclusion="failure"), "promotion_ci_not_green"),
        (ci_tests.check(app_id=2), "promotion_ci_app_mismatch"),
    ],
)
def test_bad_live_provider_ci_never_creates_merge_permission(
    database, proposal_case, tmp_path, check, error
):
    _store, _agent, claim, request, transport, service = _remote_fixture(
        database, proposal_case, tmp_path, checks=[check]
    )
    with pytest.raises(DeveloperError, match=error):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 2
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_old_ci_snapshot_must_match_new_provider_report_exactly(database, proposal_case, tmp_path):
    _store, _agent, claim, request, transport, service = _remote_fixture(
        database, proposal_case, tmp_path
    )
    stale = request.ci.checks[0].model_copy(update={"check_run_id": 9000})
    altered = request.model_copy(update={"ci": request.ci.model_copy(update={"checks": [stale]})})
    with pytest.raises(DeveloperError, match="skill_publication_remote_ci_changed"):
        asyncio.run(service.verify(claim.id, altered, OPERATOR))
    assert len(transport.calls) == 2
    assert all(method == "GET" for method, _, _ in transport.calls)


def test_wrong_operator_and_publisher_denied_without_any_remote_calls(
    database, proposal_case, tmp_path
):
    _store, _agent, claim, request, transport, service = _remote_fixture(
        database, proposal_case, tmp_path
    )
    with pytest.raises(PermissionError, match="operator_required"):
        asyncio.run(service.verify(claim.id, request, "ahmad"))
    changed_publisher = request.promotion.publisher.model_copy(
        update={"gh_executable_sha256": "f" * 64}
    )
    altered = request.model_copy(
        update={"promotion": request.promotion.model_copy(update={"publisher": changed_publisher})}
    )
    with pytest.raises(DeveloperError, match="skill_publication_remote_publisher_changed"):
        asyncio.run(service.verify(claim.id, altered, OPERATOR))
    assert transport.calls == []


def test_local_agent_revocation_during_network_checks_refuses_latest_preview(
    database, settings, proposal_case, tmp_path
):
    store, agent, claim, request, transport, service = _remote_fixture(
        database, proposal_case, tmp_path
    )

    original_request = transport.request

    async def revoke_before_final_remote_pr(method, endpoint, body=None):
        if len(transport.calls) == 2:
            AgentInstanceService(database, store.agent_factory, MemoryService(database)).transition(
                agent.id, AgentInstanceTransition(action="pause")
            )
        return await original_request(method, endpoint, body)

    transport.request = revoke_before_final_remote_pr
    with pytest.raises(ValueError, match="candidate_stale_or_not_accepted"):
        asyncio.run(service.verify(claim.id, request, OPERATOR))
    assert len(transport.calls) == 3
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert store.get_promotion_link(claim.proposal_id) is None
