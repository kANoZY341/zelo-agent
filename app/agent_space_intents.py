"""Owner-confirmed OpenDots-inspired Space intentions, never execution authority.

Grant intent records are inert until separately qualified enforcement hooks
exist on EVERY relevant Computer, Worker and Tool use path.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import (
    AgentInstance,
    AgentInstanceEvent,
    AgentSpaceIntentRecord,
    AgentSpaceIntentRevocation,
    Project,
    new_id,
    utcnow,
)
from app.policy import DomainError
from app.storage import SessionStore

SpaceResourceKind = Literal["files", "browser", "terminal", "memory"]
SpaceReadOperation = Literal["read", "list"]
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
MAX_ACTIVE_INTENTS_PER_AGENT = 16
MIN_INTENT_TTL = 60
MAX_INTENT_TTL = 3600


@dataclass(frozen=True, slots=True)
class SpaceIntentReceipt:
    id: str
    agent_instance_id: str
    agent_id: str
    agent_version: int
    registry_version: str
    project_id: str
    space_id: str
    resource_kind: SpaceResourceKind
    resource_id: str
    operations: tuple[SpaceReadOperation, ...]
    spec_sha256: str
    intent_sha256: str
    expires_at: datetime
    revoked: bool
    current_spec: bool
    eligible_for_review: bool
    effective_resource_access: Literal[False] = False
    worker_authority: Literal[False] = False
    tool_authority: Literal[False] = False
    model_context_admission: Literal[False] = False


@dataclass(frozen=True, slots=True)
class SpaceIntentPage:
    items: tuple[SpaceIntentReceipt, ...]
    total: int
    limit: int
    offset: int


def _validate_history_page(limit: int, offset: int) -> None:
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(offset) is not int
        or not 0 <= offset <= 1_000_000
    ):
        raise DomainError("Space history pagination invalid", 422)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii")
    ).hexdigest()


def _intent_digest(record: AgentSpaceIntentRecord) -> str:
    return _hash(
        {
            "id": record.id,
            "created_at": record.created_at.isoformat(),
            "agent_instance_id": record.agent_instance_id,
            "agent_id": record.agent_id,
            "agent_version": record.agent_version,
            "registry_version": record.registry_version,
            "project_id": record.project_id,
            "space_id": record.space_id,
            "resource_kind": record.resource_kind,
            "resource_id": record.resource_id,
            "operations": json.loads(record.operations_json),
            "spec_sha256": record.spec_sha256,
            "expires_at": record.expires_at.isoformat(),
        }
    )


def _validate_operations(
    resource_kind: str, operations: tuple[str, ...], resource_id: str, space_id: str
) -> None:
    if (
        type(space_id) is not str
        or not _ID.fullmatch(space_id)
        or type(resource_id) is not str
        or not _ID.fullmatch(resource_id)
        or resource_kind not in ("files", "browser", "terminal", "memory")
        or type(operations) is not tuple
        or not operations
        or operations != tuple(sorted(set(operations)))
        or any(operation not in ("read", "list") for operation in operations)
    ):
        raise DomainError("Space resource intent must be canonical read-only metadata", 422)
    if resource_kind in {"browser", "terminal", "memory"} and "list" in operations:
        raise DomainError("Only files support list metadata intent", 422)


def _snapshot(instance: AgentInstance, factory: AgentFactory) -> AgentSpec:
    if instance.status != "active":
        raise DomainError("Agent instance must be active", 409)
    try:
        spec = AgentSpec.model_validate(instance.spec)
        binding = AgentBinding(spec=spec, registry_version=instance.registry_version)
    except ValueError:
        raise DomainError("Space intent agent snapshot invalid", 409) from None
    if (
        spec.agent_id != instance.agent_id
        or spec.version != instance.agent_version
        or not factory.matches_agent(binding)
    ):
        raise DomainError("Space intent agent snapshot stale", 409)
    return spec


class AgentSpaceIntentService:
    """No runtime enforcement, no implicit Space membership, no permission grant."""

    def __init__(self, store: SessionStore, factory: AgentFactory):
        self.store = store
        self.factory = factory

    @staticmethod
    def _load_instance(session, instance_id: str) -> AgentInstance:
        if type(instance_id) is not str or not _UUID.fullmatch(instance_id):
            raise DomainError("Agent instance not found", 404)
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError("Agent instance not found", 404)
        return instance

    @staticmethod
    def _load(session, agent_instance_id: str, intent_id: str) -> AgentSpaceIntentRecord:
        if type(intent_id) is not str or not _UUID.fullmatch(intent_id):
            raise DomainError("Space intent not found", 404)
        record = session.get(AgentSpaceIntentRecord, intent_id)
        if record is None or record.agent_instance_id != agent_instance_id:
            raise DomainError("Space intent not found", 404)
        return record

    @staticmethod
    def _receipt(
        record: AgentSpaceIntentRecord, *, revoked: bool, current: bool
    ) -> SpaceIntentReceipt:
        try:
            operations = json.loads(record.operations_json)
        except (TypeError, ValueError):
            raise DomainError("Space intent integrity failure", 503) from None
        _validate_operations(
            record.resource_kind,
            tuple(operations) if type(operations) is list else (),
            record.resource_id,
            record.space_id,
        )
        if record.intent_sha256 != _intent_digest(record):
            raise DomainError("Space intent integrity failure", 503)
        return SpaceIntentReceipt(
            id=record.id,
            agent_instance_id=record.agent_instance_id,
            agent_id=record.agent_id,
            agent_version=record.agent_version,
            registry_version=record.registry_version,
            project_id=record.project_id,
            space_id=record.space_id,
            resource_kind=record.resource_kind,
            resource_id=record.resource_id,
            operations=tuple(operations),
            spec_sha256=record.spec_sha256,
            intent_sha256=record.intent_sha256,
            expires_at=record.expires_at,
            revoked=revoked,
            current_spec=current,
            eligible_for_review=current and not revoked,
        )

    def create(
        self,
        *,
        instance_id: str,
        space_id: str,
        resource_kind: SpaceResourceKind,
        resource_id: str,
        operations: tuple[SpaceReadOperation, ...],
        ttl_seconds: int,
    ) -> SpaceIntentReceipt:
        _validate_operations(resource_kind, operations, resource_id, space_id)
        if type(ttl_seconds) is not int or not MIN_INTENT_TTL <= ttl_seconds <= MAX_INTENT_TTL:
            raise DomainError("Space intent lifetime invalid", 422)
        now = utcnow()
        with self.store.transaction() as session:
            instance = self._load_instance(session, instance_id)
            spec = _snapshot(instance, self.factory)
            project = session.get(Project, instance.project_id)
            if project is None or project.status != "active":
                raise DomainError("Space intent project must be active", 409)
            if space_id not in spec.space_permissions:
                raise DomainError("Space is not allowed by the trusted agent specification", 403)
            # Intent alone does not grant even readonly operation; backing
            # Computer/Worker policies remain unchanged and fail closed.
            if resource_kind in {"files", "browser", "terminal"}:
                computer = spec.computer
                if computer is None or not getattr(
                    computer, resource_kind if resource_kind != "terminal" else "shell"
                ):
                    raise DomainError("Agent computer profile does not include resource", 403)
            history_count = (
                session.scalar(
                    select(func.count(AgentSpaceIntentRecord.id)).where(
                        AgentSpaceIntentRecord.agent_instance_id == instance.id
                    )
                )
                or 0
            )
            active_count = (
                session.scalar(
                    select(func.count(AgentSpaceIntentRecord.id)).where(
                        AgentSpaceIntentRecord.agent_instance_id == instance.id,
                        AgentSpaceIntentRecord.expires_at > now,
                        ~select(AgentSpaceIntentRevocation.intent_id)
                        .where(AgentSpaceIntentRevocation.intent_id == AgentSpaceIntentRecord.id)
                        .exists(),
                    )
                )
                or 0
            )
            if active_count >= MAX_ACTIVE_INTENTS_PER_AGENT or history_count >= 256:
                raise DomainError("Space intent limit reached", 409)
            snapshot_sha = _hash(spec.model_dump(mode="json"))
            record = AgentSpaceIntentRecord(
                id=new_id(),
                agent_instance_id=instance.id,
                agent_id=instance.agent_id,
                agent_version=instance.agent_version,
                registry_version=instance.registry_version,
                project_id=instance.project_id,
                space_id=space_id,
                resource_kind=resource_kind,
                resource_id=resource_id,
                operations_json=json.dumps(list(operations), separators=(",", ":")),
                spec_sha256=snapshot_sha,
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_at=now,
            )
            record.intent_sha256 = _intent_digest(record)
            session.add(record)
            session.flush()
            return self._receipt(record, revoked=False, current=True)

    def list_intents(
        self, *, instance_id: str, limit: int = 25, offset: int = 0
    ) -> SpaceIntentPage:
        """Discover owned historical records even when current execution is stopped."""
        _validate_history_page(limit, offset)
        with self.store.read() as session:
            instance = self._load_instance(session, instance_id)
            scope = (
                AgentSpaceIntentRecord.agent_instance_id == instance.id,
                AgentSpaceIntentRecord.project_id == instance.project_id,
            )
            total = session.scalar(select(func.count(AgentSpaceIntentRecord.id)).where(*scope)) or 0
            ids = session.scalars(
                select(AgentSpaceIntentRecord.id)
                .where(*scope)
                .order_by(AgentSpaceIntentRecord.created_at, AgentSpaceIntentRecord.id)
                .offset(offset)
                .limit(limit)
            ).all()
        # Reuse the current single-record integrity/expiry/lifecycle projection.
        # Historical visibility is not permission to execute or reactivate.
        items = tuple(self.get(instance_id=instance_id, intent_id=key) for key in ids)
        return SpaceIntentPage(items=items, total=total, limit=limit, offset=offset)

    def get(self, *, instance_id: str, intent_id: str) -> SpaceIntentReceipt:
        with self.store.read() as session:
            instance = self._load_instance(session, instance_id)
            record = self._load(session, instance_id, intent_id)
            revoked = session.get(AgentSpaceIntentRevocation, intent_id) is not None
            current = False
            if record.expires_at > utcnow():
                try:
                    spec = _snapshot(instance, self.factory)
                    project = session.get(Project, instance.project_id)
                    # A lifecycle interruption must terminally invalidate
                    # old review eligibility, even if the agent resumes with
                    # exactly the same AgentSpec and registry fingerprint.
                    # Only a new operator intent after resume can be eligible.
                    lifecycle_changed = session.scalar(
                        select(AgentInstanceEvent.id)
                        .where(
                            AgentInstanceEvent.agent_instance_id == record.agent_instance_id,
                            AgentInstanceEvent.event.in_(("paused", "resumed", "retired")),
                            AgentInstanceEvent.created_at >= record.created_at,
                        )
                        .limit(1)
                    )
                    current = (
                        lifecycle_changed is None
                        and project is not None
                        and project.status == "active"
                        and record.agent_id == instance.agent_id
                        and record.agent_version == instance.agent_version
                        and record.project_id == instance.project_id
                        and record.registry_version == instance.registry_version
                        and record.spec_sha256 == _hash(spec.model_dump(mode="json"))
                        and record.space_id in spec.space_permissions
                    )
                except DomainError:
                    pass
            return self._receipt(record, revoked=revoked, current=current)

    def revoke(self, *, instance_id: str, intent_id: str) -> SpaceIntentReceipt:
        with self.store.transaction() as session:
            self._load_instance(session, instance_id)
            self._load(session, instance_id, intent_id)
            revocation = session.get(AgentSpaceIntentRevocation, intent_id)
            if revocation is None:
                session.add(AgentSpaceIntentRevocation(intent_id=intent_id, revoked_at=utcnow()))
                session.flush()
        return self.get(instance_id=instance_id, intent_id=intent_id)
