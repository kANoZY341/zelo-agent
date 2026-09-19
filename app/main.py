"""ASGI entry point and application assembly."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.agent import AgentRuntime
from app.api import router
from app.config import Settings
from app.database import Database
from app.developer import DeveloperRuntime
from app.developer_contracts import DeveloperWorker
from app.gateway import ModelGateway, ModelProvider, OllamaProvider
from app.logging_config import configure_logging
from app.policy import DomainError
from app.runner import Runner
from app.task_api import router as task_router
from app.tasks import TaskService
from app.tools import ToolGateway
from app.workers import Worker, execute


def create_app(
    settings: Settings | None = None,
    *,
    worker: Worker = execute,
    provider: ModelProvider | None = None,
    developer_worker: DeveloperWorker | None = None,
) -> FastAPI:
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
            configuration.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            model_provider = provider or OllamaProvider(configuration.ollama_url)
            gateway = ModelGateway(
                model_provider,
                configuration.ollama_general_model,
                configuration.ollama_reasoning_model,
                configuration.model_config_version,
            )
            tool_gateway = ToolGateway(configuration)
            developer_runtime = DeveloperRuntime(configuration, developer_worker)
            tasks = TaskService(database, configuration, gateway, tool_gateway, developer_runtime)
            tasks.recover()
            tasks.expire_approvals()
            agent_runtime = AgentRuntime(configuration, gateway, tool_gateway)
            runner = Runner(tasks, worker, gateway, agent_runtime, developer_runtime)
            application.state.database = database
            application.state.tasks = tasks
            application.state.tool_gateway = tool_gateway
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
