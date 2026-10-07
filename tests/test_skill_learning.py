"""Pure governed Agent skill-learning proposal tests."""

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec
from app.skill_learning import (
    SkillLearningAgentSnapshot,
    SkillLearningEvidenceRef,
    SkillLearningEvidenceSnapshot,
    SkillLearningProposal,
    SkillProcedureDraft,
    SkillProcedureRevision,
    propose_skill_revision,
)
from app.skills import SkillRegistry, SkillSpec


def source_skill(*, version: int = 1, lifecycle: str = "active") -> SkillSpec:
    return SkillSpec(
        skill_id="evidence_retrieval",
        version=version,
        description="Gather bounded evidence.",
        required_capabilities=("evidence_synthesis", "research"),
        procedures=("evidence_retrieval",),
        task_kinds=("research_run",),
        tools=("list_workspace_files", "read_workspace_file"),
        lifecycle=lifecycle,
    )


def binding(
    *,
    learning: str = "propose",
    skill_ids: tuple[str, ...] = ("evidence_retrieval",),
    procedures: tuple[str, ...] = ("evidence_retrieval",),
    spaces: tuple[str, ...] = ("project_docs",),
):
    spec = AgentSpec(
        agent_id="research",
        role="research",
        objective="Produce bounded evidence-grounded research.",
        capabilities=("evidence_synthesis", "research"),
        allowed_task_kinds=("research_run",),
        allowed_tools=("list_workspace_files", "read_workspace_file"),
        procedures=procedures,
        skill_ids=skill_ids,
        space_permissions=spaces,
        memory_namespace="agent/research",
        skill_learning=learning,
        lifecycle="active",
    )
    registry = AgentRegistry((spec,))
    return registry, AgentFactory(registry).bind_agent("research")


def revision(**updates) -> SkillProcedureRevision:
    values = {
        "source_skill_id": "evidence_retrieval",
        "source_skill_version": 1,
        "procedure_id": "evidence_retrieval",
        "version": 1,
        "title": "Verified evidence retrieval",
        "instructions": "Gather only authorized evidence and retain exact source identity.",
        "preconditions": ("Confirm the evidence scope is authorized.",),
        "verification_steps": (
            "Verify every retained source identity.",
            "Refuse missing or mutated evidence.",
        ),
        "failure_conditions": ("Any required source cannot be reverified.",),
        "required_capabilities": ("evidence_synthesis", "research"),
        "task_kinds": ("research_run",),
        "tools": ("list_workspace_files", "read_workspace_file"),
        "space_permissions": ("project_docs",),
    }
    values.update(updates)
    return SkillProcedureDraft(**values).freeze()


def evidence() -> tuple[SkillLearningEvidenceRef, ...]:
    return (
        SkillLearningEvidenceRef(
            kind="task_attempt",
            source_id="task:attempt-1",
            content_sha256="1" * 64,
        ),
        SkillLearningEvidenceRef(
            kind="operator_feedback",
            source_id="feedback:1",
            content_sha256="2" * 64,
        ),
    )


INSTANCE_ID = "00000000-0000-4000-8000-000000000001"
OTHER_INSTANCE_ID = "00000000-0000-4000-8000-000000000002"


def instance(agent, *, status="active", agent_id=None, agent_version=None, registry_version=None):
    return SkillLearningAgentSnapshot(
        agent_instance_id=INSTANCE_ID,
        agent_id=agent_id or agent.spec.agent_id,
        agent_version=agent_version or agent.spec.version,
        agent_registry_version=registry_version or agent.registry_version,
        status=status,
    )


def evidence_resolver(*, changes=None):
    rows = {
        (item.kind, item.source_id): SkillLearningEvidenceSnapshot(
            kind=item.kind,
            source_id=item.source_id,
            content_sha256=item.content_sha256,
            owner_agent_instance_id=INSTANCE_ID,
            agent_registry_version=binding()[0].version,
            terminal=True,
            accepted=True,
        )
        for item in evidence()
    }
    if changes:
        rows.update(changes)
    return lambda kind, source_id: rows.get((kind, source_id))


