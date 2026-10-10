"""Relational state. Domain services own lifecycle mutations."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores UTC without a timezone; application values are always aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Expected an aware timestamp")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value else None


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    timeout_seconds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), index=True)
    action_version: Mapped[int] = mapped_column(Integer, default=1)
    action_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(256))
    selected_alias: Mapped[str | None] = mapped_column(String(64))
    selected_model: Mapped[str | None] = mapped_column(String(128))
    config_version: Mapped[str | None] = mapped_column(String(64))
    capacity_backoff_attempts: Mapped[int] = mapped_column(Integer, default=0)
    capacity_backoff_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    developer_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    research_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    python_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    api_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    browser_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    computer_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    shell_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    promotion_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    merge_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    release_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    agent_execution_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (UniqueConstraint("task_id", "number", name="uq_attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(256))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    action_version: Mapped[int] = mapped_column(Integer)
    action_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("task_attempts.id"))
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    event: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','completed','archived')",
            name="ck_project_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentInstance(Base):
    __tablename__ = "agent_instances"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','retired')",
            name="ck_agent_instance_status",
        ),
        CheckConstraint("agent_version >= 1", name="ck_agent_instance_version"),
        CheckConstraint(
            "length(registry_version) = 64",
            name="ck_agent_instance_registry_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[int] = mapped_column(Integer)
    registry_version: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentComputerPermission(Base):
    __tablename__ = "agent_computer_permissions"
    __table_args__ = (
        CheckConstraint(
            "enabled IN (0,1) AND browser IN (0,1) AND files IN (0,1) AND shell IN (0,1)",
            name="ck_agent_computer_permission_booleans",
        ),
    )

    agent_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    browser: Mapped[bool] = mapped_column(Boolean, default=False)
    files: Mapped[bool] = mapped_column(Boolean, default=False)
    shell: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentComputerEvent(Base):
    __tablename__ = "agent_computer_events"
    __table_args__ = (
        UniqueConstraint(
            "agent_instance_id",
            "sequence",
            name="uq_agent_computer_event_sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_agent_computer_event_sequence"),
        CheckConstraint(
            "actor IN ('owner','system')",
            name="ck_agent_computer_event_actor",
        ),
        CheckConstraint(
            "outcome IN ('succeeded','failed')",
            name="ck_agent_computer_event_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentInstanceEvent(Base):
    __tablename__ = "agent_instance_events"
    __table_args__ = (
        UniqueConstraint(
            "agent_instance_id",
            "sequence",
            name="uq_agent_instance_event_sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_agent_instance_event_sequence"),
        CheckConstraint(
            "event IN ('created','paused','resumed','retired')",
            name="ck_agent_instance_event_name",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentDelegation(Base):
    __tablename__ = "agent_delegations"
    __table_args__ = (
        UniqueConstraint(
            "parent_instance_id",
            "idempotency_key",
            name="uq_agent_delegation_idempotency",
        ),
        UniqueConstraint(
            "budget_reservation_id",
            name="uq_agent_delegation_budget_reservation",
        ),
        CheckConstraint(
            "parent_instance_id <> child_instance_id",
            name="ck_agent_delegation_not_self",
        ),
        CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_agent_delegation_status",
        ),
        CheckConstraint(
            "depth >= 1 AND depth <= max_depth AND max_depth <= 4",
            name="ck_agent_delegation_depth",
        ),
        CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_agent_delegation_revocation_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        index=True,
    )
    child_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        index=True,
    )
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    budget_reservation_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_budget_reservations.id"),
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    objective: Mapped[str] = mapped_column(Text)
    allowed_task_kinds: Mapped[list[str]] = mapped_column(JSON)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON)
    depth: Mapped[int] = mapped_column(Integer)
    max_depth: Mapped[int] = mapped_column(Integer)
    budget_resource: Mapped[str] = mapped_column(String(64))
    budget_units: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revocation_reason: Mapped[str | None] = mapped_column(String(512))


class AgentChildCreation(Base):
    __tablename__ = "agent_child_creations"
    __table_args__ = (
        UniqueConstraint(
            "parent_instance_id",
            "idempotency_key",
            name="uq_agent_child_creation_idempotency",
        ),
        UniqueConstraint(
            "child_instance_id",
            name="uq_agent_child_creation_child",
        ),
        UniqueConstraint(
            "child_assignment_id",
            name="uq_agent_child_creation_assignment",
        ),
        UniqueConstraint(
            "delegation_id",
            name="uq_agent_child_creation_delegation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        index=True,
    )
    parent_assignment_id: Mapped[str] = mapped_column(
        ForeignKey("organization_assignments.id"),
        index=True,
    )
    child_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        unique=True,
    )
    child_assignment_id: Mapped[str] = mapped_column(
        ForeignKey("organization_assignments.id"),
        unique=True,
    )
    delegation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_delegations.id"),
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OrganizationUnit(Base):
    __tablename__ = "organization_units"
    __table_args__ = (
        CheckConstraint(
            "unit_type IN ('division','department','team')",
            name="ck_organization_unit_type",
        ),
        CheckConstraint(
            "status IN ('active','retired')",
            name="ck_organization_unit_status",
        ),
        CheckConstraint(
            "(status = 'active' AND retired_at IS NULL) OR "
            "(status = 'retired' AND retired_at IS NOT NULL)",
            name="ck_organization_unit_retirement_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    unit_type: Mapped[str] = mapped_column(String(16), index=True)
    parent_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization_units.id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class OrganizationAssignment(Base):
    __tablename__ = "organization_assignments"
    __table_args__ = (
        CheckConstraint(
            "level IN ('vp','director','manager','specialist')",
            name="ck_organization_assignment_level",
        ),
        CheckConstraint(
            "status IN ('active','ended')",
            name="ck_organization_assignment_status",
        ),
        CheckConstraint(
            "(status = 'active' AND ended_at IS NULL AND end_reason IS NULL) OR "
            "(status = 'ended' AND ended_at IS NOT NULL AND end_reason IS NOT NULL)",
            name="ck_organization_assignment_end_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_instance_id: Mapped[str] = mapped_column(
        ForeignKey("agent_instances.id"),
        index=True,
    )
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("organization_units.id"),
        index=True,
    )
    level: Mapped[str] = mapped_column(String(16), index=True)
    reports_to_assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization_assignments.id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    end_reason: Mapped[str | None] = mapped_column(String(512))


class MemoryRecord(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        UniqueConstraint("replaces_id", name="uq_memory_record_replaces"),
        CheckConstraint(
            "scope_kind IN ('personal','project','agent')",
            name="ck_memory_record_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind = 'personal' AND scope_id IS NULL) OR "
            "(scope_kind IN ('project','agent') AND scope_id IS NOT NULL)",
            name="ck_memory_record_scope_shape",
        ),
        CheckConstraint(
            "kind IN ('fact','preference','decision','experience')",
            name="ck_memory_record_kind",
        ),
        CheckConstraint(
            "provenance_kind IN ('operator','conversation_turn','task','project','agent','import')",
            name="ck_memory_record_provenance_kind",
        ),
        CheckConstraint(
            "(replaces_id IS NULL AND replacement_kind IS NULL) OR "
            "(replaces_id IS NOT NULL AND replacement_kind IN ('correction','supersession'))",
            name="ck_memory_record_replacement_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope_kind: Mapped[str] = mapped_column(String(16), index=True)
    scope_id: Mapped[str | None] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text)
    provenance_kind: Mapped[str] = mapped_column(String(32))
    provenance_ref: Mapped[str | None] = mapped_column(String(256))
    provenance_note: Mapped[str] = mapped_column(String(1024), default="")
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    replaces_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_records.id"),
        unique=True,
    )
    replacement_kind: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','cancelled')",
            name="ck_goal_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','completed','cancelled')",
            name="ck_campaign_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignTaskLink(Base):
    __tablename__ = "campaign_task_links"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
        CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependency_not_self"),
    )

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    depends_on_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)


class CampaignEvent(Base):
    __tablename__ = "campaign_events"
    __table_args__ = (
        UniqueConstraint("campaign_id", "sequence", name="uq_campaign_event_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CampaignCheckpoint(Base):
    __tablename__ = "campaign_checkpoints"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_campaign_checkpoint_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignLease(Base):
    __tablename__ = "campaign_leases"
    __table_args__ = (
        UniqueConstraint("lease_token", name="uq_campaign_lease_token"),
        CheckConstraint("generation >= 0", name="ck_campaign_lease_generation"),
        CheckConstraint("length(scope) BETWEEN 1 AND 64", name="ck_campaign_lease_scope"),
        CheckConstraint(
            "(holder_id IS NULL AND lease_token IS NULL AND acquired_at IS NULL "
            "AND expires_at IS NULL) OR "
            "(holder_id IS NOT NULL AND lease_token IS NOT NULL AND acquired_at IS NOT NULL "
            "AND expires_at IS NOT NULL)",
            name="ck_campaign_lease_shape",
        ),
    )

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    holder_id: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    acquired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignBudgetLimit(Base):
    __tablename__ = "campaign_budget_limits"
    __table_args__ = (
        CheckConstraint("limit_units >= 0", name="ck_campaign_budget_limit_nonnegative"),
        CheckConstraint("length(resource) BETWEEN 1 AND 64", name="ck_campaign_budget_resource"),
    )

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    resource: Mapped[str] = mapped_column(String(64), primary_key=True)
    limit_units: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignBudgetReservation(Base):
    __tablename__ = "campaign_budget_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "resource"],
            ["campaign_budget_limits.campaign_id", "campaign_budget_limits.resource"],
            name="fk_campaign_budget_reservation_limit",
        ),
        UniqueConstraint(
            "campaign_id",
            "idempotency_key",
            name="uq_campaign_budget_reservation_idempotency",
        ),
        CheckConstraint("units > 0", name="ck_campaign_budget_reservation_units"),
        CheckConstraint(
            "committed_units >= 0 AND committed_units <= units",
            name="ck_campaign_budget_reservation_committed_units",
        ),
        CheckConstraint(
            "status IN ('reserved','committed','released','expired')",
            name="ck_campaign_budget_reservation_status",
        ),
        CheckConstraint(
            "status = 'committed' OR committed_units = 0",
            name="ck_campaign_budget_reservation_terminal_units",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    resource: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    units: Mapped[int] = mapped_column(Integer)
    committed_units: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignSchedule(Base):
    __tablename__ = "campaign_schedules"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "idempotency_key",
            name="uq_campaign_schedule_idempotency",
        ),
        CheckConstraint(
            "status IN ('active','completed','cancelled')",
            name="ck_campaign_schedule_status",
        ),
        CheckConstraint(
            "max_runs BETWEEN 1 AND 100",
            name="ck_campaign_schedule_max_runs",
        ),
        CheckConstraint(
            "run_count >= 0 AND run_count <= max_runs",
            name="ck_campaign_schedule_run_count",
        ),
        CheckConstraint(
            "(interval_seconds IS NULL AND max_runs = 1) OR "
            "(interval_seconds BETWEEN 60 AND 86400 AND max_runs > 1)",
            name="ck_campaign_schedule_cadence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    prompt: Mapped[str] = mapped_column(String(16384))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    max_runs: Mapped[int] = mapped_column(Integer, default=1)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignScheduleContinuation(Base):
    __tablename__ = "campaign_schedule_continuations"

    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_schedules.id"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignScheduleRun(Base):
    __tablename__ = "campaign_schedule_runs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "sequence", name="uq_campaign_schedule_run_sequence"),
        UniqueConstraint(
            "budget_reservation_id",
            name="uq_campaign_schedule_run_budget_reservation",
        ),
        UniqueConstraint(
            "conversation_turn_id",
            name="uq_campaign_schedule_run_conversation_turn",
        ),
        CheckConstraint("sequence >= 1", name="ck_campaign_schedule_run_sequence"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','interrupted')",
            name="ck_campaign_schedule_run_status",
        ),
        CheckConstraint(
            "lease_generation >= 1",
            name="ck_campaign_schedule_run_lease_generation",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status IN ('succeeded','failed','interrupted') AND completed_at IS NOT NULL)",
            name="ck_campaign_schedule_run_completion",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR "
            "(budget_reservation_id IS NOT NULL AND conversation_turn_id IS NOT NULL)",
            name="ck_campaign_schedule_run_success_bindings",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("campaign_schedules.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    lease_holder_id: Mapped[str] = mapped_column(String(128))
    lease_token: Mapped[str] = mapped_column(String(36))
    lease_generation: Mapped[int] = mapped_column(Integer)
    budget_reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_budget_reservations.id")
    )
    conversation_turn_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_turns.id"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(String(256))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(120), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "number", name="uq_conversation_turn_number"),
        UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_conversation_turn_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    user_content: Mapped[str] = mapped_column(String(16384))
    assistant_content: Mapped[str] = mapped_column(String(16384))
    mind_action: Mapped[dict[str, Any]] = mapped_column(JSON)
    selected_alias: Mapped[str] = mapped_column(String(64))
    selected_model: Mapped[str] = mapped_column(String(128))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    core_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelThreadBindingRecord(Base):
    """Operator-approved opaque thread-to-conversation binding; never delivery authority."""

    __tablename__ = "channel_thread_bindings"
    __table_args__ = (
        UniqueConstraint(
            "channel",
            "account_key",
            "sender_key",
            "thread_key",
            name="uq_channel_bound_thread_identity",
        ),
        CheckConstraint(
            "length(thread_fingerprint) = 64 AND thread_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_thread_fingerprint",
        ),
        CheckConstraint(
            "channel IN ('browser_text','whatsapp','email')", name="ck_channel_thread_kind"
        ),
        CheckConstraint(
            "length(account_key) = 64 AND account_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_account_key",
        ),
        CheckConstraint(
            "length(sender_key) = 64 AND sender_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_sender_key",
        ),
        CheckConstraint(
            "length(thread_key) = 64 AND thread_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_thread_key",
        ),
        CheckConstraint("operator_id = 'ahmad'", name="ck_channel_thread_operator"),
        CheckConstraint("provider_authenticated = 0", name="ck_channel_thread_unverified"),
        CheckConstraint("transport_authority = 0", name="ck_channel_thread_transport"),
    )

    thread_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    account_key: Mapped[str] = mapped_column(String(64))
    sender_key: Mapped[str] = mapped_column(String(64))
    thread_key: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    operator_id: Mapped[str] = mapped_column(String(32), default="ahmad")
    provider_authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    transport_authority: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelIngressObservationRecord(Base):
    """Bounded unverified message-id metadata; never an admitted Mind turn."""

    __tablename__ = "channel_ingress_observations"
    __table_args__ = (
        UniqueConstraint(
            "thread_fingerprint",
            "message_key",
            name="uq_channel_ingress_message_identity",
        ),
        CheckConstraint(
            "length(message_fingerprint) = 64 AND message_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_message_fingerprint",
        ),
        CheckConstraint(
            "length(message_key) = 64 AND message_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_message_key",
        ),
        CheckConstraint(
            "length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_channel_hex_text_sha256",
        ),
        CheckConstraint("operator_id = 'ahmad'", name="ck_channel_observation_operator"),
        CheckConstraint("state = 'unverified'", name="ck_channel_observation_state"),
        CheckConstraint("mind_executed = 0", name="ck_channel_observation_mind"),
        CheckConstraint("delivery_authority = 0", name="ck_channel_observation_delivery"),
    )

    message_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_fingerprint: Mapped[str] = mapped_column(
        ForeignKey("channel_thread_bindings.thread_fingerprint"), index=True
    )
    message_key: Mapped[str] = mapped_column(String(64))
    text_sha256: Mapped[str] = mapped_column(String(64))
    provider_received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    operator_id: Mapped[str] = mapped_column(String(32), default="ahmad")
    state: Mapped[str] = mapped_column(String(16), default="unverified")
    mind_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_authority: Mapped[bool] = mapped_column(Boolean, default=False)


class ChannelOperatorAccountSelectionRecord(Base):
    """Inert, append-only operator selection; never provider authenticity."""

    __tablename__ = "channel_operator_account_selections"
    __table_args__ = (
        CheckConstraint(
            "length(account_key)=64 AND account_key NOT GLOB '*[^0-9a-f]*' "
            "AND account_key != lower(hex(zeroblob(32)))",
            name="ck_channel_selection_hex_account_key",
        ),
        CheckConstraint(
            "length(app_secret_identity)=64 AND app_secret_identity NOT GLOB '*[^0-9a-f]*' "
            "AND app_secret_identity != lower(hex(zeroblob(32)))",
            name="ck_channel_selection_hex_app_secret_identity",
        ),
        CheckConstraint("operator_id='ahmad'", name="ck_channel_selection_operator"),
        CheckConstraint("provider_authenticated=0", name="ck_channel_selection_not_provider_auth"),
        CheckConstraint("mind_admission_authority=0", name="ck_channel_selection_not_mind"),
        CheckConstraint("delivery_authority=0", name="ck_channel_selection_not_delivery"),
    )

    account_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_secret_identity: Mapped[str] = mapped_column(String(64))
    operator_id: Mapped[str] = mapped_column(String(32), default="ahmad")
    selected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    provider_authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    mind_admission_authority: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_authority: Mapped[bool] = mapped_column(Boolean, default=False)


class ChannelOperatorAccountRevocationRecord(Base):
    """Irreversible account selection refusal; no reapproval with the same key."""

    __tablename__ = "channel_operator_account_revocations"
    __table_args__ = (
        CheckConstraint(
            "length(account_key)=64 AND account_key NOT GLOB '*[^0-9a-f]*' "
            "AND account_key != lower(hex(zeroblob(32)))",
            name="ck_channel_selection_hex_account_key",
        ),
        CheckConstraint("operator_id='ahmad'", name="ck_channel_revocation_operator"),
    )

    account_key: Mapped[str] = mapped_column(
        ForeignKey("channel_operator_account_selections.account_key"),
        primary_key=True,
    )
    operator_id: Mapped[str] = mapped_column(String(32), default="ahmad")
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SkillLearningProposalRecord(Base):
    """Frozen candidate. Status is projected from append-only events."""

    __tablename__ = "skill_learning_proposals"
    __table_args__ = (
        CheckConstraint("length(proposal_json) <= 65536", name="ck_skill_proposal_size"),
        CheckConstraint("length(proposal_fingerprint) = 64", name="ck_skill_proposal_fingerprint"),
        CheckConstraint("length(revision_sha256) = 64", name="ck_skill_proposal_revision"),
        CheckConstraint(
            "length(agent_registry_version) = 64", name="ck_skill_proposal_agent_registry"
        ),
        CheckConstraint(
            "length(skill_registry_version) = 64", name="ck_skill_proposal_skill_registry"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    agent_instance_id: Mapped[str] = mapped_column(ForeignKey("agent_instances.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[int] = mapped_column(Integer)
    agent_registry_version: Mapped[str] = mapped_column(String(64))
    skill_registry_version: Mapped[str] = mapped_column(String(64))
    source_skill_id: Mapped[str] = mapped_column(String(64))
    source_skill_version: Mapped[int] = mapped_column(Integer)
    procedure_id: Mapped[str] = mapped_column(String(64))
    revision_sha256: Mapped[str] = mapped_column(String(64))
    prior_revision_sha256: Mapped[str | None] = mapped_column(String(64))
    proposal_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SkillLearningEvent(Base):
    """Append-only proposal and review status history."""

    __tablename__ = "skill_learning_events"
    __table_args__ = (
        UniqueConstraint("proposal_id", "sequence", name="uq_skill_learning_event_sequence"),
        CheckConstraint("sequence >= 1", name="ck_skill_learning_event_sequence"),
        CheckConstraint(
            "event IN ('proposed','reviewed','accepted','rejected')",
            name="ck_skill_learning_event_name",
        ),
        CheckConstraint(
            "(sequence = 1 AND event = 'proposed' AND reviewer_id IS NULL) OR "
            "(sequence IN (2,3) AND event IN ('reviewed','accepted','rejected') "
            "AND reviewer_id = 'ahmad')",
            name="ck_skill_learning_event_reviewer",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("skill_learning_proposals.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(32))
    reviewer_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SkillLearningReviewRecord(Base):
    """One immutable human decision bound to an exact proposal snapshot."""

    __tablename__ = "skill_learning_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('accepted','rejected')", name="ck_skill_learning_review_decision"
        ),
        CheckConstraint("reviewer_id = 'ahmad'", name="ck_skill_learning_review_reviewer"),
        CheckConstraint("length(review_json) <= 16384", name="ck_skill_learning_review_size"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("skill_learning_proposals.id"), unique=True, index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))
    review_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    review_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SkillLearningPromotionLinkRecord(Base):
    """Immutable historical proof of an operator-bound successful promotion/merge lineage."""

    __tablename__ = "skill_learning_promotion_links"
    __table_args__ = (
        CheckConstraint("reviewer_id = 'ahmad'", name="ck_skill_learning_promotion_reviewer"),
        CheckConstraint("runtime_authority = 0", name="ck_skill_learning_promotion_runtime"),
        CheckConstraint(
            "length(review_fingerprint) = 64", name="ck_skill_learning_promotion_review"
        ),
        CheckConstraint(
            "length(revision_sha256) = 64", name="ck_skill_learning_promotion_revision"
        ),
        CheckConstraint(
            "length(artifact_sha256) = 64", name="ck_skill_learning_promotion_artifact"
        ),
        CheckConstraint(
            "length(promotion_action_hash) = 64", name="ck_skill_learning_promotion_action"
        ),
        CheckConstraint("length(merge_action_hash) = 64", name="ck_skill_learning_merge_action"),
        CheckConstraint("length(link_fingerprint) = 64", name="ck_skill_learning_link_fingerprint"),
        CheckConstraint(
            "length(promotion_review_fingerprint) = 64",
            name="ck_skill_learning_promotion_review_fingerprint",
        ),
        CheckConstraint("length(pr_head_sha) = 40", name="ck_skill_learning_promotion_pr_head"),
        CheckConstraint(
            "length(merge_commit_sha) = 40", name="ck_skill_learning_promotion_merge_commit"
        ),
        CheckConstraint(
            "artifact_bytes BETWEEN 1 AND 65536", name="ck_skill_learning_promotion_artifact_bytes"
        ),
        CheckConstraint("pr_number >= 1", name="ck_skill_learning_promotion_pr_number"),
        CheckConstraint(
            "promotion_action_version >= 1", name="ck_skill_learning_promotion_action_version"
        ),
        CheckConstraint("merge_action_version >= 1", name="ck_skill_learning_merge_action_version"),
        CheckConstraint("length(link_json) <= 32768", name="ck_skill_learning_promotion_link_size"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("skill_learning_proposals.id"), unique=True, index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(32))
    review_fingerprint: Mapped[str] = mapped_column(String(64))
    revision_sha256: Mapped[str] = mapped_column(String(64))
    artifact_path: Mapped[str] = mapped_column(String(4096))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    artifact_bytes: Mapped[int] = mapped_column(Integer)
    repository: Mapped[str] = mapped_column(String(256))
    promotion_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    promotion_action_version: Mapped[int] = mapped_column(Integer)
    promotion_action_hash: Mapped[str] = mapped_column(String(64))
    promotion_review_fingerprint: Mapped[str] = mapped_column(String(64))
    pr_number: Mapped[int] = mapped_column(Integer)
    pr_head_sha: Mapped[str] = mapped_column(String(40))
    merge_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    merge_action_version: Mapped[int] = mapped_column(Integer)
    merge_action_hash: Mapped[str] = mapped_column(String(64))
    merge_commit_sha: Mapped[str] = mapped_column(String(40))
    link_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    link_json: Mapped[str] = mapped_column(Text)
    runtime_authority: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ReaOperatorTargetSelectionRecord(Base):
    """Operator-asserted inert target identity; NOT verified bytes or tool authority."""

    __tablename__ = "rea_operator_target_selections"
    __table_args__ = (
        CheckConstraint(
            "length(snapshot_id)=39 AND substr(snapshot_id,1,7)='target-' "
            "AND substr(snapshot_id,8) NOT GLOB '*[^0-9a-f]*'",
            name="ck_rea_target_selection_snapshot_id",
        ),
        *(
            CheckConstraint(
                f"length({field})=64 AND {field} NOT GLOB '*[^0-9a-f]*'",
                name=f"ck_rea_target_selection_{field}",
            )
            for field in ("receipt_sha256", "content_sha256", "review_fingerprint")
        ),
        CheckConstraint("file_count BETWEEN 1 AND 128", name="ck_rea_target_selection_file_count"),
        CheckConstraint(
            "total_bytes BETWEEN 0 AND 4194304", name="ck_rea_target_selection_total_bytes"
        ),
        CheckConstraint("operator_id='ahmad'", name="ck_rea_target_selection_operator"),
        CheckConstraint("execution_authority=0", name="ck_rea_target_selection_no_execution"),
        CheckConstraint("worker_authority=0", name="ck_rea_target_selection_no_worker"),
        CheckConstraint("approval_authority=0", name="ck_rea_target_selection_no_approval"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(39), primary_key=True)
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str] = mapped_column(String(64))
    review_fingerprint: Mapped[str] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)
    operator_id: Mapped[str] = mapped_column(String(32), default="ahmad")
    selected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    execution_authority: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_authority: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_authority: Mapped[bool] = mapped_column(Boolean, default=False)


class ReaOperatorTargetRevocationRecord(Base):
    """Append-only, terminal refusal; cannot reselect the same snapshot identity."""

    __tablename__ = "rea_operator_target_revocations"
    __table_args__ = (
        CheckConstraint(
            "length(snapshot_id)=39 AND substr(snapshot_id,1,7)='target-' "
            "AND substr(snapshot_id,8) NOT GLOB '*[^0-9a-f]*'",
            name="ck_rea_target_revocation_snapshot_id",
        ),
        CheckConstraint("operator_id='ahmad'", name="ck_rea_target_revocation_operator"),
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("rea_operator_target_selections.snapshot_id"), primary_key=True
    )
    operator_id: Mapped[str] = mapped_column(String(32), default="ahmad")
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CommunityContextArchiveRecord(Base):
    """Manual, untrusted context source bytes; never promoted to model instructions."""

    __tablename__ = "community_context_archives"
    __table_args__ = (
        UniqueConstraint("scope_key", "source_ref", name="uq_context_archive_source"),
        CheckConstraint(
            "scope_kind IN ('operator', 'project')", name="ck_context_archive_scope_kind"
        ),
        CheckConstraint(
            "(scope_kind='operator' AND scope_id IS NULL AND scope_key='operator') OR "
            "(scope_kind='project' AND scope_id IS NOT NULL AND scope_key='project:' || scope_id)",
            name="ck_context_archive_scope",
        ),
        CheckConstraint(
            "length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_context_archive_sha",
        ),
        CheckConstraint("total_bytes BETWEEN 1 AND 1048576", name="ck_context_archive_bytes"),
        CheckConstraint("length(raw_data)=total_bytes", name="ck_context_archive_data_length"),
        CheckConstraint("line_count BETWEEN 1 AND 15000", name="ck_context_archive_lines"),
        CheckConstraint("length(excerpt) <= 2048", name="ck_context_archive_excerpt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str | None] = mapped_column(String(36))
    scope_key: Mapped[str] = mapped_column(String(48))
    source_ref: Mapped[str] = mapped_column(String(128))
    source_sha256: Mapped[str] = mapped_column(String(64))
    total_bytes: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text)
    excerpt_is_partial: Mapped[bool] = mapped_column(Boolean)
    raw_data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentSpaceResourceRecord(Base):
    """Immutable operator-selected logical Space root; does not open files."""

    __tablename__ = "agent_space_resources"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "space_id",
            "resource_kind",
            "resource_id",
            name="uq_space_resource_project_identity",
        ),
        CheckConstraint("resource_kind='files'", name="ck_space_resource_kind"),
        CheckConstraint("length(resource_sha256)=64", name="ck_space_resource_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    space_id: Mapped[str] = mapped_column(String(64))
    resource_kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str] = mapped_column(String(64))
    operations_json: Mapped[str] = mapped_column(Text)
    root_path: Mapped[str] = mapped_column(Text)
    root_device: Mapped[int] = mapped_column(Integer)
    root_inode: Mapped[int] = mapped_column(Integer)
    resource_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentSpaceResourceRetirement(Base):
    """Permanent resource retirement; retired identities are never reused."""

    __tablename__ = "agent_space_resource_retirements"

    resource_record_id: Mapped[str] = mapped_column(
        ForeignKey("agent_space_resources.id"), primary_key=True
    )
    retired_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentSpaceActivationRecord(Base):
    """Owner-confirmed metadata prerequisite; grants NO Worker/Tool authority."""

    __tablename__ = "agent_space_activations"
    __table_args__ = (
        UniqueConstraint("intent_id", name="uq_space_activation_intent"),
        CheckConstraint("length(activation_sha256)=64", name="ck_space_activation_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("agent_space_intents.id"), index=True)
    resource_record_id: Mapped[str] = mapped_column(
        ForeignKey("agent_space_resources.id"), index=True
    )
    agent_instance_id: Mapped[str] = mapped_column(ForeignKey("agent_instances.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    agent_version: Mapped[int] = mapped_column(Integer)
    registry_version: Mapped[str] = mapped_column(String(64))
    spec_sha256: Mapped[str] = mapped_column(String(64))
    intent_sha256: Mapped[str] = mapped_column(String(64))
    resource_sha256: Mapped[str] = mapped_column(String(64))
    activation_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentSpaceActivationRevocation(Base):
    """One-way terminal revocation for one owner-confirmed activation."""

    __tablename__ = "agent_space_activation_revocations"

    activation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_space_activations.id"), primary_key=True
    )
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentSpaceIntentRecord(Base):
    """Immutable owner-reviewed Space intent; no Worker or resource authorization."""

    __tablename__ = "agent_space_intents"
    __table_args__ = (
        CheckConstraint("agent_version >= 1", name="ck_space_intent_agent_version"),
        CheckConstraint("length(registry_version)=64", name="ck_space_intent_registry"),
        CheckConstraint(
            "length(spec_sha256)=64 AND spec_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_space_intent_spec_hash",
        ),
        CheckConstraint(
            "length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_space_intent_hash",
        ),
        CheckConstraint(
            "resource_kind IN ('files','browser','terminal','memory')", name="ck_space_intent_kind"
        ),
        CheckConstraint(
            "length(operations_json) BETWEEN 8 AND 32", name="ck_space_intent_operations_size"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_instance_id: Mapped[str] = mapped_column(ForeignKey("agent_instances.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[int] = mapped_column(Integer)
    registry_version: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    space_id: Mapped[str] = mapped_column(String(64))
    resource_kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str] = mapped_column(String(64))
    operations_json: Mapped[str] = mapped_column(Text)
    spec_sha256: Mapped[str] = mapped_column(String(64))
    intent_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentSpaceIntentRevocation(Base):
    """Irreversible revocation; one terminal row for each recorded Space intent."""

    __tablename__ = "agent_space_intent_revocations"

    intent_id: Mapped[str] = mapped_column(ForeignKey("agent_space_intents.id"), primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
