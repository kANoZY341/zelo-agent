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
from app.agent_computer_api import router as agent_computer_router
from app.agent_computer_service import AgentComputerService
from app.agent_computers import OpenBotComputerBackend
from app.agent_delegation_api import router as agent_delegation_router
from app.agent_delegations import AgentDelegationService
from app.agent_execution import AgentExecutionService
from app.agent_execution_api import router as agent_execution_router
from app.agent_factory import BUILTIN_AGENT_TASK_KINDS, AgentFactory, default_agent_registry
from app.agent_instance_api import router as agent_instance_router
from app.agent_instances import AgentInstanceService
from app.api import router
from app.api_contracts import ApiEndpointRegistry
from app.api_worker import ApiRuntime
from app.browser_worker import BrowserRuntime, configured_browser_runtime
from app.campaign_api import router as campaign_router
from app.campaign_control import CampaignControlService
from app.campaign_control_api import router as campaign_control_router
from app.campaigns import CampaignService
from app.capabilities import default_capability_registry
from app.capability_api import router as capability_router
from app.computer_egress import ComputerEgressPolicy
from app.computer_egress_proxy import ComputerEgressProxy
from app.computer_worker import ComputerRuntime
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
from app.python_worker import PythonRuntime
from app.research import ResearchRuntime
from app.runner import Runner
from app.scheduler import CampaignScheduler
from app.scheduler_api import router as scheduler_router
from app.self_improvement import RepositoryInspector
from app.self_improvement_planner import SelfImprovementPlanner
from app.self_improvement_runtime import SelfImprovementRuntime
from app.shell_worker import ShellRuntime, configured_shell_runtime
from app.skill_api import router as skill_router
from app.skills import default_skill_registry
from app.storage import build_storage
from app.task_api import router as task_router
from app.tasks import TaskService
from app.tools import ToolGateway
from app.worker_registry import (
    Supervisor,
    WorkerRegistry,
    descriptor_for_api_worker,
    descriptor_for_browser_worker,
    descriptor_for_computer_navigate_worker,
    descriptor_for_computer_worker,
    descriptor_for_python_worker,
    descriptor_for_research_worker,
    descriptor_for_shell_worker,
)
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
        browser_runtime = None
        shell_runtime = None
        computer_egress_proxy = None
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
            agent_computers = OpenBotComputerBackend(configuration)
            developer_runtime = DeveloperRuntime(configuration, developer_worker)
            research_runtime = ResearchRuntime(configuration, gateway, tool_gateway)
            research_registry = WorkerRegistry[ResearchRuntime](
                ((descriptor_for_research_worker(), research_runtime),)
            )
            research_supervisor = Supervisor[ResearchRuntime](research_registry)
            python_runtime = PythonRuntime()
            python_registry = WorkerRegistry[PythonRuntime](
                ((descriptor_for_python_worker(), python_runtime),)
            )
            python_supervisor = Supervisor[PythonRuntime](python_registry)
            api_endpoint_registry = ApiEndpointRegistry.from_entries(
                configuration.api_worker_endpoints
            )
            computer_browser_endpoint_registry = ApiEndpointRegistry.from_entries(
                configuration.computer_browser_endpoints
            )
            api_runtime = ApiRuntime(api_endpoint_registry)
            api_worker_registry = WorkerRegistry[ApiRuntime](
                ((descriptor_for_api_worker(), api_runtime),)
            )
            api_supervisor = Supervisor[ApiRuntime](api_worker_registry)
            browser_runtime = configured_browser_runtime(configuration)
            browser_worker_registry = WorkerRegistry[BrowserRuntime](
                ((descriptor_for_browser_worker(), browser_runtime),) if browser_runtime else ()
            )
            browser_supervisor = Supervisor[BrowserRuntime](browser_worker_registry)
            computer_runtime = ComputerRuntime(
                database,
                agent_computers,
                computer_browser_endpoint_registry,
            )
            computer_registrations: list[tuple] = []
            if agent_computers.configured:
                computer_registrations.append((descriptor_for_computer_worker(), computer_runtime))
                if (
                    configuration.computer_internal_network_cidr is not None
                    and configuration.computer_egress_proxy_host is not None
                    and computer_browser_endpoint_registry.list()
                ):
                    computer_registrations.append(
                        (descriptor_for_computer_navigate_worker(), computer_runtime)
                    )
            computer_worker_registry = WorkerRegistry[ComputerRuntime](
                tuple(computer_registrations)
            )
            computer_supervisor = Supervisor[ComputerRuntime](computer_worker_registry)
            capability_registry = default_capability_registry()
            capability_registry.validate_workers(browser_worker_registry.list())
            capability_registry.validate_workers(computer_worker_registry.list())
            shell_runtime = configured_shell_runtime(configuration)
            shell_worker_registry = WorkerRegistry[ShellRuntime](
                ((descriptor_for_shell_worker(), shell_runtime),) if shell_runtime else ()
            )
            shell_supervisor = Supervisor[ShellRuntime](shell_worker_registry)
            capability_registry.validate_workers(shell_worker_registry.list())
            capability_registry.validate_workers(developer_runtime.registry.list())
            capability_registry.validate_workers(research_registry.list())
            capability_registry.validate_workers(python_registry.list())
            capability_registry.validate_workers(api_worker_registry.list())
            agent_registry = default_agent_registry()
            skill_registry = default_skill_registry()
            available_tools = {item["name"] for item in tool_gateway.registry.metadata()}
            agent_registry.validate_activation(
                available_task_kinds=BUILTIN_AGENT_TASK_KINDS,
                available_tools=available_tools,
            )
            skill_registry.validate(
                capability_registry,
                available_task_kinds=BUILTIN_AGENT_TASK_KINDS,
                available_tools=available_tools,
            )
            skill_registry.validate_agents(agent_registry.list_agents())
            agent_activation_report = AgentActivationEvaluator(
                agent_registry,
                available_task_kinds=BUILTIN_AGENT_TASK_KINDS,
                available_tools=available_tools,
                worker_descriptors=(
                    *developer_runtime.registry.list(),
                    *research_registry.list(),
                    *python_registry.list(),
                    *api_worker_registry.list(),
                    *browser_worker_registry.list(),
                    *computer_worker_registry.list(),
                    *shell_worker_registry.list(),
                ),
                capability_registry=capability_registry,
            ).require()
            agent_factory = AgentFactory(agent_registry)
            computer_egress_policy = ComputerEgressPolicy(
                database,
                agent_factory,
                computer_browser_endpoint_registry,
                configuration,
            )
            computer_egress_policy.validate_agents(agent_registry.list_agents())
            if configuration.computer_egress_proxy_host is not None:
                computer_egress_proxy = ComputerEgressProxy(
                    computer_egress_policy,
                    host=configuration.computer_egress_proxy_host,
                    port=configuration.computer_egress_proxy_port,
                )
                await computer_egress_proxy.start()
            projects = ProjectService(database)
            memories = MemoryService(database)
            agent_instances = AgentInstanceService(database, agent_factory, memories)
            agent_computer_service = AgentComputerService(
                database,
                agent_factory,
                agent_computers,
            )
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
                research_supervisor=research_supervisor,
                python_supervisor=python_supervisor,
                api_supervisor=api_supervisor,
                browser_supervisor=browser_supervisor,
                shell_supervisor=shell_supervisor,
                computer_supervisor=computer_supervisor,
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
                research_supervisor=research_supervisor,
                python_supervisor=python_supervisor,
                api_supervisor=api_supervisor,
                browser_supervisor=browser_supervisor,
                shell_supervisor=shell_supervisor,
                computer_supervisor=computer_supervisor,
            )
            application.state.capability_registry = capability_registry
            application.state.skill_registry = skill_registry
            application.state.agent_registry = agent_registry
            application.state.agent_activation_report = agent_activation_report
            application.state.agent_factory = agent_factory
            application.state.agent_computers = agent_computers
            application.state.storage = database
            application.state.database = database
            application.state.tasks = tasks
            application.state.projects = projects
            application.state.memories = memories
            application.state.agent_instances = agent_instances
            application.state.agent_computer_service = agent_computer_service
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
            application.state.research_registry = research_registry
            application.state.research_supervisor = research_supervisor
            application.state.python_runtime = python_runtime
            application.state.python_registry = python_registry
            application.state.python_supervisor = python_supervisor
            application.state.api_endpoint_registry = api_endpoint_registry
            application.state.computer_browser_endpoint_registry = (
                computer_browser_endpoint_registry
            )
            application.state.computer_egress_policy = computer_egress_policy
            application.state.computer_egress_proxy = computer_egress_proxy
            application.state.api_runtime = api_runtime
            application.state.api_worker_registry = api_worker_registry
            application.state.api_supervisor = api_supervisor
            application.state.browser_runtime = browser_runtime
            application.state.browser_supervisor = browser_supervisor
            application.state.browser_worker_registry = browser_worker_registry
            application.state.computer_runtime = computer_runtime
            application.state.computer_worker_registry = computer_worker_registry
            application.state.computer_supervisor = computer_supervisor
            application.state.shell_runtime = shell_runtime
            application.state.shell_supervisor = shell_supervisor
            application.state.shell_worker_registry = shell_worker_registry
            application.state.tool_gateway = tool_gateway
            application.state.runner = runner
            runner.start()
            scheduler.start()
            yield
        finally:
            try:
                if scheduler:
                    await scheduler.stop()
                if runner:
                    await runner.stop()
            finally:
                try:
                    if browser_runtime:
                        await browser_runtime.aclose()
                finally:
                    try:
                        if shell_runtime:
                            await shell_runtime.aclose()
                    finally:
                        try:
                            if computer_egress_proxy:
                                await computer_egress_proxy.aclose()
                        finally:
                            database.close()

    application = FastAPI(title=configuration.app_name, version=__version__, lifespan=lifespan)
    application.state.settings = configuration
    application.include_router(router)
    application.include_router(task_router)
    application.include_router(capability_router)
    application.include_router(skill_router)
    application.include_router(agent_activation_router)
    application.include_router(agent_instance_router)
    application.include_router(agent_computer_router)
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
