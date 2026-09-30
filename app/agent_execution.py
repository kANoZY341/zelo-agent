"""Trusted v0.14 bridge from persisted agent identity to normal Core tasks."""

from sqlalchemy.orm import Session

from app.agent_execution_contracts import (
    AgentExecutionBinding,
    DelegationExecutionBinding,
)
from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import AgentDelegation, AgentInstance, Project
from app.policy import DomainError
from app.schemas import AgentTaskCreate, TaskResponse
from app.storage import SessionStore
from app.tasks import TaskService


class AgentExecutionService:
    """Construct trusted execution bindings; TaskService remains task authority."""

    def __init__(
        self,
        database: SessionStore,
        factory: AgentFactory,
        tasks: TaskService,
    ) -> None:
        self.database = database
        self.factory = factory
        self.tasks = tasks

    @staticmethod
    def _load_instance(session: Session, instance_id: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError("Agent instance not found", 404)
        return instance

    def _current_spec(self, instance: AgentInstance) -> AgentSpec:
        if instance.status != "active":
            raise DomainError("Agent instance is not active", 409)
        try:
            spec = AgentSpec.model_validate(instance.spec)
            binding = AgentBinding(
                spec=spec,
                registry_version=instance.registry_version,
            )
        except ValueError:
            raise DomainError("Agent instance spec is invalid", 409) from None
        if not self.factory.matches_agent(binding):
            raise DomainError("Agent instance spec is stale", 409)
        return spec

    @staticmethod
    def _delegation_binding(item: AgentDelegation) -> DelegationExecutionBinding:
        return DelegationExecutionBinding(
            delegation_id=item.id,
            parent_instance_id=item.parent_instance_id,
            child_instance_id=item.child_instance_id,
            campaign_id=item.campaign_id,
            budget_reservation_id=item.budget_reservation_id,
            idempotency_key=item.idempotency_key,
            request_hash=item.request_hash,
            objective=item.objective,
            allowed_task_kinds=tuple(item.allowed_task_kinds),
            required_capabilities=tuple(item.required_capabilities),
            depth=item.depth,
            max_depth=item.max_depth,
            budget_resource=item.budget_resource,
            budget_units=item.budget_units,
        )

    def _binding(
        self,
        session: Session,
        instance_id: str,
        body: AgentTaskCreate,
    ) -> AgentExecutionBinding:
        instance = self._load_instance(session, instance_id)
        project = session.get(Project, instance.project_id)
        if project is None:
            raise DomainError("Project not found", 404)
        if project.status != "active":
            raise DomainError("Project is not active", 409)

        spec = self._current_spec(instance)
        task = body.task
        if task.kind not in spec.allowed_task_kinds:
            raise DomainError("Agent is not authorized for this task kind", 409)

        delegated = None
        if body.delegation_id is not None:
            item = session.get(AgentDelegation, body.delegation_id)
            if item is None:
                raise DomainError("Agent delegation not found", 404)
            if item.status != "active":
                raise DomainError("Agent delegation is not active", 409)
            if item.child_instance_id != instance.id:
                raise DomainError("Agent delegation does not belong to this child", 409)
            if task.campaign_id != item.campaign_id:
                raise DomainError("Delegated agent task must use the delegation Campaign", 409)
            if task.kind not in item.allowed_task_kinds:
                raise DomainError("Delegation does not authorize this task kind", 409)
            if not set(item.required_capabilities).issubset(spec.capabilities):
                raise DomainError("Agent does not satisfy delegation capabilities", 409)
            delegated = self._delegation_binding(item)

        return AgentExecutionBinding(
            agent_instance_id=instance.id,
            project_id=instance.project_id,
            registry_version=instance.registry_version,
            spec=spec,
            task_kind=task.kind,
            delegation=delegated,
        )

    def submit(
        self,
        instance_id: str,
        body: AgentTaskCreate,
        key: str | None,
    ) -> tuple[TaskResponse, bool]:
        with self.database.read() as session:
            binding = self._binding(session, instance_id, body)
        return self.tasks.submit(body.task, key, binding)
