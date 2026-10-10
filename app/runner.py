"""One capacity-aware runner; SQLite is the bounded waiting list, not a replay engine."""

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from app.agent import AgentRuntime, AgentRuntimeError
from app.api_worker import ApiRuntime, ApiRuntimeError
from app.browser_worker import BrowserRuntime, BrowserRuntimeError
from app.computer_worker import ComputerRuntime, ComputerRuntimeError
from app.developer import DeveloperRuntime, DeveloperRuntimeError
from app.gateway import ModelGateway, ModelGatewayError
from app.models import utcnow
from app.policy import DomainError, State
from app.promotion import PromotionRuntime
from app.promotion_merge import PromotionMergeRuntime
from app.promotion_release import PromotionReleaseRuntime
from app.python_worker import PythonRuntime, PythonRuntimeError
from app.research import ResearchRuntime, ResearchRuntimeError
from app.schemas import (
    ComputerFilesReadResult,
    ComputerRunResult,
    SkillLearningProposalPayload,
    SkillLearningProposalRunResult,
)
from app.self_improvement import SelfImprovementError
from app.self_improvement_runtime import (
    SelfImprovementPreparation,
    SelfImprovementRuntime,
    developer_instruction,
)
from app.shell_worker import ShellRuntime, ShellRuntimeError
from app.space_file_gateway import SpaceFileReadGateway
from app.tasks import Execution, TaskService
from app.tools import ToolRuntimeError
from app.worker_registry import Supervisor
from app.workers import RESULT_MODELS, Worker, execute

logger = logging.getLogger("zelo")


