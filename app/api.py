"""HTTP endpoints for the Core foundation."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.config import Settings

router = APIRouter()


class RootResponse(BaseModel):
    name: str
    version: str
    status: Literal["online"] = "online"


class HealthResponse(BaseModel):
    status: Literal["healthy", "unavailable"] = "healthy"


@router.get("/", response_model=RootResponse)
def root(request: Request) -> RootResponse:
    settings: Settings = request.app.state.settings
    return RootResponse(name=settings.app_name, version=__version__)


@router.get("/health", response_model=HealthResponse)
def health(request: Request, response: Response) -> HealthResponse:
    """Readiness without paths, credentials, or configuration in the response."""
    try:
        storage = request.app.state.storage
        storage.check_schema()
        with storage.read() as session:
            session.execute(text("SELECT 1"))
        if not request.app.state.runner.ready:
            raise RuntimeError("Runner unavailable")
        scheduler = request.app.state.scheduler
        if not scheduler.ready:
            raise RuntimeError("Scheduler unavailable")
    except Exception:
        response.status_code = 503
        return HealthResponse(status="unavailable")
    return HealthResponse()
