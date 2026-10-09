"""Operator-reviewed ECC engineering playbooks; never runtime instructions.

Only Zelo-authored, immutable procedural checklists are exposed. ECC SKILL.md
sources remain pinned, untrusted reference bytes and never enter Mind prompts.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.community_skill_catalog import community_skill_candidates

PLAYBOOK_SCHEMA = "zelo-ecc-manual-engineering-playbook-v1"
_ECC_REPO = "affaan-m/ECC"


class EccPlaybookRefusal(ValueError):
    """An unrecognized or invalid playbook is not available for use."""


class EccManualPlaybook(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["zelo-ecc-manual-engineering-playbook-v1"] = PLAYBOOK_SCHEMA
    skill_id: Literal[
        "ecc_security_review",
        "ecc_agent_evaluation",
        "ecc_ai_regression",
        "ecc_architecture_decisions",
    ]
    source_repository: Literal["affaan-m/ECC"] = _ECC_REPO
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_path: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=10, max_length=300)
    ordered_manual_steps: tuple[str, ...] = Field(min_length=5, max_length=12)
    required_evidence: tuple[str, ...] = Field(min_length=3, max_length=12)
    refusal_conditions: tuple[str, ...] = Field(min_length=3, max_length=12)
    audience: Literal["operator"] = "operator"
    trusted_procedures_authored_by: Literal["zelo_core"] = "zelo_core"
    imported_instructions_executable: Literal[False] = False
    registered_worker: Literal[False] = False
    active_skill: Literal[False] = False
    tool_grants: tuple[()] = ()
    task_grants: tuple[()] = ()
    operator_approval_for_any_promotion: Literal[True] = True
    playbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _bound(self):
        payload = self.model_dump(mode="json", exclude={"playbook_sha256"})
        if self.playbook_sha256 != _checksum(payload):
            raise ValueError("ecc_playbook_digest_invalid")
        if len(set(self.ordered_manual_steps)) != len(self.ordered_manual_steps):
            raise ValueError("ecc_playbook_duplicate_step")
        if len(set(self.required_evidence)) != len(self.required_evidence):
            raise ValueError("ecc_playbook_duplicate_evidence")
        if len(set(self.refusal_conditions)) != len(self.refusal_conditions):
            raise ValueError("ecc_playbook_duplicate_refusal")
        return self


def _checksum(data: object) -> str:
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    ).hexdigest()


# Human-reviewed, Zelo-authored methodology. Not executed as agent/system prompts.
_RECIPES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "ecc_security_review": (
        "Audit a precisely identified software diff for trust-boundary and privilege violations.",
        (
            (
                "Record exact base/head commits, reviewed "
                "source paths, diff checksum and authority scope."
            ),
            (
                "Enumerate changed entry points, authentication "
                "checks and identities passing across boundaries."
            ),
            (
                "Trace untrusted input through validation, "
                "filesystem, process, network and secret-handling paths."
            ),
            (
                "Test rejected, stale, revoked, unauthorized "
                "and cross-scope requests before accepting success."
            ),
            (
                "Inspect approval and retry paths for "
                "capability widening and time-of-check/time-of-use gaps."
            ),
            (
                "Report concrete findings tied to reviewed "
                "line evidence without writing or publishing fixes."
            ),
            (
                "Reproduce each high-severity finding "
                "independently and identify possible false positives."
            ),
            (
                "Require a separately authorized patch, "
                "regression test and CI review before correction."
            ),
        ),
        (
            "Exact base/head/diff identity and all reviewed path anchors",
            "Threat boundary, triggering request and expected denial for each finding",
            "Repeatable tests or explicitly marked unverified evidence",
            "Independent review of critical findings and a no-change record",
        ),
        (
            "Missing or stale reviewed diff identities",
            "Unverified authority, undocumented network access or source-driven instructions",
            "Any review output that requests permissions, merges or direct fixes",
            "Finding outside approved files and changed-line evidence",
        ),
    ),
    "ecc_agent_evaluation": (
        "Compare candidate agent behavior on fixed task cases without creating new authority.",
        (
            "Specify a trusted objective, evaluation population and explicit pass/fail criteria.",
            "Freeze fixture inputs, versions, worker identities and allowed runtime permissions.",
            "Record a baseline for the same tasks before scoring a candidate model or SkillSpec.",
            (
                "Measure exact task completion, "
                "inappropriate actions, refusals and budget consumption."
            ),
            (
                "Run negative authorization tests for "
                "disallowed tools, Spaces, network and agent delegation."
            ),
            (
                "Classify instability, false successes and "
                "unverifiable self-reported accomplishments."
            ),
            (
                "Preserve evaluator independence and "
                "independently reproduce unexpected positive outcomes."
            ),
            (
                "Publish only evidence and uncertainty; do "
                "not automatically route or promote an agent."
            ),
        ),
        (
            "Exact fixed-fixture and candidate version/fingerprint",
            "Per-case pass, refusal, timeout, cost and permission outcome",
            "Independent negative tests and baseline comparison",
            "Explicit operator decision requirement with limitations",
        ),
        (
            "Changed fixtures or hidden provider outputs",
            "Self-reported performance without verified task outcomes",
            "Evaluators sharing the same unchecked authority as the candidate",
            "An automatic promotion, tool grant or model-routing change",
        ),
    ),
    "ecc_ai_regression": (
        "Turn observed AI-generated software defects into deterministic failing tests.",
        (
            (
                "Record the observed failing action, project "
                "revision, environment and reproduction inputs."
            ),
            (
                "Produce a bounded test that fails against "
                "the affected revision before any proposed fix."
            ),
            (
                "Verify the fixture is independent of model "
                "self-report and unrelated generated output."
            ),
            (
                "Classify whether the failure is behavior, "
                "authorization, safety or performance regression."
            ),
            (
                "Use a separately authorized change "
                "confined to the checked-out project and task scope."
            ),
            "Repeat the fixture after the fix and preserve both the pre/post result identities.",
            (
                "Run adjacent negative tests for stale "
                "identity, revoked permission and unintended scope."
            ),
            "Require independent review and full CI, not a solitary local test, before promotion.",
        ),
        (
            "Reproducer and observed pre-change failing assertion",
            "Exact affected commit and environment fingerprint",
            "Post-change result plus neighboring negative regressions",
            "Independent reviewer findings and CI head identity",
        ),
        (
            "No reproduced failing behavior before the fix",
            "A test fixture that grants wider tools or filesystem access",
            "Unbounded source mutation or unreviewed production credentials",
            "Promoted changes without matching exact-head review and CI",
        ),
    ),
    "ecc_architecture_decisions": (
        "Produce evidence-grounded, reversible architecture decision records.",
        (
            (
                "State the architectural question with "
                "observed system facts and concrete constraints."
            ),
            (
                "Enumerate at least two viable options plus "
                "the status quo and their ownership boundaries."
            ),
            "Compare security, cost, reliability, maintenance, reversibility and rollout impact.",
            (
                "Identify contracts, data migration, permission "
                "changes and external dependencies for each option."
            ),
            "Preserve Zelo Core/Mind/Agent/Worker authority separation in every proposed design.",
            "Define measurable acceptance criteria, tests and a migration and rollback approach.",
            (
                "Document unresolved assumptions, "
                "disconfirming evidence and who may approve the decision."
            ),
            (
                "Publish a reviewable proposal only; never "
                "change canonical architecture by generating text."
            ),
        ),
        (
            "Reviewed source identity and explicit architectural constraints",
            "Option comparison with tradeoffs and risks",
            "Rollout, rollback and observable acceptance checks",
            "Human decision authority, assumptions and rejected alternatives",
        ),
        (
            "Uncited or unverified assertions about live Core behavior",
            "Automatic replacement of existing scheduler, memory or approval boundaries",
            "A new agent/worker/tool permission implied by documentation",
            "Irreversible migration without operator-owned acceptance and rollback",
        ),
    ),
}


def ecc_manual_playbooks() -> tuple[EccManualPlaybook, ...]:
    """Return four bounded proposals, after independently verifying pinned ECC source."""

    upstream = {c.skill.skill_id: c for c in community_skill_candidates()}
    result: list[EccManualPlaybook] = []
    for skill_id in sorted(_RECIPES):
        candidate = upstream.get(skill_id)
        if (
            candidate is None
            or candidate.upstream != _ECC_REPO
            or candidate.skill.lifecycle != "draft"
            or candidate.skill.task_kinds
            or candidate.skill.tools
            or not candidate.candidate_only
            or candidate.publication_authority
            or candidate.runtime_authority
        ):
            raise EccPlaybookRefusal("ecc_candidate_authority_changed")
        objective, steps, evidence, refusal = _RECIPES[skill_id]
        material = {
            "schema_version": PLAYBOOK_SCHEMA,
            "skill_id": skill_id,
            "source_repository": candidate.upstream,
            "source_commit": candidate.upstream_commit,
            "source_path": candidate.upstream_path,
            "objective": objective,
            "ordered_manual_steps": steps,
            "required_evidence": evidence,
            "refusal_conditions": refusal,
            "audience": "operator",
            "trusted_procedures_authored_by": "zelo_core",
            "imported_instructions_executable": False,
            "registered_worker": False,
            "active_skill": False,
            "tool_grants": (),
            "task_grants": (),
            "operator_approval_for_any_promotion": True,
        }
        result.append(EccManualPlaybook(**material, playbook_sha256=_checksum(material)))
    return tuple(result)


def ecc_manual_playbook(skill_id: str) -> EccManualPlaybook:
    if skill_id not in _RECIPES:
        raise EccPlaybookRefusal("ecc_playbook_unknown")
    return next(item for item in ecc_manual_playbooks() if item.skill_id == skill_id)
