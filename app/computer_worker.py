"""Read-only per-AgentInstance computer worker behind Zelo Core authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.agent_computers import (
    ComputerBackendError,
    OpenBotComputerBackend,
)
from app.agent_execution_contracts import AgentExecutionBinding
from app.api_contracts import ApiEndpointRegistry
from app.computer_contracts import ComputerAction, ComputerExecutionSpec
from app.computer_egress import computer_egress_policy_version
from app.developer_contracts import WorkerSelectionSnapshot
from app.models import AgentComputerPermission
from app.schemas import (
    ComputerBrowserNavigateResult,
    ComputerBrowserReadResult,
    ComputerBrowserSnapshotResult,
    ComputerFileEntry,
    ComputerFilesListResult,
    ComputerFilesReadResult,
    ComputerRunResult,
    ComputerSnapshotElement,
)
from app.storage import SessionStore

Emit = Callable[[str, dict[str, Any]], None]


class ComputerRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def computer_read_policy_semantics() -> dict[str, object]:
    """Fixed v1 policy: observe only; no navigation, input, writes or shell."""
    return {
        "schema": "zelo-computer-read-policy-v1",
        "actions": (
            "browser_read",
            "browser_snapshot",
            "files_list",
            "files_read",
        ),
        "browser_mutation": False,
        "filesystem_write": False,
        "shell": False,
        "host_fallback": False,
        "credential_injection": False,
        "human_takeover_barrier": True,
    }


def computer_navigate_policy_semantics() -> dict[str, object]:
    """Fixed v1 policy: named HTTPS endpoint navigation only."""
    return {
        "schema": "zelo-computer-navigate-policy-v1",
        "actions": ("browser_navigate",),
        "target_authority": "trusted-endpoint-id-plus-bounded-path",
        "egress": "core-authenticated-allowlist-proxy",
        "direct_public_egress": False,
        "click": False,
        "type": False,
        "key": False,
        "scroll": False,
        "filesystem_write": False,
        "shell": False,
        "host_fallback": False,
        "credential_injection": False,
        "human_takeover_barrier": True,
    }


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


class ComputerRuntime:
    """Bind read-only computer work to exact Core grants and backend identity."""

    def __init__(
        self,
        database: SessionStore,
        backend: OpenBotComputerBackend,
        endpoints: ApiEndpointRegistry | None = None,
    ) -> None:
        self.database = database
        self.backend = backend
        self.endpoints = endpoints or ApiEndpointRegistry()

    @staticmethod
    def _permission_row(session: Session, instance_id: str) -> AgentComputerPermission:
        row = session.get(AgentComputerPermission, instance_id)
        if row is None:
            raise ComputerRuntimeError("computer_permission_unavailable")
        return row

    @staticmethod
    def _permission_version(row: AgentComputerPermission) -> str:
        return _canonical_digest(
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

    @staticmethod
    def _required_permission(action: ComputerAction) -> str:
        return "browser" if action.startswith("browser_") else "files"

    @classmethod
    def _permission_allows(
        cls,
        row: AgentComputerPermission,
        action: ComputerAction,
    ) -> bool:
        return row.enabled and bool(getattr(row, cls._required_permission(action)))

    @staticmethod
    def _required_capability(action: ComputerAction) -> str:
        return "computer_navigate" if action == "browser_navigate" else "computer_read"

    def bind(
        self,
        worker_selection: WorkerSelectionSnapshot,
        agent_execution: AgentExecutionBinding,
        action: ComputerAction,
        path: str,
        timeout_seconds: float,
        *,
        endpoint_id: str | None = None,
        space_activation_id: str | None = None,
    ) -> ComputerExecutionSpec:
        profile = agent_execution.spec.computer
        if profile is None:
            raise ComputerRuntimeError("computer_profile_unavailable")
        if space_activation_id is not None and action not in ("files_read", "files_list"):
            raise ComputerRuntimeError("computer_space_activation_action_denied")
        required_permission = self._required_permission(action)
        required_capability = self._required_capability(action)
        if not getattr(profile, required_permission):
            raise ComputerRuntimeError("computer_action_exceeds_agent_spec")
        if required_capability not in agent_execution.spec.capabilities:
            raise ComputerRuntimeError("computer_capability_denied")
        if tuple(worker_selection.requested_capabilities) != (required_capability,):
            raise ComputerRuntimeError("computer_worker_capability_mismatch")
        if not self.backend.configured:
            raise ComputerRuntimeError("computer_backend_not_configured")

        endpoint = None
        egress_policy_version = None
        if action == "browser_navigate":
            if (
                endpoint_id is None
                or endpoint_id not in profile.browser_endpoint_ids
                or self.backend.settings.computer_internal_network_cidr is None
                or self.backend.settings.computer_egress_proxy_host is None
            ):
                raise ComputerRuntimeError("computer_navigation_authority_denied")
            try:
                endpoint = self.endpoints.bind(endpoint_id)
            except ValueError:
                raise ComputerRuntimeError("computer_navigation_endpoint_unavailable") from None
            egress_policy_version = computer_egress_policy_version()
        elif endpoint_id is not None:
            raise ComputerRuntimeError("computer_read_endpoint_forbidden")

        with self.database.read() as session:
            row = self._permission_row(session, agent_execution.agent_instance_id)
            if not self._permission_allows(row, action):
                raise ComputerRuntimeError("computer_permission_denied")
            permission_version = self._permission_version(row)

        try:
            return ComputerExecutionSpec(
                agent_instance_id=agent_execution.agent_instance_id,
                worker_selection=worker_selection,
                action=action,
                path=path,
                space_activation_id=space_activation_id,
                endpoint=endpoint,
                egress_policy_version=egress_policy_version,
                permission_version=permission_version,
                backend_identity=self.backend.identity,
                timeout_seconds=timeout_seconds,
            )
        except ValueError:
            raise ComputerRuntimeError("computer_execution_spec_invalid") from None

    def matches(self, spec: ComputerExecutionSpec) -> bool:
        if not self.backend.configured or spec.backend_identity != self.backend.identity:
            return False
        required_capability = self._required_capability(spec.action)
        if tuple(spec.worker_selection.requested_capabilities) != (required_capability,):
            return False
        if spec.action == "browser_navigate":
            if (
                spec.endpoint is None
                or spec.egress_policy_version != computer_egress_policy_version()
                or not self.endpoints.matches(spec.endpoint)
                or self.backend.settings.computer_internal_network_cidr is None
                or self.backend.settings.computer_egress_proxy_host is None
            ):
                return False
        elif spec.endpoint is not None or spec.egress_policy_version is not None:
            return False
        try:
            with self.database.read() as session:
                row = self._permission_row(session, spec.agent_instance_id)
                return (
                    self._permission_allows(row, spec.action)
                    and self._permission_version(row) == spec.permission_version
                )
        except ComputerRuntimeError:
            return False

    @staticmethod
    def request_matches(
        spec: ComputerExecutionSpec,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> bool:
        endpoint_id = spec.endpoint.endpoint.endpoint_id if spec.endpoint is not None else None
        return (
            payload.get("action") == spec.action
            and payload.get("endpoint_id") == endpoint_id
            and payload.get("path", "") == spec.path
            and payload.get("space_activation_id") == spec.space_activation_id
            and timeout_seconds == spec.timeout_seconds
        )

    @staticmethod
    def _string(
        value: Any,
        limit: int,
        *,
        nonempty: bool = False,
        truncate: bool = False,
    ) -> tuple[str, bool]:
        if not isinstance(value, str) or (nonempty and not value):
            raise ComputerRuntimeError("computer_response_invalid")
        if len(value) <= limit:
            return value, False
        if not truncate:
            raise ComputerRuntimeError("computer_response_invalid")
        return value[:limit], True

    @staticmethod
    def _boolean(value: Any) -> bool:
        if not isinstance(value, bool):
            raise ComputerRuntimeError("computer_response_invalid")
        return value

    @staticmethod
    def _integer(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ComputerRuntimeError("computer_response_invalid")
        return value

    @classmethod
    def _normalize(
        cls,
        spec: ComputerExecutionSpec,
        raw: object,
    ) -> ComputerRunResult:
        if not isinstance(raw, dict):
            raise ComputerRuntimeError("computer_response_invalid")
        action = spec.action
        try:
            if action == "browser_snapshot":
                elements_raw = raw.get("elements")
                if not isinstance(elements_raw, list) or len(elements_raw) > 200:
                    raise ComputerRuntimeError("computer_response_invalid")
                snapshot_id = cls._integer(raw.get("snapshotId"))
                if snapshot_id < 0:
                    raise ComputerRuntimeError("computer_response_invalid")
                url, _ = cls._string(raw.get("url"), 2048, nonempty=False)
                title, title_cut = cls._string(raw.get("title"), 512, truncate=True)
                upstream_truncated = cls._boolean(raw.get("truncated"))
                elements = []
                local_truncated = title_cut
                for item in elements_raw:
                    if not isinstance(item, dict):
                        raise ComputerRuntimeError("computer_response_invalid")
                    ref, _ = cls._string(item.get("ref"), 100, nonempty=True)
                    role, _ = cls._string(item.get("role"), 100, nonempty=True)
                    name, name_cut = cls._string(item.get("name"), 500, truncate=True)
                    value = None
                    value_cut = False
                    if item.get("value") is not None:
                        value, value_cut = cls._string(item.get("value"), 500, truncate=True)
                    disabled = item.get("disabled")
                    if disabled is not None and not isinstance(disabled, bool):
                        raise ComputerRuntimeError("computer_response_invalid")
                    checked = item.get("checked")
                    if checked is not None and not isinstance(checked, bool):
                        raise ComputerRuntimeError("computer_response_invalid")
                    local_truncated = local_truncated or name_cut or value_cut
                    elements.append(
                        ComputerSnapshotElement(
                            ref=ref,
                            role=role,
                            name=name,
                            value=value,
                            disabled=disabled,
                            checked=checked,
                        )
                    )
                result = ComputerBrowserSnapshotResult(
                    snapshot_id=snapshot_id,
                    url=url,
                    title=title,
                    elements=elements,
                    truncated=upstream_truncated or local_truncated,
                    challenge_present=raw.get("challenge") is not None,
                )
                return ComputerRunResult(
                    action=action,
                    agent_instance_id=spec.agent_instance_id,
                    browser_snapshot=result,
                    completed_at=datetime.now(UTC),
                )

            if action == "browser_read":
                url, _ = cls._string(raw.get("url"), 2048)
                title, title_cut = cls._string(raw.get("title"), 512, truncate=True)
                text, text_cut = cls._string(raw.get("text"), 6000, truncate=True)
                result = ComputerBrowserReadResult(
                    url=url,
                    title=title,
                    text=text,
                    truncated=(cls._boolean(raw.get("truncated")) or title_cut or text_cut),
                    challenge_present=raw.get("challenge") is not None,
                )
                return ComputerRunResult(
                    action=action,
                    agent_instance_id=spec.agent_instance_id,
                    browser_read=result,
                    completed_at=datetime.now(UTC),
                )

            if action == "browser_navigate":
                if spec.endpoint is None:
                    raise ComputerRuntimeError("computer_response_invalid")
                final_url, _ = cls._string(raw.get("url"), 2048, nonempty=True)
                try:
                    parsed = urlsplit(final_url)
                    endpoint = spec.endpoint.endpoint
                    final_port = parsed.port or 443
                except ValueError:
                    raise ComputerRuntimeError("computer_response_invalid") from None
                if (
                    parsed.scheme != "https"
                    or parsed.hostname != endpoint.host
                    or final_port != endpoint.port
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise ComputerRuntimeError("computer_navigation_final_origin_denied")
                title, title_cut = cls._string(raw.get("title"), 512, truncate=True)
                text, text_cut = cls._string(raw.get("text"), 6000, truncate=True)
                elapsed_ms = cls._integer(raw.get("elapsedMs"))
                if elapsed_ms < 0 or elapsed_ms > 300_000:
                    raise ComputerRuntimeError("computer_response_invalid")
                result = ComputerBrowserNavigateResult(
                    endpoint_id=endpoint.endpoint_id,
                    url=final_url,
                    title=title,
                    text=text,
                    truncated=(cls._boolean(raw.get("truncated")) or title_cut or text_cut),
                    elapsed_ms=elapsed_ms,
                    challenge_present=raw.get("challenge") is not None,
                )
                return ComputerRunResult(
                    action=action,
                    agent_instance_id=spec.agent_instance_id,
                    browser_navigate=result,
                    completed_at=datetime.now(UTC),
                )

            if action == "files_list":
                entries_raw = raw.get("entries")
                if not isinstance(entries_raw, list) or len(entries_raw) > 500:
                    raise ComputerRuntimeError("computer_response_invalid")
                listed_path, _ = cls._string(raw.get("path"), 1024)
                entries = []
                for item in entries_raw:
                    if not isinstance(item, dict):
                        raise ComputerRuntimeError("computer_response_invalid")
                    entry_path, _ = cls._string(item.get("path"), 1024, nonempty=True)
                    kind = item.get("kind")
                    if kind not in {"file", "folder"}:
                        raise ComputerRuntimeError("computer_response_invalid")
                    size = item.get("bytes")
                    if size is not None and (
                        isinstance(size, bool) or not isinstance(size, int) or size < 0
                    ):
                        raise ComputerRuntimeError("computer_response_invalid")
                    entries.append(
                        ComputerFileEntry(
                            path=entry_path,
                            kind=kind,
                            bytes=size,
                        )
                    )
                result = ComputerFilesListResult(
                    path=listed_path,
                    entries=entries,
                    truncated=cls._boolean(raw.get("truncated")),
                )
                return ComputerRunResult(
                    action=action,
                    agent_instance_id=spec.agent_instance_id,
                    files_list=result,
                    completed_at=datetime.now(UTC),
                )

            if action == "files_read":
                read_path, _ = cls._string(raw.get("path"), 1024, nonempty=True)
                file_text, text_cut = cls._string(raw.get("text"), 64000, truncate=True)
                size = cls._integer(raw.get("bytes"))
                if size < 0:
                    raise ComputerRuntimeError("computer_response_invalid")
                result = ComputerFilesReadResult(
                    path=read_path,
                    text=file_text,
                    truncated=cls._boolean(raw.get("truncated")) or text_cut,
                    bytes=size,
                )
                return ComputerRunResult(
                    action=action,
                    agent_instance_id=spec.agent_instance_id,
                    files_read=result,
                    completed_at=datetime.now(UTC),
                )
        except ComputerRuntimeError:
            raise
        except (TypeError, ValueError):
            raise ComputerRuntimeError("computer_response_invalid") from None
        raise ComputerRuntimeError("computer_action_unsupported")

    async def run(
        self,
        *,
        spec: ComputerExecutionSpec,
        action: ComputerAction,
        path: str,
        timeout_seconds: float,
        emit: Emit,
        authorize_action: Callable[[], bool],
        endpoint_id: str | None = None,
    ) -> dict[str, Any]:
        # The Core Space gateway is the ONLY allowed path for Space content.
        if spec.space_activation_id is not None:
            raise ComputerRuntimeError("computer_space_requires_mediated_gateway")
        if not self.request_matches(
            spec,
            {"action": action, "endpoint_id": endpoint_id, "path": path},
            timeout_seconds,
        ) or not self.matches(spec) or not authorize_action():
            raise ComputerRuntimeError("computer_execution_spec_stale")

        try:
            control = await self.backend.control(spec.agent_instance_id)
        except ComputerBackendError as error:
            raise ComputerRuntimeError(str(error)) from None
        if control.holder == "human" or control.requested or control.transitioning:
            raise ComputerRuntimeError("computer_human_control_active")

        event_prefix = "computer_navigation" if action == "browser_navigate" else "computer_read"
        emit(
            f"{event_prefix}_started",
            {
                "agent_instance_id": spec.agent_instance_id,
                "action": action,
                **({"endpoint_id": endpoint_id} if endpoint_id is not None else {}),
            },
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                # The control lookup above suspends, and the started-event
                # callback can change Core state. Recheck the exact permission,
                # backend and endpoint binding before dispatching any operation;
                # a post-result refusal alone is too late to prevent access.
                if not self.matches(spec) or not authorize_action():
                    raise ComputerRuntimeError("computer_execution_spec_stale")
                if action == "browser_navigate":
                    if spec.endpoint is None:
                        raise ComputerRuntimeError("computer_execution_spec_stale")
                    raw = await self.backend.navigate_action(
                        spec.agent_instance_id,
                        spec.endpoint.endpoint.origin + path,
                    )
                else:
                    raw = await self.backend.read_action(
                        spec.agent_instance_id,
                        action,
                        path=path,
                    )
        except TimeoutError:
            raise ComputerRuntimeError("computer_timeout") from None
        except ComputerBackendError as error:
            raise ComputerRuntimeError(str(error)) from None

        if not self.matches(spec) or not authorize_action():
            raise ComputerRuntimeError("computer_execution_spec_stale")
        if action == "browser_navigate":
            try:
                control = await self.backend.control(spec.agent_instance_id)
            except ComputerBackendError as error:
                raise ComputerRuntimeError(str(error)) from None
            if control.holder == "human" or control.requested or control.transitioning:
                raise ComputerRuntimeError("computer_human_control_active")
        if not self.matches(spec) or not authorize_action():
            raise ComputerRuntimeError("computer_execution_spec_stale")
        result = self._normalize(spec, raw)
        emit(
            f"{event_prefix}_completed",
            {
                "agent_instance_id": spec.agent_instance_id,
                "action": action,
                **({"endpoint_id": endpoint_id} if endpoint_id is not None else {}),
            },
        )
        return result.model_dump(mode="json")
