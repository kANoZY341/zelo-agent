"""A local operator credential, never available to task workers."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def require_operator(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    configured = request.app.state.settings.operator_token
    supplied = credentials.credentials if credentials else ""
    if configured is None or not secrets.compare_digest(
        supplied.encode("utf-8"), configured.get_secret_value().encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Valid operator bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
