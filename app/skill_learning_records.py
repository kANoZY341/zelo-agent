"""Read-only trusted Core snapshots for inert skill-learning proposals.

Only successful Agent-bound task attempts have a qualified retained source.
Other evidence kinds and prior procedure revisions have no Core resolver yet.
Nothing in this module creates a proposal, persists learning, or activates a skill.
"""

import hashlib
import json
import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_execution_contracts import AgentExecutionBinding
from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import AgentInstance, AuditEvent, Project, Task, TaskAttempt
from app.policy import State
from app.schemas import ResearchRunResult
from app.skill_learning import (
    EvidenceKind,
    SkillLearningAgentSnapshot,
    SkillLearningEvidenceSnapshot,
)
from app.storage import SessionStore

_TASK_ATTEMPT_SOURCE = re.compile(
    r"^task:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}):"
    r"attempt:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$"
)
_TASK_ATTEMPT_EVIDENCE_SCHEMA = "zelo-skill-learning-task-attempt-evidence-v1"


def _canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def task_attempt_source_id(task_id: str, attempt_id: str) -> str:
    """Canonical ID for a retained Core task attempt, with no embedded result."""

    if not _canonical_uuid(task_id) or not _canonical_uuid(attempt_id):
        raise ValueError("skill_learning_task_attempt_id_invalid")
    return f"task:{task_id}:attempt:{attempt_id}"


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


class CoreSkillLearningRecords:
    """Fetch only current trusted identity and retained accepted attempt facts."""

    def __init__(self, database: SessionStore, agent_factory: AgentFactory) -> None:
        self.database = database
        self.agent_factory = agent_factory

    def _agent_snapshot(
        self, session: Session, instance_id: str
    ) -> SkillLearningAgentSnapshot | None:
        row = session.get(AgentInstance, instance_id)
        if row is None:
            return None
        project = session.get(Project, row.project_id)
        if project is None or project.status != "active":
            return None
        try:
            spec = AgentSpec.model_validate(row.spec)
            binding = AgentBinding(spec=spec, registry_version=row.registry_version)
            if (
                row.agent_id != spec.agent_id
                or row.agent_version != spec.version
                or not self.agent_factory.matches_agent(binding)
            ):
                return None
            return SkillLearningAgentSnapshot(
                agent_instance_id=row.id,
                agent_id=row.agent_id,
                agent_version=row.agent_version,
                agent_registry_version=row.registry_version,
                status=row.status,
            )
        except (TypeError, ValueError, ValidationError):
            return None

    def agent_snapshot(self, instance_id: str) -> SkillLearningAgentSnapshot | None:
        """Return an exact current AgentInstance snapshot, never a mutable ORM row."""

        if not _canonical_uuid(instance_id):
            return None
        with self.database.read() as session:
            return self._agent_snapshot(session, instance_id)

    def evidence_snapshot(
        self, kind: EvidenceKind, source_id: str
    ) -> SkillLearningEvidenceSnapshot | None:
        """Resolve an owned successful attempt; unsupported sources deny by default."""

        if kind != "task_attempt" or not isinstance(source_id, str):
            return None
        matched = _TASK_ATTEMPT_SOURCE.fullmatch(source_id)
        if matched is None:
            return None
        task_id, attempt_id = matched.groups()
        if not _canonical_uuid(task_id) or not _canonical_uuid(attempt_id):
            return None
        with self.database.read() as session:
            task = session.get(Task, task_id)
            attempt = session.get(TaskAttempt, attempt_id)
            if (
                task is None
                or attempt is None
                or attempt.task_id != task.id
                or task.kind != "research_run"
                or task.status != State.SUCCEEDED
                or attempt.outcome != State.SUCCEEDED
                or attempt.ended_at is None
                or attempt.ended_at < attempt.started_at
                or task.cancel_requested
                or task.error is not None
                or attempt.error is not None
                or not isinstance(attempt.result, dict)
                or task.result != attempt.result
                or not isinstance(task.agent_execution_spec, dict)
            ):
                return None
            completed = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.task_id == task.id,
                    AuditEvent.attempt_id == attempt.id,
                    AuditEvent.event == "execution_completed",
                )
            ).all()
            bound = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.task_id == task.id,
                    AuditEvent.event == "agent_execution_bound",
                )
            ).all()
            if len(completed) != 1 or len(bound) != 1:
                return None
            try:
                execution = AgentExecutionBinding.model_validate(task.agent_execution_spec)
                research = ResearchRunResult.model_validate(attempt.result)
                if (
                    execution.task_kind != task.kind
                    or not attempt.started_at <= research.completed_at <= attempt.ended_at
                    or not isinstance(bound[0].details, dict)
                    or bound[0].details.get("agent_instance_id") != execution.agent_instance_id
                    or bound[0].details.get("agent_id") != execution.spec.agent_id
                    or bound[0].details.get("agent_version") != execution.spec.version
                    or bound[0].details.get("registry_version") != execution.registry_version
                    or bound[0].details.get("project_id") != execution.project_id
                ):
                    return None
                agent = self._agent_snapshot(session, execution.agent_instance_id)
                if (
                    agent is None
                    or agent.status != "active"
                    or agent.agent_registry_version != execution.registry_version
                ):
                    return None
                stored = session.get(AgentInstance, execution.agent_instance_id)
                if (
                    stored is None
                    or stored.project_id != execution.project_id
                    or AgentSpec.model_validate(stored.spec) != execution.spec
                ):
                    return None
                digest = _digest(
                    {
                        "schema": _TASK_ATTEMPT_EVIDENCE_SCHEMA,
                        "task_id": task.id,
                        "attempt_id": attempt.id,
                        "attempt_number": attempt.number,
                        "action_version": task.action_version,
                        "action_hash": task.action_hash,
                        "request_hash": task.request_hash,
                        "agent_execution": execution.model_dump(mode="json"),
                        "outcome": attempt.outcome,
                        "ended_at": attempt.ended_at.isoformat(),
                        "bound_event_id": bound[0].id,
                        "completed_event_id": completed[0].id,
                        "result": research.model_dump(mode="json"),
                    }
                )
                return SkillLearningEvidenceSnapshot(
                    kind="task_attempt",
                    source_id=source_id,
                    content_sha256=digest,
                    owner_agent_instance_id=execution.agent_instance_id,
                    agent_registry_version=execution.registry_version,
                    terminal=True,
                    accepted=True,
                )
            except (TypeError, ValueError, ValidationError):
                return None
