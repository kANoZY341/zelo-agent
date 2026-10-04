"""Fail-closed adapter for persistent per-agent OpenBot computers.

The backend is intentionally narrower than OpenDots' full ComputerService:
this slice proves configuration, per-agent credentials, supervisor identity and
persistent computer lifecycle. Agent browser/file/shell execution remains a
separate Zelo Worker/authority slice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings

_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_RESPONSE_BYTES = 4_000_000


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
            or parsed.hostname != "127.0.0.1"
            or state.port is None
            or parsed.port != state.port
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
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


ComputerBackendKind = Literal["openbot"]
