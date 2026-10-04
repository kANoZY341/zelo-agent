"""Core-owned per-Agent authorization for persistent-computer HTTPS egress."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agent_factory import AgentBinding, AgentFactory, AgentSpec
from app.api_contracts import ApiEndpointBinding, ApiEndpointRegistry
from app.config import Settings
from app.models import AgentComputerPermission, AgentInstance
from app.storage import SessionStore

EGRESS_POLICY_SCHEMA = "zelo-computer-egress-policy-v1"
EGRESS_AUTH_SCHEMA = "zelo-computer-egress-authorization-v1"
EGRESS_HMAC_CONTEXT = "zelo-egress:"


class ComputerEgressError(ValueError):
    """Sanitized fail-closed egress denial."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EgressContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComputerEgressAuthorization(EgressContract):
    schema_version: Literal["zelo-computer-egress-authorization-v1"] = EGRESS_AUTH_SCHEMA
    policy_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_instance_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    agent_registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    permission_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint: ApiEndpointBinding


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def computer_egress_policy_version() -> str:
    return _digest(
        {
            "schema": EGRESS_POLICY_SCHEMA,
            "protocol": "http-connect-only",
            "scheme": "https-only",
            "agent_auth": "hmac-sha256-v1",
            "hmac_context": EGRESS_HMAC_CONTEXT,
            "target_identity": "api-endpoint-registry-v1",
            "agent_current": True,
            "computer_enabled": True,
            "browser_permission": True,
            "capability": "computer_navigate",
            "endpoint_grants": "agent-computer-spec-v1",
            "resolver": "owned-public-address-v1",
            "direct_fallback": False,
        }
    )


def _canonical_instance_id(value: str) -> str:
    if not isinstance(value, str):
        raise ComputerEgressError("computer_egress_agent_id_invalid")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ComputerEgressError("computer_egress_agent_id_invalid") from None
    if str(parsed) != value:
        raise ComputerEgressError("computer_egress_agent_id_invalid")
    return value


def _permission_version(row: AgentComputerPermission) -> str:
    return _digest(
        {
            "schema": "zelo-agent-computer-permission-v1",
            "agent_instance_id": row.agent_instance_id,
            "enabled": row.enabled,
            "browser": row.browser,
            "files": row.files,
            "shell": row.shell,
            "updated_at": row.updated_at.isoformat(),
        }
    )


