"""Task-bound, bounded, read-only Space file operations through trusted Core.

A Space activation alone is never enough. This gateway has no HTTP route and
is not registered as a Worker/Tool; it only accepts an already claimed Core
Computer execution and rechecks it at the exact read use-point.
"""

import asyncio
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.agent_computers import ComputerBackendError
from app.agent_execution_contracts import AgentExecutionBinding
from app.agent_factory import AgentBinding, AgentFactory
from app.agent_space_intents import _UUID, _hash, _intent_digest, _snapshot
from app.agent_space_resources import (
    AgentSpaceResourceService,
    _activation_digest,
)
from app.computer_contracts import ComputerExecutionSpec
from app.computer_control_fence import human_takeover_unreleased
from app.computer_worker import ComputerRuntime
from app.models import (
    AgentComputerPermission,
    AgentInstance,
    AgentInstanceEvent,
    AgentSpaceActivationRecord,
    AgentSpaceActivationRevocation,
    AgentSpaceIntentRecord,
    AgentSpaceIntentRevocation,
    AgentSpaceResourceRecord,
    AgentSpaceResourceRetirement,
    AuditEvent,
    CampaignTaskLink,
    Project,
    Task,
    TaskAttempt,
    utcnow,
)
from app.policy import DomainError, State
from app.storage import SessionStore
from app.tasks import Execution, TaskService

MAX_SPACE_TEXT_BYTES = 65536
MAX_SPACE_FILE_PARTS = 4
MAX_SPACE_LIST_ENTRIES = 128
_MAX_PATH_LENGTH = 255
_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".json", ".csv", ".log"})


@dataclass(frozen=True, slots=True)
class SpaceFileListResult:
    task_id: str
    attempt_id: str
    agent_instance_id: str
    activation_id: str
    resource_record_id: str
    relative_path: str
    entries: tuple[dict[str, str | int | None], ...]
    truncated: bool = False
    model_context_admitted: bool = False


@dataclass(frozen=True, slots=True)
class SpaceFileReadResult:
    task_id: str
    attempt_id: str
    agent_instance_id: str
    activation_id: str
    resource_record_id: str
    relative_path: str
    text: str
    bytes_read: int
    content_sha256: str
    model_context_admitted: bool = False


def _path_components(value: str) -> tuple[str, ...]:
    if type(value) is not str or not value or len(value) > _MAX_PATH_LENGTH:
        raise DomainError("Space file path is not canonical", 422)
    parts = value.split("/")
    if (
        not 1 <= len(parts) <= MAX_SPACE_FILE_PARTS
        or any(not _PART.fullmatch(part) or part.endswith(".") for part in parts)
        or Path(parts[-1]).suffix.lower() not in _ALLOWED_SUFFIXES
    ):
        raise DomainError("Space file path is not canonical", 422)
    return tuple(parts)


