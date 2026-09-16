"""The single authority for task state transitions and immutable action identity."""

import hashlib
import json
from enum import StrEnum
from typing import Any


class State(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TRANSITIONS: dict[State, frozenset[State]] = {
    State.PENDING: frozenset({State.RUNNING, State.CANCELLED}),
    State.AWAITING_APPROVAL: frozenset({State.PENDING, State.CANCELLED}),
    State.RUNNING: frozenset({State.SUCCEEDED, State.FAILED, State.CANCELLED, State.INTERRUPTED}),
    State.SUCCEEDED: frozenset(),
    State.FAILED: frozenset({State.PENDING, State.AWAITING_APPROVAL}),
    State.CANCELLED: frozenset({State.PENDING, State.AWAITING_APPROVAL}),
    State.INTERRUPTED: frozenset({State.PENDING, State.AWAITING_APPROVAL}),
}


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def validate_transition(previous: str, target: State) -> None:
    if target not in TRANSITIONS[State(previous)]:
        raise DomainError(f"Cannot transition from {previous} to {target}")


def fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def action_hash(kind: str, payload: dict[str, Any], timeout_seconds: float, version: int) -> str:
    return fingerprint(
        {"kind": kind, "payload": payload, "timeout_seconds": timeout_seconds, "version": version}
    )
