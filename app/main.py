"""ASGI entry point and application assembly."""

from fastapi import FastAPI

from app import __version__
from app.api import router
from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application with optional explicit settings for tests or tools."""
    configuration = settings if settings is not None else Settings()
    application = FastAPI(title=configuration.app_name, version=__version__)
    application.state.settings = configuration
    application.include_router(router)
    return application


app = create_app()
