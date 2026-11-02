"""Authenticate and bound JSON-capable ingress before FastAPI parses request bodies."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

MAX_CORE_BODY_BYTES = 16 * 1024 * 1024
MAX_VOICE_SYNTHESIS_BODY_BYTES = 64 * 1024

AuthToken = Callable[[], bytes]


def bearer_matches(headers: list[tuple[bytes, bytes]], expected_token: bytes) -> bool:
    values = [value for name, value in headers if name.lower() == b"authorization"]
    if len(values) != 1:
        return False
    scheme, separator, supplied = values[0].partition(b" ")
    return (
        separator == b" "
        and scheme.lower() == b"bearer"
        and bool(supplied)
        and secrets.compare_digest(supplied, expected_token)
    )


class BoundedAuthenticatedBody:
    """Pure ASGI gate; no body reaches a parser before auth and the byte ceiling."""

    def __init__(
        self,
        app: Any,
        *,
        token: AuthToken,
        max_bytes: int,
        unauthorized_detail: str,
        too_large_detail: str,
        paths: frozenset[str] | None = None,
        methods: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"}),
    ) -> None:
        self.app = app
        self.token = token
        self.max_bytes = max_bytes
        self.unauthorized_detail = unauthorized_detail
        self.too_large_detail = too_large_detail
        self.paths = paths
        self.methods = methods

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in self.methods
            or (self.paths is not None and scope["path"] not in self.paths)
        ):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        if not bearer_matches(headers, self.token()):
            response = JSONResponse(
                {"detail": self.unauthorized_detail},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        declared = [value for name, value in headers if name.lower() == b"content-length"]
        if declared:
            try:
                length = int(declared[0]) if len(declared) == 1 else -1
            except ValueError:
                length = -1
            if length < 0:
                await JSONResponse({"detail": "request_content_length_invalid"}, status_code=400)(
                    scope, receive, send
                )
                return
            if length > self.max_bytes:
                await JSONResponse({"detail": self.too_large_detail}, status_code=413)(
                    scope, receive, send
                )
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await JSONResponse({"detail": self.too_large_detail}, status_code=413)(
                    scope, receive, send
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
