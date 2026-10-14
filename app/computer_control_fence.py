"""Durable Core human-takeover barrier for Agent Computer task authority.

An owner's request is committed before the external backend takeover begins.
Reads of the current lease state are serialized with Task/Space gates through
Core's SQLite writer; backend calls and human takeover never occur under a
long-lived network-spanning database transaction.

This protects Zelo-managed takeover, not out-of-band direct backend changes.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentComputerEvent

_CONTROL_TRANSITIONS = (
    "take_control_requested",
    "take_control",
    "release_control_requested",
    "release_control",
)


def latest_control_transition(session: Session, instance_id: str) -> AgentComputerEvent | None:
    """Return the latest durable Core takeover/handback transition."""
    return session.scalar(
        select(AgentComputerEvent)
        .where(
            AgentComputerEvent.agent_instance_id == instance_id,
            AgentComputerEvent.event.in_(_CONTROL_TRANSITIONS),
        )
        .order_by(AgentComputerEvent.sequence.desc())
        .limit(1)
    )


def human_takeover_unreleased(session: Session, instance_id: str) -> bool:
    """Deny since the last requested takeover until a successful handback.

    Include the legacy take-control completion event, and do not clear denial
    on a failed handback or a failed/ambiguous takeover. A late completed
    takeover after an earlier handback closes the gate again.
    """
    latest = latest_control_transition(session, instance_id)
    return latest is not None and not (
        latest.event == "release_control" and latest.outcome == "succeeded"
    )
