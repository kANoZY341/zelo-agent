"""Read-only Specialist view of current Core Space file Task prerequisites.

This projection is a snapshot for a human operator, never an authorization
token, runtime permission check, File tool or independent computer grant.
"""

import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agent_factory import AgentFactory, AgentSpec
from app.agent_space_resources import AgentSpaceResourceService
from app.agent_specialist_profile import AgentSpecialistProfileService
from app.models import (
    AgentComputerPermission,
    AgentInstance,
    AgentSpaceActivationRecord,
    AgentSpaceActivationRevocation,
    AgentSpaceIntentRecord,
    AgentSpaceIntentRevocation,
    AgentSpaceResourceRecord,
    AgentSpaceResourceRetirement,
    utcnow,
)
from app.policy import DomainError
from app.skills import SkillRegistry
from app.storage import SessionStore

SpaceRouteStatus = Literal[
    "eligible_for_scoped_file_task",
    "owner_revoked",
    "expired",
    "resource_retired",
    "resource_root_changed",
    "resource_directory_unsafe",
    "agent_or_project_inactive",
    "unverified_binding",
    "agent_file_capability_missing",
    "computer_file_permission_denied",
    "computer_backend_unavailable",
]

MAX_SPACE_ROUTE_PROJECTIONS = 128


class SpecialistSpaceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    activation_id: str
    intent_id: str
    resource_record_id: str
    space_id: str
    resource_id: str
    eligible_operation: Literal["read"] = "read"
    expires_at: datetime
    owner_confirmation_recorded: Literal[True] = True
    eligible_for_scoped_file_task: bool
    reason: SpaceRouteStatus
    automatic_worker_authority: Literal[False] = False
    model_context_admitted: Literal[False] = False


class SpecialistSpaceAccessView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["zelo-specialist-space-access-v1"] = "zelo-specialist-space-access-v1"
    agent_instance_id: str
    inspection_state: Literal["current", "instance_inactive", "project_inactive", "stale"]
    routes: tuple[SpecialistSpaceRoute, ...] = Field(max_length=MAX_SPACE_ROUTE_PROJECTIONS)
    provenance: Literal["zelo_core_owner_confirmation"] = "zelo_core_owner_confirmation"
    projection_is_authority: Literal[False] = False
    upstream_dot_runtime_loaded: Literal[False] = False


class SpecialistSpaceAccessService:
    """Reports *why* a file Task would currently be denied, without enabling it."""

    def __init__(
        self,
        store: SessionStore,
        factory: AgentFactory,
        skills: SkillRegistry,
        runtime_dir: Path,
        *,
        computer_backend_available: bool = False,
    ) -> None:
        self.store = store
        self.factory = factory
        self.skills = skills
        self.resources = AgentSpaceResourceService(store, factory, runtime_dir)
        self.computer_backend_available = computer_backend_available

    def _safe_directory_chain(self, resource: AgentSpaceResourceRecord) -> bool:
        """Project the same directory-mode/ownership constraints as the read gateway."""
        root = self.resources.runtime_dir
        try:
            if not root.is_absolute() or root.resolve(strict=True) != root:
                return False
            for segment in ("spaces", resource.project_id, resource.space_id, resource.resource_id):
                root = root / segment
                info = root.lstat()
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o022
                ):
                    return False
            return True
        except (OSError, ValueError, RuntimeError):
            return False

    def get(self, instance_id: str) -> SpecialistSpaceAccessView:
        profile = AgentSpecialistProfileService(self.store, self.factory, self.skills).get(
            instance_id
        )
        results: list[SpecialistSpaceRoute] = []

        with self.store.read() as session:
            instance = session.get(AgentInstance, instance_id)
            if instance is None:
                raise DomainError("Agent instance not found", 404)
            try:
                spec = AgentSpec.model_validate(instance.spec)
            except ValueError:
                raise DomainError("Specialist Space snapshot unavailable", 409) from None
            permission = session.get(AgentComputerPermission, instance_id)
            activations = session.scalars(
                select(AgentSpaceActivationRecord)
                .where(AgentSpaceActivationRecord.agent_instance_id == instance_id)
                .order_by(
                    AgentSpaceActivationRecord.created_at.desc(),
                    AgentSpaceActivationRecord.id.desc(),
                )
                .limit(MAX_SPACE_ROUTE_PROJECTIONS)
            ).all()

            for activation in activations:
                intent = session.get(AgentSpaceIntentRecord, activation.intent_id)
                resource = session.get(AgentSpaceResourceRecord, activation.resource_record_id)
                # Never turn a damaged record into permission. The operator
                # may see a denial without accessing raw paths or source bytes.
                if intent is None or resource is None:
                    results.append(
                        SpecialistSpaceRoute(
                            activation_id=activation.id,
                            intent_id=activation.intent_id,
                            resource_record_id=activation.resource_record_id,
                            space_id="unavailable",
                            resource_id="unavailable",
                            expires_at=activation.expires_at,
                            eligible_for_scoped_file_task=False,
                            reason="unverified_binding",
                        )
                    )
                    continue

                revoked = (
                    session.get(AgentSpaceActivationRevocation, activation.id) is not None
                    or session.get(AgentSpaceIntentRevocation, intent.id) is not None
                )
                retired = session.get(AgentSpaceResourceRetirement, resource.id) is not None
                integrity_unavailable = False
                try:
                    receipt = self.resources.get_activation(
                        instance_id=instance_id, activation_id=activation.id
                    )
                    resource_info = self.resources._resource_receipt(session, resource)
                    verified = receipt.eligible_for_mediation
                    root_attested = resource_info.root_attested
                except (DomainError, ValueError, TypeError):
                    verified = False
                    root_attested = False
                    integrity_unavailable = True

                now = utcnow()
                if revoked:
                    reason: SpaceRouteStatus = "owner_revoked"
                elif activation.expires_at <= now:
                    reason = "expired"
                elif retired:
                    reason = "resource_retired"
                elif profile.inspection_state != "current":
                    reason = "agent_or_project_inactive"
                elif integrity_unavailable:
                    reason = "unverified_binding"
                elif not root_attested:
                    reason = "resource_root_changed"
                elif not self._safe_directory_chain(resource):
                    reason = "resource_directory_unsafe"
                elif not verified or resource.space_id not in spec.space_permissions:
                    reason = "unverified_binding"
                elif (
                    spec.computer is None
                    or not spec.computer.files
                    or "computer_read" not in spec.capabilities
                    or "computer_run" not in spec.allowed_task_kinds
                ):
                    reason = "agent_file_capability_missing"
                elif permission is None or not permission.enabled or not permission.files:
                    reason = "computer_file_permission_denied"
                elif not self.computer_backend_available:
                    reason = "computer_backend_unavailable"
                else:
                    reason = "eligible_for_scoped_file_task"

                results.append(
                    SpecialistSpaceRoute(
                        activation_id=activation.id,
                        intent_id=activation.intent_id,
                        resource_record_id=activation.resource_record_id,
                        space_id=resource.space_id,
                        resource_id=resource.resource_id,
                        expires_at=activation.expires_at,
                        eligible_for_scoped_file_task=(reason == "eligible_for_scoped_file_task"),
                        reason=reason,
                    )
                )
        return SpecialistSpaceAccessView(
            agent_instance_id=instance_id,
            inspection_state=profile.inspection_state,
            routes=tuple(results),
        )
