"""Persisted Core authority for per-AgentInstance computers."""

from __future__ import annotations

from sqlalchemy import func, select

from app.agent_computers import (
    ComputerBackendError,
    ComputerControlState,
    OpenBotComputerBackend,
)
from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.models import (
    AgentComputerEvent,
    AgentComputerPermission,
    AgentInstance,
    utcnow,
)
from app.policy import DomainError
from app.schemas import (
    AgentComputerControlResponse,
    AgentComputerEventPage,
    AgentComputerEventResponse,
    AgentComputerPermissionPatch,
    AgentComputerPermissionsResponse,
    AgentComputerStatusResponse,
)
from app.storage import SessionStore


class AgentComputerService:
    """Own computer grants and owner lifecycle/control without Agent execution."""

    def __init__(
        self,
        database: SessionStore,
        factory: AgentFactory,
        backend: OpenBotComputerBackend,
    ) -> None:
        self.database = database
        self.factory = factory
        self.backend = backend

    @staticmethod
    def _load_instance(session, instance_id: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise DomainError("Agent instance not found", 404)
        return instance

    @staticmethod
    def _spec(instance: AgentInstance) -> AgentSpec:
        try:
            spec = AgentSpec.model_validate(instance.spec)
        except ValueError:
            raise DomainError("Agent instance spec is invalid", 409) from None
        if spec.computer is None:
            raise DomainError("Agent instance has no computer profile", 404)
        return spec

    def _require_current_active(self, instance: AgentInstance, spec: AgentSpec) -> None:
        if instance.status != "active":
            raise DomainError("Agent instance is not active", 409)
        binding = AgentBinding(
            spec=spec,
            registry_version=instance.registry_version,
        )
        if not self.factory.matches_agent(binding):
            raise DomainError("Agent instance spec is stale", 409)

    @staticmethod
    def _permissions(session, instance_id: str) -> AgentComputerPermission:
        permissions = session.get(AgentComputerPermission, instance_id)
        if permissions is None:
            raise DomainError("Agent computer permission record is missing", 409)
        return permissions

    @staticmethod
    def _permissions_response(
        permissions: AgentComputerPermission,
    ) -> AgentComputerPermissionsResponse:
        return AgentComputerPermissionsResponse(
            enabled=permissions.enabled,
            browser=permissions.browser,
            files=permissions.files,
            shell=permissions.shell,
            updated_at=permissions.updated_at,
        )

    @staticmethod
    def _control_response(
        control: ComputerControlState,
    ) -> AgentComputerControlResponse:
        return AgentComputerControlResponse(
            holder=control.holder,
            requested=control.requested,
            transitioning=control.transitioning,
            resume_snapshot_required=control.resume_snapshot_required,
            request_id=control.request.id if control.request else None,
            request_status=control.request.status if control.request else None,
        )

    @staticmethod
    def _event_response(event: AgentComputerEvent) -> AgentComputerEventResponse:
        return AgentComputerEventResponse.model_validate(event)

    @staticmethod
    def _append_event(
        session,
        instance_id: str,
        *,
        event: str,
        actor: str,
        outcome: str,
        details: dict | None = None,
    ) -> AgentComputerEvent:
        sequence = (
            session.scalar(
                select(func.max(AgentComputerEvent.sequence)).where(
                    AgentComputerEvent.agent_instance_id == instance_id
                )
            )
            or 0
        ) + 1
        item = AgentComputerEvent(
            agent_instance_id=instance_id,
            sequence=sequence,
            event=event,
            actor=actor,
            outcome=outcome,
            details=details or {},
            created_at=utcnow(),
        )
        session.add(item)
        session.flush()
        return item

    def _record(
        self,
        instance_id: str,
        event: str,
        outcome: str,
        details: dict | None = None,
        *,
        actor: str = "owner",
    ) -> None:
        with self.database.transaction() as session:
            self._load_instance(session, instance_id)
            self._append_event(
                session,
                instance_id,
                event=event,
                actor=actor,
                outcome=outcome,
                details=details,
            )

    @staticmethod
    def _backend_error(error: ComputerBackendError) -> DomainError:
        code = str(error)
        if code in {
            "computer_not_running",
            "computer_control_request_missing",
            "computer_http_409",
        }:
            return DomainError(code, 409)
        if code.startswith("computer_http_4"):
            return DomainError(code, 409)
        return DomainError(code, 503)

    async def status(self, instance_id: str) -> AgentComputerStatusResponse:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            spec = self._spec(instance)
            permissions = self._permissions_response(self._permissions(session, instance_id))

        if not self.backend.configured:
            return AgentComputerStatusResponse(
                agent_instance_id=instance_id,
                configured=False,
                state="not_configured",
                spec=spec.computer,
                permissions=permissions,
            )
        try:
            state = await self.backend.existing(instance_id)
            if state is None or state.status != "running":
                return AgentComputerStatusResponse(
                    agent_instance_id=instance_id,
                    configured=True,
                    state="stopped",
                    spec=spec.computer,
                    permissions=permissions,
                )
            control = await self.backend.control(instance_id)
            return AgentComputerStatusResponse(
                agent_instance_id=instance_id,
                configured=True,
                state="running",
                spec=spec.computer,
                permissions=permissions,
                control=self._control_response(control),
            )
        except ComputerBackendError as error:
            return AgentComputerStatusResponse(
                agent_instance_id=instance_id,
                configured=True,
                state="unavailable",
                spec=spec.computer,
                permissions=permissions,
                error=str(error),
            )

    async def patch_permissions(
        self,
        instance_id: str,
        patch: AgentComputerPermissionPatch,
    ) -> AgentComputerStatusResponse:
        revoked_enabled = False
        with self.database.transaction() as session:
            instance = self._load_instance(session, instance_id)
            spec = self._spec(instance)
            permissions = self._permissions(session, instance_id)
            before = {
                "enabled": permissions.enabled,
                "browser": permissions.browser,
                "files": permissions.files,
                "shell": permissions.shell,
            }
            requested = patch.model_dump(exclude_none=True)
            after = {**before, **requested}
            for field in ("browser", "files", "shell"):
                if after[field] and not getattr(spec.computer, field):
                    raise DomainError(
                        f"Agent computer {field} permission exceeds trusted spec",
                        409,
                    )

            widened = any(
                after[field] and not before[field]
                for field in ("enabled", "browser", "files", "shell")
            )
            if widened:
                self._require_current_active(instance, spec)

            now = utcnow()
            for field, value in requested.items():
                setattr(permissions, field, value)
            permissions.updated_at = now
            revoked_enabled = before["enabled"] and not after["enabled"]
            self._append_event(
                session,
                instance_id,
                event="permissions_changed",
                actor="owner",
                outcome="succeeded",
                details={
                    "changed": sorted(requested),
                    "permissions": after,
                },
            )
            session.flush()

        if revoked_enabled and self.backend.configured:
            try:
                await self.backend.stop(instance_id)
            except ComputerBackendError as error:
                self._record(
                    instance_id,
                    "revocation_stop",
                    "failed",
                    {"error": str(error)},
                    actor="system",
                )
            else:
                self._record(
                    instance_id,
                    "revocation_stop",
                    "succeeded",
                    actor="system",
                )
        return await self.status(instance_id)

    async def start(self, instance_id: str) -> AgentComputerStatusResponse:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            spec = self._spec(instance)
            self._require_current_active(instance, spec)
            permissions = self._permissions(session, instance_id)
            if not permissions.enabled:
                raise DomainError("Agent computer is disabled", 409)
            if not any((permissions.browser, permissions.files, permissions.shell)):
                raise DomainError("Agent computer has no enabled capability", 409)
        if not self.backend.configured:
            raise DomainError("Agent computer backend is not configured", 503)
        try:
            await self.backend.ensure(instance_id)
        except ComputerBackendError as error:
            self._record(
                instance_id,
                "start",
                "failed",
                {"error": str(error)},
            )
            raise self._backend_error(error) from None
        self._record(instance_id, "start", "succeeded")
        return await self.status(instance_id)

    async def stop(self, instance_id: str) -> AgentComputerStatusResponse:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            self._spec(instance)
        if not self.backend.configured:
            raise DomainError("Agent computer backend is not configured", 503)
        try:
            await self.backend.stop(instance_id)
        except ComputerBackendError as error:
            self._record(
                instance_id,
                "stop",
                "failed",
                {"error": str(error)},
            )
            raise self._backend_error(error) from None
        self._record(instance_id, "stop", "succeeded")
        return await self.status(instance_id)

    async def take(self, instance_id: str) -> AgentComputerStatusResponse:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            spec = self._spec(instance)
            self._require_current_active(instance, spec)
            permissions = self._permissions(session, instance_id)
            if not (permissions.enabled and permissions.browser and spec.computer.human_takeover):
                raise DomainError("Human computer takeover is not permitted", 409)
        if not self.backend.configured:
            raise DomainError("Agent computer backend is not configured", 503)
        try:
            control = await self.backend.take(instance_id)
        except ComputerBackendError as error:
            self._record(
                instance_id,
                "take_control",
                "failed",
                {"error": str(error)},
            )
            raise self._backend_error(error) from None
        self._record(
            instance_id,
            "take_control",
            "succeeded",
            {"holder": control.holder},
        )
        return await self.status(instance_id)

    async def release(self, instance_id: str) -> AgentComputerStatusResponse:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            self._spec(instance)
        if not self.backend.configured:
            raise DomainError("Agent computer backend is not configured", 503)
        try:
            control = await self.backend.release(instance_id)
        except ComputerBackendError as error:
            self._record(
                instance_id,
                "release_control",
                "failed",
                {"error": str(error)},
            )
            raise self._backend_error(error) from None
        self._record(
            instance_id,
            "release_control",
            "succeeded",
            {
                "holder": control.holder,
                "resume_snapshot_required": control.resume_snapshot_required,
            },
        )
        return await self.status(instance_id)

    def events(
        self,
        instance_id: str,
        *,
        limit: int,
        offset: int,
    ) -> AgentComputerEventPage:
        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            self._spec(instance)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentComputerEvent)
                    .where(AgentComputerEvent.agent_instance_id == instance_id)
                )
                or 0
            )
            events = list(
                session.scalars(
                    select(AgentComputerEvent)
                    .where(AgentComputerEvent.agent_instance_id == instance_id)
                    .order_by(AgentComputerEvent.sequence)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return AgentComputerEventPage(
                items=[self._event_response(event) for event in events],
                limit=limit,
                offset=offset,
                total=total,
            )
