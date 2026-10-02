"""Private fixed one-shot anonymous pipe controller. No public CDP forwarding API."""

import asyncio
import html
import json
import math
from dataclasses import dataclass

from qualification.dns import QualificationError

MESSAGE_BYTES = 524288
TOTAL_BYTES = 2097152
MAX_DEPTH = 16
MAX_NODES = 4096
MAX_TEXT = 65536
CHROMIUM_TEXT_PREVIEW_UNITS = 10000
CSP = (
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
    "script-src 'none'; style-src 'none'; base-uri 'none'; form-action 'none'\">"
)
BENIGN_EVENTS = frozenset(
    {
        "Page.domContentEventFired",
        "Page.loadEventFired",
        "Page.lifecycleEvent",
        "DOM.documentUpdated",
    }
)


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid(_):
        raise ValueError

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite
        )
    except (ValueError, UnicodeError, RecursionError):
        raise QualificationError("invalid_json") from None


@dataclass(frozen=True)
class DOMText:
    text: str
    truncated: bool
    nodes: int


def extract_text(root: dict) -> DOMText:
    """DOM text, not a claim of CSS visibility. Missing children are never complete."""
    stack = [(root, 0, False)]
    parts = []
    count = size = 0
    truncated = False
    while stack:
        node, depth, excluded = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH or type(node) is not dict:
            raise QualificationError("dom_bounds")
        kind, name = node.get("nodeType"), node.get("nodeName")
        if type(kind) is not int or type(name) is not str:
            raise QualificationError("invalid_dom")
        excluded = excluded or name.upper() in {"SCRIPT", "STYLE", "HEAD", "META", "TEMPLATE"}
        children = node.get("children", [])
        if type(children) is not list:
            raise QualificationError("invalid_dom")
        expected = node.get("childNodeCount", len(children))
        if (
            type(children) is not list
            or type(expected) is not int
            or expected < len(children)
            or expected < 0
        ):
            raise QualificationError("invalid_dom")
        if (
            (kind in (1, 9, 11) and "childNodeCount" not in node)
            or expected > len(children)
            or any(
                k in node
                for k in ("contentDocument", "shadowRoots", "templateContent", "pseudoElements")
            )
        ):
            truncated = True
        if kind == 3 and not excluded:
            value = node.get("nodeValue")
            if type(value) is not str:
                raise QualificationError("invalid_dom")
            try:
                encoded = value.encode("utf-8")
                # DOM.getDocument returns a preview: Chromium clips long text
                # after 10,000 UTF-16 code units and appends an ellipsis. Never
                # report that structural preview as a complete text node.
                # A literal string exactly matching that form is conservatively
                # marked incomplete too; no extra evaluation authority is added.
                if (
                    value.endswith("\u2026")
                    and len(value[:-1].encode("utf-16-le")) // 2 >= CHROMIUM_TEXT_PREVIEW_UNITS
                ):
                    truncated = True
            except UnicodeError:
                raise QualificationError("invalid_dom") from None
            if size + len(encoded) > MAX_TEXT:
                truncated = True
                value = encoded[: MAX_TEXT - size].decode("utf-8", errors="ignore")
            size += len(value.encode("utf-8"))
            parts.append(value)
        if depth == MAX_DEPTH and children:
            raise QualificationError("dom_depth")
        if len(children) + count + len(stack) > MAX_NODES:
            raise QualificationError("dom_nodes")
        stack.extend((child, depth + 1, excluded) for child in reversed(children))
    return DOMText("".join(parts), truncated, count)


