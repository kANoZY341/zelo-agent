"""Operator-bound proof that accepted learned revisions passed existing promotion/merge gates."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.auth import OperatorPrincipal
from app.developer_contracts import ValidationResult, WorkerIdentity
from app.models import AuditEvent, SkillLearningPromotionLinkRecord, Task, TaskAttempt
from app.policy import State, action_hash
from app.promotion_contracts import (
    CICheckSnapshot,
    MergeRequest,
    MergeRunResult,
    PromotionCIVerification,
    PromotionCommitResult,
    PromotionPublisherIdentity,
    PromotionPublishResult,
    PromotionRequest,
    PromotionReview,
    PromotionRunResult,
)
from app.schemas import AgentLearnedSkillItem
from app.skill_learning import (
    SkillLearningPromotionLink,
    skill_procedure_artifact_bytes,
    skill_procedure_artifact_path,
    skill_procedure_artifact_sha256,
)
from app.skill_learning_store import SkillLearningProposalStore
from tests import test_skill_learning_store as skill_learning_store_tests
from tests.conftest import AUTH
from tests.test_skill_learning_store import accepted


@pytest.fixture(name="proposal_case")
def proposal_case_fixture(database, settings):
    return skill_learning_store_tests.proposal_case.__wrapped__(database, settings)


REPOSITORY = "kANoZY341/zelo-agent"


def promotion_store(database, store):
    return SkillLearningProposalStore(
        database,
        store.agent_factory,
        store.skill_registry,
        promotion_repository=REPOSITORY,
    )


def successful_lineage(
    database,
    revision,
    *,
    manifest_override=None,
    repository=REPOSITORY,
    reused=False,
):
    artifact = skill_procedure_artifact_bytes(revision)
    path = skill_procedure_artifact_path(revision)
    artifact_sha = skill_procedure_artifact_sha256(revision)
    entry = {
        "path": path,
        "operation": "add",
        "object_type": "file",
        "mode": "100644",
        "size": len(artifact),
        "sha256": artifact_sha,
    }
    if manifest_override:
        entry.update(manifest_override)

    publisher = PromotionPublisherIdentity(
        gateway_version="github-promotion-v1",
        repository=repository,
        gh_executable="/usr/bin/gh",
        gh_executable_sha256="1" * 64,
        gh_config_dir="/trusted/gh-config",
        timeout_seconds=30,
    )
    review = PromotionReview(
        developer_task_id="10000000-0000-4000-8000-000000000001",
        developer_attempt_id="10000000-0000-4000-8000-000000000002",
        source_repository="/trusted/source",
        workspace="developer/attempt/repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        worker_identity=WorkerIdentity(
            implementation="fake",
            config_version="1",
            cli_version="fake",
            capability_profile="test",
            executable="/fake",
            executable_sha256="2" * 64,
            code_mode_host="/fake-host",
            code_mode_host_sha256="3" * 64,
            auth_location="none",
        ),
        validation_identity="4" * 64,
        validation_profile="zelo_core",
        validation_version="1",
        sandbox_version="1",
        validation_passed=True,
        changed_file_count=1,
        candidate_manifest=[entry],
        review_fingerprint="5" * 64,
    )
    promotion = PromotionRequest(
        review=review,
        publisher=publisher,
        repository=repository,
        branch="zelo/promotion/learned-skill-test",
        commit_title="feat: promote learned skill candidate",
        commit_author_name="Zelo Agent",
        commit_author_email="zelo@localhost.invalid",
        commit_timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
        pr_title="feat: promote learned skill candidate",
    )
    validation = [
        ValidationResult(
            identifier=name,
            exit_code=0,
            output="",
            duration_seconds=0,
            completed_at=datetime(2026, 10, 7, 12, 1, tzinfo=UTC),
        )
        for name in ("ruff_check", "ruff_format", "pytest", "git_diff_check")
    ]
    commit = PromotionCommitResult(
        base_sha=review.base_sha,
        tree_sha="6" * 40,
        commit_sha="7" * 40,
        branch=promotion.branch,
        review_fingerprint=review.review_fingerprint,
        validation=validation,
    )
    publication = PromotionPublishResult(
        repository=repository,
        base_branch="main",
        base_sha=review.base_sha,
        branch=promotion.branch,
        commit_sha=commit.commit_sha,
        pr_number=101,
        pr_url=f"https://github.com/{repository}/pull/101",
        pr_head_sha=commit.commit_sha,
        pr_base_sha=review.base_sha,
        branch_created=True,
        pr_created=True,
    )
    promotion_result = PromotionRunResult(commit=commit, publication=publication)
    promotion_payload = {
        "developer_task_id": review.developer_task_id,
        "developer_attempt_id": review.developer_attempt_id,
        "target_base_branch": "main",
        "branch": promotion.branch,
        "commit_title": promotion.commit_title,
        "commit_message": promotion.commit_message,
        "pr_title": promotion.pr_title,
        "pr_body": promotion.pr_body,
    }
    promotion_hash = action_hash(
        "promotion_run",
        promotion_payload,
        300.0,
        1,
        promotion.model_dump(mode="json"),
    )

    with database.transaction() as session:
        promotion_task = Task(
            kind="promotion_run",
            payload=promotion_payload,
            timeout_seconds=300.0,
            status=State.SUCCEEDED,
            action_version=1,
            action_hash=promotion_hash,
            request_hash=hashlib.sha256(b"learned-promotion-source").hexdigest(),
            promotion_spec=promotion.model_dump(mode="json"),
            result=promotion_result.model_dump(mode="json"),
        )
        session.add(promotion_task)
        session.flush()
        session.add(
            TaskAttempt(
                task_id=promotion_task.id,
                number=1,
                outcome=State.SUCCEEDED,
                result=promotion_result.model_dump(mode="json"),
                ended_at=datetime.now(UTC),
            )
        )
        session.flush()
        promotion_task_id = promotion_task.id

    check = CICheckSnapshot(
        check_run_id=123,
        name="checks",
        app_id=15368,
        app_slug="github-actions",
        status="completed",
        conclusion="success",
        completed_at=datetime(2026, 10, 7, 12, 30, tzinfo=UTC),
    )
    ci = PromotionCIVerification(
        repository=repository,
        pr_number=publication.pr_number,
        base_branch="main",
        base_sha=review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        required_checks=["checks"],
        checks=[check],
        verified_at=datetime(2026, 10, 7, 12, 31, tzinfo=UTC),
    )
    merge = MergeRequest(
        promotion_task_id=promotion_task_id,
        promotion_action_version=1,
        promotion_action_hash=promotion_hash,
        promotion=promotion,
        publication=publication,
        ci=ci,
        merge_commit_title=promotion.pr_title,
        merge_commit_message=promotion.pr_body,
    )
    merge_result = MergeRunResult(
        repository=repository,
        pr_number=publication.pr_number,
        base_branch="main",
        base_sha=review.base_sha,
        head_branch=promotion.branch,
        head_sha=publication.pr_head_sha,
        merge_method="squash",
        merge_commit_sha="8" * 40,
        reused=reused,
    )
    merge_payload = {"promotion_task_id": promotion_task_id}
    merge_hash = action_hash(
        "merge_run",
        merge_payload,
        120.0,
        1,
        merge.model_dump(mode="json"),
    )
    with database.transaction() as session:
        merge_task = Task(
            kind="merge_run",
            payload=merge_payload,
            timeout_seconds=120.0,
            status=State.SUCCEEDED,
            action_version=1,
            action_hash=merge_hash,
            request_hash=hashlib.sha256(b"learned-merge-source").hexdigest(),
            merge_spec=merge.model_dump(mode="json"),
            result=merge_result.model_dump(mode="json"),
        )
        session.add(merge_task)
        session.flush()
        session.add(
            TaskAttempt(
                task_id=merge_task.id,
                number=1,
                outcome=State.SUCCEEDED,
                result=merge_result.model_dump(mode="json"),
                ended_at=datetime.now(UTC),
            )
        )
        session.flush()
        return merge_task.id


@pytest.mark.parametrize(
    "updates",
    (
        {"promoted": True},
        {
            "promoted": False,
            "promoted_revision_sha256": "a" * 64,
            "promotion_merge_commit_sha": "b" * 40,
        },
    ),
)
def test_learned_skill_projection_requires_complete_promotion_proof(updates):
    base = {
        "proposal_id": "00000000-0000-4000-8000-000000000001",
        "source_skill_id": "research",
        "source_skill_version": 1,
        "procedure_id": "inspect_sources",
        "proposed_version": 1,
        "revision_sha256": "c" * 64,
        "status": "accepted",
        "evidence_count": 1,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(updates)
    with pytest.raises(ValidationError, match="learned_skill_promotion_proof"):
        AgentLearnedSkillItem.model_validate(base)


def test_ahmad_links_exact_successful_promotion_and_projection_stays_inert(
    client, database, proposal_case
):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision)

    link, created = store.record_promotion_link(
        reviewed.proposal.proposal_id,
        merge_task_id,
        OperatorPrincipal(),
    )
    assert created is True
    assert link.proposal_id == reviewed.proposal.proposal_id
    assert link.revision_sha256 == revision.content_sha256
    assert link.artifact_path == skill_procedure_artifact_path(revision)
    assert link.artifact_sha256 == skill_procedure_artifact_sha256(revision)
    assert link.repository == REPOSITORY
    assert link.merge_task_id == merge_task_id
    assert link.runtime_authority is False
    assert store.get_promotion_link(reviewed.proposal.proposal_id) == link

    retried, created = store.record_promotion_link(
        reviewed.proposal.proposal_id,
        merge_task_id,
        OperatorPrincipal(),
    )
    assert created is False
    assert retried == link

    with database.read() as session:
        promoted_events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.task_id == merge_task_id,
                AuditEvent.event == "skill_learning_promoted",
            )
        ).all()
    assert len(promoted_events) == 1
    assert promoted_events[0].details == {
        "proposal_id": reviewed.proposal.proposal_id,
        "link_id": link.link_id,
        "revision_sha256": revision.content_sha256,
        "promotion_task_id": link.promotion_task_id,
        "merge_task_id": merge_task_id,
        "merge_commit_sha": link.merge_commit_sha,
        "runtime_authority": False,
    }

    client.app.state.skill_learning_store = store
    response = client.get(f"/agents/{agent.id}/learned-skills", headers=AUTH)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["status"] == "accepted"
    assert item["promoted"] is True
    assert item["promoted_revision_sha256"] == revision.content_sha256
    assert item["promotion_merge_commit_sha"] == "8" * 40
    assert item["active_revision_sha256"] is None
    assert item["runtime_authority"] is False
    assert revision.instructions not in json.dumps(page, sort_keys=True)


@pytest.mark.parametrize(
    "override",
    [
        {"path": "skills/procedures/other/procedure/v1-" + "0" * 64 + ".json"},
        {"sha256": "0" * 64},
        {"operation": "modify"},
        {"mode": "100755"},
        {"size": 1},
    ],
)
def test_promotion_link_refuses_candidate_manifest_drift(database, proposal_case, override):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision, manifest_override=override)

    with pytest.raises(ValueError, match="skill_learning_promotion_lineage_invalid"):
        store.record_promotion_link(
            reviewed.proposal.proposal_id,
            merge_task_id,
            OperatorPrincipal(),
        )
    assert store.get_promotion_link(reviewed.proposal.proposal_id) is None


def test_promotion_link_refuses_repository_drift_and_unaccepted_proposal(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    proposed, _ = base.submit(agent_instance_id=agent.id, revision=revision, evidence_refs=(ref,))
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision)

    with pytest.raises(ValueError, match="not_accepted_or_stale"):
        store.record_promotion_link(
            proposed.proposal.proposal_id, merge_task_id, OperatorPrincipal()
        )

    reviewed = accepted(base, agent, revision, ref)
    wrong_repo_merge = successful_lineage(database, revision, repository="other/repo")
    with pytest.raises(ValueError, match="promotion_lineage_invalid"):
        store.record_promotion_link(
            reviewed.proposal.proposal_id,
            wrong_repo_merge,
            OperatorPrincipal(),
        )


def test_promotion_link_recomputes_source_action_hashes(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision)

    with database.transaction() as session:
        merge_task = session.get(Task, merge_task_id)
        merge = MergeRequest.model_validate(merge_task.merge_spec)
        promotion_task = session.get(Task, merge.promotion_task_id)
        promotion_task.action_hash = "f" * 64

    with pytest.raises(ValueError, match="skill_learning_promotion_lineage_invalid"):
        store.record_promotion_link(
            reviewed.proposal.proposal_id,
            merge_task_id,
            OperatorPrincipal(),
        )
    assert store.get_promotion_link(reviewed.proposal.proposal_id) is None


def test_promotion_link_recomputes_merge_action_hash(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision)

    with database.transaction() as session:
        session.get(Task, merge_task_id).action_hash = "f" * 64

    with pytest.raises(ValueError, match="skill_learning_promotion_lineage_invalid"):
        store.record_promotion_link(
            reviewed.proposal.proposal_id,
            merge_task_id,
            OperatorPrincipal(),
        )
    assert store.get_promotion_link(reviewed.proposal.proposal_id) is None


def test_promotion_link_accepts_exact_reused_merge_result(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision, reused=True)

    link, created = store.record_promotion_link(
        reviewed.proposal.proposal_id,
        merge_task_id,
        OperatorPrincipal(),
    )
    assert created is True
    assert link.merge_task_id == merge_task_id
    assert link.merge_commit_sha == "8" * 40
    assert link.runtime_authority is False


def forged_link(
    database,
    reviewed,
    revision,
    merge_task_id,
    **overrides,
):
    with database.read() as session:
        merge_task = session.get(Task, merge_task_id)
        merge = MergeRequest.model_validate(merge_task.merge_spec)
        merge_result = MergeRunResult.model_validate(merge_task.result)
        promotion_task = session.get(Task, merge.promotion_task_id)
        promotion = PromotionRequest.model_validate(promotion_task.promotion_spec)
        promotion_result = PromotionRunResult.model_validate(promotion_task.result)

    artifact = skill_procedure_artifact_bytes(revision)
    material = {
        "schema_version": "zelo-skill-learning-promotion-link-v1",
        "proposal_id": reviewed.proposal.proposal_id,
        "proposal_fingerprint": reviewed.proposal.proposal_fingerprint,
        "review_fingerprint": reviewed.review.review_fingerprint,
        "reviewer_id": "ahmad",
        "revision_sha256": revision.content_sha256,
        "artifact_path": skill_procedure_artifact_path(revision),
        "artifact_sha256": skill_procedure_artifact_sha256(revision),
        "artifact_bytes": len(artifact),
        "repository": promotion.repository,
        "promotion_task_id": promotion_task.id,
        "promotion_action_version": promotion_task.action_version,
        "promotion_action_hash": promotion_task.action_hash,
        "promotion_review_fingerprint": promotion.review.review_fingerprint,
        "pr_number": promotion_result.publication.pr_number,
        "pr_head_sha": promotion_result.publication.pr_head_sha,
        "merge_task_id": merge_task.id,
        "merge_action_version": merge_task.action_version,
        "merge_action_hash": merge_task.action_hash,
        "merge_commit_sha": merge_result.merge_commit_sha,
        "runtime_authority": False,
    }
    material.update(overrides)
    fingerprint = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()
    link_id = str(uuid5(NAMESPACE_URL, f"zelo-skill-learning-promotion-link-v1:{fingerprint}"))
    return SkillLearningPromotionLink(
        **material,
        link_id=link_id,
        link_fingerprint=fingerprint,
        linked_at=datetime.now(UTC),
    )


def insert_link_directly(database, link):
    with database.transaction() as session:
        session.execute(
            text(
                """
                INSERT INTO skill_learning_promotion_links (
                    id, proposal_id, reviewer_id, review_fingerprint,
                    revision_sha256, artifact_path, artifact_sha256,
                    artifact_bytes, repository, promotion_task_id,
                    promotion_action_version, promotion_action_hash,
                    promotion_review_fingerprint, pr_number, pr_head_sha,
                    merge_task_id, merge_action_version, merge_action_hash,
                    merge_commit_sha, link_fingerprint, link_json,
                    runtime_authority, created_at
                ) VALUES (
                    :id, :proposal_id, :reviewer_id, :review_fingerprint,
                    :revision_sha256, :artifact_path, :artifact_sha256,
                    :artifact_bytes, :repository, :promotion_task_id,
                    :promotion_action_version, :promotion_action_hash,
                    :promotion_review_fingerprint, :pr_number, :pr_head_sha,
                    :merge_task_id, :merge_action_version, :merge_action_hash,
                    :merge_commit_sha, :link_fingerprint, :link_json,
                    0, :created_at
                )
                """
            ),
            {
                "id": link.link_id,
                "proposal_id": link.proposal_id,
                "reviewer_id": link.reviewer_id,
                "review_fingerprint": link.review_fingerprint,
                "revision_sha256": link.revision_sha256,
                "artifact_path": link.artifact_path,
                "artifact_sha256": link.artifact_sha256,
                "artifact_bytes": link.artifact_bytes,
                "repository": link.repository,
                "promotion_task_id": link.promotion_task_id,
                "promotion_action_version": link.promotion_action_version,
                "promotion_action_hash": link.promotion_action_hash,
                "promotion_review_fingerprint": link.promotion_review_fingerprint,
                "pr_number": link.pr_number,
                "pr_head_sha": link.pr_head_sha,
                "merge_task_id": link.merge_task_id,
                "merge_action_version": link.merge_action_version,
                "merge_action_hash": link.merge_action_hash,
                "merge_commit_sha": link.merge_commit_sha,
                "link_fingerprint": link.link_fingerprint,
                "link_json": link.model_dump_json(),
                "created_at": link.linked_at,
            },
        )


@pytest.mark.parametrize(
    "override",
    (
        {"promotion_action_version": 2},
        {"promotion_action_hash": "f" * 64},
        {"merge_action_version": 2},
        {"merge_action_hash": "e" * 64},
        {"repository": "other/repo"},
        {"pr_number": 999},
        {"pr_head_sha": "9" * 40},
        {"artifact_sha256": "0" * 64},
        {"artifact_bytes": 1},
        {"promotion_review_fingerprint": "d" * 64},
        {"merge_commit_sha": "c" * 40},
    ),
)
def test_database_rejects_self_consistent_forged_promotion_lineage(
    database, proposal_case, override
):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    merge_task_id = successful_lineage(database, revision)
    link = forged_link(database, reviewed, revision, merge_task_id, **override)

    with pytest.raises(IntegrityError, match="not authorized"):
        insert_link_directly(database, link)


def test_database_rejects_forged_promotion_link_review_fingerprint(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    merge_task_id = successful_lineage(database, revision)

    with database.read() as session:
        merge_task = session.get(Task, merge_task_id)
        merge = MergeRequest.model_validate(merge_task.merge_spec)
        merge_result = MergeRunResult.model_validate(merge_task.result)
        promotion_task = session.get(Task, merge.promotion_task_id)
        promotion = PromotionRequest.model_validate(promotion_task.promotion_spec)
        promotion_result = PromotionRunResult.model_validate(promotion_task.result)

    artifact = skill_procedure_artifact_bytes(revision)
    artifact_path = skill_procedure_artifact_path(revision)
    artifact_sha = skill_procedure_artifact_sha256(revision)
    wrong_review = "f" * 64
    assert wrong_review != reviewed.review.review_fingerprint
    link_id = "00000000-0000-4000-8000-000000000099"
    link_fingerprint = "e" * 64
    link_json = json.dumps(
        {
            "schema_version": "zelo-skill-learning-promotion-link-v1",
            "link_id": link_id,
            "proposal_id": reviewed.proposal.proposal_id,
            "proposal_fingerprint": reviewed.proposal.proposal_fingerprint,
            "review_fingerprint": wrong_review,
            "reviewer_id": "ahmad",
            "revision_sha256": revision.content_sha256,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha,
            "artifact_bytes": len(artifact),
            "repository": REPOSITORY,
            "promotion_task_id": promotion_task.id,
            "promotion_action_version": promotion_task.action_version,
            "promotion_action_hash": promotion_task.action_hash,
            "promotion_review_fingerprint": promotion.review.review_fingerprint,
            "pr_number": promotion_result.publication.pr_number,
            "pr_head_sha": promotion_result.publication.pr_head_sha,
            "merge_task_id": merge_task.id,
            "merge_action_version": merge_task.action_version,
            "merge_action_hash": merge_task.action_hash,
            "merge_commit_sha": merge_result.merge_commit_sha,
            "link_fingerprint": link_fingerprint,
            "linked_at": datetime.now(UTC).isoformat(),
            "runtime_authority": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(IntegrityError, match="not authorized"):
        with database.transaction() as session:
            session.execute(
                text(
                    """
                    INSERT INTO skill_learning_promotion_links (
                        id, proposal_id, reviewer_id, review_fingerprint,
                        revision_sha256, artifact_path, artifact_sha256,
                        artifact_bytes, repository, promotion_task_id,
                        promotion_action_version, promotion_action_hash,
                        promotion_review_fingerprint, pr_number, pr_head_sha,
                        merge_task_id, merge_action_version, merge_action_hash,
                        merge_commit_sha, link_fingerprint, link_json,
                        runtime_authority, created_at
                    ) VALUES (
                        :id, :proposal_id, 'ahmad', :review_fingerprint,
                        :revision_sha256, :artifact_path, :artifact_sha256,
                        :artifact_bytes, :repository, :promotion_task_id,
                        :promotion_action_version, :promotion_action_hash,
                        :promotion_review_fingerprint, :pr_number, :pr_head_sha,
                        :merge_task_id, :merge_action_version, :merge_action_hash,
                        :merge_commit_sha, :link_fingerprint, :link_json,
                        0, :created_at
                    )
                    """
                ),
                {
                    "id": link_id,
                    "proposal_id": reviewed.proposal.proposal_id,
                    "review_fingerprint": wrong_review,
                    "revision_sha256": revision.content_sha256,
                    "artifact_path": artifact_path,
                    "artifact_sha256": artifact_sha,
                    "artifact_bytes": len(artifact),
                    "repository": REPOSITORY,
                    "promotion_task_id": promotion_task.id,
                    "promotion_action_version": promotion_task.action_version,
                    "promotion_action_hash": promotion_task.action_hash,
                    "promotion_review_fingerprint": promotion.review.review_fingerprint,
                    "pr_number": promotion_result.publication.pr_number,
                    "pr_head_sha": promotion_result.publication.pr_head_sha,
                    "merge_task_id": merge_task.id,
                    "merge_action_version": merge_task.action_version,
                    "merge_action_hash": merge_task.action_hash,
                    "merge_commit_sha": merge_result.merge_commit_sha,
                    "link_fingerprint": link_fingerprint,
                    "link_json": link_json,
                    "created_at": datetime.now(UTC),
                },
            )


def _direct_link_values(database, reviewed, revision, merge_task_id, **overrides):
    with database.read() as session:
        merge_task = session.get(Task, merge_task_id)
        merge = MergeRequest.model_validate(merge_task.merge_spec)
        merge_result = MergeRunResult.model_validate(merge_task.result)
        promotion_task = session.get(Task, merge.promotion_task_id)
        promotion = PromotionRequest.model_validate(promotion_task.promotion_spec)
        promotion_result = PromotionRunResult.model_validate(promotion_task.result)

    artifact = skill_procedure_artifact_bytes(revision)
    values = {
        "id": "00000000-0000-4000-8000-000000000199",
        "proposal_id": reviewed.proposal.proposal_id,
        "proposal_fingerprint": reviewed.proposal.proposal_fingerprint,
        "reviewer_id": "ahmad",
        "review_fingerprint": reviewed.review.review_fingerprint,
        "revision_sha256": revision.content_sha256,
        "artifact_path": skill_procedure_artifact_path(revision),
        "artifact_sha256": skill_procedure_artifact_sha256(revision),
        "artifact_bytes": len(artifact),
        "repository": REPOSITORY,
        "promotion_task_id": promotion_task.id,
        "promotion_action_version": promotion_task.action_version,
        "promotion_action_hash": promotion_task.action_hash,
        "promotion_review_fingerprint": promotion.review.review_fingerprint,
        "pr_number": promotion_result.publication.pr_number,
        "pr_head_sha": promotion_result.publication.pr_head_sha,
        "merge_task_id": merge_task.id,
        "merge_action_version": merge_task.action_version,
        "merge_action_hash": merge_task.action_hash,
        "merge_commit_sha": merge_result.merge_commit_sha,
        "link_fingerprint": "d" * 64,
        "runtime_authority": False,
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    link_payload = {
        "schema_version": "zelo-skill-learning-promotion-link-v1",
        "link_id": values["id"],
        "proposal_id": values["proposal_id"],
        "proposal_fingerprint": values["proposal_fingerprint"],
        "review_fingerprint": values["review_fingerprint"],
        "reviewer_id": values["reviewer_id"],
        "revision_sha256": values["revision_sha256"],
        "artifact_path": values["artifact_path"],
        "artifact_sha256": values["artifact_sha256"],
        "artifact_bytes": values["artifact_bytes"],
        "repository": values["repository"],
        "promotion_task_id": values["promotion_task_id"],
        "promotion_action_version": values["promotion_action_version"],
        "promotion_action_hash": values["promotion_action_hash"],
        "promotion_review_fingerprint": values["promotion_review_fingerprint"],
        "pr_number": values["pr_number"],
        "pr_head_sha": values["pr_head_sha"],
        "merge_task_id": values["merge_task_id"],
        "merge_action_version": values["merge_action_version"],
        "merge_action_hash": values["merge_action_hash"],
        "merge_commit_sha": values["merge_commit_sha"],
        "link_fingerprint": values["link_fingerprint"],
        "linked_at": values["created_at"].isoformat(),
        "runtime_authority": False,
    }
    values["link_json"] = json.dumps(
        link_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return values


_DIRECT_LINK_INSERT = text(
    """
    INSERT INTO skill_learning_promotion_links (
        id, proposal_id, reviewer_id, review_fingerprint,
        revision_sha256, artifact_path, artifact_sha256,
        artifact_bytes, repository, promotion_task_id,
        promotion_action_version, promotion_action_hash,
        promotion_review_fingerprint, pr_number, pr_head_sha,
        merge_task_id, merge_action_version, merge_action_hash,
        merge_commit_sha, link_fingerprint, link_json,
        runtime_authority, created_at
    ) VALUES (
        :id, :proposal_id, :reviewer_id, :review_fingerprint,
        :revision_sha256, :artifact_path, :artifact_sha256,
        :artifact_bytes, :repository, :promotion_task_id,
        :promotion_action_version, :promotion_action_hash,
        :promotion_review_fingerprint, :pr_number, :pr_head_sha,
        :merge_task_id, :merge_action_version, :merge_action_hash,
        :merge_commit_sha, :link_fingerprint, :link_json,
        0, :created_at
    )
    """
)


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("promotion_action_version", lambda value: value + 1),
        ("promotion_action_hash", lambda _value: "f" * 64),
        ("merge_action_version", lambda value: value + 1),
        ("merge_action_hash", lambda _value: "f" * 64),
        ("artifact_path", lambda value: value + ".forged"),
        ("artifact_sha256", lambda _value: "f" * 64),
        ("artifact_bytes", lambda value: value - 1),
        ("pr_number", lambda value: value + 1),
        ("pr_head_sha", lambda _value: "f" * 40),
    ],
)
def test_database_rejects_forged_promotion_lineage_metadata(database, proposal_case, field, mutate):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    merge_task_id = successful_lineage(database, revision)
    exact = _direct_link_values(database, reviewed, revision, merge_task_id)
    forged = mutate(exact[field])
    assert forged != exact[field]
    values = _direct_link_values(
        database,
        reviewed,
        revision,
        merge_task_id,
        **{field: forged},
    )

    with pytest.raises(IntegrityError, match="not authorized"):
        with database.transaction() as session:
            session.execute(_DIRECT_LINK_INSERT, values)


@pytest.mark.parametrize(
    "missing_field",
    [
        "promotion_action_version",
        "merge_action_version",
        "artifact_sha256",
        "pr_head_sha",
    ],
)
def test_database_rejects_missing_link_json_identity(database, proposal_case, missing_field):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    merge_task_id = successful_lineage(database, revision)
    values = _direct_link_values(database, reviewed, revision, merge_task_id)
    payload = json.loads(values["link_json"])
    payload.pop(missing_field)
    values["link_json"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(IntegrityError, match="not authorized"):
        with database.transaction() as session:
            session.execute(_DIRECT_LINK_INSERT, values)


def test_database_rejects_missing_promotion_manifest_identity(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    merge_task_id = successful_lineage(database, revision)
    values = _direct_link_values(database, reviewed, revision, merge_task_id)

    with database.transaction() as session:
        merge_task = session.get(Task, merge_task_id)
        merge = dict(merge_task.merge_spec)
        promotion_task = session.get(Task, merge["promotion_task_id"])
        promotion = dict(promotion_task.promotion_spec)
        review = dict(promotion["review"])
        review.pop("candidate_manifest")
        promotion["review"] = review
        promotion_task.promotion_spec = promotion

    with pytest.raises(IntegrityError, match="not authorized"):
        with database.transaction() as session:
            session.execute(_DIRECT_LINK_INSERT, values)


def test_promotion_link_records_are_append_only(database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision)
    link, _ = store.record_promotion_link(
        reviewed.proposal.proposal_id,
        merge_task_id,
        OperatorPrincipal(),
    )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text("UPDATE skill_learning_promotion_links SET id = id WHERE id = :id"),
                {"id": link.link_id},
            )
    with pytest.raises(IntegrityError, match="append-only"):
        with database.transaction() as session:
            session.execute(
                text("DELETE FROM skill_learning_promotion_links WHERE id = :id"),
                {"id": link.link_id},
            )
    with database.read() as session:
        assert session.scalar(select(SkillLearningPromotionLinkRecord)).id == link.link_id


def test_operator_api_records_existing_merge_only(client, database, proposal_case):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    merge_task_id = successful_lineage(database, revision)
    client.app.state.skill_learning_store = store

    denied = client.post(
        f"/skill-learning/proposals/{reviewed.proposal.proposal_id}/promotion-link",
        headers={"Authorization": "Bearer invalid"},
        json={"merge_task_id": merge_task_id},
    )
    assert denied.status_code == 401

    response = client.post(
        f"/skill-learning/proposals/{reviewed.proposal.proposal_id}/promotion-link",
        headers=AUTH,
        json={"merge_task_id": merge_task_id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["merge_task_id"] == merge_task_id
    assert payload["runtime_authority"] is False

    fetched = client.get(
        f"/skill-learning/proposals/{reviewed.proposal.proposal_id}/promotion-link",
        headers=AUTH,
    )
    assert fetched.status_code == 200
    assert fetched.json() == payload

    serialized = json.dumps(payload, sort_keys=True)
    assert revision.instructions not in serialized


def test_operator_api_normalizes_unexpected_promotion_link_error(
    client, database, proposal_case, monkeypatch
):
    base, agent, _task, _attempt, revision, ref = proposal_case
    reviewed = accepted(base, agent, revision, ref)
    store = promotion_store(database, base)
    client.app.state.skill_learning_store = store

    def unexpected(*args, **kwargs):
        raise ValueError("/private/host/path must not leak")

    monkeypatch.setattr(store, "record_promotion_link", unexpected)
    response = client.post(
        f"/skill-learning/proposals/{reviewed.proposal.proposal_id}/promotion-link",
        headers=AUTH,
        json={"merge_task_id": "00000000-0000-4000-8000-000000000001"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "skill_learning_promotion_link_refused"
    assert "private" not in response.text
