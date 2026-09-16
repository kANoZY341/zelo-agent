"""Typed local model gateway and the only provider integration point."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx


@dataclass(frozen=True)
class ModelRequest:
    model_id: str
    system_prompt: str
    user_messages: list[str]
    timeout_seconds: float


@dataclass(frozen=True)
class ModelResult:
    text: str
    provider: str
    model_id: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_eval_duration_ns: int | None = None
    generation_duration_ns: int | None = None
    total_duration_ns: int | None = None
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResult: ...

    async def models(self) -> list[str]: ...


class ModelGatewayError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ModelRouter:
    def __init__(self, general_model: str, reasoning_model: str, config_version: str) -> None:
        self.aliases = {"local_general": general_model, "local_reasoning": reasoning_model}
        self.config_version = config_version

    def select(self, profile: str) -> tuple[str, str]:
        if profile not in {"general", "reasoning"}:
            raise ModelGatewayError("invalid_profile")
        alias = f"local_{profile}"
        return alias, self.aliases[alias]


class OllamaProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=5) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
                return [m["name"] for m in response.json().get("models", []) if "name" in m]
        except (httpx.HTTPError, ValueError, KeyError):
            raise ModelGatewayError("model_unavailable") from None

    async def generate(self, request: ModelRequest) -> ModelResult:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=request.timeout_seconds
            ) as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": request.model_id,
                        "stream": False,
                        "messages": [
                            {"role": "system", "content": request.system_prompt},
                            *({"role": "user", "content": m} for m in request.user_messages),
                        ],
                    },
                )
                response.raise_for_status()
                body = response.json()
                if body.get("error"):
                    raise ModelGatewayError("model_unavailable")
                message = body.get("message", {})
                return ModelResult(
                    text=message["content"],
                    provider="ollama",
                    model_id=request.model_id,
                    prompt_tokens=body.get("prompt_eval_count"),
                    completion_tokens=body.get("eval_count"),
                    total_tokens=(
                        (body["prompt_eval_count"] + body["eval_count"])
                        if body.get("prompt_eval_count") is not None
                        and body.get("eval_count") is not None
                        else None
                    ),
                    prompt_eval_duration_ns=body.get("prompt_eval_duration"),
                    generation_duration_ns=body.get("eval_duration"),
                    total_duration_ns=body.get("total_duration"),
                    completed_at=datetime.now(UTC),
                )
        except ModelGatewayError:
            raise
        except httpx.TimeoutException:
            raise ModelGatewayError("model_timeout") from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ModelGatewayError("model_unavailable") from None


class ModelGateway:
    def __init__(
        self, provider: ModelProvider, general_model: str, reasoning_model: str, config_version: str
    ) -> None:
        self.provider = provider
        self.router = ModelRouter(general_model, reasoning_model, config_version)

    def select(self, profile: str) -> tuple[str, str]:
        return self.router.select(profile)

    @property
    def aliases(self) -> dict[str, str]:
        return self.router.aliases

    @property
    def config_version(self) -> str:
        return self.router.config_version

    async def generate(self, profile: str, prompt: str, timeout: float) -> tuple[ModelResult, str]:
        alias, model = self.select(profile)
        return await self.provider.generate(
            ModelRequest(model, "You are a local Zelo assistant.", [prompt], timeout)
        ), alias