class ComputerEgressPolicy:
    """Authenticate one Agent computer and authorize one trusted HTTPS authority."""

    def __init__(
        self,
        database: SessionStore,
        factory: AgentFactory,
        endpoints: ApiEndpointRegistry,
        settings: Settings,
    ) -> None:
        self.database = database
        self.factory = factory
        self.endpoints = endpoints
        self.settings = settings
        self.policy_version = computer_egress_policy_version()

    @staticmethod
    def _load_instance(session: Session, instance_id: str) -> AgentInstance:
        instance = session.get(AgentInstance, instance_id)
        if instance is None:
            raise ComputerEgressError("computer_egress_agent_unavailable")
        return instance

    @staticmethod
    def _spec(instance: AgentInstance) -> AgentSpec:
        try:
            return AgentSpec.model_validate(instance.spec)
        except ValueError:
            raise ComputerEgressError("computer_egress_agent_invalid") from None

    @staticmethod
    def _permissions(session: Session, instance_id: str) -> AgentComputerPermission:
        row = session.get(AgentComputerPermission, instance_id)
        if row is None:
            raise ComputerEgressError("computer_egress_permission_unavailable")
        return row

    def derive_password(self, instance_id: str) -> str:
        instance_id = _canonical_instance_id(instance_id)
        if self.settings.computer_token is None:
            raise ComputerEgressError("computer_egress_not_configured")
        master = self.settings.computer_token.get_secret_value().encode("ascii")
        return hmac.new(
            master,
            f"{EGRESS_HMAC_CONTEXT}{instance_id}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def authenticate(self, instance_id: str, password: str) -> None:
        if not isinstance(password, str) or len(password) != 64 or not password.isascii():
            raise ComputerEgressError("computer_egress_auth_denied")
        expected = self.derive_password(instance_id)
        if not hmac.compare_digest(expected, password):
            raise ComputerEgressError("computer_egress_auth_denied")

    def validate_agents(self, agents: Iterable[AgentSpec]) -> None:
        for spec in agents:
            computer = spec.computer
            if computer is None:
                if "computer_navigate" in spec.capabilities:
                    raise ValueError(f"computer_navigation_profile_required:{spec.agent_id}")
                continue
            endpoint_ids = computer.browser_endpoint_ids
            if endpoint_ids and "computer_navigate" not in spec.capabilities:
                raise ValueError(f"computer_navigation_capability_required:{spec.agent_id}")
            if "computer_navigate" in spec.capabilities:
                if not computer.browser or not endpoint_ids:
                    raise ValueError(f"computer_navigation_grant_required:{spec.agent_id}")
            for endpoint_id in endpoint_ids:
                if self.endpoints.get(endpoint_id) is None:
                    raise ValueError(
                        f"computer_navigation_endpoint_unavailable:{spec.agent_id}:{endpoint_id}"
                    )

    def authorize(
        self,
        instance_id: str,
        *,
        host: str,
        port: int,
    ) -> ComputerEgressAuthorization:
        instance_id = _canonical_instance_id(instance_id)
        if (
            not isinstance(host, str)
            or not host
            or host.strip() != host
            or not host.isascii()
            or host != host.lower()
            or isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ComputerEgressError("computer_egress_target_invalid")

        with self.database.read() as session:
            instance = self._load_instance(session, instance_id)
            if instance.status != "active":
                raise ComputerEgressError("computer_egress_agent_inactive")
            spec = self._spec(instance)
            binding = AgentBinding(
                spec=spec,
                registry_version=instance.registry_version,
            )
            if not self.factory.matches_agent(binding):
                raise ComputerEgressError("computer_egress_agent_stale")

            computer = spec.computer
            if (
                computer is None
                or not computer.browser
                or "computer_navigate" not in spec.capabilities
            ):
                raise ComputerEgressError("computer_egress_not_authorized")

            permissions = self._permissions(session, instance_id)
            if not permissions.enabled or not permissions.browser:
                raise ComputerEgressError("computer_egress_permission_denied")
            permission_version = _permission_version(permissions)

            candidates: list[ApiEndpointBinding] = []
            for endpoint_id in computer.browser_endpoint_ids:
                endpoint = self.endpoints.get(endpoint_id)
                if endpoint is None:
                    raise ComputerEgressError("computer_egress_endpoint_registry_stale")
                if endpoint.host == host and endpoint.port == port:
                    candidates.append(self.endpoints.bind(endpoint_id))

            if not candidates:
                raise ComputerEgressError("computer_egress_target_denied")
            endpoint = min(
                candidates,
                key=lambda item: item.endpoint.endpoint_id,
            )
            return ComputerEgressAuthorization(
                policy_version=self.policy_version,
                agent_instance_id=instance_id,
                agent_registry_version=instance.registry_version,
                permission_version=permission_version,
                endpoint=endpoint,
            )

    def authorize_credentials(
        self,
        instance_id: str,
        password: str,
        *,
        host: str,
        port: int,
    ) -> ComputerEgressAuthorization:
        self.authenticate(instance_id, password)
        return self.authorize(instance_id, host=host, port=port)

    def matches(self, authorization: ComputerEgressAuthorization) -> bool:
        """Revalidate a live tunnel against current Core authority."""
        try:
            if authorization.policy_version != self.policy_version:
                return False
            endpoint = authorization.endpoint.endpoint
            current = self.authorize(
                authorization.agent_instance_id,
                host=endpoint.host,
                port=endpoint.port,
            )
            return current == authorization
        except (ComputerEgressError, ValueError):
            return False
