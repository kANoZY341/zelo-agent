"""ASGI entry point and application assembly."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.agent import AgentRuntime
from app.agent_activation import AgentActivationEvaluator
from app.agent_activation_api import router as agent_activation_router
from app.agent_child_creation import AgentChildCreationService
from app.agent_child_creation_api import router as agent_child_creation_router
from app.agent_delegation_api import router as agent_delegation_router
from app.agent_delegations import AgentDelegationService
from app.agent_execution import AgentExecutionService
from app.agent_execution_api import router as agent_execution_router
from app.agent_factory import BUILTIN_AGENT_TASK_KINDS, AgentFactory, default_agent_registry
from app.agent_instance_api import router as agent_instance_router
from app.agent_instances import AgentInstanceService
from app.api import router
from app.campaign_api import router as campaign_router
from app.campaign_control import CampaignControlService
from app.campaign_control_api import router as campaign_control_router
from app.campaigns import CampaignService
from app.config import ROOT, Settings
from app.context import ContextResolver
from app.developer import DeveloperRuntime
from app.developer_contracts import DeveloperWorker
from app.gateway import ModelGateway, ModelProvider, OllamaProvider
from app.logging_config import configure_logging
from app.memory import MemoryService
from app.memory_api import router as memory_router
from app.mind import MindService
from app.mind_api import router as mind_router
from app.organization import OrganizationService
from app.organization_api import router as organization_router
from app.performance import PerformanceEvidenceService
from app.performance_api import router as performance_router
from app.policy import DomainError
from app.project_api import router as project_router
from app.projects import ProjectService
from app.promotion import PromotionRuntime
from app.promotion_ci import PromotionCIVerifier
from app.promotion_git import PromotionCommitter
from app.promotion_github import GitHubPromotionPublisher
from app.promotion_merge import PromotionMergeRuntime
from app.promotion_release import PromotionReleaseRuntime
from app.promotion_review import PromotionReviewer
from app.research import ResearchRuntime
from app.runner import Runner
from app.scheduler import CampaignScheduler
from app.scheduler_api import router as scheduler_router
from app.self_improvement import RepositoryInspector
from app.self_improvement_planner import SelfImprovementPlanner
from app.self_improvement_runtime import SelfImprovementRuntime
from app.storage import build_storage
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
        database = build_storage(configuration)
        runner = None
        scheduler = None
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
                mind_model=configuration.ollama_mind_model,
            )
            tool_gateway = ToolGateway(configuration)
            developer_runtime = DeveloperRuntime(configuration, developer_worker)
            agent_registry = default_agent_registry()
            available_tools = {item["name"] for item in tool_gateway.registry.metadata()}
            agent_registry.validate_activation(
                available_task_kinds=BUILTIN_AGENT_TASK_KINDS,
                available_tools=available_tools,
            )
            agent_activation_report = AgentActivationEvaluator(
                agent_registry,
                available_task_kinds=BUILTIN_AGENT_TASK_KINDS,
                available_tools=available_tools,
                worker_descriptors=developer_runtime.registry.list(),
            ).require()
            agent_factory = AgentFactory(agent_registry)
            projects = ProjectService(database)
            memories = MemoryService(database)
            agent_instances = AgentInstanceService(database, agent_factory, memories)
            organization = OrganizationService(database, agent_factory)
            performance_evidence = PerformanceEvidenceService(database)
            campaigns = CampaignService(database)
            campaign_controls = CampaignControlService(database)
            agent_delegations = AgentDelegationService(database, agent_factory, campaign_controls)
            agent_child_creation = AgentChildCreationService(database, agent_factory)
            context_resolver = ContextResolver(projects, memories=memories)
            self_improvement_runtime = SelfImprovementRuntime(
                RepositoryInspector(ROOT),
                SelfImprovementPlanner(
                    gateway,
                    timeout_seconds=configuration.model_timeout_seconds,
                ),
            )
            promotion_reviewer = PromotionReviewer(developer_runtime)
            promotion_committer = PromotionCommitter(developer_runtime, promotion_reviewer)
            promotion_runtime = None
            promotion_ci_verifier = None
            promotion_merge_runtime = None
            promotion_release_runtime = None
            if configuration.promotion_repository is not None:
                promotion_publisher = GitHubPromotionPublisher(
                    configuration,
                    developer_runtime,
                    promotion_reviewer,
                )
                promotion_runtime = PromotionRuntime(
                    promotion_committer,
                    promotion_publisher,
                )
                promotion_ci_verifier = PromotionCIVerifier(configuration)
                promotion_merge_runtime = PromotionMergeRuntime(
                    configuration,
                    promotion_ci_verifier,
                )
                promotion_release_runtime = PromotionReleaseRuntime(configuration)
            tasks = TaskService(
                database,
                configuration,
                gateway,
                tool_gateway,
                developer_runtime,
                promotion_reviewer,
                promotion_ci_verifier,
                promotion_release_runtime,
                agent_factory=agent_factory,
            )
            agent_execution = AgentExecutionService(database, agent_factory, tasks)
            mind = MindService(
                database,
                configuration,
                gateway,
                tasks,
                context_resolver=context_resolver,
            )
            scheduler = CampaignScheduler(
                database,
                configuration,
                campaign_controls,
                mind,
            )
            scheduler.recover()
            campaign_controls.recover_expired()
            tasks.recover()
            tasks.expire_approvals()
            agent_runtime = AgentRuntime(configuration, gateway, tool_gateway)
            research_runtime = ResearchRuntime(configuration, gateway, tool_gateway)
            runner = Runner(
                tasks,
                worker,
                gateway,
                agent_runtime,
                developer_runtime,
                promotion_runtime,
                promotion_merge_runtime,
                promotion_release_runtime,
                self_improvement_runtime,
                research_runtime=research_runtime,
            )
            application.state.agent_registry = agent_registry
            application.state.agent_activation_report = agent_activation_report
            application.state.agent_factory = agent_factory
            application.state.storage = database
            application.state.database = database
            application.state.tasks = tasks
            application.state.projects = projects
            application.state.memories = memories
            application.state.agent_instances = agent_instances
            application.state.organization = organization
            application.state.performance_evidence = performance_evidence
            application.state.campaigns = campaigns
            application.state.campaign_controls = campaign_controls
            application.state.agent_delegations = agent_delegations
            application.state.agent_child_creation = agent_child_creation
            application.state.agent_execution = agent_execution
            application.state.mind = mind
            application.state.scheduler = scheduler
            application.state.research_runtime = research_runtime
            application.state.tool_gateway = tool_gateway
            application.state.runner = runner
            runner.start()
            scheduler.start()
            yield
        finally:
            if scheduler:
                await scheduler.stop()
            if runner:
                await runner.stop()
            database.close()

    application = FastAPI(title=configuration.app_name, version=__version__, lifespan=lifespan)
    application.state.settings = configuration
    application.include_router(router)
    application.include_router(task_router)
    application.include_router(agent_activation_router)
    application.include_router(agent_instance_router)
    application.include_router(agent_child_creation_router)
    application.include_router(agent_delegation_router)
    application.include_router(agent_execution_router)
    application.include_router(organization_router)
    application.include_router(project_router)
    application.include_router(memory_router)
    application.include_router(performance_router)
    application.include_router(campaign_router)
    application.include_router(campaign_control_router)
    application.include_router(scheduler_router)
    application.include_router(mind_router)

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        headers = {"Retry-After": "1"} if error.status_code == 503 else None
        return JSONResponse(
            status_code=error.status_code, content={"detail": error.message}, headers=headers
        )

    return application


app = create_app()
