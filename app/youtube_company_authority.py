"""Enforce the actual YouTube company org chain on *every* Agent Task admission.

An operator creating an Active AgentSpec is NOT enough to bypass hierarchy.
The three executive/producer child roles require an active Organization
Assignment, its exact reporting parent AND the live budget-bound incoming
Delegation. The VP root requires an active CEO-rooted Division assignment.
This guard is called from Core TaskService on BOTH submit and claim/retry.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_execution_contracts import AgentExecutionBinding
from app.models import (
    AgentDelegation,
    AgentInstance,
    Campaign,
    CampaignBudgetReservation,
    OrganizationAssignment,
    OrganizationUnit,
    utcnow,
)
from app.policy import DomainError

_TEAM_LEVELS = {
    "youtube": ("vp", "division", None),
    "youtube_content": ("director", "department", "vp"),
    "youtube_research": ("manager", "team", "director"),
    "youtube_script_writer": ("specialist", "team", "manager"),
}
_ROLE_PARENT = {
    "youtube_content": "youtube",
    "youtube_research": "youtube_content",
    "youtube_script_writer": "youtube_research",
}
_ROLE_CHILD = {parent: child for child, parent in _ROLE_PARENT.items()}
_ROLE_DEPTH = {"youtube_content": 1, "youtube_research": 2, "youtube_script_writer": 3}
_COMPANY_MAX_DEPTH = 3


def company_child_requires_atomic_creation(agent_id: str) -> bool:
    """Never admit a detached descendant via generic POST /agents."""

    return agent_id in _TEAM_LEVELS and agent_id != "youtube"


def validate_youtube_company_child_parent(parent_id: str, child_id: str) -> None:
    """One preapproved reporting edge, not arbitrary roles with a matching level."""

    expected = _ROLE_PARENT.get(child_id)
    if expected is not None and expected != parent_id:
        raise DomainError("YouTube company child requires exact trusted parent role", 409)
    if parent_id in _ROLE_CHILD and child_id != _ROLE_CHILD[parent_id]:
        raise DomainError("YouTube company executive may hire only assigned child role", 409)


def validate_youtube_company_child_depth(
    parent_agent_id: str, child_agent_id: str, max_depth: int
) -> None:
    """Company reporting hierarchy has exactly three allowed delegation edges."""

    if (parent_agent_id in _ROLE_CHILD or child_agent_id in _ROLE_PARENT) and (
        max_depth != _COMPANY_MAX_DEPTH
    ):
        raise DomainError("YouTube company child must inherit exact depth three", 409)


def deny_generic_company_executive_delegation(parent_agent_id: str) -> None:
    """No company executive may create authority through generic DelegationService.

    Trusted permanent children must be created atomically with their
    Organization assignment and reservation by AgentChildCreationService.
    """

    if parent_agent_id in _ROLE_CHILD:
        raise DomainError("YouTube company delegation requires budget-bound child creation", 409)


def validate_youtube_company_execution(
    session: Session,
    binding: AgentExecutionBinding,
    *,
    campaign_id: str | None,
) -> None:
    """Reject invisible org/budget bypasses before any persisted Task/Worker action."""

    shape = _TEAM_LEVELS.get(binding.spec.agent_id)
    if shape is None:
        return
    level, required_unit_type, parent_level = shape
    if (
        binding.spec.organizational_level != level
        or binding.spec.lifecycle != "active"
        or binding.task_kind != "research_run"
    ):
        raise DomainError("YouTube company AgentSpec execution boundary invalid", 409)

    assignment = session.scalar(
        select(OrganizationAssignment).where(
            OrganizationAssignment.agent_instance_id == binding.agent_instance_id,
            OrganizationAssignment.status == "active",
        )
    )
    if assignment is None or assignment.level != level:
        raise DomainError("YouTube company execution requires active org assignment", 409)
    unit = session.get(OrganizationUnit, assignment.unit_id)
    if unit is None or unit.status != "active" or unit.unit_type != required_unit_type:
        raise DomainError("YouTube company organization unit is invalid", 409)
    # Every company Research Task, including the CEO-rooted VP, must belong to
    # one concrete, currently active Project Campaign. This alone does NOT
    # claim monetary usage enforcement; per-Task budgets remain separate.
    if campaign_id is None:
        raise DomainError("YouTube company research requires active Campaign", 409)

    delegated = binding.delegation
    if parent_level is None:
        if assignment.reports_to_assignment_id is not None or delegated is not None:
            raise DomainError("YouTube company VP must report to CEO", 409)
        return

    if delegated is None or assignment.reports_to_assignment_id is None:
        raise DomainError("YouTube company descendant requires active delegation", 409)
    parent = session.get(OrganizationAssignment, assignment.reports_to_assignment_id)
    if (
        parent is None
        or parent.status != "active"
        or parent.level != parent_level
        or parent.agent_instance_id != delegated.parent_instance_id
    ):
        raise DomainError("YouTube company delegation and reporting line mismatch", 409)
    parent_unit = session.get(OrganizationUnit, parent.unit_id)
    if parent_unit is None or parent_unit.status != "active":
        raise DomainError("YouTube company parent organization unit is invalid", 409)
    if level in {"director", "manager"}:
        if unit.parent_unit_id != parent_unit.id:
            raise DomainError("YouTube company organizational unit tree changed", 409)
    elif unit.id != parent_unit.id:
        raise DomainError("YouTube company Specialist reports outside Manager team", 409)
    _validate_company_ancestor_chain(
        session,
        assignment=assignment,
        agent_id=binding.spec.agent_id,
        project_id=binding.project_id,
        registry_version=binding.registry_version,
        campaign_id=delegated.campaign_id,
        first_delegation_id=delegated.delegation_id,
        now=utcnow(),
    )


def validate_youtube_company_hiring(
    session: Session,
    *,
    parent: AgentInstance,
    parent_assignment: OrganizationAssignment,
    incoming: AgentDelegation | None,
    campaign: Campaign,
    now: datetime,
) -> None:
    """Reject parent-role escalation after a Delegation or budget has expired.

    Only the Managing Director's existing authenticated ChildCreationService
    invokes this check. Exact project/owner/unit/role/campaign proofs are
    checked INSIDE that service's serialized transaction before any child,
    assignment, or new delegated authority is created.
    """

    if parent.agent_id not in ("youtube", "youtube_content", "youtube_research"):
        return
    expected_level, expected_unit_type, parent_level = _TEAM_LEVELS[parent.agent_id]
    if parent_assignment.level != expected_level:
        raise DomainError("YouTube company parent level does not match role", 409)
    unit = session.get(OrganizationUnit, parent_assignment.unit_id)
    if unit is None or unit.status != "active" or unit.unit_type != expected_unit_type:
        raise DomainError("YouTube company parent unit inactive or invalid", 409)

    if parent_level is None:
        if incoming is not None or parent_assignment.reports_to_assignment_id is not None:
            raise DomainError("YouTube company VP hiring requires CEO-rooted Division", 409)
        return

    # A revoked parent Delegation must never silently turn an executive into
    # an undelegated hierarchy root, resetting nested depth and Campaign scope.
    if incoming is None or incoming.status != "active":
        raise DomainError("YouTube company parent incoming delegation not active", 409)
    if incoming.child_instance_id != parent.id or incoming.campaign_id != campaign.id:
        raise DomainError("YouTube company hiring must use parent Delegation Campaign", 409)
    parent_report = session.get(OrganizationAssignment, parent_assignment.reports_to_assignment_id)
    if (
        parent_report is None
        or parent_report.status != "active"
        or parent_report.level != parent_level
        or parent_report.agent_instance_id != incoming.parent_instance_id
    ):
        raise DomainError("YouTube company parent reporting chain is stale", 409)
    parent_unit = session.get(OrganizationUnit, parent_report.unit_id)
    if parent_unit is None or parent_unit.status != "active":
        raise DomainError("YouTube company parent reporting unit is inactive", 409)
    if unit.parent_unit_id != parent_unit.id:
        raise DomainError("YouTube company parent organization hierarchy changed", 409)

    source_parent = session.get(AgentInstance, incoming.parent_instance_id)
    if (
        source_parent is None
        or source_parent.status != "active"
        or source_parent.project_id != parent.project_id
        or source_parent.registry_version != parent.registry_version
    ):
        raise DomainError("YouTube company hiring parent Agent identity stale", 409)
    reserved = session.get(CampaignBudgetReservation, incoming.budget_reservation_id)
    if (
        reserved is None
        or reserved.status != "reserved"
        or reserved.expires_at <= now
        or reserved.campaign_id != incoming.campaign_id
        or reserved.owner_id != incoming.parent_instance_id
        or reserved.resource != incoming.budget_resource
        or reserved.units != incoming.budget_units
    ):
        raise DomainError("YouTube company parent Delegation budget not live", 409)
    _validate_company_ancestor_chain(
        session,
        assignment=parent_assignment,
        agent_id=parent.agent_id,
        project_id=parent.project_id,
        registry_version=parent.registry_version,
        campaign_id=campaign.id,
        first_delegation_id=incoming.id,
        now=now,
    )


def _validate_company_ancestor_chain(
    session: Session,
    *,
    assignment: OrganizationAssignment,
    agent_id: str,
    project_id: str,
    registry_version: str,
    campaign_id: str,
    first_delegation_id: str,
    now: datetime,
) -> None:
    """Check the entire VP→...→actor chain, not merely the incoming edge.

    Revoking the VP→Director Delegation must revoke all descendant execution
    and further hiring. Each link must retain its original Campaign, reserved
    resource proof, verified parent role, AgentSpec registry and exact org
    reporting unit through the entire ancestry. Never trust model output for
    ancestry or delegation state. Maximum depth is three edges.
    """

    role = agent_id
    current = assignment
    expected_first = first_delegation_id
    for _edge in range(4):
        expected_level, expected_unit_type, _ = _TEAM_LEVELS[role]
        current_unit = session.get(OrganizationUnit, current.unit_id)
        if (
            current.status != "active"
            or current.level != expected_level
            or current_unit is None
            or current_unit.status != "active"
            or current_unit.unit_type != expected_unit_type
        ):
            raise DomainError("YouTube company ancestor organization inactive or invalid", 409)
        parent_role = _ROLE_PARENT.get(role)
        if parent_role is None:
            if current.reports_to_assignment_id is not None:
                raise DomainError("YouTube company VP ancestor must report to CEO", 409)
            return

        delegations = tuple(
            session.scalars(
                select(AgentDelegation).where(
                    AgentDelegation.child_instance_id == current.agent_instance_id,
                    AgentDelegation.status == "active",
                )
            )
        )
        if len(delegations) != 1:
            raise DomainError("YouTube company ancestor Delegation missing or ambiguous", 409)
        delegated = delegations[0]
        if expected_first is not None and delegated.id != expected_first:
            raise DomainError("YouTube company first Delegation changed", 409)
        expected_first = None
        if delegated.campaign_id != campaign_id:
            raise DomainError("YouTube company ancestor Campaign identity changed", 409)
        if delegated.depth != _ROLE_DEPTH[role] or delegated.max_depth != _COMPANY_MAX_DEPTH:
            raise DomainError("YouTube company ancestor delegation depth authority changed", 409)
        receipt = session.get(CampaignBudgetReservation, delegated.budget_reservation_id)
        if (
            receipt is None
            or receipt.status != "reserved"
            or receipt.expires_at <= now
            or receipt.campaign_id != delegated.campaign_id
            or receipt.owner_id != delegated.parent_instance_id
            or receipt.resource != delegated.budget_resource
            or receipt.units != delegated.budget_units
        ):
            raise DomainError("YouTube company ancestor budget is not live", 409)

        parent = session.get(OrganizationAssignment, current.reports_to_assignment_id)
        ancestor = session.get(AgentInstance, delegated.parent_instance_id)
        if (
            parent is None
            or parent.status != "active"
            or parent.agent_instance_id != delegated.parent_instance_id
            or ancestor is None
            or ancestor.id != parent.agent_instance_id
            or ancestor.agent_id != parent_role
            or ancestor.status != "active"
            or ancestor.project_id != project_id
            or ancestor.registry_version != registry_version
        ):
            raise DomainError("YouTube company ancestor parent identity mismatch", 409)
        expected_parent_level = _TEAM_LEVELS[parent_role][0]
        if parent.level != expected_parent_level:
            raise DomainError("YouTube company ancestor reporting level mismatch", 409)
        parent_unit = session.get(OrganizationUnit, parent.unit_id)
        if parent_unit is None or parent_unit.status != "active":
            raise DomainError("YouTube company ancestor parent unit inactive", 409)
        if expected_level in {"director", "manager"}:
            if current_unit.parent_unit_id != parent_unit.id:
                raise DomainError("YouTube company ancestor parent unit tree changed", 409)
        elif current_unit.id != parent_unit.id:
            raise DomainError("YouTube company ancestor Specialist team changed", 409)
        current = parent
        role = parent_role
    raise DomainError("YouTube company ancestor depth bound exceeded", 409)