def proposal(
    *,
    learning: str = "propose",
    skills: SkillRegistry | None = None,
    skill_ids: tuple[str, ...] = ("evidence_retrieval",),
    procedures: tuple[str, ...] = ("evidence_retrieval",),
    candidate: SkillProcedureRevision | None = None,
    prior_revision: SkillProcedureRevision | None = None,
    resolver=None,
):
    agents, agent = binding(
        learning=learning,
        skill_ids=skill_ids,
        procedures=procedures,
    )
    skills = skills or SkillRegistry((source_skill(),))
    return (
        agents,
        skills,
        propose_skill_revision(
            agent_instance=instance(agent),
            agent_binding=agent,
            skill_registry=skills,
            revision=candidate or revision(),
            evidence_refs=evidence(),
            evidence_resolver=resolver or evidence_resolver(),
            prior_revision_resolver=lambda _skill, _procedure: prior_revision,
        ),
    )


def test_proposal_is_deterministic_non_authoritative_and_does_not_mutate_registries():
    agents, agent = binding()
    skills = SkillRegistry((source_skill(),))
    agent_version = agents.version
    skill_version = skills.version
    agent_snapshot = agents.get_agent("research")
    skill_snapshot = skills.snapshot()

    first = propose_skill_revision(
        agent_instance=instance(agent),
        agent_binding=agent,
        skill_registry=skills,
        revision=revision(),
        evidence_refs=evidence(),
        evidence_resolver=evidence_resolver(),
        prior_revision_resolver=lambda _skill, _procedure: None,
    )
    second = propose_skill_revision(
        agent_instance=instance(agent),
        agent_binding=agent,
        skill_registry=skills,
        revision=revision(),
        evidence_refs=tuple(reversed(evidence())),
        evidence_resolver=evidence_resolver(),
        prior_revision_resolver=lambda _skill, _procedure: None,
    )

    assert first == second
    assert first.proposal_id != first.proposal_fingerprint
    assert UUID(first.proposal_id).version == 5
    assert len(first.proposal_fingerprint) == 64
    assert len(first.proposed_revision.content_sha256) == 64
    assert first.status == "proposed"
    assert first.authority_widening is False
    assert first.runtime_authority is False
    assert [item.kind for item in first.evidence_refs] == [
        "operator_feedback",
        "task_attempt",
    ]
    assert agents.version == agent_version
    assert skills.version == skill_version
    assert agents.get_agent("research") == agent_snapshot
    assert skills.snapshot() == skill_snapshot
    assert not hasattr(first, "run")
    assert not hasattr(first, "execute")
    assert not hasattr(first, "activate")


def test_disabled_agent_cannot_propose():
    with pytest.raises(ValueError, match="skill_learning_not_allowed"):
        proposal(learning="disabled")


@pytest.mark.parametrize(
    ("skills", "candidate"),
    [
        (
            SkillRegistry((source_skill(version=2),)),
            revision(source_skill_version=1),
        ),
        (
            SkillRegistry((source_skill(lifecycle="retired"),)),
            revision(),
        ),
    ],
)
def test_stale_or_inactive_source_skill_is_refused(skills, candidate):
    with pytest.raises(ValueError, match="skill_learning_source_stale"):
        proposal(skills=skills, candidate=candidate)


def test_procedure_must_exist_in_source_skill():
    with pytest.raises(ValueError, match="skill_learning_procedure_unavailable"):
        proposal(candidate=revision(procedure_id="other_procedure"))


def test_agent_must_be_bound_to_source_skill_or_existing_procedure():
    with pytest.raises(ValueError, match="skill_learning_agent_unbound"):
        proposal(skill_ids=(), procedures=())


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        (
            revision(
                required_capabilities=(
                    "code_change",
                    "evidence_synthesis",
                    "research",
                )
            ),
            "skill_learning_capability_widening",
        ),
        (
            revision(task_kinds=("research_run", "shell_run")),
            "skill_learning_task_kind_widening",
        ),
        (
            revision(
                tools=(
                    "list_workspace_files",
                    "read_workspace_file",
                    "repo_status",
                )
            ),
            "skill_learning_tool_widening",
        ),
        (
            revision(space_permissions=("other_space", "project_docs")),
            "skill_learning_space_widening",
        ),
    ],
)
def test_proposal_cannot_widen_agent_or_skill_authority(candidate, code):
    with pytest.raises(ValueError, match=code):
        proposal(candidate=candidate)


