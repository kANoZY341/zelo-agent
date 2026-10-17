"""Durable immutable memory records for the v0.13 foundation."""

import re
from collections.abc import Callable
from datetime import datetime
from time import monotonic

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from app.models import MemoryRecord, Project, utcnow
from app.policy import DomainError
from app.schemas import (
    MemoryCreate,
    MemoryHit,
    MemoryPage,
    MemoryProvenance,
    MemoryResponse,
    MemoryRetrievalResponse,
    MemoryRetrieveRequest,
    MemoryScope,
    MemoryScopeKind,
)
from app.storage import SessionStore

MEMORY_RETRIEVAL_CANDIDATES = 2048
MEMORY_HIT_CONTENT_CHARS = 1200
_MEMORY_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MEMORY_STOP_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "can",
        "could",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "would",
        "should",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)


def _memory_terms(value: str, *, limit: int | None = None) -> tuple[str, ...]:
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _MEMORY_TOKEN.findall(value.casefold()):
        if len(raw) < 2 or raw in _MEMORY_STOP_TERMS or raw in seen:
            continue
        seen.add(raw)
        terms.append(raw)
        if limit is not None and len(terms) >= limit:
            break
    return tuple(terms)


def _memory_snippet(content: str, matched_terms: tuple[str, ...]) -> tuple[str, bool]:
    if len(content) <= MEMORY_HIT_CONTENT_CHARS:
        return content, False

    folded = content.casefold()
    positions = [folded.find(term) for term in matched_terms]
    positions = [position for position in positions if position >= 0]
    first = min(positions) if positions else 0
    start = max(0, first - 200)
    prefix = "...[bounded] " if start > 0 else ""
    available = MEMORY_HIT_CONTENT_CHARS - len(prefix)
    body = content[start : start + available]
    if start + available < len(content):
        marker = " ...[bounded]"
        body = body[: max(0, available - len(marker))] + marker
    return prefix + body, True


class MemoryService:
    """Own typed memory persistence without automatic model-driven writes."""

    def __init__(
        self,
        database: SessionStore,
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
            status = "corrected" if replacement.replacement_kind == "correction" else "superseded"
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
                session.scalar(select(func.count()).select_from(MemoryRecord).where(*conditions))
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

    def retrieve(self, body: MemoryRetrieveRequest) -> MemoryRetrievalResponse:
        query = " ".join(body.query.split())
        if not query:
            raise DomainError("Memory retrieval query cannot be blank", 422)
        terms = _memory_terms(query, limit=32)

        now = self.clock()
        with self.database.read() as session:
            scope_conditions = []
            for scope in body.scopes:
                scope_id = scope.id.strip() if scope.id is not None else None
                self._validate_scope(session, scope.kind, scope_id)
                if scope_id is None:
                    scope_conditions.append(
                        and_(
                            MemoryRecord.scope_kind == scope.kind,
                            MemoryRecord.scope_id.is_(None),
                        )
                    )
                else:
                    scope_conditions.append(
                        and_(
                            MemoryRecord.scope_kind == scope.kind,
                            MemoryRecord.scope_id == scope_id,
                        )
                    )

            if not terms:
                return MemoryRetrievalResponse(
                    query=query,
                    items=[],
                    candidate_count=0,
                    limit=body.limit,
                )

            minimum_matches = 1 if len(terms) == 1 else 2
            # Core SQLite connections fold content once using Python's Unicode
            # normalization. Substring decoys may pass this prefilter, but a
            # matching token cannot be lost due to casing.
            content_condition = (
                func.zelo_casefold_overlap(MemoryRecord.content, minimum_matches, *terms) == 1
            )

            replacement = aliased(MemoryRecord)
            # The result cap alone does not bound a no-match SQL scan. Interrupt
            # an expensive scan before it monopolizes the Core read connection.
            # The progress handler belongs to this pooled DB-API connection,
            # so always remove it before returning that connection to the pool.
            dbapi = session.connection().connection.driver_connection
            deadline = monotonic() + 2.0
            interrupted = False

            def exceeded_budget() -> int:
                nonlocal interrupted
                interrupted = monotonic() >= deadline
                return int(interrupted)

            dbapi.set_progress_handler(exceeded_budget, 1000)
            try:
                records = list(
                    session.scalars(
                        select(MemoryRecord)
                        .where(
                            or_(*scope_conditions),
                            or_(MemoryRecord.expires_at.is_(None), MemoryRecord.expires_at > now),
                            ~exists(select(1).where(replacement.replaces_id == MemoryRecord.id)),
                            content_condition,
                        )
                        .order_by(MemoryRecord.created_at.desc(), MemoryRecord.id)
                        .limit(MEMORY_RETRIEVAL_CANDIDATES)
                    )
                )
            except OperationalError:
                if interrupted:
                    raise DomainError("Memory retrieval exceeded work budget", 503) from None
                raise
            finally:
                dbapi.set_progress_handler(None, 0)

            meaningful_phrase = " ".join(terms)
            ranked: list[tuple[int, datetime, str, MemoryRecord, tuple[str, ...]]] = []
            for record in records:
                content_terms = set(_memory_terms(record.content))
                matched = tuple(term for term in terms if term in content_terms)
                if len(matched) < minimum_matches:
                    continue
                score = (len(matched) * 100) + sum(min(len(term), 20) for term in matched)
                if meaningful_phrase and meaningful_phrase in record.content.casefold():
                    score += 500
                ranked.append((score, record.created_at, record.id, record, matched))

            ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
            items: list[MemoryHit] = []
            for score, _created_at, _id, record, matched in ranked[: body.limit]:
                snippet, truncated = _memory_snippet(record.content, matched)
                items.append(
                    MemoryHit(
                        id=record.id,
                        scope=MemoryScope(kind=record.scope_kind, id=record.scope_id),
                        kind=record.kind,
                        content=snippet,
                        content_truncated=truncated,
                        provenance=MemoryProvenance(
                            kind=record.provenance_kind,
                            ref=record.provenance_ref,
                            note=record.provenance_note,
                        ),
                        score=score,
                        matched_terms=list(matched),
                        created_at=record.created_at,
                    )
                )

            return MemoryRetrievalResponse(
                query=query,
                items=items,
                candidate_count=len(records),
                limit=body.limit,
            )
