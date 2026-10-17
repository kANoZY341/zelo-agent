"""Owner-created/registered Space roots and independently reviewed confirmations.

Explicit owner provisioning may seed a new private root with bounded text.
No resource receipt or confirmation alone grants Computer/Worker/Tool access;
file reading remains behind the separate Task-bound gateway.
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
    MAX_INTENT_HISTORY_PER_AGENT,
    AgentSpaceIntentService,
    _hash,
    _snapshot,
    _validate_history_page,
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
class SpaceResourcePage:
    items: tuple[SpaceResourceReceipt, ...]
    total: int
    limit: int
    offset: int


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


@dataclass(frozen=True, slots=True)
class SpaceActivationPage:
    items: tuple[SpaceActivationReceipt, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class SpaceApprovalRevocationReceipt:
    agent_instance_id: str
    project_id: str
    revoked_at: datetime
    total_intents: int
    total_activations: int
    newly_revoked_intent_ids: tuple[str, ...]
    newly_revoked_activation_ids: tuple[str, ...]
    scope: Literal["existing_agent_space_approvals"] = "existing_agent_space_approvals"
    future_owner_approvals_blocked: Literal[False] = False
    resources_deleted: Literal[False] = False


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
    """Owner resource management and confirmations, never Worker authority."""

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

    def provision(
        self,
        *,
        instance_id: str,
        space_id: str,
        resource_id: str,
        operations: tuple[str, ...],
        files: tuple[tuple[str, str], ...],
    ) -> SpaceResourceReceipt:
        """Initialize a new private owner resource, without granting Agent access."""
        from app.space_owner_provision import encode_seed_files, provision_seeded_root

        _validate_operations("files", operations, resource_id, space_id)
        encoded = encode_seed_files(files)
        with self.store.transaction() as session:
            instance, spec = self._project_and_agent(session, instance_id)
            if space_id not in spec.space_permissions:
                raise DomainError("Space not in trusted AgentSpec", 403)
            if spec.computer is None or not spec.computer.files:
                raise DomainError("AgentSpec has no file resource capability", 403)
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
            device, inode = provision_seeded_root(
                self.runtime_dir,
                project_id=instance.project_id,
                space_id=space_id,
                resource_id=resource_id,
                files=encoded,
            )
            root, visible_device, visible_inode = self._root(
                instance.project_id, space_id, resource_id
            )
            if (device, inode) != (visible_device, visible_inode):
                raise DomainError("Provisioned Space root changed; data preserved", 409)
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
            receipt = self._resource_receipt(session, record)
            if not receipt.root_attested:
                raise DomainError("Provisioned Space root changed; data preserved", 409)
            return receipt

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

    def list_resources(
        self,
        *,
        instance_id: str,
        limit: int = 25,
        offset: int = 0,
        space_id: str | None = None,
    ) -> SpaceResourcePage:
        """Discover owner records, never file contents or effective grants.

        Counts and rows share the same Project/AgentSpec filter. Retired roots
        remain visible for recovery, and each receipt reuses integrity/identity
        attestation. Separate Task admission still decides actual resource use.
        """
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1_000_000
        ):
            raise DomainError("Space resource pagination invalid", 422)
        if space_id is not None and (type(space_id) is not str or not _ID.fullmatch(space_id)):
            raise DomainError("Space resource filter invalid", 422)
        with self.store.read() as session:
            instance, spec = self._project_and_agent(session, instance_id)
            if spec.computer is None or not spec.computer.files:
                raise DomainError("AgentSpec has no file resource capability", 403)
            allowed = spec.space_permissions
            if space_id is not None and space_id not in allowed:
                raise DomainError("Space not in trusted AgentSpec", 403)
            filters = (
                AgentSpaceResourceRecord.project_id == instance.project_id,
                AgentSpaceResourceRecord.resource_kind == "files",
                AgentSpaceResourceRecord.space_id.in_(allowed),
            )
            if space_id is not None:
                filters += (AgentSpaceResourceRecord.space_id == space_id,)
            total = (
                session.scalar(select(func.count(AgentSpaceResourceRecord.id)).where(*filters)) or 0
            )
            records = session.scalars(
                select(AgentSpaceResourceRecord)
                .where(*filters)
                .order_by(AgentSpaceResourceRecord.created_at, AgentSpaceResourceRecord.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return SpaceResourcePage(
                items=tuple(self._resource_receipt(session, record) for record in records),
                total=total,
                limit=limit,
                offset=offset,
            )

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

    def list_activations(
        self, *, instance_id: str, limit: int = 25, offset: int = 0
    ) -> SpaceActivationPage:
        """Keep owner confirmation discovery available for emergency revocation."""
        _validate_history_page(limit, offset)
        with self.store.read() as session:
            instance = self.intents._load_instance(session, instance_id)
            scope = (
                AgentSpaceActivationRecord.agent_instance_id == instance.id,
                AgentSpaceActivationRecord.project_id == instance.project_id,
            )
            total = (
                session.scalar(select(func.count(AgentSpaceActivationRecord.id)).where(*scope)) or 0
            )
            ids = session.scalars(
                select(AgentSpaceActivationRecord.id)
                .where(*scope)
                .order_by(AgentSpaceActivationRecord.created_at, AgentSpaceActivationRecord.id)
                .offset(offset)
                .limit(limit)
            ).all()
        items = tuple(
            self.get_activation(instance_id=instance_id, activation_id=key) for key in ids
        )
        return SpaceActivationPage(items=items, total=total, limit=limit, offset=offset)

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

    def revoke_all(self, *, instance_id: str) -> SpaceApprovalRevocationReceipt:
        """Withdraw existing owned approvals in the same writer as grant/use checks.

        Emergency withdrawal must work with inactive/stale AgentSpec bindings
        or unavailable roots. Only persisted ownership and history bounds are
        needed; inspecting or executing the stored approval contents is not.
        This is not a permanent ban on separately approved future intentions.
        """
        with self.store.transaction() as session:
            instance = self.intents._load_instance(session, instance_id)
            intents = session.execute(
                select(AgentSpaceIntentRecord.id, AgentSpaceIntentRecord.project_id)
                .where(AgentSpaceIntentRecord.agent_instance_id == instance.id)
                .order_by(AgentSpaceIntentRecord.created_at, AgentSpaceIntentRecord.id)
                .limit(MAX_INTENT_HISTORY_PER_AGENT + 1)
            ).all()
            activations = session.execute(
                select(AgentSpaceActivationRecord.id, AgentSpaceActivationRecord.project_id)
                .where(AgentSpaceActivationRecord.agent_instance_id == instance.id)
                .order_by(AgentSpaceActivationRecord.created_at, AgentSpaceActivationRecord.id)
                .limit(MAX_CONFIRMATIONS_PER_AGENT + 1)
            ).all()
            if (
                len(intents) > MAX_INTENT_HISTORY_PER_AGENT
                or len(activations) > MAX_CONFIRMATIONS_PER_AGENT
                or any(project != instance.project_id for _, project in (*intents, *activations))
            ):
                raise DomainError("Space approval ownership or history integrity failure", 503)
            now = utcnow()
            intent_ids = []
            activation_ids = []
            # Keep both classes in one transaction/flush. A failure at either
            # table rolls back all newly added terminal revocation records.
            with session.no_autoflush:
                for key, _ in intents:
                    if session.get(AgentSpaceIntentRevocation, key) is None:
                        session.add(AgentSpaceIntentRevocation(intent_id=key, revoked_at=now))
                        intent_ids.append(key)
                for key, _ in activations:
                    if session.get(AgentSpaceActivationRevocation, key) is None:
                        session.add(
                            AgentSpaceActivationRevocation(activation_id=key, revoked_at=now)
                        )
                        activation_ids.append(key)
            session.flush()
            receipt = SpaceApprovalRevocationReceipt(
                agent_instance_id=instance.id,
                project_id=instance.project_id,
                revoked_at=now,
                total_intents=len(intents),
                total_activations=len(activations),
                newly_revoked_intent_ids=tuple(intent_ids),
                newly_revoked_activation_ids=tuple(activation_ids),
            )
        # Return only after the Core writer actually commits, never from a
        # speculative pre-commit state. Existing row timestamps are untouched.
        return receipt

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