def test_duplicate_evidence_is_refused():
    agents, agent = binding()
    skills = SkillRegistry((source_skill(),))
    item = evidence()[0]
    with pytest.raises(ValueError, match="skill_learning_evidence_invalid"):
        propose_skill_revision(
            agent_instance=instance(agent),
            agent_binding=agent,
            skill_registry=skills,
            revision=revision(),
            evidence_refs=(item, item),
            evidence_resolver=evidence_resolver(),
            prior_revision_resolver=lambda _skill, _procedure: None,
        )


def test_direct_noncanonical_evidence_order_is_refused():
    _, _, accepted = proposal()
    payload = accepted.model_dump(mode="json")
    payload["evidence_refs"] = list(reversed(payload["evidence_refs"]))
    with pytest.raises(ValidationError, match="evidence_refs_not_canonical"):
        SkillLearningProposal.model_validate(payload)


def test_proposal_fingerprint_rejects_material_tampering():
    _, _, accepted = proposal()
    payload = deepcopy(accepted.model_dump(mode="json"))
    payload["proposed_revision"]["instructions"] = (
        "Gather different evidence while keeping the old fingerprint."
    )
    with pytest.raises(ValidationError, match="procedure_digest_mismatch"):
        SkillLearningProposal.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["required_capabilities", "task_kinds", "tools", "space_permissions"],
)
def test_revision_authority_fields_must_be_canonical(field):
    values = revision().model_dump()
    current = list(values[field])
    if not current:
        current = ["x"]
    values[field] = tuple(reversed(current)) + (current[0],)
    with pytest.raises(ValidationError, match=f"{field}_not_canonical"):
        SkillProcedureRevision(**values)


def test_revision_text_is_bounded_canonical_content_not_hidden_authority():
    with pytest.raises(ValidationError, match="procedure_text_not_canonical"):
        revision(instructions=" trailing whitespace ")

    with pytest.raises(ValidationError, match="verification_steps_duplicate"):
        revision(
            verification_steps=(
                "Verify the result.",
                "Verify the result.",
            )
        )


def test_instance_and_proposal_ids_are_canonical_uuids():
    agents, agent = binding()
    with pytest.raises(ValidationError):
        SkillLearningAgentSnapshot(
            agent_instance_id="x",
            agent_id=agent.spec.agent_id,
            agent_version=agent.spec.version,
            agent_registry_version=agents.version,
            status="active",
        )
    _, _, accepted = proposal()
    assert str(UUID(accepted.agent_instance_id)) == accepted.agent_instance_id
    assert str(UUID(accepted.proposal_id)) == accepted.proposal_id
    payload = accepted.model_dump(mode="json")
    payload["proposal_id"] = OTHER_INSTANCE_ID
    with pytest.raises(ValidationError, match="proposal_identity_mismatch"):
        SkillLearningProposal.model_validate(payload)
    payload = accepted.model_dump(mode="json")
    payload["agent_version"] += 1
    with pytest.raises(ValidationError, match="proposal_identity_mismatch"):
        SkillLearningProposal.model_validate(payload)


@pytest.mark.parametrize("change", ("status", "agent_id", "agent_version", "registry_version"))
def test_agent_snapshot_must_match_active_trusted_binding(change):
    _, agent = binding()
    updates = {
        "status": "paused",
        "agent_id": "other_agent",
        "agent_version": 999,
        "registry_version": "0" * 64,
    }
    values = {change: updates[change]}
    with pytest.raises(ValueError, match="skill_learning_agent_stale"):
        propose_skill_revision(
            agent_instance=instance(agent, **values),
            agent_binding=agent,
            skill_registry=SkillRegistry((source_skill(),)),
            revision=revision(),
            evidence_refs=evidence(),
            evidence_resolver=evidence_resolver(),
            prior_revision_resolver=lambda _skill, _procedure: None,
        )


