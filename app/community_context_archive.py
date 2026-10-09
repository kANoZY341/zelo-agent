"""Operator-invoked durable windows for untrusted text; no automatic Mind admission.

This is independent Zelo code informed by Context Mode concepts, with no
Elastic-2.0 source copied or executed. A stored archive is data, never authority.
"""

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.community_context_window import (
    CommunityContextRefusal,
    index_tool_output,
    read_explicit_window,
)
from app.models import CommunityContextArchiveRecord, Project, new_id
from app.storage import SessionStore

ARCHIVE_MAX_ITEMS = 128
ARCHIVE_MAX_TOTAL_BYTES = 16 * 1024 * 1024
EXPLICIT_RECALL_WINDOW_BYTES = (
    4096  # independent retrieval cap; no automatic ContextResolver admission
)
_SOURCE_REF = re.compile(r"[a-z][a-z0-9_.:/-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
ScopeKind = Literal["operator", "project"]


class ContextArchiveRefusal(ValueError):
    def __init__(self, reason: str, status: int = 422):
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True, slots=True)
class ContextArchiveReceipt:
    archive_id: str
    scope_kind: ScopeKind
    scope_id: str | None
    source_ref: str
    source_sha256: str
    total_bytes: int
    line_count: int
    excerpt: str
    excerpt_is_partial: bool
    provenance: Literal["operator_submitted_untrusted_text"] = "operator_submitted_untrusted_text"
    instruction_trust: Literal["none"] = "none"
    auto_context_admission: Literal[False] = False
    agent_or_worker_authority: Literal[False] = False


@dataclass(frozen=True, slots=True)
class ContextArchiveWindow:
    archive_id: str
    source_ref: str
    source_sha256: str
    start_line: int
    line_count: int
    content: str
    provenance: Literal["operator_submitted_untrusted_text"] = "operator_submitted_untrusted_text"
    instruction_trust: Literal["none"] = "none"
    auto_context_admission: Literal[False] = False
    agent_or_worker_authority: Literal[False] = False


def _scope(session: Session, kind: str, scope_id: str | None) -> str:
    if kind == "operator":
        if scope_id is not None:
            raise ContextArchiveRefusal("context_archive_scope_invalid")
        return "operator"
    if kind != "project" or type(scope_id) is not str or not _ID.fullmatch(scope_id):
        raise ContextArchiveRefusal("context_archive_scope_invalid")
    if session.get(Project, scope_id) is None:
        raise ContextArchiveRefusal("context_archive_scope_not_found", 404)
    return f"project:{scope_id}"


def _record(session: Session, archive_id: str, scope_key: str) -> CommunityContextArchiveRecord:
    if type(archive_id) is not str or not _ID.fullmatch(archive_id):
        raise ContextArchiveRefusal("context_archive_not_found", 404)
    record = session.get(CommunityContextArchiveRecord, archive_id)
    if record is None or record.scope_key != scope_key:
        raise ContextArchiveRefusal("context_archive_not_found", 404)
    return record


def _receipt(record: CommunityContextArchiveRecord) -> ContextArchiveReceipt:
    return ContextArchiveReceipt(
        archive_id=record.id,
        scope_kind=record.scope_kind,
        scope_id=record.scope_id,
        source_ref=record.source_ref,
        source_sha256=record.source_sha256,
        total_bytes=record.total_bytes,
        line_count=record.line_count,
        excerpt=record.excerpt,
        excerpt_is_partial=record.excerpt_is_partial,
    )


def _verified_bytes(record: CommunityContextArchiveRecord) -> bytes:
    raw = record.raw_data
    try:
        actual = index_tool_output(raw)
    except CommunityContextRefusal:
        raise ContextArchiveRefusal("context_archive_corrupt", 503) from None
    if (
        actual.source_sha256 != record.source_sha256
        or actual.total_bytes != record.total_bytes
        or actual.line_count != record.line_count
        or actual.excerpt != record.excerpt
        or actual.excerpt_is_partial != record.excerpt_is_partial
    ):
        raise ContextArchiveRefusal("context_archive_corrupt", 503)
    return raw


