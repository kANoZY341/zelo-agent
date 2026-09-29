"""Durable immutable memory records for the v0.13 foundation."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.database import Database
from app.models import MemoryRecord, Project, utcnow
from app.policy import DomainError
from app.schemas import (
    MemoryCreate,
    MemoryPage,
    MemoryProvenance,
    MemoryResponse,
    MemoryScope,
    MemoryScopeKind,
)


class MemoryService:
    """Own typed memory persistence without automatic model-driven writes."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock

    @staticmethod
    def _validate_scope(
        session: Session,
        scope_kind: MemoryScopeKind,
        scope_id: str | None,
    ) -> None:
        if scope_kind == "personal":
            if scope_id is not None:
                raise DomainError("Personal memory scope cannot set scope_id", 422)
            return

        if scope_id is None or not scope_id.strip():
            raise DomainError(f"{scope_kind.capitalize()} memory scope requires scope_id", 422)

        if scope_kind == "project" and session.get(Project, scope_id) is None:
            raise DomainError("Project not found", 404)

    @staticmethod
    def _replacement(session: Session, memory_id: str) -> MemoryRecord | None:
        return session.scalar(select(MemoryRecord).where(MemoryRecord.replaces_id == memory_id))

    def _response(
        self,
        session: Session,
        record: MemoryRecord,
        *,
        now: datetime | None = None,
    ) -> MemoryResponse:
        current_time = now or self.clock()
        replacement = self._replacement(session, record.id)
        if replacement is not None:
            status = (
                "corrected"
                if replacement.replacement_kind == "correction"
                else "superseded"
            )
        elif record.expires_at is not None and record.expires_at <= current_time:
            status = "expired"
        else:
            status = "active"

        return MemoryResponse(
            id=record.id,
            scope=MemoryScope(kind=record.scope_kind, id=record.scope_id),
            kind=record.kind,
            content=record.content,
            provenance=MemoryProvenance(
                kind=record.provenance_kind,
                ref=record.provenance_ref,
                note=record.provenance_note,
            ),
            expires_at=record.expires_at,
            replaces_id=record.replaces_id,
            replacement_kind=record.replacement_kind,
            replaced_by_id=replacement.id if replacement is not None else None,
            status=status,
            created_at=record.created_at,
        )

    def create(self, body: MemoryCreate) -> MemoryResponse:
        content = body.content.strip()
        if not content:
            raise DomainError("Memory content cannot be blank", 422)

        provenance_note = body.provenance.note.strip()
        scope_id = body.scope.id.strip() if body.scope.id is not None else None
        now = self.clock()

        with self.database.transaction() as session:
            self._validate_scope(session, body.scope.kind, scope_id)

            if body.replaces_id is not None:
                target = session.get(MemoryRecord, body.replaces_id)
                if target is None:
                    raise DomainError("Memory to replace not found", 404)
                if target.scope_kind != body.scope.kind or target.scope_id != scope_id:
                    raise DomainError("Replacement must stay within the same memory scope", 409)
                if self._replacement(session, target.id) is not None:
                    raise DomainError("Memory has already been replaced", 409)

            record = MemoryRecord(
                scope_kind=body.scope.kind,
                scope_id=scope_id,
                kind=body.kind,
                content=content,
                provenance_kind=body.provenance.kind,
                provenance_ref=body.provenance.ref,
                provenance_note=provenance_note,
                expires_at=body.expires_at,
                replaces_id=body.replaces_id,
                replacement_kind=body.replacement_kind,
                created_at=now,
            )
            session.add(record)
            session.flush()
            return self._response(session, record, now=now)

    def get(self, memory_id: str) -> MemoryResponse:
        with self.database.read() as session:
            record = session.get(MemoryRecord, memory_id)
            if record is None:
                raise DomainError("Memory not found", 404)
            return self._response(session, record)

    def list(
        self,
        scope_kind: MemoryScopeKind,
        scope_id: str | None,
        *,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> MemoryPage:
        normalized_scope_id = scope_id.strip() if scope_id is not None else None
        now = self.clock()

        with self.database.read() as session:
            self._validate_scope(session, scope_kind, normalized_scope_id)

            conditions = [MemoryRecord.scope_kind == scope_kind]
            if normalized_scope_id is None:
                conditions.append(MemoryRecord.scope_id.is_(None))
            else:
                conditions.append(MemoryRecord.scope_id == normalized_scope_id)

            if not include_inactive:
                replacement = aliased(MemoryRecord)
                conditions.extend(
                    [
                        or_(MemoryRecord.expires_at.is_(None), MemoryRecord.expires_at > now),
                        ~exists(select(1).where(replacement.replaces_id == MemoryRecord.id)),
                    ]
                )

            total = (
                session.scalar(
                    select(func.count()).select_from(MemoryRecord).where(*conditions)
                )
                or 0
            )
            records = list(
                session.scalars(
                    select(MemoryRecord)
                    .where(*conditions)
                    .order_by(MemoryRecord.created_at.desc(), MemoryRecord.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return MemoryPage(
                items=[self._response(session, record, now=now) for record in records],
                limit=limit,
                offset=offset,
                total=total,
            )