def test_conflicting_digests_for_one_evidence_source_are_refused():
    item = evidence()[0]
    changed = item.model_copy(update={"content_sha256": "a" * 64})
    _, agent = binding()
    with pytest.raises(ValueError, match="skill_learning_evidence_invalid"):
        propose_skill_revision(
            agent_instance=instance(agent),
            agent_binding=agent,
            skill_registry=SkillRegistry((source_skill(),)),
            revision=revision(),
            evidence_refs=(item, changed),
            evidence_resolver=evidence_resolver(),
            prior_revision_resolver=lambda _skill, _procedure: None,
        )
    _, _, accepted = proposal()
    payload = accepted.model_dump(mode="json")
    payload["evidence_refs"] = [item.model_dump(mode="json"), changed.model_dump(mode="json")]
    with pytest.raises(ValidationError, match="evidence_refs_not_canonical"):
        SkillLearningProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("record", "code"),
    [
        (None, "skill_learning_evidence_unavailable"),
        ({"content_sha256": "a" * 64}, "skill_learning_evidence_stale"),
        ({"agent_registry_version": "0" * 64}, "skill_learning_evidence_stale"),
        ({"terminal": False, "accepted": False}, "skill_learning_evidence_not_accepted"),
        ({"terminal": True, "accepted": False}, "skill_learning_evidence_not_accepted"),
        ({"owner_agent_instance_id": OTHER_INSTANCE_ID}, "skill_learning_evidence_out_of_scope"),
    ],
)
def test_trusted_evidence_resolver_fails_closed(record, code):
    item = evidence()[0]
    resolved = (
        None
        if record is None
        else SkillLearningEvidenceSnapshot(
            kind=item.kind,
            source_id=item.source_id,
            content_sha256=item.content_sha256,
            owner_agent_instance_id=INSTANCE_ID,
            agent_registry_version=binding()[0].version,
            terminal=True,
            accepted=True,
        ).model_copy(update=record)
    )
    with pytest.raises(ValueError, match=code):
        proposal(resolver=evidence_resolver(changes={(item.kind, item.source_id): resolved}))


def test_explicitly_visible_accepted_evidence_can_support_proposal():
    item = evidence()[0]
    shared = SkillLearningEvidenceSnapshot(
        kind=item.kind,
        source_id=item.source_id,
        content_sha256=item.content_sha256,
        owner_agent_instance_id=OTHER_INSTANCE_ID,
        agent_registry_version=binding()[0].version,
        visible_to_agent_instance_ids=(INSTANCE_ID,),
        terminal=True,
        accepted=True,
    )
    _, _, accepted = proposal(
        resolver=evidence_resolver(changes={(item.kind, item.source_id): shared})
    )
    assert accepted.evidence_refs[1] == item


def test_revision_digest_and_lineage_are_bound_before_proposal():
    prior = revision(version=1)
    current = revision(version=2, prior_revision_sha256=prior.content_sha256)
    assert current.content_sha256 != prior.content_sha256
    _, _, accepted = proposal(candidate=current, prior_revision=prior)
    assert accepted.proposed_revision.prior_revision_sha256 == prior.content_sha256

    payload = current.model_dump(mode="json")
    payload["instructions"] = "Altered content with old digest."
    with pytest.raises(ValidationError, match="procedure_digest_mismatch"):
        SkillProcedureRevision.model_validate(payload)

    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        proposal(candidate=current)
    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        proposal(
            candidate=revision(version=3, prior_revision_sha256=prior.content_sha256),
            prior_revision=prior,
        )
    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        proposal(
            candidate=revision(version=2, prior_revision_sha256="0" * 64), prior_revision=prior
        )


def test_initial_revision_without_prior_record_must_start_at_one():
    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        proposal(candidate=revision(version=2))


def test_proposal_fingerprint_rejects_valid_shape_identity_tampering():
    _, _, accepted = proposal()
    payload = accepted.model_dump(mode="json")
    payload["agent_registry_version"] = "0" * 64
    with pytest.raises(ValidationError, match="proposal_identity_mismatch"):
        SkillLearningProposal.model_validate(payload)


def test_prior_revision_must_match_source_and_procedure():
    wrong_source = revision(version=1, source_skill_id="other_skill")
    wrong_procedure = revision(version=1, procedure_id="other_procedure")
    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        proposal(
            candidate=revision(version=2, prior_revision_sha256=wrong_source.content_sha256),
            prior_revision=wrong_source,
        )
    with pytest.raises(ValueError, match="skill_learning_prior_revision_stale"):
        proposal(
            candidate=revision(version=2, prior_revision_sha256=wrong_procedure.content_sha256),
            prior_revision=wrong_procedure,
        )


def test_revision_freeze_is_stable_and_prior_requires_later_version():
    frozen = revision()
    assert frozen.freeze() is frozen
    with pytest.raises(ValidationError, match="skill_learning_prior_revision_version_invalid"):
        revision(version=1, prior_revision_sha256="0" * 64)
