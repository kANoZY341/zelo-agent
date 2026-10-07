"""Fail-closed adapter for persistent per-agent OpenBot computers.

The backend is intentionally narrower than OpenDots' full ComputerService:
this slice proves configuration, per-agent credentials, supervisor identity and
persistent computer lifecycle. Agent browser/file/shell execution remains a
separate Zelo Worker/authority slice.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.computer_contracts import ComputerReadAction
from app.config import Settings

_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_RESPONSE_BYTES = 4_000_000
OPENBOT_COMPUTER_REVISION = "b6932d31a8d6e7896c15139dfc27a6c6911deb27"
COMPUTER_ADAPTER_VERSION = "zelo-openbot-computer-v1"


class ComputerBackendError(RuntimeError):
    """Computer backend failed closed."""


class ComputerState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    agent_id: str = Field(alias="botId")
    container: str
    status: str
    port: int | None = Field(default=None, ge=1024, le=65535)
    url: str | None = None


class ComputerListing(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    computers: tuple[ComputerState, ...]


class ComputerControlRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    status: str


class ComputerControlState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    holder: Literal["bot", "human"]
    requested: bool = False
    transitioning: bool = False
    resume_snapshot_required: bool = Field(alias="resumeSnapshotRequired")
    request: ComputerControlRequest | None = None


class OpenBotComputerBackend:
    """Zelo-owned boundary over OpenBot's computer supervisor."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def configured(self) -> bool:
        return all(
            value is not None
            for value in (
                self.settings.computer_supervisor_url,
                self.settings.computer_supervisor_token,
                self.settings.computer_token,
            )
        )

    @property
    def identity(self) -> str:
        payload = {
            "adapter": COMPUTER_ADAPTER_VERSION,
            "openbot_revision": OPENBOT_COMPUTER_REVISION,
            "namespace": self.settings.computer_namespace,
            "supervisor_url": self.settings.computer_supervisor_url,
            "internal_network_cidr": self.settings.computer_internal_network_cidr,
            "network_name": self.settings.computer_network_name,
            "egress_proxy_host": self.settings.computer_egress_proxy_host,
            "egress_proxy_port": self.settings.computer_egress_proxy_port,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()

    @staticmethod
    def _require_agent_id(agent_instance_id: str) -> str:
        if not _AGENT_ID.fullmatch(agent_instance_id):
            raise ComputerBackendError("computer_agent_id_invalid")
        return agent_instance_id

    def derive_agent_token(self, agent_instance_id: str) -> str:
        agent_instance_id = self._require_agent_id(agent_instance_id)
        if self.settings.computer_token is None:
            raise ComputerBackendError("computer_backend_not_configured")
        master = self.settings.computer_token.get_secret_value().encode("ascii")
        return hmac.new(
            master,
            f"zelo-computer:{agent_instance_id}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def _supervisor_url(self) -> str:
        if not self.configured or self.settings.computer_supervisor_url is None:
            raise ComputerBackendError("computer_backend_not_configured")
        return self.settings.computer_supervisor_url

    async def _request(
        self,
        path: str,
        *,
        body: dict | None = None,
    ) -> object:
        if self.settings.computer_supervisor_token is None:
            raise ComputerBackendError("computer_backend_not_configured")
        token = self.settings.computer_supervisor_token.get_secret_value()
        timeout = httpx.Timeout(self.settings.computer_request_timeout_seconds)
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                follow_redirects=False,
                timeout=timeout,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET" if body is None else "POST",
                    f"{self._supervisor_url()}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ComputerBackendError(
                            f"computer_supervisor_http_{response.status_code}"
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_RESPONSE_BYTES:
                            raise ComputerBackendError("computer_response_too_large")
                        chunks.append(chunk)
        except ComputerBackendError:
            raise
        except (httpx.HTTPError, ValueError):
            raise ComputerBackendError("computer_supervisor_unavailable") from None

        raw = b"".join(chunks)
        if not raw:
            raise ComputerBackendError("computer_response_empty")
        text = raw.decode("utf-8", errors="replace")
        for secret in (
            token,
            self.settings.computer_token.get_secret_value()
            if self.settings.computer_token is not None
            else None,
        ):
            if secret:
                text = text.replace(secret, "[redacted]")
        try:
            return json.loads(text)
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None

    def _validate_state_identity(self, state: ComputerState) -> ComputerState:
        agent_id = self._require_agent_id(state.agent_id)
        expected = f"{self.settings.computer_namespace}-computer-{agent_id}"
        if state.container != expected:
            raise ComputerBackendError("computer_identity_mismatch")
        return state

    async def _computer_request(
        self,
        agent_instance_id: str,
        state: ComputerState,
        path: str,
        *,
        body: dict | None = None,
    ) -> object:
        agent_instance_id = self._require_agent_id(agent_instance_id)
        endpoint = self.endpoint(agent_instance_id, state)
        token = self.derive_agent_token(agent_instance_id)
        timeout = httpx.Timeout(self.settings.computer_request_timeout_seconds)
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                follow_redirects=False,
                timeout=timeout,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET" if body is None else "POST",
                    f"{endpoint}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-openbot-bot-id": agent_instance_id,
                        "Content-Type": "application/json",
                    },
                    json=body,
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ComputerBackendError(f"computer_http_{response.status_code}")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_RESPONSE_BYTES:
                            raise ComputerBackendError("computer_response_too_large")
                        chunks.append(chunk)
        except ComputerBackendError:
            raise
        except (httpx.HTTPError, ValueError):
            raise ComputerBackendError("computer_unavailable") from None

        raw = b"".join(chunks)
        if not raw:
            raise ComputerBackendError("computer_response_empty")
        text = raw.decode("utf-8", errors="replace")
        for secret in (
            token,
            self.settings.computer_token.get_secret_value()
            if self.settings.computer_token is not None
            else None,
            self.settings.computer_supervisor_token.get_secret_value()
            if self.settings.computer_supervisor_token is not None
            else None,
        ):
            if secret:
                text = text.replace(secret, "[redacted]")
        try:
            return json.loads(text)
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None

    async def list(self) -> tuple[ComputerState, ...]:
        try:
            payload = ComputerListing.model_validate(await self._request("/computers"))
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None
        return tuple(self._validate_state_identity(item) for item in payload.computers)

    async def existing(self, agent_instance_id: str) -> ComputerState | None:
        agent_instance_id = self._require_agent_id(agent_instance_id)
        return next(
            (item for item in await self.list() if item.agent_id == agent_instance_id),
            None,
        )

    def endpoint(self, agent_instance_id: str, state: ComputerState) -> str:
        agent_instance_id = self._require_agent_id(agent_instance_id)
        self._validate_state_identity(state)
        if state.agent_id != agent_instance_id:
            raise ComputerBackendError("computer_identity_mismatch")

        candidate = state.url or (
            f"http://127.0.0.1:{state.port}" if state.port is not None else ""
        )
        parsed = urlparse(candidate)
        if (
            parsed.scheme != "http"
            or parsed.hostname is None
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ComputerBackendError("computer_endpoint_invalid")

        cidr = self.settings.computer_internal_network_cidr
        proxy_host = self.settings.computer_egress_proxy_host
        if cidr is not None or proxy_host is not None:
            if cidr is None or proxy_host is None or parsed.port != 4100:
                raise ComputerBackendError("computer_endpoint_invalid")
            try:
                address = ipaddress.ip_address(parsed.hostname)
                network = ipaddress.ip_network(cidr, strict=True)
            except ValueError:
                raise ComputerBackendError("computer_endpoint_invalid") from None
            if (
                address.version != 4
                or str(address) != parsed.hostname
                or address not in network
                or address in {network.network_address, network.broadcast_address}
                or str(address) == proxy_host
            ):
                raise ComputerBackendError("computer_endpoint_invalid")
            return f"http://{address}:4100"

        if parsed.hostname != "127.0.0.1" or state.port is None or parsed.port != state.port:
            raise ComputerBackendError("computer_endpoint_invalid")
        return f"http://127.0.0.1:{state.port}"

    async def ensure(self, agent_instance_id: str) -> ComputerState:
        agent_instance_id = self._require_agent_id(agent_instance_id)
        try:
            state = ComputerState.model_validate(
                await self._request(f"/computers/{agent_instance_id}/ensure", body={})
            )
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None
        self.endpoint(agent_instance_id, state)
        return state

    async def stop(self, agent_instance_id: str) -> None:
        agent_instance_id = self._require_agent_id(agent_instance_id)
        await self._request(f"/computers/{agent_instance_id}/stop", body={})

    async def read_action(
        self,
        agent_instance_id: str,
        action: ComputerReadAction,
        *,
        path: str = "",
    ) -> object:
        state = await self.existing(agent_instance_id)
        if state is None or state.status != "running":
            raise ComputerBackendError("computer_not_running")
        if action == "browser_snapshot":
            return await self._computer_request(
                agent_instance_id,
                state,
                "/snapshot",
                body={},
            )
        if action == "browser_read":
            return await self._computer_request(agent_instance_id, state, "/read")
        if action == "files_list":
            return await self._computer_request(
                agent_instance_id,
                state,
                "/files/list",
                body={"path": path},
            )
        if action == "files_read":
            return await self._computer_request(
                agent_instance_id,
                state,
                "/files/read",
                body={"path": path},
            )
        raise ComputerBackendError("computer_action_unsupported")

    async def navigate_action(
        self,
        agent_instance_id: str,
        url: str,
    ) -> object:
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 2048
            or url.strip() != url
            or not url.startswith("https://")
            or any(character in url for character in "\r\n\x00")
        ):
            raise ComputerBackendError("computer_navigation_url_invalid")
        try:
            parsed = urlparse(url)
            port = parsed.port or 443
        except ValueError:
            raise ComputerBackendError("computer_navigation_url_invalid") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or not parsed.hostname.isascii()
            or parsed.netloc != parsed.netloc.lower()
            or parsed.username is not None
            or parsed.password is not None
            or not 1 <= port <= 65535
        ):
            raise ComputerBackendError("computer_navigation_url_invalid")
        state = await self.existing(agent_instance_id)
        if state is None or state.status != "running":
            raise ComputerBackendError("computer_not_running")
        return await self._computer_request(
            agent_instance_id,
            state,
            "/navigate",
            body={"url": url},
        )

    async def control(self, agent_instance_id: str) -> ComputerControlState:
        state = await self.existing(agent_instance_id)
        if state is None or state.status != "running":
            raise ComputerBackendError("computer_not_running")
        try:
            return ComputerControlState.model_validate(
                await self._computer_request(agent_instance_id, state, "/control")
            )
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None

    async def take(self, agent_instance_id: str) -> ComputerControlState:
        state = await self.existing(agent_instance_id)
        if state is None or state.status != "running":
            raise ComputerBackendError("computer_not_running")
        current = await self.control(agent_instance_id)
        if current.holder == "human":
            return current
        request = current.request
        if request is None or request.status not in {"waiting", "taken"}:
            try:
                current = ComputerControlState.model_validate(
                    await self._computer_request(
                        agent_instance_id,
                        state,
                        "/control/request",
                        body={"reason": "Owner requested control."},
                    )
                )
            except ValueError:
                raise ComputerBackendError("computer_response_invalid") from None
            request = current.request
        if request is None:
            raise ComputerBackendError("computer_control_request_missing")
        try:
            return ComputerControlState.model_validate(
                await self._computer_request(
                    agent_instance_id,
                    state,
                    "/control/take",
                    body={"requestId": request.id},
                )
            )
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None

    async def release(self, agent_instance_id: str) -> ComputerControlState:
        state = await self.existing(agent_instance_id)
        if state is None or state.status != "running":
            raise ComputerBackendError("computer_not_running")
        current = await self.control(agent_instance_id)
        if current.holder == "bot":
            return current
        request = current.request
        if request is None:
            raise ComputerBackendError("computer_control_request_missing")
        try:
            return ComputerControlState.model_validate(
                await self._computer_request(
                    agent_instance_id,
                    state,
                    "/control/release",
                    body={"requestId": request.id},
                )
            )
        except ValueError:
            raise ComputerBackendError("computer_response_invalid") from None


ComputerBackendKind = Literal["openbot"]
