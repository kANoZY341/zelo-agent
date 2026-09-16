"""One cooperative runner; SQLite is the bounded waiting list, not a replay engine."""

import asyncio
import logging
from typing import Any

from app.policy import State
from app.tasks import Execution, TaskService
from app.workers import RESULT_MODELS, Worker, execute

logger = logging.getLogger("zelo")


class Runner:
    def __init__(self, service: TaskService, worker: Worker = execute) -> None:
        self.service = service
        self.worker = worker
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
        self.operation = asyncio.create_task(self.worker(execution))
        try:
            raw = await asyncio.wait_for(self.operation, timeout=execution.timeout_seconds)
            result = RESULT_MODELS[execution.kind].model_validate(raw).model_dump(mode="json")
            self.service.finish(execution, State.SUCCEEDED, result=result)
        except TimeoutError:
            self.service.finish(execution, State.FAILED, error="execution_timeout")
        except asyncio.CancelledError:
            outcome = State.INTERRUPTED if self.stopping else State.CANCELLED
            reason = "service_shutdown" if self.stopping else "cancelled_by_operator"
            self.service.finish(execution, outcome, error=reason)
        except Exception:
            self.service.finish(execution, State.FAILED, error="worker_error")
        finally:
            logger.info("attempt_finished", extra={"task_id": execution.task_id})
            self.operation = None
            self.active_id = None