def _directory_components(value: str) -> tuple[str, ...]:
    """Canonical relative directory; the empty string names the attested root."""
    if type(value) is not str or len(value) > _MAX_PATH_LENGTH:
        raise DomainError("Space directory path is not canonical", 422)
    if not value:
        return ()
    parts = value.split("/")
    if len(parts) > MAX_SPACE_FILE_PARTS - 1 or any(
        not _PART.fullmatch(part) or part.endswith(".") for part in parts
    ):
        raise DomainError("Space directory path is not canonical", 422)
    return tuple(parts)


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _list_bounded_entries(
    runtime_dir: Path,
    *,
    project_id: str,
    space_id: str,
    resource_id: str,
    device: int,
    inode: int,
    relative_parts: tuple[str, ...],
) -> tuple[dict[str, str | int | None], ...]:
    """Enumerate only safe file metadata using pinned directory descriptors."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    opened: list[int] = []
    children: list[tuple[int, str, int]] = []
    try:
        if not runtime_dir.is_absolute() or runtime_dir.resolve(strict=True) != runtime_dir:
            raise OSError("runtime_alias")
        parent_fd = os.open(runtime_dir, directory_flags)
        opened.append(parent_fd)
        for index, part in enumerate(
            ("spaces", project_id, space_id, resource_id, *relative_parts)
        ):
            ancestor_fd = parent_fd
            parent_fd = os.open(part, directory_flags, dir_fd=ancestor_fd)
            opened.append(parent_fd)
            children.append((ancestor_fd, part, parent_fd))
            info = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise OSError("directory_not_private")
            if index == 3 and (info.st_dev != device or info.st_ino != inode):
                raise OSError("registered_resource_root_changed")

        initial = os.fstat(parent_fd)
        names: list[dict[str, str | int | None]] = []
        # Fail closed instead of partially exposing a directory with unbounded
        # enumeration. Raw entries include untrusted, hidden and unsafe names.
        with os.scandir(parent_fd) as iterator:
            for count, entry in enumerate(iterator, start=1):
                if count > MAX_SPACE_LIST_ENTRIES:
                    raise OSError("directory_entry_limit")
                name = entry.name
                path_parts = (*relative_parts, name)
                if (
                    not _PART.fullmatch(name)
                    or name.endswith(".")
                    or len("/".join(path_parts)) > _MAX_PATH_LENGTH
                ):
                    continue
                before = entry.stat(follow_symlinks=False)
                if before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) & 0o022:
                    continue
                if stat.S_ISDIR(before.st_mode):
                    if len(path_parts) >= MAX_SPACE_FILE_PARTS:
                        continue
                    kind = "folder"
                    size = None
                elif (
                    stat.S_ISREG(before.st_mode)
                    and Path(name).suffix.lower() in _ALLOWED_SUFFIXES
                    and before.st_nlink == 1
                    and before.st_size <= MAX_SPACE_TEXT_BYTES
                ):
                    kind = "file"
                    size = before.st_size
                else:
                    continue
                after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if _stamp(before) != _stamp(after):
                    raise OSError("listed_file_changed")
                names.append({"path": "/".join(path_parts), "kind": kind, "bytes": size})
        if _stamp(initial) != _stamp(os.fstat(parent_fd)):
            raise OSError("listed_directory_changed")
        for ancestor_fd, name, child_fd in children:
            visible = os.stat(name, dir_fd=ancestor_fd, follow_symlinks=False)
            referenced = os.fstat(child_fd)
            if not stat.S_ISDIR(visible.st_mode) or (
                visible.st_dev,
                visible.st_ino,
            ) != (referenced.st_dev, referenced.st_ino):
                raise OSError("space_directory_alias_changed")
        return tuple(sorted(names, key=lambda item: str(item["path"])))
    except (OSError, ValueError):
        raise DomainError("Space directory listing unavailable or unsafe", 409) from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _open_bounded_utf8(
    runtime_dir: Path,
    *,
    project_id: str,
    space_id: str,
    resource_id: str,
    device: int,
    inode: int,
    relative_parts: tuple[str, ...],
) -> tuple[str, int, str]:
    """Open from verified directory FDs, not from a concatenated host path."""
    opened: list[int] = []
    children: list[tuple[int, str, int]] = []
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        if not runtime_dir.is_absolute() or runtime_dir.resolve(strict=True) != runtime_dir:
            raise OSError("runtime_alias")
        parent_fd = os.open(runtime_dir, directory_flags)
        opened.append(parent_fd)
        for index, part in enumerate(
            ("spaces", project_id, space_id, resource_id, *relative_parts[:-1])
        ):
            ancestor_fd = parent_fd
            parent_fd = os.open(part, directory_flags, dir_fd=ancestor_fd)
            opened.append(parent_fd)
            children.append((ancestor_fd, part, parent_fd))
            info = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise OSError("directory_not_private")
            if index == 3 and (info.st_dev != device or info.st_ino != inode):
                raise OSError("registered_resource_root_changed")
        fd = os.open(relative_parts[-1], file_flags, dir_fd=parent_fd)
        opened.append(fd)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > MAX_SPACE_TEXT_BYTES
        ):
            raise OSError("file_type_or_size_denied")
        content = bytearray()
        while len(content) <= MAX_SPACE_TEXT_BYTES:
            chunk = os.read(fd, min(8192, MAX_SPACE_TEXT_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(fd)
        at_path = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        if (
            _stamp(before) != _stamp(after)
            or _stamp(after) != _stamp(at_path)
            or len(content) != before.st_size
            or len(content) > MAX_SPACE_TEXT_BYTES
        ):
            raise OSError("file_changed_or_oversized")
        # A directory moved outside the approved root after its FD opened
        # must not silently serve content from the relocated subtree.
        for ancestor_fd, name, child_fd in children:
            visible = os.stat(name, dir_fd=ancestor_fd, follow_symlinks=False)
            referenced = os.fstat(child_fd)
            if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != (
                referenced.st_dev,
                referenced.st_ino,
            ):
                raise OSError("space_directory_alias_changed")
        raw = bytes(content)
        text = raw.decode("utf-8", errors="strict")
        return text, len(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, UnicodeError):
        raise DomainError("Space file content unavailable or unsafe", 409) from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


class SpaceFileReadGateway:
    """Exact running-task authority + live activation + bounded file operations."""

    def __init__(
        self,
        store: SessionStore,
        factory: AgentFactory,
        runtime_dir: Path,
        computer: ComputerRuntime,
        task_service: TaskService | None = None,
    ) -> None:
        self.store = store
        self.factory = factory
        self.runtime_dir = runtime_dir
        self.computer = computer
        self.task_service = task_service
        self.resources = AgentSpaceResourceService(store, factory, runtime_dir)

    def _verify_execution(
        self, session, execution: Execution, relative_path: str, *, action: str = "files_read"
    ):
        if (
            not isinstance(execution, Execution)
            or execution.kind != "computer_run"
            or execution.agent_execution is None
            or execution.computer_request is None
        ):
            raise DomainError("Space access requires claimed Computer execution", 403)
        task = session.get(Task, execution.task_id)
        attempt = session.get(TaskAttempt, execution.attempt_id)
        if (
            task is None
            or attempt is None
            or attempt.task_id != execution.task_id
            or task.kind != "computer_run"
            or task.status != State.RUNNING
            or task.cancel_requested
            or attempt.outcome != State.RUNNING
            or attempt.ended_at is not None
            or task.agent_execution_spec is None
            or task.computer_spec is None
        ):
            raise DomainError("Space read task or attempt no longer active", 409)
        latest = session.scalar(
            select(func.max(TaskAttempt.number)).where(TaskAttempt.task_id == task.id)
        )
        if latest != attempt.number:
            raise DomainError("Space read attempt is no longer current", 409)
        try:
            agent = AgentExecutionBinding.model_validate(task.agent_execution_spec)
            computer = ComputerExecutionSpec.model_validate(task.computer_spec)
        except ValueError:
            raise DomainError("Space read task binding invalid", 409) from None
        if (
            agent != execution.agent_execution
            or computer != execution.computer_request
            or agent.task_kind != "computer_run"
            or computer.agent_instance_id != agent.agent_instance_id
            or computer.space_activation_id is None
            or computer.space_activation_id != execution.payload.get("space_activation_id")
            or computer.action != action
            or computer.path != relative_path
            or computer.timeout_seconds != task.timeout_seconds
            or tuple(computer.worker_selection.requested_capabilities) != ("computer_read",)
            or task.payload.get("action") != action
            or task.payload.get("path") != relative_path
            or execution.payload != task.payload
            or execution.timeout_seconds != task.timeout_seconds
            or "computer_read" not in agent.spec.capabilities
            or "computer_run" not in agent.spec.allowed_task_kinds
            or agent.spec.computer is None
            or not agent.spec.computer.files
        ):
            raise DomainError("Space read does not match exact task authority", 403)
        if agent.delegation is not None:
            link = session.get(CampaignTaskLink, task.id)
            if self.task_service is None or link is None:
                raise DomainError("Space delegated authority unavailable", 403)
            # Recheck the live delegation, parent, campaign and budget under
            # the same Core transaction used for the exact file operation.
            self.task_service._validate_agent_execution(
                session, agent, campaign_id=link.campaign_id
            )
            delegated = agent.delegation
            parent = session.get(AgentInstance, delegated.parent_instance_id)
            if (
                "computer_read" not in delegated.required_capabilities
                or parent is None
                or "computer_read"
                not in self.task_service._current_agent_binding(parent).spec.capabilities
            ):
                raise DomainError("Space delegated computer authority invalid", 403)
        instance = session.get(AgentInstance, agent.agent_instance_id)
        project = session.get(Project, agent.project_id)
        if (
            instance is None
            or instance.status != "active"
            or project is None
            or project.status != "active"
            or instance.project_id != agent.project_id
            or instance.registry_version != agent.registry_version
            or instance.agent_id != agent.spec.agent_id
            or instance.agent_version != agent.spec.version
        ):
            raise DomainError("Space read agent or project stale", 409)
        try:
            current_spec = _snapshot(instance, self.factory)
        except DomainError:
            raise DomainError("Space read AgentSpec stale", 409) from None
        if current_spec != agent.spec or not self.factory.matches_agent(
            AgentBinding(spec=agent.spec, registry_version=agent.registry_version)
        ):
            raise DomainError("Space read AgentSpec binding changed", 409)
        # The owner's pre-network takeover marker is committed under the
        # same Core writer used by the final file read. A stale backend bot
        # snapshot from before takeover is never enough to override it.
        if human_takeover_unreleased(session, instance.id):
            raise DomainError("Space computer human control active", 409)
        permission = session.get(AgentComputerPermission, instance.id)
        if (
            permission is None
            or not permission.enabled
            or not permission.files
            or ComputerRuntime._permission_version(permission) != computer.permission_version
            or not self.computer.matches(computer)
        ):
            raise DomainError("Space read computer permission stale", 409)
        return agent, current_spec

    def _verified_resource(
        self, session, *, instance_id: str, activation_id: str, operation: str = "read"
    ):
        if type(activation_id) is not str or not _UUID.fullmatch(activation_id):
            raise DomainError("Space activation not found", 404)
        activation = session.get(AgentSpaceActivationRecord, activation_id)
        if activation is None or activation.agent_instance_id != instance_id:
            raise DomainError("Space activation not found", 404)
        intent = session.get(AgentSpaceIntentRecord, activation.intent_id)
        resource = session.get(AgentSpaceResourceRecord, activation.resource_record_id)
        if intent is None or resource is None:
            raise DomainError("Space binding incomplete", 503)
        if (
            activation.activation_sha256 != _activation_digest(activation)
            or session.get(AgentSpaceActivationRevocation, activation_id) is not None
            or session.get(AgentSpaceIntentRevocation, intent.id) is not None
            or session.get(AgentSpaceResourceRetirement, resource.id) is not None
            or activation.expires_at <= utcnow()
            or intent.expires_at <= utcnow()
            or activation.expires_at != intent.expires_at
        ):
            raise DomainError("Space activation revoked, expired or invalid", 403)
        receipt = self.resources._resource_receipt(session, resource)
        intent_receipt = self.resources.intents._receipt(intent, revoked=False, current=True)
        instance = self.resources.intents._load_instance(session, instance_id)
        spec = _snapshot(instance, self.factory)
        lifecycle = session.scalar(
            select(AgentInstanceEvent.id)
            .where(
                AgentInstanceEvent.agent_instance_id == instance.id,
                AgentInstanceEvent.event.in_(("paused", "resumed", "retired")),
                AgentInstanceEvent.created_at >= intent.created_at,
            )
            .limit(1)
        )
        if (
            lifecycle is not None
            or not receipt.root_attested
            or receipt.retired
            or operation not in receipt.operations
            or operation not in intent_receipt.operations
            or intent.agent_instance_id != instance_id
            or intent.agent_id != instance.agent_id
            or intent.agent_version != instance.agent_version
            or intent.registry_version != instance.registry_version
            or intent.project_id != instance.project_id
            or intent.spec_sha256 != _hash(spec.model_dump(mode="json"))
            or intent.space_id not in spec.space_permissions
            or activation.agent_instance_id != instance_id
            or activation.agent_version != intent.agent_version
            or activation.registry_version != intent.registry_version
            or activation.project_id != intent.project_id
            or activation.spec_sha256 != intent.spec_sha256
            or activation.intent_sha256 != _intent_digest(intent)
            or intent.intent_sha256 != activation.intent_sha256
            or activation.resource_sha256 != resource.resource_sha256
            or resource.project_id != intent.project_id
            or resource.space_id != intent.space_id
            or resource.resource_id != intent.resource_id
            or resource.resource_kind != intent.resource_kind
            or not set(intent_receipt.operations).issubset(receipt.operations)
        ):
            raise DomainError("Space activation binding or lifecycle stale", 403)
        return resource

    @staticmethod
    def _require_claim_lifecycle(session, execution: Execution) -> None:
        """Fence child and delegating parent before the physical file use."""
        agent = execution.agent_execution
        if agent is None or execution.computer_agent_lifecycle is None:
            raise DomainError("Space read Agent lifecycle changed after claim", 409)
        delegated = agent.delegation
        current = (
            TaskService._agent_lifecycle_sequence(session, agent.agent_instance_id),
            TaskService._agent_lifecycle_sequence(session, delegated.parent_instance_id)
            if delegated is not None
            else None,
        )
        if current != execution.computer_agent_lifecycle:
            raise DomainError("Space read Agent lifecycle changed after claim", 409)

    async def read(
        self, *, execution: Execution, activation_id: str, relative_path: str
    ) -> SpaceFileReadResult:
        parts = _path_components(relative_path)
        if (
            execution.computer_request is None
            or execution.computer_request.space_activation_id != activation_id
        ):
            raise DomainError("Space read activation not bound to Task action", 403)
        # The same human-control barrier as the Computer Worker applies.
        # Check task authority before the external control lookup, then
        # recheck everything after the await under a SQLite write lock.
        with self.store.read() as session:
            agent, _ = self._verify_execution(session, execution, relative_path)
        try:
            # Unlike the normal Computer Worker this gateway has a separate
            # dispatch path; preserve its bounded control-lookup deadline.
            async with asyncio.timeout(execution.timeout_seconds):
                control = await self.computer.backend.control(agent.agent_instance_id)
        except TimeoutError:
            raise DomainError("Space computer control timed out", 504) from None
        except ComputerBackendError:
            raise DomainError("Space computer control unavailable", 503) from None
        if (
            control.holder == "human"
            or control.requested
            or control.transitioning
            or control.resume_snapshot_required
        ):
            raise DomainError("Space computer human control active", 409)
        # Transaction uses BEGIN IMMEDIATE on SQLite. No concurrent Core
        # revocation/cancellation can commit between this check and this read.
        # The critical section is bounded to 64 KiB and contains no await.
        with self.store.transaction() as session:
            agent, _ = self._verify_execution(session, execution, relative_path)
            resource = self._verified_resource(
                session,
                instance_id=agent.agent_instance_id,
                activation_id=activation_id,
            )
            self._require_claim_lifecycle(session, execution)
            text, size, digest = _open_bounded_utf8(
                self.runtime_dir,
                project_id=resource.project_id,
                space_id=resource.space_id,
                resource_id=resource.resource_id,
                device=resource.root_device,
                inode=resource.root_inode,
                relative_parts=parts,
            )
            session.add(
                AuditEvent(
                    task_id=execution.task_id,
                    attempt_id=execution.attempt_id,
                    event="space_file_read_completed",
                    details={
                        "agent_instance_id": agent.agent_instance_id,
                        "activation_id": activation_id,
                        "resource_record_id": resource.id,
                        "path_sha256": hashlib.sha256(relative_path.encode("utf-8")).hexdigest(),
                        "content_sha256": digest,
                        "bytes": size,
                    },
                    created_at=utcnow(),
                )
            )
            return SpaceFileReadResult(
                task_id=execution.task_id,
                attempt_id=execution.attempt_id,
                agent_instance_id=agent.agent_instance_id,
                activation_id=activation_id,
                resource_record_id=resource.id,
                relative_path=relative_path,
                text=text,
                bytes_read=size,
                content_sha256=digest,
            )

    async def list(
        self, *, execution: Execution, activation_id: str, relative_path: str
    ) -> SpaceFileListResult:
        parts = _directory_components(relative_path)
        if (
            execution.computer_request is None
            or execution.computer_request.space_activation_id != activation_id
        ):
            raise DomainError("Space list activation not bound to Task action", 403)
        with self.store.read() as session:
            agent, _ = self._verify_execution(
                session, execution, relative_path, action="files_list"
            )
        try:
            async with asyncio.timeout(execution.timeout_seconds):
                control = await self.computer.backend.control(agent.agent_instance_id)
        except TimeoutError:
            raise DomainError("Space computer control timed out", 504) from None
        except ComputerBackendError:
            raise DomainError("Space computer control unavailable", 503) from None
        if (
            control.holder == "human"
            or control.requested
            or control.transitioning
            or control.resume_snapshot_required
        ):
            raise DomainError("Space computer human control active", 409)

        # The exact use and audit remain in one Core SQLite serialized writer.
        # No untrusted await or remote provider runs inside this transaction.
        with self.store.transaction() as session:
            agent, _ = self._verify_execution(
                session, execution, relative_path, action="files_list"
            )
            resource = self._verified_resource(
                session,
                instance_id=agent.agent_instance_id,
                activation_id=activation_id,
                operation="list",
            )
            self._require_claim_lifecycle(session, execution)
            entries = _list_bounded_entries(
                self.runtime_dir,
                project_id=resource.project_id,
                space_id=resource.space_id,
                resource_id=resource.resource_id,
                device=resource.root_device,
                inode=resource.root_inode,
                relative_parts=parts,
            )
            session.add(
                AuditEvent(
                    task_id=execution.task_id,
                    attempt_id=execution.attempt_id,
                    event="space_file_list_completed",
                    details={
                        "agent_instance_id": agent.agent_instance_id,
                        "activation_id": activation_id,
                        "resource_record_id": resource.id,
                        "path_sha256": hashlib.sha256(relative_path.encode("utf-8")).hexdigest(),
                        "entry_count": len(entries),
                    },
                    created_at=utcnow(),
                )
            )
            return SpaceFileListResult(
                task_id=execution.task_id,
                attempt_id=execution.attempt_id,
                agent_instance_id=agent.agent_instance_id,
                activation_id=activation_id,
                resource_record_id=resource.id,
                relative_path=relative_path,
                entries=entries,
            )
