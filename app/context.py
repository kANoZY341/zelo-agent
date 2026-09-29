"""Bounded read-only context assembly for Zelo Mind."""

import json
from pathlib import Path

from app.config import ROOT
from app.projects import ProjectService

CONTEXT_MAX_CHARS = 6000
ACTIVE_STATUS_CHARS = 2200
ROADMAP_CHARS = 1700
PROJECT_RECORDS_CHARS = 1200

_CONTEXT_PHRASES = (
    "what are we building",
    "what are we working on",
    "what are you building",
    "building with zelo",
    "current project",
    "current milestone",
    "our roadmap",
    "zelo roadmap",
    "zelo architecture",
    "zelo project status",
    "where are we with zelo",
    "what should you become",
    "what do you think you should become",
)
_ZELO_PROJECT_TERMS = (
    "project",
    "roadmap",
    "architecture",
    "milestone",
    "goal",
    "vision",
)


def _bounded(value: str, limit: int) -> str:
    value = value.strip()
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    marker = "\n...[bounded]"
    if limit <= len(marker):
        return value[:limit]
    return value[: limit - len(marker)] + marker


def _markdown_section(text: str, heading_prefix: str) -> str:
    lines = text.splitlines()
    start = None
    level = None
    for index, line in enumerate(lines):
        if line.startswith(heading_prefix):
            start = index
            level = len(line) - len(line.lstrip("#"))
            break
    if start is None or level is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.startswith("#"):
            continue
        next_level = len(line) - len(line.lstrip("#"))
        if next_level <= level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _serialize(envelope: dict) -> str:
    return json.dumps(
        envelope,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _fit_serialized_budget(envelope: dict) -> str:
    serialized = _serialize(envelope)
    while len(serialized) > CONTEXT_MAX_CHARS:
        sources = envelope["sources"]
        candidates = [source for source in sources if source["content"]]
        if not candidates:
            raise RuntimeError("Context envelope metadata exceeds hard budget")
        source = max(candidates, key=lambda item: len(item["content"]))
        excess = len(serialized) - CONTEXT_MAX_CHARS
        target = max(0, len(source["content"]) - max(excess + 64, 128))
        source["content"] = _bounded(source["content"], target)
        serialized = _serialize(envelope)
    return serialized


class ContextResolver:
    """Assemble factual context without creating or mutating memory."""

    def __init__(
        self,
        projects: ProjectService,
        *,
        root: Path = ROOT,
    ) -> None:
        self.projects = projects
        self.root = root

    @staticmethod
    def relevant(text: str) -> bool:
        normalized = " ".join(text.casefold().split())
        if any(phrase in normalized for phrase in _CONTEXT_PHRASES):
            return True
        return "zelo" in normalized and any(term in normalized for term in _ZELO_PROJECT_TERMS)

    def _read(self, relative: str) -> str:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def resolve(self, text: str) -> str | None:
        if not self.relevant(text):
            return None

        status = self._read("docs/zelo-project-status.md")
        active_status = _markdown_section(status, "## Active increment:")
        roadmap = self._read("docs/zelo-canonical-architecture-roadmap.md")
        roadmap_v013 = _markdown_section(
            roadmap,
            "### v0.13 — Memory, context and evidence",
        )

        project_records = self.projects.context_snapshot()
        sources = [
            {
                "source": "docs/zelo-project-status.md#active",
                "provenance": "canonical_repository",
                "instruction_trust": "none",
                "content": _bounded(active_status, ACTIVE_STATUS_CHARS),
            },
            {
                "source": "docs/zelo-canonical-architecture-roadmap.md#v0.13",
                "provenance": "canonical_repository",
                "instruction_trust": "none",
                "content": _bounded(roadmap_v013, ROADMAP_CHARS),
            },
            {
                "source": "core.projects",
                "provenance": "operator_records",
                "instruction_trust": "none",
                "content": _bounded(
                    json.dumps(
                        project_records,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                    PROJECT_RECORDS_CHARS,
                ),
            },
        ]
        envelope = {
            "type": "zelo_context",
            "purpose": "factual_project_context",
            "instruction_trust": "none",
            "sources": sources,
        }
        return _fit_serialized_budget(envelope)
