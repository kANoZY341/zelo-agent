"""Bounded read-only context assembly for Zelo Mind."""

import json
from pathlib import Path

from app.config import ROOT
from app.projects import ProjectService

CONTEXT_MAX_CHARS = 6000
PROJECT_STATUS_CHARS = 2200
ROADMAP_CHARS = 1700
PROJECT_RECORDS_CHARS = 1200

_CONTEXT_PHRASES = (
    "what are we building",
    "what are we working on",
    "what are you building",
    "current project",
    "current milestone",
    "our roadmap",
    "zelo roadmap",
    "zelo architecture",
    "what should you become",
    "what do you think you should become",
)
_ZELO_CONTEXT_TERMS = (
    "build",
    "building",
    "project",
    "roadmap",
    "architecture",
    "milestone",
    "working",
    "goal",
    "current",
    "next",
    "vision",
    "become",
)


def _bounded(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    marker = "\n...[bounded]"
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
        return "zelo" in normalized and any(term in normalized for term in _ZELO_CONTEXT_TERMS)

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
        roadmap = self._read("docs/zelo-canonical-architecture-roadmap.md")
        roadmap_v012 = _markdown_section(
            roadmap,
            "### v0.12 — Durable goals and first useful campaign",
        )

        project_records = self.projects.context_snapshot()
        sources = [
            {
                "source": "docs/zelo-project-status.md",
                "content": _bounded(status, PROJECT_STATUS_CHARS),
            },
            {
                "source": "docs/zelo-canonical-architecture-roadmap.md#v0.12",
                "content": _bounded(roadmap_v012, ROADMAP_CHARS),
            },
            {
                "source": "core.projects",
                "content": _bounded(
                    json.dumps(
                        project_records,
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                    PROJECT_RECORDS_CHARS,
                ),
            },
        ]
        envelope = {
            "type": "zelo_context",
            "trusted": True,
            "purpose": "factual_project_context",
            "sources": sources,
        }
        serialized = json.dumps(
            envelope,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(serialized) > CONTEXT_MAX_CHARS:
            raise RuntimeError("Context resolver exceeded its hard budget")
        return serialized
