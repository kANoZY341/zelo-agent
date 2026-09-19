"""One cooperative runner; SQLite is the bounded waiting list, not a replay engine."""

import asyncio
import logging
from typing import Any

from app.agent import AgentRuntime, AgentRuntimeError
from app.developer import DeveloperRuntime, DeveloperRuntimeError
from app.gateway import ModelGateway, ModelGatewayError
from app.policy import State
from app.promotion import PromotionRuntime
from app.promotion_merge import PromotionMergeRuntime
from app.tasks import Execution, TaskService
from app.tools import ToolRuntimeError
from app.workers import RESULT_MODELS, Worker, execute

logger = logging.getLogger("zelo")


class Runner:
    def __init__(
        self,
        service: TaskService,
        worker: Worker = execute,
        gateway: ModelGateway | None = None,
        agent_runtime: AgentRuntime | None = None,
        developer_runtime: DeveloperRuntime | None = None,
        promotion_runtime: PromotionRuntime | None = None,
        promotion_merge_runtime: PromotionMergeRuntime | None = None,
    ) -> None:
        self.service = service
        self.worker = worker
        self.gateway = gateway
        self.agent_runtime = agent_runtime
        self.developer_runtime = developer_runtime
        self.promotion_runtime = promotion_runtime
        self.promotion_merge_runtime = promotion_merge_runtime
        self.stopping = False
        self.wake = asyncio.Event()
        self.loop_task: asyncio.Task[None] | None = None
        self.operation: asyncio.Task[dict[str, Any]] | None = None
        self.active_id: str | None = None

    def start(self) -> None:
        self.loop_task = asyncio.create_task(self._loop(), name="zelo-runner")

    @property
    def ready(self) -> bool:
        return self.loop_task is not None and not self.loop_task.done() and not self.stopping

    def cancel(self, task_id: str) -> None:
        if self.active_id == task_id and self.operation:
            self.operation.cancel()
        self.wake.set()

    async def stop(self) -> None:
        self.stopping = True
        if self.operation:
            self.operation.cancel()
        self.wake.set()
        if self.loop_task:
            await self.loop_task

    async def _loop(self) -> None:
        try:
            while not self.stopping:
                self.wake.clear()
                self.service.expire_approvals()
                execution = self.service.claim()
                if execution:
                    await self._run(execution)
                else:
                    try:
                        await asyncio.wait_for(
                            self.wake.wait(), timeout=self.service.settings.runner_poll_seconds
                        )
                    except TimeoutError:
                        pass
        except Exception:
            # Fail closed; readiness becomes false. Never log exception payloads/private input.
            logger.error("runner_failed")

    async def _run(self, execution: Execution) -> None:
        self.active_id = execution.task_id
        if execution.kind == "ai_prompt":
            self.service.model_event(
                execution.task_id,
                "model_execution_started",
                execution.attempt_id,
                alias=execution.selected_alias,
                actual_model=execution.selected_model,
            )
        if execution.kind == "tool_call":
            self.service.model_event(
                execution.task_id,
                "tool_execution_started",
                execution.attempt_id,
                tool=execution.tool_name,
            )
        if execution.kind == "ai_prompt":
            self.operation = asyncio.create_task(self._model_worker(execution))
        elif execution.kind == "tool_call":
            self.operation = asyncio.create_task(self._tool_worker(execution))
        elif execution.kind == "agent_run":
            self.operation = asyncio.create_task(self._agent_worker(execution))
        elif execution.kind == "developer_run":
            self.operation = asyncio.create_task(self._developer_worker(execution))
        elif execution.kind == "promotion_run":
            self.operation = asyncio.create_task(self._promotion_worker(execution))
        elif execution.kind == "merge_run":
            self.operation = asyncio.create_task(self._merge_worker(execution))
        else:
            self.operation = asyncio.create_task(self.worker(execution))
        try:
            # AgentRuntime enforces both its configured deadline and the task deadline.
            raw = (
                await self.operation
                if execution.kind in {"agent_run", "developer_run"}
                else await asyncio.wait_for(self.operation, timeout=execution.timeout_seconds)
            )
            result = (
                raw
                if execution.kind == "tool_call"
                else RESULT_MODELS[execution.kind].model_validate(raw).model_dump(mode="json")
            )
            outcome = (
                State.FAILED
                if execution.kind == "developer_run" and not result["validation_passed"]
                else State.SUCCEEDED
            )
            if execution.kind == "developer_run" and result["cancelled"]:
                outcome = State.INTERRUPTED if self.stopping else State.CANCELLED
            self.service.finish(
                execution,
                outcome,
                result=result,
                error=result.get("error") if execution.kind == "developer_run" else None,
            )
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id, "model_execution_completed", execution.attempt_id
                )
            if execution.kind == "tool_call":
                self.service.model_event(
                    execution.task_id,
                    "tool_execution_completed",
                    execution.attempt_id,
                    tool=execution.tool_name,
                )
        except TimeoutError:
            error = "model_timeout" if execution.kind == "ai_prompt" else "execution_timeout"
            self.service.finish(execution, State.FAILED, error=error)
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id,
                    "model_execution_failed",
                    execution.attempt_id,
                    reason="model_timeout",
                )
            if execution.kind == "tool_call":
                self.service.model_event(
                    execution.task_id,
                    "tool_execution_failed",
                    execution.attempt_id,
                    tool=execution.tool_name,
                    reason="tool_timeout",
                )
        except AgentRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except DeveloperRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.args[0])
        except ModelGatewayError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id,
                    "model_execution_failed",
                    execution.attempt_id,
                    reason=error.code,
                )
            if execution.kind == "tool_call":
                self.service.model_event(
                    execution.task_id,
                    "tool_execution_failed",
                    execution.attempt_id,
                    tool=execution.tool_name,
                    reason=error.code,
                )
        except ToolRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.args[0])
            if execution.kind == "tool_call":
                self.service.model_event(
                    execution.task_id,
                    "tool_execution_failed",
                    execution.attempt_id,
                    tool=execution.tool_name,
                    reason=error.args[0],
                )
        except asyncio.CancelledError:
            outcome = State.INTERRUPTED if self.stopping else State.CANCELLED
            reason = "service_shutdown" if self.stopping else "cancelled_by_operator"
            self.service.finish(execution, outcome, error=reason)
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id, "model_execution_failed", execution.attempt_id, reason=reason
                )
            if execution.kind == "tool_call":
                self.service.model_event(
                    execution.task_id,
                    "tool_execution_failed",
                    execution.attempt_id,
                    tool=execution.tool_name,
                    reason=reason,
                )
        except Exception:
            self.service.finish(execution, State.FAILED, error="worker_error")
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id,
                    "model_execution_failed",
                    execution.attempt_id,
                    reason="worker_error",
                )
            if execution.kind == "tool_call":
                self.service.model_event(
                    execution.task_id,
                    "tool_execution_failed",
                    execution.attempt_id,
                    tool=execution.tool_name,
                    reason="worker_error",
                )
        finally:
            logger.info("attempt_finished", extra={"task_id": execution.task_id})
            self.operation = None
            self.active_id = None

    async def _model_worker(self, execution: Execution) -> dict[str, Any]:
        if self.gateway is None or execution.profile is None:
            raise ModelGatewayError("model_unavailable")
        result, alias = await self.gateway.generate(
            execution.profile, execution.payload["prompt"], execution.timeout_seconds
        )
        timing = {
            "prompt_eval_duration_ns": result.prompt_eval_duration_ns,
            "generation_duration_ns": result.generation_duration_ns,
            "total_duration_ns": result.total_duration_ns,
        }
        if result.generation_duration_ns and result.completion_tokens is not None:
            timing["approx_tokens_per_second"] = result.completion_tokens / (
                result.generation_duration_ns / 1_000_000_000
            )
        return {
            "text": result.text,
            "provider": result.provider,
            "alias": alias,
            "actual_model": result.model_id,
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            },
            "timing": timing,
            "completed_at": result.completed_at.isoformat(),
        }

    async def _tool_worker(self, execution: Execution) -> dict[str, Any]:
        if self.service.tool_gateway is None or execution.tool_name is None:
            raise ToolRuntimeError("tool_gateway_unavailable")
        return await asyncio.to_thread(
            self.service.tool_gateway.execute,
            execution.tool_name,
            execution.tool_arguments or {},
        )

    async def _agent_worker(self, execution: Execution) -> dict[str, Any]:
        if self.agent_runtime is None:
            raise AgentRuntimeError("agent_runtime_unavailable")

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await self.agent_runtime.run(
            prompt=execution.payload["prompt"],
            profile=execution.profile or "",
            selected_alias=execution.selected_alias or "",
            selected_model=execution.selected_model or "",
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
        )

    async def _developer_worker(self, execution: Execution) -> dict[str, Any]:
        if self.developer_runtime is None or execution.developer_request is None:
            raise DeveloperRuntimeError("developer_runtime_unavailable")

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await self.developer_runtime.run(
            execution.task_id, execution.attempt_id, execution.developer_request, emit
        )

    async def _promotion_worker(self, execution: Execution) -> dict[str, Any]:
        if (
            self.promotion_runtime is None
            or execution.promotion_request is None
            or execution.developer_request is None
        ):
            raise DeveloperRuntimeError("promotion_runtime_unavailable")

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await self.promotion_runtime.run(
            execution.task_id,
            execution.attempt_id,
            execution.promotion_request,
            execution.developer_request,
            emit,
        )


    async def _merge_worker(self, execution: Execution) -> dict[str, Any]:
        if self.promotion_merge_runtime is None or execution.merge_request is None:
            raise DeveloperRuntimeError("promotion_merge_runtime_unavailable")

        self.service.model_event(
            execution.task_id,
            "merge_reverified",
            execution.attempt_id,
            pr_number=execution.merge_request.publication.pr_number,
            head_sha=execution.merge_request.publication.pr_head_sha,
        )
        result = await self.promotion_merge_runtime.run(execution.merge_request)
        return result.model_dump(mode="json")
