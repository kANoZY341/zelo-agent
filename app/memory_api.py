"""Authenticated v0.13 memory foundation routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_operator
from app.memory import MemoryService
from app.schemas import MemoryCreate, MemoryPage, MemoryResponse, MemoryScopeKind

router = APIRouter(prefix="/memories", dependencies=[Depends(require_operator)])


def service(request: Request) -> MemoryService:
    return request.app.state.memories


Memories = Annotated[MemoryService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post("", response_model=MemoryResponse, status_code=201)
def create_memory(body: MemoryCreate, memories: Memories) -> MemoryResponse:
    return memories.create(body)


@router.get("", response_model=MemoryPage)
def list_memories(
    memories: Memories,
    scope_kind: MemoryScopeKind,
    scope_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    include_inactive: bool = False,
    limit: Limit = 20,
    offset: Offset = 0,
) -> MemoryPage:
    return memories.list(
        scope_kind,
        scope_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/{memory_id}", response_model=MemoryResponse)
def get_memory(memory_id: str, memories: Memories) -> MemoryResponse:
    return memories.get(memory_id)