class CommunityContextArchive:
    """Operator-only archive. No agent-facing binding, background ingestion or tool grant."""

    def __init__(self, store: SessionStore):
        self.store = store

    def create(
        self, *, scope_kind: ScopeKind, scope_id: str | None, source_ref: str, text: str
    ) -> ContextArchiveReceipt:
        if type(source_ref) is not str or not _SOURCE_REF.fullmatch(source_ref):
            raise ContextArchiveRefusal("context_archive_source_invalid")
        if type(text) is not str:
            raise ContextArchiveRefusal("context_archive_text_invalid")
        try:
            raw = text.encode("utf-8", "strict")
            entry = index_tool_output(raw)
        except (CommunityContextRefusal, UnicodeError):
            raise ContextArchiveRefusal("context_archive_text_invalid") from None
        with self.store.transaction() as session:
            scope_key = _scope(session, scope_kind, scope_id)
            existing = session.scalar(
                select(CommunityContextArchiveRecord).where(
                    CommunityContextArchiveRecord.scope_key == scope_key,
                    CommunityContextArchiveRecord.source_ref == source_ref,
                )
            )
            if existing is not None:
                if (
                    existing.source_sha256 != entry.source_sha256
                    or _verified_bytes(existing) != raw
                ):
                    raise ContextArchiveRefusal("context_archive_source_changed", 409)
                return _receipt(existing)
            count, total = session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(CommunityContextArchiveRecord.total_bytes), 0),
                )
            ).one()
            if count >= ARCHIVE_MAX_ITEMS or total + len(raw) > ARCHIVE_MAX_TOTAL_BYTES:
                raise ContextArchiveRefusal("context_archive_quota_exceeded", 409)
            record = CommunityContextArchiveRecord(
                id=new_id(),
                scope_kind=scope_kind,
                scope_id=scope_id,
                scope_key=scope_key,
                source_ref=source_ref,
                source_sha256=entry.source_sha256,
                total_bytes=entry.total_bytes,
                line_count=entry.line_count,
                excerpt=entry.excerpt,
                excerpt_is_partial=entry.excerpt_is_partial,
                raw_data=raw,
            )
            session.add(record)
            session.flush()
            return _receipt(record)

    def window(
        self,
        *,
        archive_id: str,
        scope_kind: ScopeKind,
        scope_id: str | None,
        expected_sha256: str,
        start_line: int,
        count: int,
    ) -> ContextArchiveWindow:
        if type(expected_sha256) is not str or not _SHA256.fullmatch(expected_sha256):
            raise ContextArchiveRefusal("context_archive_digest_invalid")
        with self.store.read() as session:
            record = _record(session, archive_id, _scope(session, scope_kind, scope_id))
            if record.source_sha256 != expected_sha256:
                raise ContextArchiveRefusal("context_archive_source_changed", 409)
            raw = _verified_bytes(record)
            try:
                window = read_explicit_window(
                    raw, expected_sha256=expected_sha256, start_line=start_line, count=count
                )
            except CommunityContextRefusal:
                raise ContextArchiveRefusal("context_archive_window_invalid") from None
            if len(window.encode("utf-8")) > EXPLICIT_RECALL_WINDOW_BYTES:
                raise ContextArchiveRefusal("context_archive_context_budget_exceeded")
            return ContextArchiveWindow(
                archive_id=record.id,
                source_ref=record.source_ref,
                source_sha256=record.source_sha256,
                start_line=start_line,
                line_count=count,
                content=window,
            )

    def purge(self, *, archive_id: str, scope_kind: ScopeKind, scope_id: str | None) -> None:
        """Only an explicit operator request removes stored source bytes."""
        with self.store.transaction() as session:
            record = _record(session, archive_id, _scope(session, scope_kind, scope_id))
            session.delete(record)
