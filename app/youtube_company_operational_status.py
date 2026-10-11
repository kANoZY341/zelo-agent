"""Read-only real YouTube company state: roles, reporting chain and task pulse.

Unlike inert design previews, this assembles *persisted* AgentInstances,
Organization assignments, Campaign Delegations and Task evidence. It does
not confer execution, hiring, approval or spending authority. The Runner
always revalidates actual Task/Agent/Campaign/Ancestor identities itself.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agent_factory import AgentRegistry, AgentSpec
from app.models import (
    AgentDelegation,
    AgentInstance,
    Campaign,
    CampaignBudgetReservation,
    Goal,
    OrganizationAssignment,
    OrganizationUnit,
    Project,
    Task,
    utcnow,
)
from app.policy import DomainError
from app.storage import SessionStore
from app.youtube_company_team import (
    TEAM_LEVELS,
    TEAM_ROLE_IDS,
    trusted_youtube_company_specs,
    youtube_company_registry_digest,
)

_Status = Literal[
    "not_created",
    "ambiguous_instances",
    "registry_disabled",
    "paused",
    "project_inactive",
    "identity_stale",
    "assignment_missing_or_ambiguous",
    "assignment_invalid",
    "parent_chain_incomplete",
    "delegation_missing_or_ambiguous",
    "delegation_invalid",
    "campaign_or_reservation_invalid",
    "root_assigned",
    "delegated_chain_intact",
]
_ACTIVE_STATES = ("pending", "awaiting_approval", "running")
_ACTIVE_ROLE_STATE = ("root_assigned", "delegated_chain_intact")


class YoutubeCompanyRoleStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role_agent_id: Literal[
        "youtube", "youtube_content", "youtube_research", "youtube_script_writer"
    ]
    organizational_level: Literal["vp", "director", "manager", "specialist"]
    reports_to_agent_id: str | None
    observed_status: _Status
    agent_instance_id: str | None = None
    assignment_id: str | None = None
    organization_unit_id: str | None = None
    incoming_delegation_id: str | None = None
    campaign_id: str | None = None
    reservation_expires_at: datetime | None = None
    project_role_research_tasks_24h: int = Field(ge=0)
    project_role_active_tasks: int = Field(ge=0)
    evidence_of_active_reporting_chain: bool
    does_not_grant_execution_or_hiring_authority: Literal[True] = True


class YoutubeCompanyOperationalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-youtube-company-operational-status-v1"] = (
        "zelo-youtube-company-operational-status-v1"
    )
    project_id: str
    project_name: str
    project_status: Literal["active", "paused", "completed", "archived"]
    observed_at: datetime
    actual_registry_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_company_registry_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    company_registry_selected: bool
    unambiguous_active_role_instance_count: int = Field(ge=0, le=4)
    roles_with_consistent_chain_evidence: int = Field(ge=0, le=4)
    all_four_reporting_chains_consistent: bool
    roles: tuple[
        YoutubeCompanyRoleStatus,
        YoutubeCompanyRoleStatus,
        YoutubeCompanyRoleStatus,
        YoutubeCompanyRoleStatus,
    ]
    real_project_state_not_a_design_preview: Literal[True] = True
    does_not_approve_bootstrap_or_new_teams: Literal[True] = True
    does_not_grant_execution_hiring_or_spending: Literal[True] = True
    campaign_reservations_do_not_prove_currency_cost: Literal[True] = True

    @model_validator(mode="after")
    def _check_faithful_company_shape(self):
        if (
            self.required_company_registry_version != youtube_company_registry_digest()
            or tuple(x.role_agent_id for x in self.roles) != TEAM_ROLE_IDS
            or tuple(x.organizational_level for x in self.roles) != TEAM_LEVELS
            or tuple(x.reports_to_agent_id for x in self.roles) != (None, *TEAM_ROLE_IDS[:-1])
            or (
                self.company_registry_selected
                and (self.actual_registry_version != self.required_company_registry_version)
            )
            or any(
                x.evidence_of_active_reporting_chain != (x.observed_status in _ACTIVE_ROLE_STATE)
                for x in self.roles
            )
            or self.roles_with_consistent_chain_evidence
            != sum(x.evidence_of_active_reporting_chain for x in self.roles)
            or self.all_four_reporting_chains_consistent
            != (self.company_registry_selected and self.roles_with_consistent_chain_evidence == 4)
        ):
            raise ValueError("youtube_company_status_evidence_shape_invalid")
        return self


class YoutubeCompanyOperationalStatusService:
    """Read a consistent SQLite snapshot without creating or changing state."""

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    @staticmethod
    def _task_counts(session: Session, project_id: str, agent_id: str, now: datetime):
        scope = (
            Task.kind == "research_run",
            Task.agent_execution_spec.is_not(None),
            func.json_extract(Task.agent_execution_spec, "$.project_id") == project_id,
            func.json_extract(Task.agent_execution_spec, "$.spec.agent_id") == agent_id,
        )
        cutoff = now - timedelta(hours=24)
        recent = (
            session.scalar(
                select(func.count()).select_from(Task).where(*scope, Task.created_at > cutoff)
            )
            or 0
        )
        active = (
            session.scalar(
                select(func.count())
                .select_from(Task)
                .where(*scope, Task.status.in_(_ACTIVE_STATES))
            )
            or 0
        )
        return recent, active

    @staticmethod
    def _single(session: Session, query):
        """Never choose an arbitrary Actor/Org/Delegation if multiple exist."""

        values = list(session.scalars(query.limit(2)))
        return values[0] if len(values) == 1 else None, len(values)

    @staticmethod
    def _instance_exact(instance: AgentInstance, spec: AgentSpec, registry: AgentRegistry):
        try:
            recorded = AgentSpec.model_validate(instance.spec)
        except ValueError:
            return False
        return (
            recorded == spec
            and instance.agent_version == spec.version
            and instance.registry_version == registry.version
            and registry.get_agent(spec.agent_id) == spec
        )

    def preview(
        self,
        project_id: str,
        *,
        registry: AgentRegistry,
        now: datetime | None = None,
    ) -> YoutubeCompanyOperationalStatus:
        observed = now or utcnow()
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("youtube_company_status_clock_invalid")
        trusted = trusted_youtube_company_specs()
        selected = registry.version == youtube_company_registry_digest() and all(
            registry.get_agent(spec.agent_id) == spec for spec in trusted
        )

        with self.database.read() as session:
            # SQLite's ordinary SQLAlchemy read Session does NOT establish a
            # physical repeatable-read transaction automatically. Explicit
            # deferred BEGIN pins a single WAL snapshot across Project,
            # Agents, Org, Delegations, budget and Task evidence without
            # taking a writer lock or modifying state. This backend is SQLite
            # in v0.17; never derive execution permission from this snapshot.
            session.execute(text("BEGIN"))
            project = session.get(Project, project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            rows: list[YoutubeCompanyRoleStatus] = []
            selected_instances: list[AgentInstance | None] = []
            assignments: list[OrganizationAssignment | None] = []
            for index, (agent_id, level, spec) in enumerate(
                zip(TEAM_ROLE_IDS, TEAM_LEVELS, trusted, strict=True)
            ):
                parent_agent_id = TEAM_ROLE_IDS[index - 1] if index else None
                role_tasks, active_tasks = self._task_counts(
                    session, project_id, agent_id, observed
                )
                instance, instance_count = self._single(
                    session,
                    select(AgentInstance).where(
                        AgentInstance.project_id == project_id,
                        AgentInstance.agent_id == agent_id,
                        AgentInstance.status != "retired",
                    ),
                )
                # Hold all entries at same position, including not-created.
                selected_instances.append(instance)
                current_assignment = None
                current_delegation = None
                expiry = None
                campaign_id = None
                unit_id = None
                reason: _Status
                if instance_count == 0:
                    reason = "not_created"
                elif instance_count != 1:
                    reason = "ambiguous_instances"
                elif not selected:
                    reason = "registry_disabled"
                elif instance.status == "paused":
                    reason = "paused"
                elif project.status != "active":
                    reason = "project_inactive"
                elif not self._instance_exact(instance, spec, registry):
                    reason = "identity_stale"
                else:
                    current_assignment, assignment_count = self._single(
                        session,
                        select(OrganizationAssignment).where(
                            OrganizationAssignment.agent_instance_id == instance.id,
                            OrganizationAssignment.status == "active",
                        ),
                    )
                    if assignment_count != 1:
                        reason = "assignment_missing_or_ambiguous"
                    else:
                        unit = session.get(OrganizationUnit, current_assignment.unit_id)
                        expected_type = ("division", "department", "team", "team")[index]
                        unit_id = current_assignment.unit_id
                        if (
                            current_assignment.level != level
                            or unit is None
                            or unit.status != "active"
                            or unit.unit_type != expected_type
                        ):
                            reason = "assignment_invalid"
                        elif index == 0:
                            reason = (
                                "root_assigned"
                                if current_assignment.reports_to_assignment_id is None
                                else "assignment_invalid"
                            )
                        elif (
                            not rows[index - 1].evidence_of_active_reporting_chain
                            or assignments[index - 1] is None
                            or selected_instances[index - 1] is None
                        ):
                            reason = "parent_chain_incomplete"
                        else:
                            parent_assignment = assignments[index - 1]
                            parent_instance = selected_instances[index - 1]
                            parent_unit = session.get(OrganizationUnit, parent_assignment.unit_id)
                            unit_relation = (
                                unit.parent_unit_id == parent_assignment.unit_id
                                if level in ("director", "manager")
                                else unit.id == parent_assignment.unit_id
                            )
                            if (
                                current_assignment.reports_to_assignment_id != parent_assignment.id
                                or parent_unit is None
                                or parent_unit.status != "active"
                                or not unit_relation
                            ):
                                reason = "assignment_invalid"
                            else:
                                current_delegation, delegation_count = self._single(
                                    session,
                                    select(AgentDelegation).where(
                                        AgentDelegation.child_instance_id == instance.id,
                                        AgentDelegation.status == "active",
                                    ),
                                )
                                if delegation_count != 1:
                                    reason = "delegation_missing_or_ambiguous"
                                else:
                                    campaign_id = current_delegation.campaign_id
                                    if (
                                        current_delegation.parent_instance_id != parent_instance.id
                                        or current_delegation.depth != index
                                        or current_delegation.max_depth != 3
                                        or tuple(current_delegation.allowed_task_kinds)
                                        != ("research_run",)
                                        # Existing AgentChildCreationService accepts
                                        # any bounded *subset* of the trusted
                                        # child's capabilities, not one exact
                                        # fixture's selection.
                                        or not set(
                                            current_delegation.required_capabilities
                                        ).issubset(set(spec.capabilities))
                                        or (
                                            index >= 2
                                            and rows[index - 1].campaign_id != campaign_id
                                        )
                                    ):
                                        reason = "delegation_invalid"
                                    else:
                                        campaign = session.get(Campaign, campaign_id)
                                        goal = (
                                            session.get(Goal, campaign.goal_id)
                                            if campaign is not None
                                            else None
                                        )
                                        reservation = session.get(
                                            CampaignBudgetReservation,
                                            current_delegation.budget_reservation_id,
                                        )
                                        if reservation is not None:
                                            expiry = reservation.expires_at
                                        if (
                                            campaign is None
                                            or campaign.status != "active"
                                            or goal is None
                                            or goal.project_id != project_id
                                            or reservation is None
                                            or reservation.status != "reserved"
                                            or reservation.expires_at <= observed
                                            or reservation.campaign_id != campaign_id
                                            or reservation.owner_id != parent_instance.id
                                            or reservation.resource
                                            != current_delegation.budget_resource
                                            or reservation.units != current_delegation.budget_units
                                        ):
                                            reason = "campaign_or_reservation_invalid"
                                        else:
                                            reason = "delegated_chain_intact"

                assignments.append(current_assignment)
                rows.append(
                    YoutubeCompanyRoleStatus(
                        role_agent_id=agent_id,
                        organizational_level=level,
                        reports_to_agent_id=parent_agent_id,
                        observed_status=reason,
                        agent_instance_id=instance.id if instance is not None else None,
                        assignment_id=(
                            current_assignment.id if current_assignment is not None else None
                        ),
                        organization_unit_id=unit_id,
                        incoming_delegation_id=(
                            current_delegation.id if current_delegation is not None else None
                        ),
                        campaign_id=campaign_id,
                        reservation_expires_at=expiry,
                        project_role_research_tasks_24h=role_tasks,
                        project_role_active_tasks=active_tasks,
                        evidence_of_active_reporting_chain=reason in _ACTIVE_ROLE_STATE,
                    )
                )
            evidence = sum(x.evidence_of_active_reporting_chain for x in rows)
            return YoutubeCompanyOperationalStatus(
                project_id=project.id,
                project_name=project.name,
                project_status=project.status,
                observed_at=observed,
                actual_registry_version=registry.version,
                required_company_registry_version=youtube_company_registry_digest(),
                company_registry_selected=selected,
                unambiguous_active_role_instance_count=sum(
                    instance is not None and instance.status == "active"
                    for instance in selected_instances
                ),
                roles_with_consistent_chain_evidence=evidence,
                all_four_reporting_chains_consistent=selected and evidence == 4,
                roles=tuple(rows),
            )