class SkillLearningTaskError(Exception):
    """Stable denial codes for Core-owned proposal tasks."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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
        promotion_release_runtime: PromotionReleaseRuntime | None = None,
        self_improvement_runtime: SelfImprovementRuntime | None = None,
        research_supervisor: Supervisor[ResearchRuntime] | None = None,
        python_supervisor: Supervisor[PythonRuntime] | None = None,
        api_supervisor: Supervisor[ApiRuntime] | None = None,
        browser_supervisor: Supervisor[BrowserRuntime] | None = None,
        shell_supervisor: Supervisor[ShellRuntime] | None = None,
        computer_supervisor: Supervisor[ComputerRuntime] | None = None,
        space_file_gateway: SpaceFileReadGateway | None = None,
    ) -> None:
        self.service = service
        self.worker = worker
        self.gateway = gateway
        self.agent_runtime = agent_runtime
        self.research_supervisor = research_supervisor
        self.python_supervisor = python_supervisor
        self.api_supervisor = api_supervisor
        self.browser_supervisor = browser_supervisor
        self.shell_supervisor = shell_supervisor
        self.computer_supervisor = computer_supervisor
        self.space_file_gateway = space_file_gateway
        self.self_improvement_runtime = self_improvement_runtime
        self.developer_runtime = developer_runtime
        self.promotion_runtime = promotion_runtime
        self.promotion_merge_runtime = promotion_merge_runtime
        self.promotion_release_runtime = promotion_release_runtime
        self.stopping = False
        self.failed = False
        self.wake = asyncio.Event()
        self.loop_task: asyncio.Task[None] | None = None
        self.operations: dict[str, asyncio.Task[None]] = {}

    def _cancellation_outcome(self) -> tuple[State, str]:
        if self.failed:
            return State.INTERRUPTED, "runner_failed"
        if self.stopping:
            return State.INTERRUPTED, "service_shutdown"
        return State.CANCELLED, "cancelled_by_operator"

    def _finalize_unstarted_operation(
        self, execution: Execution, operation: asyncio.Task[None]
    ) -> None:
        if not operation.cancelled():
            return
        current = self.operations.get(execution.task_id)
        if current is not operation:
            return
        outcome, reason = self._cancellation_outcome()
        try:
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
        finally:
            logger.info("attempt_finished", extra={"task_id": execution.task_id})
            self.operations.pop(execution.task_id, None)
            self.wake.set()

    def start(self) -> None:
        self.loop_task = asyncio.create_task(self._loop(), name="zelo-runner")

    @property
    def ready(self) -> bool:
        return (
            self.loop_task is not None
            and not self.loop_task.done()
            and not self.stopping
            and not self.failed
        )

    def cancel(self, task_id: str) -> None:
        operation = self.operations.get(task_id)
        if operation is not None:
            operation.cancel()
        self.wake.set()

    async def stop(self) -> None:
        self.stopping = True
        for operation in tuple(self.operations.values()):
            operation.cancel()
        self.wake.set()
        if self.loop_task:
            await self.loop_task
        remaining = tuple(self.operations.values())
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)

    async def _loop(self) -> None:
        try:
            while not self.stopping:
                self.wake.clear()
                self.service.expire_approvals()
                while not self.stopping:
                    if self.browser_supervisor is None and self.shell_supervisor is None:
                        execution = self.service.claim()
                    else:
                        # Claim hashes the browser bundle; its DB session stays in
                        # this thread. Stop waits for the claim before finalizing it.
                        claim = asyncio.create_task(asyncio.to_thread(self.service.claim))
                        try:
                            execution = await asyncio.shield(claim)
                        except asyncio.CancelledError:
                            # A thread cannot be cancelled. Account for any claim
                            # it commits before letting this loop terminate.
                            execution = await claim
                            if execution is not None:
                                self.service.finish(
                                    execution, State.INTERRUPTED, error="service_shutdown"
                                )
                            raise
                    if execution is None:
                        break
                    if self.stopping:
                        self.service.finish(execution, State.INTERRUPTED, error="service_shutdown")
                        break
                    if (
                        self.browser_supervisor is not None or self.shell_supervisor is not None
                    ) and self.service.get(execution.task_id).cancel_requested:
                        # Cancellation may arrive while the threaded claim has
                        # committed but no operation is registered yet.
                        self.service.finish(
                            execution, State.CANCELLED, error="cancelled_by_operator"
                        )
                        continue
                    if execution.task_id in self.operations:
                        raise RuntimeError("duplicate_running_task")
                    operation = asyncio.create_task(
                        self._run(execution),
                        name=f"zelo-runner-{execution.kind}-{execution.task_id}",
                    )
                    operation.add_done_callback(
                        lambda task, execution=execution: self._finalize_unstarted_operation(
                            execution, task
                        )
                    )
                    self.operations[execution.task_id] = operation
                try:
                    await asyncio.wait_for(
                        self.wake.wait(), timeout=self.service.settings.runner_poll_seconds
                    )
                except TimeoutError:
                    pass
        except Exception:
            # Fail closed; readiness becomes false. Never leave already-launched
            # operations running without the scheduler that owns them.
            logger.error("runner_failed")
            self.failed = True
            active = tuple(self.operations.values())
            for operation in active:
                operation.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)

    async def _run(self, execution: Execution) -> None:
        wake_scheduler = True
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
            operation = asyncio.create_task(self._model_worker(execution))
        elif execution.kind == "tool_call":
            operation = asyncio.create_task(self._tool_worker(execution))
        elif execution.kind == "agent_run":
            operation = asyncio.create_task(self._agent_worker(execution))
        elif execution.kind == "research_run":
            operation = asyncio.create_task(self._research_worker(execution))
        elif execution.kind == "skill_learning_proposal_run":
            operation = asyncio.create_task(self._skill_learning_proposal_worker(execution))
        elif execution.kind == "python_run":
            operation = asyncio.create_task(self._python_worker(execution))
        elif execution.kind == "shell_run":
            operation = asyncio.create_task(self._shell_worker(execution))
        elif execution.kind == "browser_run":
            operation = asyncio.create_task(self._browser_worker(execution))
        elif execution.kind == "computer_run":
            operation = asyncio.create_task(self._computer_worker(execution))
        elif execution.kind == "api_run":
            operation = asyncio.create_task(self._api_worker(execution))
        elif execution.kind == "self_improvement_run":
            operation = asyncio.create_task(self._self_improvement_worker(execution))
        elif execution.kind == "developer_run":
            operation = asyncio.create_task(self._developer_worker(execution))
        elif execution.kind == "promotion_run":
            operation = asyncio.create_task(self._promotion_worker(execution))
        elif execution.kind == "merge_run":
            operation = asyncio.create_task(self._merge_worker(execution))
        elif execution.kind == "release_run":
            operation = asyncio.create_task(self._release_worker(execution))
        else:
            operation = asyncio.create_task(self.worker(execution))
        try:
            # AgentRuntime enforces both its configured deadline and the task deadline.
            raw = (
                await operation
                if execution.kind
                in {
                    "agent_run",
                    "research_run",
                    "python_run",
                    "developer_run",
                    "browser_run",
                    "computer_run",
                    "shell_run",
                }
                else await asyncio.wait_for(operation, timeout=execution.timeout_seconds)
            )
            result = (
                raw
                if execution.kind == "tool_call"
                else RESULT_MODELS[execution.kind].model_validate(raw).model_dump(mode="json")
            )
            if execution.kind == "self_improvement_run":
                preparation = SelfImprovementPreparation.model_validate(result)
                instruction = developer_instruction(preparation)
                await asyncio.to_thread(
                    self.service.finish_self_improvement,
                    execution,
                    preparation,
                    instruction,
                )
                return

            outcome = State.SUCCEEDED
            error = None
            if execution.kind == "developer_run" and not result["validation_passed"]:
                outcome = State.FAILED
                error = result.get("error")
            if execution.kind == "developer_run" and result["cancelled"]:
                outcome = State.INTERRUPTED if self.stopping else State.CANCELLED
                error = result.get("error")
            if execution.kind == "python_run" and result["exit_code"] != 0:
                outcome = State.FAILED
                error = "python_exit_nonzero"
            if execution.kind == "shell_run" and result["exit_code"] != 0:
                outcome = State.FAILED
                error = "shell_exit_nonzero"
            if execution.kind == "api_run" and not 200 <= result["status_code"] < 300:
                outcome = State.FAILED
                error = "api_http_status"
            self.service.finish(
                execution,
                outcome,
                result=result,
                error=error,
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
        except ResearchRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except SkillLearningTaskError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except PythonRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except ShellRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except BrowserRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except ComputerRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except ApiRuntimeError as error:
            self.service.finish(execution, State.FAILED, error=error.code)
        except DomainError as error:
            if execution.kind != "self_improvement_run":
                raise
            self.service.finish(execution, State.FAILED, error=error.message[:256])
        except SelfImprovementError as error:
            self.service.finish(execution, State.FAILED, error=error.args[0])
        except DeveloperRuntimeError as error:
            if error.args and error.args[0] == "developer_worker_capacity_exhausted":
                self.service.defer(execution, error=error.args[0])
                wake_scheduler = False
            else:
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
            outcome, reason = self._cancellation_outcome()
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
            self.operations.pop(execution.task_id, None)
            if wake_scheduler:
                self.wake.set()

    async def _skill_learning_proposal_worker(self, execution: Execution) -> dict[str, Any]:
        binding = execution.agent_execution
        if (
            binding is None
            or binding.spec.skill_learning != "propose"
            or binding.delegation is not None
        ):
            raise SkillLearningTaskError("skill_learning_agent_forbidden")
        store = self.service.skill_learning_store
        if store is None:
            raise SkillLearningTaskError("skill_learning_store_unavailable")
        try:
            payload = SkillLearningProposalPayload.model_validate(execution.payload)
        except ValidationError:
            raise SkillLearningTaskError("skill_learning_payload_invalid") from None
        try:
            stored, created = store.submit(
                agent_instance_id=binding.agent_instance_id,
                revision=payload.revision,
                evidence_refs=payload.evidence_refs,
            )
        except ValueError as error:
            code = error.args[0] if error.args else None
            if (
                not isinstance(code, str)
                or not code.startswith("skill_learning_")
                or not code.replace("_", "").isalnum()
            ):
                code = "skill_learning_proposal_denied"
            raise SkillLearningTaskError(code) from None
        return SkillLearningProposalRunResult(
            proposal_id=stored.proposal.proposal_id,
            proposal_fingerprint=stored.proposal.proposal_fingerprint,
            status=stored.status,
            created=created,
        ).model_dump(mode="json")

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

    async def _research_worker(self, execution: Execution) -> dict[str, Any]:
        if execution.research_selection is None:
            raise ResearchRuntimeError("research_worker_selection_unavailable")
        if self.research_supervisor is None:
            raise ResearchRuntimeError("research_worker_registry_unavailable")
        selected = self.research_supervisor.registry.get(
            execution.research_selection.descriptor.worker_id
        )
        if selected is None or not self.research_supervisor.matches(execution.research_selection):
            raise ResearchRuntimeError("research_worker_selection_stale")
        runtime = selected.worker

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await runtime.run(
            query=execution.payload["query"],
            selected_alias=execution.selected_alias or "",
            selected_model=execution.selected_model or "",
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
        )

    async def _python_worker(self, execution: Execution) -> dict[str, Any]:
        if execution.python_selection is None:
            raise PythonRuntimeError("python_worker_selection_unavailable")
        if self.python_supervisor is None:
            raise PythonRuntimeError("python_worker_registry_unavailable")
        selected = self.python_supervisor.registry.get(
            execution.python_selection.descriptor.worker_id
        )
        if selected is None or not self.python_supervisor.matches(execution.python_selection):
            raise PythonRuntimeError("python_worker_selection_stale")
        runtime = selected.worker

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await runtime.run(
            code=execution.payload["code"],
            stdin=execution.payload.get("stdin", ""),
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
        )

    async def _api_worker(self, execution: Execution) -> dict[str, Any]:
        if execution.api_request is None:
            raise ApiRuntimeError("api_execution_spec_unavailable")
        if self.api_supervisor is None:
            raise ApiRuntimeError("api_worker_registry_unavailable")
        selection = execution.api_request.worker_selection
        selected = self.api_supervisor.registry.get(selection.descriptor.worker_id)
        if (
            selected is None
            or not self.api_supervisor.matches(selection)
            or not selected.worker.matches(execution.api_request)
        ):
            raise ApiRuntimeError("api_execution_spec_stale")

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await selected.worker.run(
            spec=execution.api_request,
            path=execution.payload["path"],
            query=execution.payload.get("query", {}),
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
        )

    async def _browser_worker(self, execution: Execution) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + execution.timeout_seconds
        if execution.browser_request is None:
            raise BrowserRuntimeError("browser_execution_spec_unavailable")
        if self.browser_supervisor is None:
            raise BrowserRuntimeError("browser_worker_registry_unavailable")
        selection = execution.browser_request.worker_selection
        selected = self.browser_supervisor.registry.get(selection.descriptor.worker_id)
        if (
            selected is None
            or not self.browser_supervisor.matches(selection)
            or not selected.worker.request_matches(
                execution.browser_request, execution.payload, execution.timeout_seconds
            )
        ):
            raise BrowserRuntimeError("browser_execution_spec_stale")
        try:
            async with asyncio.timeout_at(deadline):
                if not await asyncio.to_thread(selected.worker.matches, execution.browser_request):
                    raise BrowserRuntimeError("browser_execution_spec_stale")
        except TimeoutError:
            raise BrowserRuntimeError("browser_timeout") from None

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await selected.worker.run(
            spec=execution.browser_request,
            path=execution.payload["path"],
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
            deadline=deadline,
        )

    async def _computer_worker(self, execution: Execution) -> dict[str, Any]:
        if execution.computer_request is None:
            raise ComputerRuntimeError("computer_execution_spec_unavailable")
        if self.computer_supervisor is None:
            raise ComputerRuntimeError("computer_worker_registry_unavailable")
        selection = execution.computer_request.worker_selection
        selected = self.computer_supervisor.registry.get(selection.descriptor.worker_id)
        if (
            selected is None
            or not self.computer_supervisor.matches(selection)
            or not selected.worker.request_matches(
                execution.computer_request,
                execution.payload,
                execution.timeout_seconds,
            )
        ):
            raise ComputerRuntimeError("computer_execution_spec_stale")

        space_activation_id = execution.computer_request.space_activation_id
        if space_activation_id is not None:
            if (
                self.space_file_gateway is None
                or execution.payload.get("space_activation_id") != space_activation_id
                or execution.payload.get("action") != "files_read"
            ):
                raise ComputerRuntimeError("computer_space_gateway_unavailable")
            try:
                file = await self.space_file_gateway.read(
                    execution=execution,
                    activation_id=space_activation_id,
                    relative_path=execution.payload.get("path", ""),
                )
            except DomainError:
                # Only a stable, bounded denial is recorded on Task history.
                # Raw source path and local filesystem errors remain private.
                raise ComputerRuntimeError("computer_space_read_denied") from None
            bounded_text = file.text[:64000]
            return ComputerRunResult(
                action="files_read",
                agent_instance_id=file.agent_instance_id,
                files_read=ComputerFilesReadResult(
                    path=file.relative_path,
                    text=bounded_text,
                    truncated=len(bounded_text) != len(file.text),
                    bytes=file.bytes_read,
                ),
                completed_at=utcnow(),
            ).model_dump(mode="json")

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(
                execution.task_id,
                event,
                execution.attempt_id,
                **details,
            )

        return await selected.worker.run(
            spec=execution.computer_request,
            action=execution.payload["action"],
            path=execution.payload.get("path", ""),
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
            endpoint_id=execution.payload.get("endpoint_id"),
        )

    async def _shell_worker(self, execution: Execution) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + execution.timeout_seconds
        if execution.shell_request is None:
            raise ShellRuntimeError("shell_execution_spec_unavailable")
        if self.shell_supervisor is None:
            raise ShellRuntimeError("shell_worker_registry_unavailable")
        selection = execution.shell_request.worker_selection
        selected = self.shell_supervisor.registry.get(selection.descriptor.worker_id)
        if (
            selected is None
            or not self.shell_supervisor.matches(selection)
            or not selected.worker.request_matches(
                execution.shell_request, execution.payload, execution.timeout_seconds
            )
        ):
            raise ShellRuntimeError("shell_execution_spec_stale")

        def emit(event: str, details: dict[str, Any]) -> None:
            self.service.model_event(execution.task_id, event, execution.attempt_id, **details)

        return await selected.worker.run(
            spec=execution.shell_request,
            script=execution.payload["script"],
            timeout_seconds=execution.timeout_seconds,
            emit=emit,
            deadline=deadline,
        )

    async def _self_improvement_worker(self, execution: Execution) -> dict[str, Any]:
        if self.self_improvement_runtime is None:
            raise SelfImprovementError("self_improvement_runtime_unavailable")
        self.service.model_event(
            execution.task_id,
            "self_improvement_inspection_started",
            execution.attempt_id,
        )
        result = await self.self_improvement_runtime.prepare(
            execution.payload["goal"],
            selected_alias=execution.selected_alias,
            selected_model=execution.selected_model,
        )
        self.service.model_event(
            execution.task_id,
            "self_improvement_inspection_completed",
            execution.attempt_id,
            base_sha=result.base_sha,
            inspection_fingerprint=result.inspection_fingerprint,
            tracked_path_count=result.tracked_path_count,
            inspected_content_file_count=result.inspected_content_file_count,
            content_truncated=result.content_truncated,
        )
        return result.model_dump(mode="json")

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
            "merge_execution_started",
            execution.attempt_id,
            pr_number=execution.merge_request.publication.pr_number,
            head_sha=execution.merge_request.publication.pr_head_sha,
        )
        result = await self.promotion_merge_runtime.run(execution.merge_request)
        return result.model_dump(mode="json")

    async def _release_worker(self, execution: Execution) -> dict[str, Any]:
        if self.promotion_release_runtime is None or execution.release_request is None:
            raise DeveloperRuntimeError("promotion_release_runtime_unavailable")

        self.service.model_event(
            execution.task_id,
            "release_execution_started",
            execution.attempt_id,
            tag=execution.release_request.tag,
            main_sha=execution.release_request.main_sha,
        )
        result = await self.promotion_release_runtime.run(execution.release_request)
        return result.model_dump(mode="json")
