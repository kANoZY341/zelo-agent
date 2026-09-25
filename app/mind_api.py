"""Authenticated Zelo Mind conversation routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from app.auth import require_operator
from app.mind import MindService
from app.schemas import (
    ConversationCreate,
    ConversationPage,
    ConversationResponse,
    ConversationTurnCreate,
    ConversationTurnResponse,
)

router = APIRouter(prefix="/conversations", dependencies=[Depends(require_operator)])


def service(request: Request) -> MindService:
    return request.app.state.mind


Mind = Annotated[MindService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")
]


@router.post("", response_model=ConversationResponse, status_code=201)
def create_conversation(body: ConversationCreate, mind: Mind) -> ConversationResponse:
    return mind.create(body.title)


@router.get("", response_model=ConversationPage)
def list_conversations(mind: Mind, limit: Limit = 20, offset: Offset = 0) -> ConversationPage:
    items, total = mind.list(limit, offset)
    return ConversationPage(items=items, limit=limit, offset=offset, total=total)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(conversation_id: str, mind: Mind) -> ConversationResponse:
    return mind.get(conversation_id)


@router.post("/{conversation_id}/turns", response_model=ConversationTurnResponse, status_code=201)
async def send_turn(
    conversation_id: str,
    body: ConversationTurnCreate,
    mind: Mind,
    idempotency_key: IdempotencyKey = None,
) -> ConversationTurnResponse:
    return await mind.send_turn(conversation_id, body.text, idempotency_key)
