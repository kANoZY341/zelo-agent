"""Persisted project-bound agent instances for the v0.14 Agent Factory."""

from sqlalchemy import func, select

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.memory import MemoryService
from app.models import (
    AgentComputerPermission,
    AgentInstance,
    AgentInstanceEvent,
    OrganizationAssignment,
    Project,
    utcnow,
)
from app.policy import DomainError
from app.schemas import (
    AgentInstanceCreate,
    AgentInstanceEventPage,
    AgentInstanceEventResponse,
    AgentInstancePage,
    AgentInstanceResponse,
    AgentInstanceStatus,
    AgentInstanceTransition,
    AgentMemoryContextRequest,
    AgentMemoryContextResponse,
    MemoryRetrieveRequest,
    MemoryScope,
)
from app.storage import SessionStore
from app.youtube_company_authority import company_child_requires_atomic_creation


class AgentInstanceService:
    """Own project-bound agent identity and lifecycle without executing agent work."""

    def __init__(
        self,
        database: SessionStore,
        factory: AgentFactory,
        memories: MemoryService,
    ) -> None:
        self.database = database
        self.factory = factory
        self.memories = memories

    @staticmethod
    def _response(instance: AgentInstance) -> AgentInstanceResponse:
        return AgentInstanceResponse.model_validate(instance)

    @staticmethod
    def _event_response(event: AgentInstanceEvent) -> AgentInstanceEventResponse:
        return AgentInstanceEventResponse.model_validate(event)

    @staticmethod
    def _load_instance(session, instance_id: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError("Agent instance not found", 404)
        return instance

    def _current_binding(self, instance: AgentInstance) -> AgentBinding:
        try:
            spec = AgentSpec.model_validate(instance.spec)
        except ValueError:
            raise DomainError("Agent instance spec is invalid", 409) from None
        return AgentBinding(
            spec=spec,
            registry_version=instance.registry_version,
        )

    def _require_current(self, instance: AgentInstance) -> None:
        if not self.factory.matches_agent(self._current_binding(instance)):
            raise DomainError("Agent instance spec is stale", 409)

    @staticmethod
    def _append_event(
        session,
        instance: AgentInstance,
        *,
        event: str,
        details: dict | None = None,
    ) -> AgentInstanceEvent:
        sequence = (
            session.scalar(
                select(func.max(AgentInstanceEvent.sequence)).where(
                    AgentInstanceEvent.agent_instance_id == instance.id
                )
            )
            or 0
        ) + 1
        item = AgentInstanceEvent(
            agent_instance_id=instance.id,
            sequence=sequence,
            event=event,
            details=details or {},
            created_at=utcnow(),
        )
        session.add(item)
        session.flush()
        return item

    def create(self, body: AgentInstanceCreate) -> AgentInstanceResponse:
        try:
            binding = self.factory.bind_agent(body.agent_id)
        except ValueError as error:
            message = str(error)
            if message == "agent_not_registered":
                raise DomainError("Agent not registered", 404) from None
            raise DomainError("Agent is not active", 409) from None

        # Active YouTube company Director/Manager/Specialist Instances must
        # be created *atomically* with Org assignments and Campaign-backed
        # Delegations by AgentChildCreationService, never generic POST /agents.
        if company_child_requires_atomic_creation(binding.spec.agent_id):
            raise DomainError(
                "YouTube company descendants require budget-bound child creation", 409
            )

        now = utcnow()
        with self.database.transaction() as session:
            project = session.get(Project, body.project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            if project.status != "active":
                raise DomainError("Project is not active", 409)

            live_count = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentInstance)
                    .where(
                        AgentInstance.project_id == project.id,
                        AgentInstance.agent_id == binding.spec.agent_id,
                        AgentInstance.status != "retired",
                    )
                )
                or 0
            )
            if live_count >= binding.spec.max_active_instances:
                raise DomainError("Agent instance limit reached for project", 409)

            instance = AgentInstance(
                agent_id=binding.spec.agent_id,
                agent_version=binding.spec.version,
                registry_version=binding.registry_version,
                project_id=project.id,
                spec=binding.spec.model_dump(mode="json"),
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(instance)
            session.flush()
            if binding.spec.computer is not None:
                session.add(
                    AgentComputerPermission(
                        agent_instance_id=instance.id,
                        enabled=False,
                        browser=False,
                        files=False,
                        shell=False,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.flush()
            self._append_event(
                session,
                instance,
                event="created",
                details={
                    "agent_id": binding.spec.agent_id,
                    "agent_version": binding.spec.version,
                    "registry_version": binding.registry_version,
                    "project_id": project.id,
                },
            )
            project.updated_at = now
            session.flush()
            return self._response(instance)

    def get(self, instance_id: str) -> AgentInstanceResponse:
        with self.database.read() as session:
            return self._response(self._load_instance(session, instance_id))

    def list(
        self,
        *,
        project_id: str | None,
        status: AgentInstanceStatus | None,
        limit: int,
        offset: int,
    ) -> AgentInstancePage:
        with self.database.read() as session:
            conditions = []
            if project_id is not None:
                if session.get(Project, project_id) is None:
                    raise DomainError("Project not found", 404)
                conditions.append(AgentInstance.project_id == project_id)
            if status is not None:
                conditions.append(AgentInstance.status == status)

            total = (
                session.scalar(select(func.count()).select_from(AgentInstance).where(*conditions))
                or 0
            )
            instances = list(
                session.scalars(
                    select(AgentInstance)
                    .where(*conditions)
                    .order_by(AgentInstance.updated_at.desc(), AgentInstance.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return AgentInstancePage(
                items=[self._response(instance) for instance in instances],
                limit=limit,
                offset=offset,
                total=total,
            )

    def transition(
        self,
        instance_id: str,
        body: AgentInstanceTransition,
    ) -> AgentInstanceResponse:
        with self.database.transaction() as session:
            instance = self._load_instance(session, instance_id)
            project = session.get(Project, instance.project_id)
            if project is None:
                raise DomainError("Project not found", 404)

            action = body.action
            event: str
            target: AgentInstanceStatus
            if action == "pause":
                if instance.status != "active":
                    raise DomainError("Only an active agent instance can be paused", 409)
                target = "paused"
                event = "paused"
            elif action == "resume":
                if instance.status != "paused":
                    raise DomainError("Only a paused agent instance can be resumed", 409)
                if project.status != "active":
                    raise DomainError("Project is not active", 409)
                self._require_current(instance)
                target = "active"
                event = "resumed"
            else:
                if instance.status not in {"active", "paused"}:
                    raise DomainError("Retired agent instances are terminal", 409)
                target = "retired"
                event = "retired"

            if target != "active":
                active_assignment = session.scalar(
                    select(OrganizationAssignment.id).where(
                        OrganizationAssignment.agent_instance_id == instance.id,
                        OrganizationAssignment.status == "active",
                    )
                )
                if active_assignment is not None:
                    raise DomainError(
                        "Active organization assignment must end before agent lifecycle change",
                        409,
                    )

            now = utcnow()
            previous = instance.status
            instance.status = target
            instance.updated_at = now
            self._append_event(
                session,
                instance,
                event=event,
                details={"from": previous, "to": target},
            )
            project.updated_at = now
            session.flush()
            return self._response(instance)

    def events(
        self,
        instance_id: str,
        *,
        limit: int,
        offset: int,
    ) -> AgentInstanceEventPage:
        with self.database.read() as session:
            self._load_instance(session, instance_id)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentInstanceEvent)
                    .where(AgentInstanceEvent.agent_instance_id == instance_id)
                )
                or 0
            )
            events = list(
                session.scalars(
                    select(AgentInstanceEvent)
                    .where(AgentInstanceEvent.agent_instance_id == instance_id)
                    .order_by(AgentInstanceEvent.sequence)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return AgentInstanceEventPage(
                items=[self._event_response(event) for event in events],
                limit=limit,
                offset=offset,
                total=total,
            )

    def memory_context(
        self,
        instance_id: str,
        body: AgentMemoryContextRequest,
    ) -> AgentMemoryContextResponse:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            if instance.status != "active":
                raise DomainError("Agent instance is not active", 409)
            project = session.get(Project, instance.project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            if project.status != "active":
                raise DomainError("Project is not active", 409)
            self._require_current(instance)
            response = self._response(instance)

        scopes = [
            MemoryScope(kind="project", id=response.project_id),
            MemoryScope(kind="agent", id=response.agent_id),
        ]
        retrieval = self.memories.retrieve(
            MemoryRetrieveRequest(
                query=body.query,
                scopes=scopes,
                limit=body.limit,
            )
        )
        return AgentMemoryContextResponse(
            instance=response,
            scopes=scopes,
            retrieval=retrieval,
        )
