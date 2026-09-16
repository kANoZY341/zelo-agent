"""ASGI entry point and application assembly."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api import router
from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.policy import DomainError
from app.runner import Runner
from app.task_api import router as task_router
from app.tasks import TaskService
from app.workers import Worker, execute


def create_app(settings: Settings | None = None, *, worker: Worker = execute) -> FastAPI:
    """Build an application with optional explicit settings for tests or tools."""
    configuration = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if configuration.operator_token is None:
            raise RuntimeError("Set ZELO_OPERATOR_TOKEN before starting Zelo")
        configure_logging()
        database = Database(configuration.database_path)
        runner = None
        try:
            database.acquire_runner()
            database.check_schema()
            tasks = TaskService(database, configuration)
            tasks.recover()
            tasks.expire_approvals()
            runner = Runner(tasks, worker)
            application.state.database = database
            application.state.tasks = tasks
            application.state.runner = runner
            runner.start()
            yield
        finally:
            if runner:
                await runner.stop()
            database.close()

    application = FastAPI(title=configuration.app_name, version=__version__, lifespan=lifespan)
    application.state.settings = configuration
    application.include_router(router)
    application.include_router(task_router)

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        headers = {"Retry-After": "1"} if error.status_code == 503 else None
        return JSONResponse(
            status_code=error.status_code, content={"detail": error.message}, headers=headers
        )

    return application


app = create_app()
