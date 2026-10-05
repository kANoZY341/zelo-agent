"""Pure governed Agent skill-learning proposal tests."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent_factory import AgentFactory, AgentRegistry, AgentSpec
from app.skill_learning import (
    SkillLearningEvidenceRef,
    SkillLearningProposal,
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
        "version": 2,
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
    return SkillProcedureRevision(**values)


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


def proposal(
    *,
    learning: str = "propose",
    skills: SkillRegistry | None = None,
    skill_ids: tuple[str, ...] = ("evidence_retrieval",),
    procedures: tuple[str, ...] = ("evidence_retrieval",),
    candidate: SkillProcedureRevision | None = None,
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
            agent_instance_id="00000000-0000-4000-8000-000000000001",
            agent_binding=agent,
            skill_registry=skills,
            revision=candidate or revision(),
            evidence_refs=evidence(),
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
        agent_instance_id="00000000-0000-4000-8000-000000000001",
        agent_binding=agent,
        skill_registry=skills,
        revision=revision(),
        evidence_refs=evidence(),
    )
    second = propose_skill_revision(
        agent_instance_id="00000000-0000-4000-8000-000000000001",
        agent_binding=agent,
        skill_registry=skills,
        revision=revision(),
        evidence_refs=tuple(reversed(evidence())),
    )

    assert first == second
    assert first.proposal_id == first.proposal_fingerprint
    assert len(first.proposal_id) == 64
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
            agent_instance_id="00000000-0000-4000-8000-000000000001",
            agent_binding=agent,
            skill_registry=skills,
            revision=revision(),
            evidence_refs=(item, item),
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
    with pytest.raises(ValidationError, match="proposal_identity_mismatch"):
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
