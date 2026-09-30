"""Durable organizational units and reporting assignments for v0.14."""

from sqlalchemy import func, select

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import (
    AgentInstance,
    OrganizationAssignment,
    OrganizationUnit,
    utcnow,
)
from app.policy import DomainError
from app.schemas import (
    OrganizationAssignmentCreate,
    OrganizationAssignmentEnd,
    OrganizationAssignmentPage,
    OrganizationAssignmentResponse,
    OrganizationAssignmentStatus,
    OrganizationSnapshot,
    OrganizationUnitCreate,
    OrganizationUnitPage,
    OrganizationUnitResponse,
    OrganizationUnitStatus,
)
from app.storage import SessionStore

MANAGING_DIRECTOR_PRINCIPAL = "human_managing_director"
ZELO_CEO_PRINCIPAL = "zelo_ceo"

_PARENT_LEVEL = {
    "director": "vp",
    "manager": "director",
    "specialist": "manager",
}
_UNIT_FOR_LEVEL = {
    "vp": "division",
    "director": "department",
    "manager": "team",
    "specialist": "team",
}


class OrganizationService:
    """Own the durable org chart without widening execution authority."""

    def __init__(self, database: SessionStore, factory: AgentFactory) -> None:
        self.database = database
        self.factory = factory

    @staticmethod
    def _unit_response(unit: OrganizationUnit) -> OrganizationUnitResponse:
        return OrganizationUnitResponse.model_validate(unit)

    @staticmethod
    def _assignment_response(
        assignment: OrganizationAssignment,
    ) -> OrganizationAssignmentResponse:
        return OrganizationAssignmentResponse(
            id=assignment.id,
            agent_instance_id=assignment.agent_instance_id,
            unit_id=assignment.unit_id,
            level=assignment.level,
            reports_to_assignment_id=assignment.reports_to_assignment_id,
            reports_to_principal=(
                ZELO_CEO_PRINCIPAL
                if assignment.level == "vp"
                and assignment.reports_to_assignment_id is None
                else None
            ),
            status=assignment.status,
            created_at=assignment.created_at,
            ended_at=assignment.ended_at,
            end_reason=assignment.end_reason,
        )

    @staticmethod
    def _load_unit(session, unit_id: str) -> OrganizationUnit:
        unit = session.get(OrganizationUnit, unit_id)
        if unit is None:
            raise DomainError("Organization unit not found", 404)
        return unit

    @staticmethod
    def _load_assignment(session, assignment_id: str) -> OrganizationAssignment:
        assignment = session.get(OrganizationAssignment, assignment_id)
        if assignment is None:
            raise DomainError("Organization assignment not found", 404)
        return assignment

    @staticmethod
    def _normalize_name(value: str) -> str:
        name = " ".join(value.split())
        if not name:
            raise DomainError("Organization unit name cannot be blank", 422)
        return name

    def _require_current_instance(self, session, instance_id: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError("Agent instance not found", 404)
        if instance.status != "active":
            raise DomainError("Organization assignment requires an active agent instance", 409)
        try:
            spec = AgentSpec.model_validate(instance.spec)
            binding = AgentBinding(spec=spec, registry_version=instance.registry_version)
        except ValueError:
            raise DomainError("Agent instance spec is invalid", 409) from None
        if not self.factory.matches_agent(binding):
            raise DomainError("Agent instance spec is stale", 409)
        return instance

    def create_unit(self, body: OrganizationUnitCreate) -> OrganizationUnitResponse:
        name = self._normalize_name(body.name)
        with self.database.transaction() as session:
            parent = None
            if body.parent_unit_id is not None:
                parent = self._load_unit(session, body.parent_unit_id)
                if parent.status != "active":
                    raise DomainError("Organization parent unit is not active", 409)

            if body.unit_type == "division":
                if parent is not None:
                    raise DomainError("Division cannot have an organization parent", 422)
            elif body.unit_type == "department":
                if parent is None or parent.unit_type != "division":
                    raise DomainError("Department must belong to an active division", 422)
            else:
                if parent is None or parent.unit_type != "department":
                    raise DomainError("Team must belong to an active department", 422)

            duplicate = session.scalar(
                select(OrganizationUnit.id).where(
                    OrganizationUnit.status == "active",
                    func.lower(OrganizationUnit.name) == name.lower(),
                    OrganizationUnit.parent_unit_id.is_(body.parent_unit_id),
                )
            )
            if duplicate is not None:
                raise DomainError("Active organization unit already exists", 409)

            now = utcnow()
            unit = OrganizationUnit(
                name=name,
                unit_type=body.unit_type,
                parent_unit_id=body.parent_unit_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(unit)
            session.flush()
            return self._unit_response(unit)

    def list_units(
        self,
        *,
        status: OrganizationUnitStatus | None,
        limit: int,
        offset: int,
    ) -> OrganizationUnitPage:
        with self.database.read() as session:
            conditions = []
            if status is not None:
                conditions.append(OrganizationUnit.status == status)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(OrganizationUnit)
                    .where(*conditions)
                )
                or 0
            )
            items = list(
                session.scalars(
                    select(OrganizationUnit)
                    .where(*conditions)
                    .order_by(OrganizationUnit.created_at, OrganizationUnit.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return OrganizationUnitPage(
                items=[self._unit_response(item) for item in items],
                limit=limit,
                offset=offset,
                total=total,
            )

    def retire_unit(self, unit_id: str) -> OrganizationUnitResponse:
        with self.database.transaction() as session:
            unit = self._load_unit(session, unit_id)
            if unit.status == "retired":
                return self._unit_response(unit)

            active_child = session.scalar(
                select(OrganizationUnit.id).where(
                    OrganizationUnit.parent_unit_id == unit.id,
                    OrganizationUnit.status == "active",
                )
            )
            if active_child is not None:
                raise DomainError("Organization unit still has active child units", 409)
            active_assignment = session.scalar(
                select(OrganizationAssignment.id).where(
                    OrganizationAssignment.unit_id == unit.id,
                    OrganizationAssignment.status == "active",
                )
            )
            if active_assignment is not None:
                raise DomainError("Organization unit still has active assignments", 409)

            now = utcnow()
            unit.status = "retired"
            unit.retired_at = now
            unit.updated_at = now
            session.flush()
            return self._unit_response(unit)

    def _validate_assignment_relation(
        self,
        session,
        body: OrganizationAssignmentCreate,
        unit: OrganizationUnit,
        holder: AgentInstance,
    ) -> None:
        expected_unit_type = _UNIT_FOR_LEVEL[body.level]
        if unit.unit_type != expected_unit_type:
            raise DomainError(
                f"{body.level} assignment requires a {expected_unit_type} unit",
                422,
            )

        if body.level == "vp":
            if body.reports_to_assignment_id is not None:
                raise DomainError("VP reports to Zelo CEO, not another assignment", 422)
            return

        if body.reports_to_assignment_id is None:
            raise DomainError(f"{body.level} assignment requires a reporting parent", 422)

        parent = self._load_assignment(session, body.reports_to_assignment_id)
        if parent.status != "active":
            raise DomainError("Reporting parent assignment is not active", 409)
        if parent.agent_instance_id == holder.id:
            raise DomainError("Agent instance cannot report to itself", 422)

        self._require_current_instance(session, parent.agent_instance_id)
        expected_parent_level = _PARENT_LEVEL[body.level]
        if parent.level != expected_parent_level:
            raise DomainError(
                f"{body.level} must report to {expected_parent_level}",
                422,
            )

        if body.level in {"director", "manager"}:
            if unit.parent_unit_id != parent.unit_id:
                raise DomainError("Reporting parent does not own the parent organization unit", 409)
        elif parent.unit_id != unit.id:
            raise DomainError("Specialist must report to a manager in the same team", 409)

    def create_assignment(
        self,
        body: OrganizationAssignmentCreate,
    ) -> OrganizationAssignmentResponse:
        with self.database.transaction() as session:
            holder = self._require_current_instance(session, body.agent_instance_id)
            unit = self._load_unit(session, body.unit_id)
            if unit.status != "active":
                raise DomainError("Organization unit is not active", 409)

            if session.scalar(
                select(OrganizationAssignment.id).where(
                    OrganizationAssignment.agent_instance_id == holder.id,
                    OrganizationAssignment.status == "active",
                )
            ):
                raise DomainError("Agent instance already has an active organization assignment", 409)

            if body.level in {"vp", "director", "manager"} and session.scalar(
                select(OrganizationAssignment.id).where(
                    OrganizationAssignment.unit_id == unit.id,
                    OrganizationAssignment.level == body.level,
                    OrganizationAssignment.status == "active",
                )
            ):
                raise DomainError("Organization unit already has an active leader at this level", 409)

            self._validate_assignment_relation(session, body, unit, holder)

            assignment = OrganizationAssignment(
                agent_instance_id=holder.id,
                unit_id=unit.id,
                level=body.level,
                reports_to_assignment_id=body.reports_to_assignment_id,
                status="active",
                created_at=utcnow(),
            )
            session.add(assignment)
            session.flush()
            return self._assignment_response(assignment)

    def list_assignments(
        self,
        *,
        unit_id: str | None,
        status: OrganizationAssignmentStatus | None,
        limit: int,
        offset: int,
    ) -> OrganizationAssignmentPage:
        with self.database.read() as session:
            conditions = []
            if unit_id is not None:
                self._load_unit(session, unit_id)
                conditions.append(OrganizationAssignment.unit_id == unit_id)
            if status is not None:
                conditions.append(OrganizationAssignment.status == status)

            total = (
                session.scalar(
                    select(func.count())
                    .select_from(OrganizationAssignment)
                    .where(*conditions)
                )
                or 0
            )
            items = list(
                session.scalars(
                    select(OrganizationAssignment)
                    .where(*conditions)
                    .order_by(
                        OrganizationAssignment.created_at,
                        OrganizationAssignment.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            return OrganizationAssignmentPage(
                items=[self._assignment_response(item) for item in items],
                limit=limit,
                offset=offset,
                total=total,
            )

    def end_assignment(
        self,
        assignment_id: str,
        body: OrganizationAssignmentEnd,
    ) -> OrganizationAssignmentResponse:
        reason = body.reason.strip()
        if not reason:
            raise DomainError("Organization assignment end reason cannot be blank", 422)

        with self.database.transaction() as session:
            assignment = self._load_assignment(session, assignment_id)
            if assignment.status == "ended":
                return self._assignment_response(assignment)

            active_report = session.scalar(
                select(OrganizationAssignment.id).where(
                    OrganizationAssignment.reports_to_assignment_id == assignment.id,
                    OrganizationAssignment.status == "active",
                )
            )
            if active_report is not None:
                raise DomainError("Organization assignment still has active reports", 409)

            assignment.status = "ended"
            assignment.ended_at = utcnow()
            assignment.end_reason = reason
            session.flush()
            return self._assignment_response(assignment)

    def snapshot(self) -> OrganizationSnapshot:
        units = self.list_units(status="active", limit=100, offset=0).items
        assignments = self.list_assignments(
            unit_id=None,
            status="active",
            limit=100,
            offset=0,
        ).items
        return OrganizationSnapshot(
            managing_director={
                "id": MANAGING_DIRECTOR_PRINCIPAL,
                "role": "managing_director",
                "kind": "human",
                "reports_to": None,
            },
            ceo={
                "id": ZELO_CEO_PRINCIPAL,
                "role": "ceo",
                "kind": "system",
                "reports_to": MANAGING_DIRECTOR_PRINCIPAL,
            },
            units=units,
            assignments=assignments,
        )