class _Controller:
    def __init__(self, reader, writer, deadline: float) -> None:
        self.reader, self.writer, self.deadline = reader, writer, deadline
        self.sequence = self.total = self.events = 0
        self.session = None
        self.context = None
        self.attachment_session = None
        self.document_frame = None
        self.document_opened = False
        self.document_opened = False

    async def _call(self, method: str, params: dict | None = None, *, page=False) -> dict:
        self.sequence += 1
        if self.sequence > 24:
            raise QualificationError("command_limit")
        request = {"id": self.sequence, "method": method, "params": params or {}}
        if page:
            request["sessionId"] = self.session
        raw = json.dumps(request, separators=(",", ":"), allow_nan=False).encode() + b"\0"
        if len(raw) > MESSAGE_BYTES:
            raise QualificationError("command_bytes")
        self.total += len(raw)
        if self.total > TOTAL_BYTES:
            raise QualificationError("pipe_bytes")
        async with asyncio.timeout_at(self.deadline):
            self.writer.write(raw)
            await self.writer.drain()
            while True:
                try:
                    raw = await self.reader.readuntil(b"\0")
                except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                    raise QualificationError("pipe_framing") from None
                self.total += len(raw)
                if len(raw) > MESSAGE_BYTES or self.total > TOTAL_BYTES:
                    raise QualificationError("pipe_bytes")
                data = strict_json(raw[:-1])
                if type(data) is not dict:
                    raise QualificationError("invalid_response")
                if "id" not in data:
                    self.events += 1
                    # Page.setDocumentContent emits this notification on real Chromium.
                    # It acknowledges our one blank document; it is not navigation authority.
                    if data.get("method") == "Page.documentOpened":
                        params = data.get("params")
                        frame = params.get("frame") if type(params) is dict else None
                        if (
                            self.events > 32
                            or self.document_opened
                            or self.document_frame is None
                            or not page
                            or method
                            not in {
                                "Page.setDocumentContent",
                                "DOM.getDocument",
                                "Page.getFrameTree",
                            }
                            or data.get("sessionId") != self.session
                            or set(data) - {"method", "params", "sessionId"}
                            or type(frame) is not dict
                            or frame.get("id") != self.document_frame
                            or frame.get("url") != "about:blank"
                            or frame.get("parentId") is not None
                            or frame.get("unreachableUrl") not in (None, "")
                        ):
                            raise QualificationError("forbidden_document_open")
                        self.document_opened = True
                        continue
                    # The one explicitly requested attachment can emit a browser-level
                    # acknowledgement before its response. Unsolicited targets fail.
                    if data.get("method") == "Page.documentOpened":
                        event = data.get("params")
                        frame = event.get("frame") if type(event) is dict else None
                        if (
                            method != "Page.setDocumentContent"
                            or self.document_opened
                            or self.events > 32
                            or data.get("sessionId") != self.session
                            or self.session is None
                            or type(frame) is not dict
                            or frame.get("id") != request["params"]["frameId"]
                            or frame.get("url") != "about:blank"
                            or "parentId" in frame
                            or set(data) - {"method", "params", "sessionId"}
                        ):
                            raise QualificationError("forbidden_document_open")
                        self.document_opened = True
                        continue
                    if data.get("method") == "Target.attachedToTarget":
                        event = data.get("params", {})
                        if type(event) is not dict:
                            raise QualificationError("forbidden_attachment")
                        info = event.get("targetInfo", {})
                        if (
                            method != "Target.attachToTarget"
                            or self.attachment_session is not None
                            or self.events > 32
                            or data.get("sessionId") is not None
                            or set(data) - {"method", "params"}
                            or type(event) is not dict
                            or type(info) is not dict
                            or event.get("waitingForDebugger") is not False
                            or info.get("targetId") != request["params"]["targetId"]
                            or info.get("browserContextId", self.context) != self.context
                            or info.get("type") != "page"
                            or info.get("url") != "about:blank"
                        ):
                            raise QualificationError("forbidden_attachment")
                        self.attachment_session = _identifier(event, "sessionId")
                        continue
                    if (
                        self.events > 32
                        or data.get("method") not in BENIGN_EVENTS
                        or data.get("sessionId") != self.session
                        or self.session is None
                        or set(data) - {"method", "params", "sessionId"}
                    ):
                        raise QualificationError("forbidden_event")
                    continue
                if (
                    type(data["id"]) is not int
                    or data["id"] != self.sequence
                    or data.get("sessionId") != (self.session if page else None)
                    or set(data) - {"id", "result", "sessionId"}
                    or type(data.get("result")) is not dict
                ):
                    raise QualificationError("response_mismatch")
                if (
                    method == "Target.attachToTarget"
                    and self.attachment_session is not None
                    and data["result"].get("sessionId") != self.attachment_session
                ):
                    raise QualificationError("attachment_mismatch")
                return data["result"]

    async def _frame(self) -> str:
        data = await self._call("Page.getFrameTree", page=True)
        tree = data.get("frameTree", {})
        if type(tree) is not dict:
            raise QualificationError("frame_denied")
        frame = tree.get("frame", {})
        if type(frame) is not dict:
            raise QualificationError("frame_denied")
        if (
            tree.get("childFrames")
            or frame.get("url") != "about:blank"
            or type(frame.get("id")) is not str
            or not 0 < len(frame["id"]) <= 256
        ):
            raise QualificationError("frame_denied")
        return frame["id"]

    async def render(self, text: str, media_type: str) -> DOMText:
        if media_type not in {"text/html", "text/plain"} or len(text.encode()) > 262144:
            raise QualificationError("document_bounds")
        context = await self._call("Target.createBrowserContext", {"disposeOnDetach": True})
        context_id = _identifier(context, "browserContextId")
        self.context = context_id
        await self._call(
            "Browser.setDownloadBehavior", {"behavior": "deny", "browserContextId": context_id}
        )
        target = await self._call(
            "Target.createTarget", {"url": "about:blank", "browserContextId": context_id}
        )
        attached = await self._call(
            "Target.attachToTarget", {"targetId": _identifier(target, "targetId"), "flatten": True}
        )
        self.session = _identifier(attached, "sessionId")
        if self.attachment_session not in (None, self.session):
            raise QualificationError("attachment_mismatch")
        await self._call("Emulation.setScriptExecutionDisabled", {"value": True}, page=True)
        await self._call("Network.enable", page=True)
        await self._call("Network.setBypassServiceWorker", {"bypass": True}, page=True)
        await self._call("Network.setBlockedURLs", {"urls": ["*"]}, page=True)
        # Fetch interception stalls every request without granting a continuation API.
        await self._call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, page=True)
        await self._call("Page.enable", page=True)
        frame = await self._frame()
        self.document_frame = frame
        content = text if media_type == "text/html" else "<pre>" + html.escape(text) + "</pre>"
        await self._call(
            "Page.setDocumentContent", {"frameId": frame, "html": CSP + content}, page=True
        )
        data = await self._call("DOM.getDocument", {"depth": MAX_DEPTH, "pierce": False}, page=True)
        result = extract_text(data.get("root"))
        if await self._frame() != frame:
            raise QualificationError("frame_changed")
        return result


def _identifier(data: dict, key: str) -> str:
    value = data.get(key)
    if type(value) is not str or not 0 < len(value) <= 256:
        raise QualificationError("invalid_identifier")
    return value


async def render_document(reader, writer, deadline: float, text: str, media_type: str) -> DOMText:
    return await _Controller(reader, writer, deadline).render(text, media_type)
