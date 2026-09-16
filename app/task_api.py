"""Authenticated routes: validation and transport only, no lifecycle assignments."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.auth import require_operator
from app.schemas import (
    ApprovalDecision,
    ApprovalResponse,
    AuditResponse,
    TaskCreate,
    TaskPage,
    TaskResponse,
)
from app.tasks import TaskService

router = APIRouter(dependencies=[Depends(require_operator)])


def service(request: Request) -> TaskService:
    return request.app.state.tasks


Service = Annotated[TaskService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")
]


@router.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(
    body: TaskCreate, response: Response, tasks: Service, idempotency_key: IdempotencyKey = None
) -> TaskResponse:
    task, created = tasks.submit(body, idempotency_key)
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/tasks/{task.id}"
    return task


@router.get("/tasks", response_model=TaskPage)
def list_tasks(tasks: Service, limit: Limit = 20, offset: Offset = 0) -> TaskPage:
    return tasks.list_tasks(limit, offset)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, tasks: Service) -> TaskResponse:
    return tasks.get(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str, request: Request, response: Response, tasks: Service
) -> TaskResponse:
    task = tasks.cancel(task_id)
    request.app.state.runner.cancel(task_id)
    response.status_code = 202 if task.status == "running" else 200
    return task


@router.post("/tasks/{task_id}/retry", response_model=TaskResponse, status_code=202)
def retry_task(task_id: str, tasks: Service) -> TaskResponse:
    return tasks.retry(task_id)


@router.get("/tasks/{task_id}/events", response_model=list[AuditResponse])
def task_events(
    task_id: str, tasks: Service, limit: Limit = 100, offset: Offset = 0
) -> list[AuditResponse]:
    return tasks.events(task_id, limit, offset)


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval(approval_id: str, body: ApprovalDecision, tasks: Service) -> ApprovalResponse:
    return tasks.decide(approval_id, body.decision)


@router.get("/models")
async def models(request: Request) -> dict:
    gateway = request.app.state.tasks.gateway
    aliases = gateway.aliases if gateway else {}
    available: list[str] = []
    error = None
    if gateway:
        try:
            available = await gateway.provider.models()
        except Exception:
            error = "unavailable"
    return {
        "provider": "ollama",
        "status": "available" if error is None else "unavailable",
        "aliases": [
            {"alias": alias, "model": model, "available": model in available}
            for alias, model in aliases.items()
        ],
    }
