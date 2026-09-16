"""One cooperative runner; SQLite is the bounded waiting list, not a replay engine."""

import asyncio
import logging
from typing import Any

from app.gateway import ModelGateway, ModelGatewayError
from app.policy import State
from app.tasks import Execution, TaskService
from app.workers import RESULT_MODELS, Worker, execute

logger = logging.getLogger("zelo")


class Runner:
    def __init__(
        self, service: TaskService, worker: Worker = execute, gateway: ModelGateway | None = None
    ) -> None:
        self.service = service
        self.worker = worker
        self.gateway = gateway
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
        if execution.kind == "ai_prompt":
            self.operation = asyncio.create_task(self._model_worker(execution))
        else:
            self.operation = asyncio.create_task(self.worker(execution))
        try:
            raw = await asyncio.wait_for(self.operation, timeout=execution.timeout_seconds)
            result = RESULT_MODELS[execution.kind].model_validate(raw).model_dump(mode="json")
            self.service.finish(execution, State.SUCCEEDED, result=result)
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id, "model_execution_completed", execution.attempt_id
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
        except ModelGatewayError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id,
                    "model_execution_failed",
                    execution.attempt_id,
                    reason=error.code,
                )
        except asyncio.CancelledError:
            outcome = State.INTERRUPTED if self.stopping else State.CANCELLED
            reason = "service_shutdown" if self.stopping else "cancelled_by_operator"
            self.service.finish(execution, outcome, error=reason)
            if execution.kind == "ai_prompt":
                self.service.model_event(
                    execution.task_id, "model_execution_failed", execution.attempt_id, reason=reason
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
