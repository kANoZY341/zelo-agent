"""Bounded parent-child agent delegation contracts for v0.14."""

from sqlalchemy import func, select

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.campaign_control import CampaignControlService
from app.models import (
    AgentDelegation,
    AgentInstance,
    Campaign,
    CampaignBudgetReservation,
    Goal,
    utcnow,
)
from app.policy import DomainError, fingerprint
from app.schemas import (
    AgentDelegationCreate,
    AgentDelegationPage,
    AgentDelegationResponse,
    AgentDelegationRevoke,
)
from app.storage import SessionStore
from app.youtube_company_authority import deny_generic_company_executive_delegation


class AgentDelegationService:
    """Own delegation contracts without executing child-agent work."""

    def __init__(
        self,
        database: SessionStore,
        factory: AgentFactory,
        campaign_controls: CampaignControlService,
    ) -> None:
        self.database = database
        self.factory = factory
        self.campaign_controls = campaign_controls

    @staticmethod
    def _load_instance(session, instance_id: str, label: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError(f"{label} agent instance not found", 404)
        return instance

    @staticmethod
    def _load_binding(instance: AgentInstance) -> AgentBinding:
        try:
            spec = AgentSpec.model_validate(instance.spec)
            return AgentBinding(spec=spec, registry_version=instance.registry_version)
        except ValueError:
            raise DomainError("Agent instance spec is invalid", 409) from None

    def _require_current(self, instance: AgentInstance) -> AgentSpec:
        binding = self._load_binding(instance)
        if not self.factory.matches_agent(binding):
            raise DomainError("Agent instance spec is stale", 409)
        return binding.spec

    @staticmethod
    def _response(item: AgentDelegation) -> AgentDelegationResponse:
        return AgentDelegationResponse.model_validate(item)

    @staticmethod
    def _canonical(values: list[str]) -> tuple[str, ...]:
        return tuple(values)

    @staticmethod
    def _would_cycle(session, parent_id: str, child_id: str) -> bool:
        edges = session.execute(
            select(
                AgentDelegation.parent_instance_id,
                AgentDelegation.child_instance_id,
            ).where(AgentDelegation.status == "active")
        ).all()
        adjacency: dict[str, set[str]] = {}
        for existing_parent, existing_child in edges:
            adjacency.setdefault(existing_parent, set()).add(existing_child)

        pending = [child_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == parent_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(sorted(adjacency.get(current, ())))
        return False

    def create(
        self,
        parent_instance_id: str,
        body: AgentDelegationCreate,
    ) -> AgentDelegationResponse:
        request_hash = fingerprint(
            {
                "child_instance_id": body.child_instance_id,
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
            parent = self._load_instance(session, parent_instance_id, "Parent")
            child = self._load_instance(session, body.child_instance_id, "Child")

            existing = session.scalar(
                select(AgentDelegation).where(
                    AgentDelegation.parent_instance_id == parent.id,
                    AgentDelegation.idempotency_key == body.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise DomainError("Agent delegation idempotency conflict", 409)
                return self._response(existing)

            if parent.status != "active" or child.status != "active":
                raise DomainError("Delegation requires active parent and child instances", 409)
            if parent.id == child.id:
                raise DomainError("Agent cannot delegate to itself", 422)
            if parent.project_id != child.project_id:
                raise DomainError("Delegation must stay within one Project", 409)

            parent_spec = self._require_current(parent)
            child_spec = self._require_current(child)
            # An executive cannot evade real company hiring/revocation
            # controls by delegating separately to a generic AgentInstance.
            deny_generic_company_executive_delegation(parent_spec.agent_id)
            if not parent_spec.allow_subagents or parent_spec.max_subagents < 1:
                raise DomainError("Parent agent is not authorized to delegate", 409)

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
                raise DomainError("Parent agent subagent limit reached", 409)

            if session.scalar(
                select(AgentDelegation.id).where(
                    AgentDelegation.child_instance_id == child.id,
                    AgentDelegation.status == "active",
                )
            ):
                raise DomainError("Child agent already has an active parent", 409)

            if self._would_cycle(session, parent.id, child.id):
                raise DomainError("Agent delegation cycle is not allowed", 409)

            allowed_task_kinds = self._canonical(body.allowed_task_kinds)
            required_capabilities = self._canonical(body.required_capabilities)
            if not set(allowed_task_kinds).issubset(parent_spec.allowed_task_kinds):
                raise DomainError("Delegated task kind exceeds parent authority", 409)
            if not set(allowed_task_kinds).issubset(child_spec.allowed_task_kinds):
                raise DomainError("Delegated task kind exceeds child authority", 409)
            if not set(required_capabilities).issubset(child_spec.capabilities):
                raise DomainError("Child agent lacks required capability", 409)

            campaign = session.get(Campaign, body.campaign_id)
            if campaign is None:
                raise DomainError("Campaign not found", 404)
            goal = session.get(Goal, campaign.goal_id)
            if goal is None or goal.project_id != parent.project_id or campaign.status != "active":
                raise DomainError("Delegation campaign is not active in the bound Project", 409)

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
                    raise DomainError("Nested delegation must inherit max_depth", 409)
            if depth > max_depth:
                raise DomainError("Agent delegation depth limit reached", 409)

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
                raise DomainError("Agent delegation budget proof is invalid", 409)

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
            return self._response(delegation)

    def get(self, delegation_id: str) -> AgentDelegationResponse:
        with self.database.read() as session:
            item = session.get(AgentDelegation, delegation_id)
            if item is None:
                raise DomainError("Agent delegation not found", 404)
            return self._response(item)

    def list(
        self,
        parent_instance_id: str,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> AgentDelegationPage:
        with self.database.read() as session:
            self._load_instance(session, parent_instance_id, "Parent")
            conditions = [AgentDelegation.parent_instance_id == parent_instance_id]
            if status is not None:
                conditions.append(AgentDelegation.status == status)
            total = (
                session.scalar(select(func.count()).select_from(AgentDelegation).where(*conditions))
                or 0
            )
            items = list(
                session.scalars(
                    select(AgentDelegation)
                    .where(*conditions)
                    .order_by(AgentDelegation.created_at, AgentDelegation.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return AgentDelegationPage(
                items=[self._response(item) for item in items],
                limit=limit,
                offset=offset,
                total=total,
            )

    def revoke(
        self,
        delegation_id: str,
        body: AgentDelegationRevoke,
    ) -> AgentDelegationResponse:
        with self.database.transaction() as session:
            item = session.get(AgentDelegation, delegation_id)
            if item is None:
                raise DomainError("Agent delegation not found", 404)
            if item.status == "revoked":
                return self._response(item)

            now = utcnow()
            item.status = "revoked"
            item.revoked_at = now
            item.revocation_reason = body.reason.strip()
            session.flush()

            # Keep delegation revocation and unused-budget release atomic. The
            # Campaign control service remains the authority for reservation mutation.
            self.campaign_controls.release_delegation_budget(
                session,
                item.campaign_id,
                item.budget_reservation_id,
                now=now,
            )
            session.flush()
            return self._response(item)
