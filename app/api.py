"""HTTP endpoints for the Core foundation."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import __version__
from app.config import Settings

router = APIRouter()


class RootResponse(BaseModel):
    name: str
    version: str
    status: Literal["online"] = "online"


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"


@router.get("/", response_model=RootResponse)
def root(request: Request) -> RootResponse:
    settings: Settings = request.app.state.settings
    return RootResponse(name=settings.app_name, version=__version__)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report process liveness; there are no external dependencies to check yet."""
    return HealthResponse()
