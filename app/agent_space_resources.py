"""Operator-owned Space resource roots and reviewed activations, never Worker access.

This stage records a trusted, bounded resource binding and an independent
confirmation of a previously recorded intent. It deliberately does not expose
file bytes or install a Computer/Worker/Tool authorization hook.
"""

import json
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select

from app.agent_factory import AgentFactory
from app.agent_space_intents import (
    _ID,
    _UUID,
    AgentSpaceIntentService,
    _hash,
    _snapshot,
    _validate_operations,
)
from app.models import (
    AgentInstanceEvent,
    AgentSpaceActivationRecord,
    AgentSpaceActivationRevocation,
    AgentSpaceIntentRecord,
    AgentSpaceIntentRevocation,
    AgentSpaceResourceRecord,
    AgentSpaceResourceRetirement,
    Project,
    new_id,
    utcnow,
)
from app.policy import DomainError
from app.storage import SessionStore

MAX_RESOURCES_PER_PROJECT = 128
MAX_CONFIRMATIONS_PER_AGENT = 128


@dataclass(frozen=True, slots=True)
class SpaceResourceReceipt:
    id: str
    project_id: str
    space_id: str
    resource_kind: Literal["files"]
    resource_id: str
    operations: tuple[str, ...]
    root_path: str
    resource_sha256: str
    retired: bool
    root_attested: bool
    effective_resource_access: Literal[False] = False


@dataclass(frozen=True, slots=True)
class SpaceActivationReceipt:
    id: str
    intent_id: str
    resource_record_id: str
    agent_instance_id: str
    project_id: str
    agent_version: int
    registry_version: str
    spec_sha256: str
    intent_sha256: str
    resource_sha256: str
    activation_sha256: str
    expires_at: datetime
    revoked: bool
    eligible_for_mediation: bool
    effective_resource_access: Literal[False] = False
    worker_authority: Literal[False] = False
    tool_authority: Literal[False] = False


def _resource_digest(record: AgentSpaceResourceRecord) -> str:
    return _hash(
        {
            "id": record.id,
            "project_id": record.project_id,
            "space_id": record.space_id,
            "resource_kind": record.resource_kind,
            "resource_id": record.resource_id,
            "operations": json.loads(record.operations_json),
            "root_path": record.root_path,
            "root_device": record.root_device,
            "root_inode": record.root_inode,
            "created_at": record.created_at.isoformat(),
        }
    )


def _activation_digest(record: AgentSpaceActivationRecord) -> str:
    return _hash(
        {
            "id": record.id,
            "intent_id": record.intent_id,
            "resource_record_id": record.resource_record_id,
            "agent_instance_id": record.agent_instance_id,
            "project_id": record.project_id,
            "agent_version": record.agent_version,
            "registry_version": record.registry_version,
            "spec_sha256": record.spec_sha256,
            "intent_sha256": record.intent_sha256,
            "resource_sha256": record.resource_sha256,
            "expires_at": record.expires_at.isoformat(),
            "created_at": record.created_at.isoformat(),
        }
    )


