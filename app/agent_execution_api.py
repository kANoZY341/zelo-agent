"""Authenticated generic agent execution route for v0.14."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from app.agent_execution import AgentExecutionService
from app.auth import require_operator
from app.schemas import AgentTaskCreate, TaskResponse

router = APIRouter(prefix="/agents", dependencies=[Depends(require_operator)])


def service(request: Request) -> AgentExecutionService:
    return request.app.state.agent_execution


AgentExecution = Annotated[AgentExecutionService, Depends(service)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$"),
]


@router.post("/{instance_id}/tasks", response_model=TaskResponse, status_code=201)
def create_agent_task(
    instance_id: str,
    body: AgentTaskCreate,
    response: Response,
    execution: AgentExecution,
    idempotency_key: IdempotencyKey = None,
) -> TaskResponse:
    task, created = execution.submit(instance_id, body, idempotency_key)
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/tasks/{task.id}"
    return task
