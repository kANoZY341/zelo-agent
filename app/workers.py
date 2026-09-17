"""Only trusted deterministic workers. No command, network, or filesystem tools."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.gateway import ModelGatewayError
from app.schemas import (
    AgentRunResult,
    AIPromptResult,
    ApprovalDemoResult,
    EchoResult,
    TextPayload,
    TextStatsResult,
)
from app.tasks import Execution

Worker = Callable[[Execution], Awaitable[dict[str, Any]]]
RESULT_MODELS = {
    "echo": EchoResult,
    "text_stats": TextStatsResult,
    "approval_demo": ApprovalDemoResult,
    "ai_prompt": AIPromptResult,
    "agent_run": AgentRunResult,
}


async def execute(execution: Execution) -> dict[str, Any]:
    # Cooperative scheduling/cancellation boundary; input size is bounded by the API.
    await asyncio.sleep(0)
    text = TextPayload.model_validate(execution.payload).text
    match execution.kind:
        case "echo":
            return EchoResult(text=text).model_dump(mode="json")
        case "text_stats":
            return TextStatsResult(
                characters=len(text), words=len(text.split()), lines=len(text.splitlines())
            ).model_dump(mode="json")
        case "approval_demo":
            return ApprovalDemoResult(text=text).model_dump(mode="json")
        case "ai_prompt":
            raise ModelGatewayError("model_gateway_not_configured")
        case _:
            raise ValueError("Unsupported worker type")