class AgentSpaceResourceService:
    """Owner-only metadata. No file-opening or Worker authority exists here."""

    def __init__(self, store: SessionStore, factory: AgentFactory, runtime_dir: Path):
        self.store = store
        self.factory = factory
        self.runtime_dir = runtime_dir
        self.intents = AgentSpaceIntentService(store, factory)

    def _root(self, project_id: str, space_id: str, resource_id: str) -> tuple[Path, int, int]:
        if type(project_id) is not str or not _UUID.fullmatch(project_id):
            raise DomainError("Invalid canonical Space project identity", 409)
        # The caller cannot supply a filesystem path: only canonical logical IDs
        # below Zelo's configured, dedicated runtime/spaces tree are representable.
        try:
            runtime = self.runtime_dir.resolve(strict=True)
            if not runtime.is_dir():
                raise OSError("not_directory")
            current = runtime
            for part in ("spaces", project_id, space_id, resource_id):
                current = current / part
                info = current.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError("not_directory_or_symlink")
            final = current.lstat()
        except (OSError, RuntimeError, ValueError):
            raise DomainError("Pre-provisioned Space root unavailable or unsafe", 409) from None
        return current, final.st_dev, final.st_ino

    def _project_and_agent(self, session, instance_id: str):
        instance = self.intents._load_instance(session, instance_id)
        spec = _snapshot(instance, self.factory)
        project = session.get(Project, instance.project_id)
        if project is None or project.status != "active":
            raise DomainError("Space resource project must be active", 409)
        return instance, spec

    def _load_resource(self, session, instance_id: str, resource_record_id: str):
        instance, spec = self._project_and_agent(session, instance_id)
        if type(resource_record_id) is not str or not _UUID.fullmatch(resource_record_id):
            raise DomainError("Space resource not found", 404)
        record = session.get(AgentSpaceResourceRecord, resource_record_id)
        if (
            record is None
            or record.project_id != instance.project_id
            or record.space_id not in spec.space_permissions
        ):
            raise DomainError("Space resource not found", 404)
        return record

    def _resource_receipt(self, session, record: AgentSpaceResourceRecord) -> SpaceResourceReceipt:
        try:
            operations = json.loads(record.operations_json)
            if (
                type(operations) is not list
                or record.resource_kind != "files"
                or not _ID.fullmatch(record.space_id)
                or not _ID.fullmatch(record.resource_id)
            ):
                raise ValueError("invalid")
            _validate_operations(
                record.resource_kind, tuple(operations), record.resource_id, record.space_id
            )
            if record.resource_sha256 != _resource_digest(record):
                raise ValueError("digest_mismatch")
        except (ValueError, TypeError, DomainError):
            raise DomainError("Space registry integrity failure", 503) from None

        try:
            root, device, inode = self._root(record.project_id, record.space_id, record.resource_id)
            root_attested = (
                str(root) == record.root_path
                and device == record.root_device
                and inode == record.root_inode
            )
        except DomainError:
            root_attested = False
        return SpaceResourceReceipt(
            id=record.id,
            project_id=record.project_id,
            space_id=record.space_id,
            resource_kind="files",
            resource_id=record.resource_id,
            operations=tuple(operations),
            root_path=record.root_path,
            resource_sha256=record.resource_sha256,
            retired=session.get(AgentSpaceResourceRetirement, record.id) is not None,
            root_attested=root_attested,
        )

    def register(
        self, *, instance_id: str, space_id: str, resource_id: str, operations: tuple[str, ...]
    ) -> SpaceResourceReceipt:
        _validate_operations("files", operations, resource_id, space_id)
        with self.store.transaction() as session:
            instance, spec = self._project_and_agent(session, instance_id)
            if space_id not in spec.space_permissions:
                raise DomainError("Space not in trusted AgentSpec", 403)
            if spec.computer is None or not spec.computer.files:
                raise DomainError("AgentSpec has no file resource capability", 403)
            root, device, inode = self._root(instance.project_id, space_id, resource_id)
            existing = session.scalar(
                select(AgentSpaceResourceRecord.id).where(
                    AgentSpaceResourceRecord.project_id == instance.project_id,
                    AgentSpaceResourceRecord.space_id == space_id,
                    AgentSpaceResourceRecord.resource_kind == "files",
                    AgentSpaceResourceRecord.resource_id == resource_id,
                )
            )
            if existing is not None:
                raise DomainError("Space resource identity already recorded", 409)
            count = (
                session.scalar(
                    select(func.count(AgentSpaceResourceRecord.id)).where(
                        AgentSpaceResourceRecord.project_id == instance.project_id
                    )
                )
                or 0
            )
            if count >= MAX_RESOURCES_PER_PROJECT:
                raise DomainError("Space resource registry limit reached", 409)
            record = AgentSpaceResourceRecord(
                id=new_id(),
                project_id=instance.project_id,
                space_id=space_id,
                resource_kind="files",
                resource_id=resource_id,
                operations_json=json.dumps(list(operations), separators=(",", ":")),
                root_path=str(root),
                root_device=device,
                root_inode=inode,
                created_at=utcnow(),
            )
            record.resource_sha256 = _resource_digest(record)
            session.add(record)
            session.flush()
            return self._resource_receipt(session, record)

    def get_resource(self, *, instance_id: str, resource_record_id: str) -> SpaceResourceReceipt:
        with self.store.read() as session:
            record = self._load_resource(session, instance_id, resource_record_id)
            return self._resource_receipt(session, record)

    def retire(self, *, instance_id: str, resource_record_id: str) -> SpaceResourceReceipt:
        # Emergency retirement must work after an agent or project is paused.
        # It never opens a resource or makes a grant active.
        with self.store.transaction() as session:
            instance = self.intents._load_instance(session, instance_id)
            if type(resource_record_id) is not str or not _UUID.fullmatch(resource_record_id):
                raise DomainError("Space resource not found", 404)
            record = session.get(AgentSpaceResourceRecord, resource_record_id)
            if record is None or record.project_id != instance.project_id:
                raise DomainError("Space resource not found", 404)
            if session.get(AgentSpaceResourceRetirement, record.id) is None:
                session.add(
                    AgentSpaceResourceRetirement(resource_record_id=record.id, retired_at=utcnow())
                )
                session.flush()
            return self._resource_receipt(session, record)

    def activate(
        self, *, instance_id: str, intent_id: str, resource_record_id: str
    ) -> SpaceActivationReceipt:
        intent = self.intents.get(instance_id=instance_id, intent_id=intent_id)
        if not intent.eligible_for_review or intent.expires_at <= utcnow():
            raise DomainError("Space intent no longer eligible", 409)
        resource = self.get_resource(instance_id=instance_id, resource_record_id=resource_record_id)
        if (
            resource.retired
            or not resource.root_attested
            or resource.project_id != intent.project_id
            or resource.space_id != intent.space_id
            or resource.resource_kind != intent.resource_kind
            or resource.resource_id != intent.resource_id
            or not set(intent.operations).issubset(resource.operations)
        ):
            raise DomainError("Space intent has no matching attested resource", 409)
        with self.store.transaction() as session:
            instance, spec = self._project_and_agent(session, instance_id)
            original = session.get(AgentSpaceIntentRecord, intent.id)
            linked = session.get(AgentSpaceResourceRecord, resource.id)
            lifecycle_changed = (
                session.scalar(
                    select(AgentInstanceEvent.id)
                    .where(
                        AgentInstanceEvent.agent_instance_id == instance_id,
                        AgentInstanceEvent.event.in_(("paused", "resumed", "retired")),
                        AgentInstanceEvent.created_at >= original.created_at,
                    )
                    .limit(1)
                )
                if original is not None
                else True
            )
            current_resource = self._resource_receipt(session, linked) if linked else None
            if (
                original is None
                or original.intent_sha256 != intent.intent_sha256
                or original.expires_at <= utcnow()
                or lifecycle_changed is not None
                or current_resource is None
                or not current_resource.root_attested
                or current_resource.resource_sha256 != resource.resource_sha256
                or instance.project_id != intent.project_id
                or instance.agent_version != intent.agent_version
                or instance.registry_version != intent.registry_version
                or _hash(spec.model_dump(mode="json")) != intent.spec_sha256
                or session.get(AgentSpaceIntentRevocation, intent.id) is not None
                or session.get(AgentSpaceResourceRetirement, resource.id) is not None
            ):
                raise DomainError("Space activation prerequisites changed", 409)
            existing = session.scalar(
                select(AgentSpaceActivationRecord.id).where(
                    AgentSpaceActivationRecord.intent_id == intent.id
                )
            )
            if existing is not None:
                raise DomainError("Space intent already confirmed; new intent required", 409)
            count = (
                session.scalar(
                    select(func.count(AgentSpaceActivationRecord.id)).where(
                        AgentSpaceActivationRecord.agent_instance_id == instance_id
                    )
                )
                or 0
            )
            if count >= MAX_CONFIRMATIONS_PER_AGENT:
                raise DomainError("Space confirmation history limit reached", 409)
            record = AgentSpaceActivationRecord(
                id=new_id(),
                intent_id=intent.id,
                resource_record_id=resource.id,
                agent_instance_id=intent.agent_instance_id,
                project_id=intent.project_id,
                agent_version=intent.agent_version,
                registry_version=intent.registry_version,
                spec_sha256=intent.spec_sha256,
                intent_sha256=intent.intent_sha256,
                resource_sha256=resource.resource_sha256,
                created_at=utcnow(),
                expires_at=intent.expires_at,
            )
            if record.created_at >= record.expires_at:
                raise DomainError("Space intent expired before confirmation", 409)
            record.activation_sha256 = _activation_digest(record)
            session.add(record)
            session.flush()
        return self.get_activation(instance_id=instance_id, activation_id=record.id)

    def get_activation(self, *, instance_id: str, activation_id: str) -> SpaceActivationReceipt:
        with self.store.read() as session:
            if type(activation_id) is not str or not _UUID.fullmatch(activation_id):
                raise DomainError("Space confirmation not found", 404)
            record = session.get(AgentSpaceActivationRecord, activation_id)
            if record is None or record.agent_instance_id != instance_id:
                raise DomainError("Space confirmation not found", 404)
            if record.activation_sha256 != _activation_digest(record):
                raise DomainError("Space confirmation integrity failure", 503)
            revoked = session.get(AgentSpaceActivationRevocation, activation_id) is not None
            binding = {
                "id": record.id,
                "intent_id": record.intent_id,
                "resource_record_id": record.resource_record_id,
                "agent_instance_id": record.agent_instance_id,
                "project_id": record.project_id,
                "agent_version": record.agent_version,
                "registry_version": record.registry_version,
                "spec_sha256": record.spec_sha256,
                "intent_sha256": record.intent_sha256,
                "resource_sha256": record.resource_sha256,
                "activation_sha256": record.activation_sha256,
                "expires_at": record.expires_at,
            }
            # An owner may inspect/revoke old confirmation evidence even after
            # a pause, retirement or registry change. Eligibility is computed
            # independently by the current intent/agent lifecycle.
            linked = session.get(AgentSpaceResourceRecord, record.resource_record_id)
            if linked is None:
                raise DomainError("Space confirmation resource missing", 503)
            resource = self._resource_receipt(session, linked)
        intent = self.intents.get(instance_id=instance_id, intent_id=binding["intent_id"])
        if (
            intent.agent_instance_id != binding["agent_instance_id"]
            or intent.project_id != binding["project_id"]
            or intent.agent_version != binding["agent_version"]
            or intent.registry_version != binding["registry_version"]
            or intent.spec_sha256 != binding["spec_sha256"]
            or intent.intent_sha256 != binding["intent_sha256"]
            or resource.resource_sha256 != binding["resource_sha256"]
            or resource.project_id != intent.project_id
            or resource.space_id != intent.space_id
            or resource.resource_kind != intent.resource_kind
            or resource.resource_id != intent.resource_id
            or not set(intent.operations).issubset(resource.operations)
        ):
            raise DomainError("Space confirmation binding integrity failure", 503)
        return SpaceActivationReceipt(
            **binding,
            revoked=revoked or intent.revoked,
            eligible_for_mediation=(
                not revoked
                and intent.eligible_for_review
                and not resource.retired
                and resource.root_attested
                and binding["expires_at"] > utcnow()
            ),
        )

    def revoke(self, *, instance_id: str, activation_id: str) -> SpaceActivationReceipt:
        with self.store.transaction() as session:
            if type(activation_id) is not str or not _UUID.fullmatch(activation_id):
                raise DomainError("Space confirmation not found", 404)
            record = session.get(AgentSpaceActivationRecord, activation_id)
            if record is None or record.agent_instance_id != instance_id:
                raise DomainError("Space confirmation not found", 404)
            if session.get(AgentSpaceActivationRevocation, activation_id) is None:
                session.add(
                    AgentSpaceActivationRevocation(activation_id=activation_id, revoked_at=utcnow())
                )
                session.flush()
        return self.get_activation(instance_id=instance_id, activation_id=activation_id)
