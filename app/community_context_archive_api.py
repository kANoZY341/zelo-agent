"""Opt-in operator-only Context Mode-inspired storage and explicit narrow recall."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_operator
from app.community_context_archive import (
    CommunityContextArchive,
    ContextArchiveReceipt,
    ContextArchiveRefusal,
    ContextArchiveWindow,
)

router = APIRouter(
    prefix="/integration-candidates/community/context-archive",
    dependencies=[Depends(require_operator)],
)


class ArchiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope_kind: Literal["operator", "project"]
    scope_id: str | None = Field(default=None, max_length=36)
    source_ref: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=1024 * 1024)


def _archive(request: Request) -> CommunityContextArchive:
    return CommunityContextArchive(request.app.state.storage)


def _respond(error: ContextArchiveRefusal) -> None:
    raise HTTPException(status_code=error.status, detail=str(error)) from None


@router.post("", response_model=ContextArchiveReceipt, status_code=201)
def create_archive(body: ArchiveCreate, request: Request) -> ContextArchiveReceipt:
    try:
        return _archive(request).create(
            scope_kind=body.scope_kind,
            scope_id=body.scope_id,
            source_ref=body.source_ref,
            text=body.text,
        )
    except ContextArchiveRefusal as error:
        _respond(error)


@router.get("/{archive_id}/window", response_model=ContextArchiveWindow)
def read_archive_window(
    archive_id: str,
    request: Request,
    scope_kind: Literal["operator", "project"],
    expected_sha256: Annotated[str, Query(min_length=64, max_length=64)],
    start_line: Annotated[int, Query(ge=1)],
    count: Annotated[int, Query(ge=1, le=80)],
    scope_id: Annotated[str | None, Query(max_length=36)] = None,
) -> ContextArchiveWindow:
    try:
        return _archive(request).window(
            archive_id=archive_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            expected_sha256=expected_sha256,
            start_line=start_line,
            count=count,
        )
    except ContextArchiveRefusal as error:
        _respond(error)


@router.delete("/{archive_id}", status_code=204)
def purge_archive(
    archive_id: str,
    request: Request,
    scope_kind: Literal["operator", "project"],
    scope_id: Annotated[str | None, Query(max_length=36)] = None,
) -> None:
    try:
        _archive(request).purge(
            archive_id=archive_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
    except ContextArchiveRefusal as error:
        _respond(error)
