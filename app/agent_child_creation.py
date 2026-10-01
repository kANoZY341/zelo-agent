"""Atomic hierarchy-aware Agent Factory child creation for v0.14."""

from sqlalchemy import func, select

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import (
    AgentChildCreation,
    AgentDelegation,
    AgentInstance,
    AgentInstanceEvent,
    Campaign,
    CampaignBudgetReservation,
    Goal,
    OrganizationAssignment,
    OrganizationUnit,
    Project,
    utcnow,
)
from app.policy import DomainError, fingerprint
from app.schemas import (
    AgentChildCreate,
    AgentChildCreationResponse,
    AgentDelegationResponse,
    AgentInstanceResponse,
    OrganizationAssignmentResponse,
)
from app.storage import SessionStore

_CHILD_LEVEL = {
    "vp": "director",
    "director": "manager",
    "manager": "specialist",
}


class AgentChildCreationService:
    """Create one trusted lower-level subordinate plus one bounded delegation."""

    def __init__(self, database: SessionStore, factory: AgentFactory) -> None:
        self.database = database
        self.factory = factory

    @staticmethod
    def _load_instance(session, instance_id: str, label: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError(f"{label} agent instance not found", 404)
        return instance

    def _current_spec(self, instance: AgentInstance) -> AgentSpec:
        try:
            spec = AgentSpec.model_validate(instance.spec)
            binding = AgentBinding(spec=spec, registry_version=instance.registry_version)
        except ValueError:
            raise DomainError("Agent instance spec is invalid", 409) from None
        if instance.status != "active":
            raise DomainError("Agent instance is not active", 409)
        if not self.factory.matches_agent(binding):
            raise DomainError("Agent instance spec is stale", 409)
        return spec

    @staticmethod
    def _assignment_response(item: OrganizationAssignment) -> OrganizationAssignmentResponse:
        return OrganizationAssignmentResponse(
            id=item.id,
            agent_instance_id=item.agent_instance_id,
            unit_id=item.unit_id,
            level=item.level,
            reports_to_assignment_id=item.reports_to_assignment_id,
            reports_to_principal=None,
            status=item.status,
            created_at=item.created_at,
            ended_at=item.ended_at,
            end_reason=item.end_reason,
        )

    @classmethod
    def _response(cls, session, item: AgentChildCreation) -> AgentChildCreationResponse:
        child = session.get(AgentInstance, item.child_instance_id)
        assignment = session.get(OrganizationAssignment, item.child_assignment_id)
        delegation = session.get(AgentDelegation, item.delegation_id)
        if child is None or assignment is None or delegation is None:
            raise DomainError("Agent child creation receipt is incomplete", 409)
        return AgentChildCreationResponse(
            id=item.id,
            parent_instance_id=item.parent_instance_id,
            parent_assignment_id=item.parent_assignment_id,
            child_instance=AgentInstanceResponse.model_validate(child),
            child_assignment=cls._assignment_response(assignment),
            delegation=AgentDelegationResponse.model_validate(delegation),
            idempotency_key=item.idempotency_key,
            request_hash=item.request_hash,
            created_at=item.created_at,
        )

    @staticmethod
    def _target_unit_valid(
        parent_assignment: OrganizationAssignment,
        target_unit: OrganizationUnit,
        session,
    ) -> bool:
        if parent_assignment.level == "vp":
            return (
                target_unit.unit_type == "department"
                and target_unit.parent_unit_id == parent_assignment.unit_id
            )
        if parent_assignment.level == "director":
            return (
                target_unit.unit_type == "team"
                and target_unit.parent_unit_id == parent_assignment.unit_id
            )
        if parent_assignment.level == "manager":
            return (
                target_unit.unit_type == "team"
                and target_unit.id == parent_assignment.unit_id
            )
        return False

    def create(
        self,
        parent_instance_id: str,
        body: AgentChildCreate,
    ) -> AgentChildCreationResponse:
        request_hash = fingerprint(
            {
                "child_agent_id": body.child_agent_id,
                "unit_id": body.unit_id,
                "campaign_id": body.campaign_id,
                "budget_reservation_id": body.budget_reservation_id,
                "objective": body.objective.strip(),
                "allowed_task_kinds": body.allowed_task_kinds,
                "required_capabilities": body.required_capabilities,
                "max_depth": body.max_depth,
                "budget_resource": body.budget_resource,
                "budget_units": body.budget_units,
            }
        )

        with self.database.transaction() as session:
            existing = session.scalar(
                select(AgentChildCreation).where(
                    AgentChildCreation.parent_instance_id == parent_instance_id,
                    AgentChildCreation.idempotency_key == body.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise DomainError("Agent child creation idempotency conflict", 409)
                return self._response(session, existing)

            parent = self._load_instance(session, parent_instance_id, "Parent")
            parent_spec = self._current_spec(parent)
            project = session.get(Project, parent.project_id)
            if project is None or project.status != "active":
                raise DomainError("Parent Project is not active", 409)

            parent_assignment = session.scalar(
                select(OrganizationAssignment).where(
                    OrganizationAssignment.agent_instance_id == parent.id,
                    OrganizationAssignment.status == "active",
                )
            )
            if parent_assignment is None:
                raise DomainError("Parent agent has no active organization assignment", 409)
            expected_child_level = _CHILD_LEVEL.get(parent_assignment.level)
            if expected_child_level is None:
                raise DomainError("Parent organization level cannot create a permanent child", 409)
            if parent_spec.organizational_level != parent_assignment.level:
                raise DomainError("Parent organization assignment is stale", 409)
            if not parent_spec.allow_subagents or parent_spec.max_subagents < 1:
                raise DomainError("Parent agent is not authorized to create children", 409)

            try:
                child_binding = self.factory.bind_agent(body.child_agent_id)
            except ValueError as error:
                if str(error) == "agent_not_registered":
                    raise DomainError("Child AgentSpec is not registered", 404) from None
                raise DomainError("Child AgentSpec is not active", 409) from None
            child_spec = child_binding.spec
            if child_spec.organizational_level != expected_child_level:
                raise DomainError("Child AgentSpec is not the next organizational level", 409)

            target_unit = session.get(OrganizationUnit, body.unit_id)
            if target_unit is None:
                raise DomainError("Organization unit not found", 404)
            if target_unit.status != "active":
                raise DomainError("Organization unit is not active", 409)
            if not self._target_unit_valid(parent_assignment, target_unit, session):
                raise DomainError("Child organization unit is outside parent reporting scope", 409)

            if expected_child_level in {"director", "manager"} and session.scalar(
                select(OrganizationAssignment.id).where(
                    OrganizationAssignment.unit_id == target_unit.id,
                    OrganizationAssignment.level == expected_child_level,
                    OrganizationAssignment.status == "active",
                )
            ):
                raise DomainError("Organization unit already has an active child leader", 409)

            live_count = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentInstance)
                    .where(
                        AgentInstance.project_id == parent.project_id,
                        AgentInstance.agent_id == child_spec.agent_id,
                        AgentInstance.status != "retired",
                    )
                )
                or 0
            )
            if live_count >= child_spec.max_active_instances:
                raise DomainError("Child AgentSpec instance limit reached for Project", 409)

            active_children = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentDelegation)
                    .where(
                        AgentDelegation.parent_instance_id == parent.id,
                        AgentDelegation.status == "active",
                    )
                )
                or 0
            )
            if active_children >= parent_spec.max_subagents:
                raise DomainError("Parent agent child limit reached", 409)

            allowed_task_kinds = tuple(body.allowed_task_kinds)
            required_capabilities = tuple(body.required_capabilities)
            if not set(allowed_task_kinds).issubset(parent_spec.allowed_task_kinds):
                raise DomainError("Delegated task kind exceeds parent authority", 409)
            if not set(allowed_task_kinds).issubset(child_spec.allowed_task_kinds):
                raise DomainError("Delegated task kind exceeds child authority", 409)
            if not set(required_capabilities).issubset(child_spec.capabilities):
                raise DomainError("Child AgentSpec lacks required capability", 409)

            campaign = session.get(Campaign, body.campaign_id)
            if campaign is None:
                raise DomainError("Campaign not found", 404)
            goal = session.get(Goal, campaign.goal_id)
            if (
                goal is None
                or goal.project_id != parent.project_id
                or campaign.status != "active"
            ):
                raise DomainError("Campaign is not active in the parent Project", 409)

            incoming = session.scalar(
                select(AgentDelegation).where(
                    AgentDelegation.child_instance_id == parent.id,
                    AgentDelegation.status == "active",
                )
            )
            if incoming is None:
                depth = 1
                max_depth = body.max_depth
            else:
                depth = incoming.depth + 1
                max_depth = incoming.max_depth
                if body.max_depth != max_depth:
                    raise DomainError("Nested child creation must inherit max_depth", 409)
            if depth > max_depth:
                raise DomainError("Agent child creation depth limit reached", 409)

            reservation = session.get(
                CampaignBudgetReservation,
                body.budget_reservation_id,
            )
            now = utcnow()
            if (
                reservation is None
                or reservation.campaign_id != campaign.id
                or reservation.owner_id != parent.id
                or reservation.resource != body.budget_resource
                or reservation.units != body.budget_units
                or reservation.status != "reserved"
                or reservation.expires_at <= now
            ):
                raise DomainError("Agent child creation budget proof is invalid", 409)

            child = AgentInstance(
                agent_id=child_spec.agent_id,
                agent_version=child_spec.version,
                registry_version=child_binding.registry_version,
                project_id=parent.project_id,
                spec=child_spec.model_dump(mode="json"),
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(child)
            session.flush()
            session.add(
                AgentInstanceEvent(
                    agent_instance_id=child.id,
                    sequence=1,
                    event="created",
                    details={
                        "agent_id": child_spec.agent_id,
                        "agent_version": child_spec.version,
                        "registry_version": child_binding.registry_version,
                        "project_id": parent.project_id,
                        "created_by_parent_instance_id": parent.id,
                    },
                    created_at=now,
                )
            )

            child_assignment = OrganizationAssignment(
                agent_instance_id=child.id,
                unit_id=target_unit.id,
                level=expected_child_level,
                reports_to_assignment_id=parent_assignment.id,
                status="active",
                created_at=now,
            )
            session.add(child_assignment)
            session.flush()

            delegation = AgentDelegation(
                parent_instance_id=parent.id,
                child_instance_id=child.id,
                campaign_id=campaign.id,
                budget_reservation_id=reservation.id,
                idempotency_key=body.idempotency_key,
                request_hash=request_hash,
                objective=body.objective.strip(),
                allowed_task_kinds=list(allowed_task_kinds),
                required_capabilities=list(required_capabilities),
                depth=depth,
                max_depth=max_depth,
                budget_resource=body.budget_resource,
                budget_units=body.budget_units,
                status="active",
                created_at=now,
            )
            session.add(delegation)
            session.flush()

            receipt = AgentChildCreation(
                parent_instance_id=parent.id,
                parent_assignment_id=parent_assignment.id,
                child_instance_id=child.id,
                child_assignment_id=child_assignment.id,
                delegation_id=delegation.id,
                idempotency_key=body.idempotency_key,
                request_hash=request_hash,
                created_at=now,
            )
            session.add(receipt)
            project.updated_at = now
            session.flush()
            return self._response(session, receipt)
