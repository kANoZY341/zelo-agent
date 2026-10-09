"""Zelo-owned bounded tool-output excerpts, inspired by Context Mode concepts.

No Context Mode (Elastic-2.0) source is copied or executed. Large tool output
remains untrusted DATA and is recalled explicitly, never silently injected
into a model context or converted to a Tool/Agent instruction.
"""

import hashlib
from dataclasses import dataclass
from typing import Literal

MAX_BYTES = 1024 * 1024
MAX_LINES = 15000
MAX_WINDOW_LINES = 80
MAX_WINDOW_BYTES = 16384


class CommunityContextRefusal(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolOutputIndex:
    schema: Literal["zelo-curated-tool-output-index-v1"]
    source_sha256: str
    total_bytes: int
    line_count: int
    excerpt: str
    excerpt_is_partial: bool
    instructions_trusted: Literal[False] = False
    tool_execution_allowed: Literal[False] = False
    provider_imported: Literal[False] = False


def _lines(raw: bytes) -> list[str]:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES or b"\x00" in raw:
        raise CommunityContextRefusal("context_untrusted_output_invalid")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise CommunityContextRefusal("context_text_only") from None
    lines = text.splitlines(keepends=True)
    if len(lines) > MAX_LINES:
        raise CommunityContextRefusal("context_too_many_lines")
    return lines


def index_tool_output(raw: bytes) -> ToolOutputIndex:
    lines = _lines(raw)
    head = "".join(lines[:8])
    while len(head.encode("utf-8")) > 2048:
        head = head[: len(head) // 2]
    return ToolOutputIndex(
        schema="zelo-curated-tool-output-index-v1",
        source_sha256=hashlib.sha256(raw).hexdigest(),
        total_bytes=len(raw),
        line_count=len(lines),
        excerpt=head,
        excerpt_is_partial=len(lines) > 8 or len(raw) > len(head.encode("utf-8")),
    )


def read_explicit_window(raw: bytes, *, expected_sha256: str, start_line: int, count: int) -> str:
    """Caller must provide original bytes, digest and bounded requested window."""

    if (
        type(expected_sha256) is not str
        or len(expected_sha256) != 64
        or type(start_line) is not int
        or start_line < 1
        or type(count) is not int
        or not 1 <= count <= MAX_WINDOW_LINES
    ):
        raise CommunityContextRefusal("context_window_request_invalid")
    lines = _lines(raw)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CommunityContextRefusal("context_window_source_changed")
    if start_line > len(lines) or start_line + count - 1 > len(lines):
        raise CommunityContextRefusal("context_window_out_of_bounds")
    text = "".join(lines[start_line - 1 : start_line - 1 + count])
    if len(text.encode("utf-8")) > MAX_WINDOW_BYTES:
        raise CommunityContextRefusal("context_window_size_limit")
    return text
