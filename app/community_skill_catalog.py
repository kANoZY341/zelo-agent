"""Reviewed, inert ECC/Harness/Archify draft skill packages for Zelo Core.

Upstream SKILL.md is retained as source evidence, NEVER automatically loaded
into Mind, Agent prompts or runtime procedures. These are Zelo-authored bounded
candidate definitions, not learned-skill promotions or active grants.
"""

from dataclasses import dataclass
from typing import Literal

from app.skills import SkillSpec
from qualification.community_sources import verify_community_sources


@dataclass(frozen=True, slots=True)
class CommunitySkillCandidate:
    skill: SkillSpec
    upstream: str
    upstream_path: str
    upstream_commit: str
    reviewed_steps: tuple[str, ...]
    candidate_only: Literal[True] = True
    publication_authority: Literal[False] = False
    runtime_authority: Literal[False] = False
    tool_grants: tuple[()] = ()


_PRESETS = (
    (
        "ecc_security_review",
        "ecc",
        "skills/security-review/SKILL.md",
        "Review sensitive code for auth, injection and secret-handling defects.",
        ("code_review",),
        ("ecc_security_review",),
        (
            "Identify trust boundaries and exact changed lines",
            "Check authorization, data validation, secret and filesystem exposure",
            "Describe reproducible evidence and require independent validation",
        ),
    ),
    (
        "ecc_agent_evaluation",
        "ecc",
        "skills/agent-eval/SKILL.md",
        "Compare bounded coding agents against repeatable objective checks.",
        ("code_review",),
        ("ecc_agent_evaluation",),
        (
            "Specify a trusted fixed evaluation fixture and judge criteria",
            "Record observed pass rate, time, cost and failure evidence",
            "Do not select a model or grant execution authority from one test",
        ),
    ),
    (
        "ecc_ai_regression",
        "ecc",
        "skills/ai-regression-testing/SKILL.md",
        "Reproduce and test AI-generated code regressions with fixed fixtures.",
        ("code_review",),
        ("ecc_ai_regression",),
        (
            "Convert an observed failure into a deterministic bounded fixture",
            "Check pre-change failure and post-change regression behavior",
            "Capture measured test evidence without automatically publishing fixes",
        ),
    ),
    (
        "ecc_architecture_decisions",
        "ecc",
        "skills/architecture-decision-records/SKILL.md",
        "Record architectural tradeoffs, alternatives and decision evidence.",
        ("code_planning",),
        ("ecc_architecture_decisions",),
        (
            "Document the current bounded problem and constraints",
            "Compare alternatives, risks, reversibility and migration cost",
            "Record a human-reviewable proposal, not an automatic design decision",
        ),
    ),
    (
        "harness_team_design",
        "harness",
        "skills/harness/SKILL.md",
        "Draft specialized Zelo company teams under the existing Agent Factory.",
        ("code_planning",),
        ("harness_team_design",),
        (
            "Define business objective and specialist responsibilities",
            "Prepare an explicit reporting hierarchy and compatible draft AgentSpecs",
            "Require separate operator activation and permission review for every role",
        ),
    ),
    (
        "archify_architecture",
        "archify",
        "archify/SKILL.md",
        "Prepare inspectable architecture diagrams from documented Core state.",
        ("code_planning",),
        ("archify_architecture",),
        (
            "Ground diagram components and relationships in a reviewed source",
            "Validate a bounded candidate against the pinned diagram schema",
            "Export data only; do not execute renderer scripts automatically",
        ),
    ),
)


def community_skill_candidates() -> tuple[CommunitySkillCandidate, ...]:
    """Verify upstream identity and emit trusted draft specs with zero authority."""

    sources = {source.name: source for source in verify_community_sources()}
    candidates = []
    for name, source, path, desc, capabilities, procedures, steps in _PRESETS:
        assert sources[source].file_count > 0
        candidates.append(
            CommunitySkillCandidate(
                skill=SkillSpec(
                    skill_id=name,
                    description=desc,
                    required_capabilities=capabilities,
                    procedures=procedures,
                    task_kinds=(),
                    tools=(),
                    lifecycle="draft",
                ),
                upstream=sources[source].repository,
                upstream_path=path,
                upstream_commit=sources[source].commit,
                reviewed_steps=steps,
            )
        )
    return tuple(candidates)
