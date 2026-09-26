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
    str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")
]
TurnLimit = Annotated[int, Query(alias="turn_limit", ge=1, le=100)]
TurnOffset = Annotated[int, Query(alias="turn_offset", ge=0, le=1_000_000)]


@router.post("", response_model=ConversationResponse, status_code=201)
def create_conversation(body: ConversationCreate, mind: Mind) -> ConversationResponse:
    return mind.create(body.title)


@router.get("", response_model=ConversationPage)
def list_conversations(mind: Mind, limit: Limit = 20, offset: Offset = 0) -> ConversationPage:
    items, total = mind.list_conversations(limit, offset)
    return ConversationPage(items=items, limit=limit, offset=offset, total=total)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    mind: Mind,
    turn_limit: TurnLimit = 50,
    turn_offset: TurnOffset = 0,
) -> ConversationResponse:
    return mind.get(
        conversation_id,
        turn_limit=turn_limit,
        turn_offset=turn_offset,
    )


@router.post("/{conversation_id}/turns", response_model=ConversationTurnResponse, status_code=201)
async def send_turn(
    conversation_id: str,
    body: ConversationTurnCreate,
    mind: Mind,
    idempotency_key: IdempotencyKey,
) -> ConversationTurnResponse:
    if idempotency_key.startswith("schedule:"):
        from app.policy import DomainError

        raise DomainError("Reserved conversation idempotency namespace", 422)
    return await mind.send_turn(conversation_id, body.text, idempotency_key)
