"""Disposable native R1 qualification; run only on an approved Zelo host checkout.

This command creates a private Core database and Docker namespace, exercises two
real per-AgentInstance computers and delegated Space file use, then removes only
its own resources. It grants no default or production authority.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.agent_computer_service import AgentComputerService
from app.agent_computers import OpenBotComputerBackend
from app.agent_delegations import AgentDelegationService
from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, default_agent_registry
from app.agent_instances import AgentInstanceService
from app.agent_space_resources import AgentSpaceResourceService
from app.campaign_control import CampaignControlService
from app.campaigns import CampaignService
from app.computer_observer import (
    admitted_computer_observer_registry,
    computer_observer_spec,
    computer_read_team_registry,
    space_read_manager_spec,
)
from app.computer_worker import ComputerRuntime
from app.config import Settings
from app.database import Database
from app.memory import MemoryService
from app.policy import DomainError, State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentComputerPermissionPatch,
    AgentDelegationCreate,
    AgentDelegationRevoke,
    AgentInstanceCreate,
    AgentTaskCreate,
    CampaignBudgetReserve,
    CampaignBudgetSet,
    CampaignCreate,
    GoalCreate,
    GoalSpec,
    ProjectCreate,
)
from app.space_file_gateway import SpaceFileReadGateway
from app.tasks import TaskService
from app.worker_registry import Supervisor, WorkerRegistry, descriptor_for_computer_worker

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_IMAGE = "zelo-agent-computer-supervisor:b6932d3-zelo-auth"
CHILD_IMAGE = "zelo-agent-computer:b6932d3"
SUPERVISOR_DIGEST = "sha256:02f291d4e58234335aaa73932ad237d0e60e5bba82753524efd7a43d5a92efa0"
CHILD_DIGEST = "sha256:c233f25e08f394e8aed40d39d27c55d3c1103601016d26faeadce4f152570926"
SUBNET = "172.30.254.0/24"
GATEWAY = "172.30.254.1"
FIXTURE = "two native computers, two independent approvals"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def docker(*args: str, timeout: int = 100, allow_failure: bool = False) -> str:
    completed = subprocess.run(
        ["/usr/bin/docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if completed.returncode and not allow_failure:
        raise RuntimeError(
            f"docker {args[0]} failed ({completed.returncode}): {completed.stderr[-240:]}"
        )
    return completed.stdout.strip()


def source_sha() -> str:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    require(not dirty, "qualification checkout is dirty")
    return sha


def owned_containers(tag: str) -> list[str]:
    names = docker("ps", "-a", "--format", "{{.Names}}")
    return [name for name in names.splitlines() if name.startswith(tag + "-")]


def owned_volumes(tag: str) -> list[str]:
    names = docker("volume", "ls", "--format", "{{.Name}}")
    return [name for name in names.splitlines() if name.startswith(tag + "-")]


class IsolationSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


async def qualify(settings: IsolationSettings) -> tuple[str, str, str]:
    db = Database(settings.database_path)
    try:
        db.migrate()
        backend = OpenBotComputerBackend(settings)
        child_spec = computer_observer_spec()
        parent_spec = space_read_manager_spec()
        registry = admitted_computer_observer_registry(
            db,
            default_agent_registry(),
            approved_registry_sha256=computer_read_team_registry().version,
            backend_configured=backend.configured,
        )
        factory = AgentFactory(registry)
        agents = AgentInstanceService(db, factory, MemoryService(db))
        projects = ProjectService(db)
        project = projects.create(
            ProjectCreate(name="R1 native pair", summary="Disposable host proof")
        )
        goal = projects.create_goal(
            project.id,
            GoalCreate(
                title="Qualify two Computer Space authorities",
                spec=GoalSpec(
                    objective="Read one explicitly approved private fixture",
                    success_criteria=["exact persisted content"],
                    constraints=["no writes or host fallback"],
                ),
            ),
        )
        campaign = CampaignService(db).create(
            goal.id, CampaignCreate(title="R1 native pair", summary="Disposable qualification")
        )
        parent = agents.create(
            AgentInstanceCreate(agent_id=parent_spec.agent_id, project_id=project.id)
        )
        child = agents.create(
            AgentInstanceCreate(agent_id=child_spec.agent_id, project_id=project.id)
        )
        require(parent.id != child.id, "AgentInstance identities collapsed")
        service = AgentComputerService(db, factory, backend)
        for instance in (parent, child):
            try:
                await service.start(instance.id)
            except DomainError as error:
                require(error.status_code == 409, "ungranted Computer refusal changed")
            else:
                raise RuntimeError("Computer started without owner grant")
            await service.patch_permissions(
                instance.id, AgentComputerPermissionPatch(enabled=True, browser=True, files=True)
            )
            started = await service.start(instance.id)
            require(started.state == "running", "native Computer did not start")
            existing = await backend.existing(instance.id)
            require(
                existing is not None and existing.agent_id == instance.id, "backend identity drift"
            )
            require((await backend.control(instance.id)).holder == "bot", "Computer not bot-held")
        print("TWO_REAL_COMPUTERS=PASS", flush=True)

        names = [f"{settings.computer_namespace}-computer-{item.id}" for item in (parent, child)]
        require(len(set(names)) == 2, "Docker child names collapsed")
        for name in names:
            info = json.loads(docker("inspect", name))[0]
            require(info["State"]["Running"], "native Computer container stopped")
            require(
                tuple(info["NetworkSettings"]["Networks"]) == (settings.computer_network_name,),
                "Computer escaped private network",
            )
            require(
                not any(info["NetworkSettings"]["Ports"].values()),
                "Computer exposed a host port",
            )
        print("TWO_PRIVATE_CHILD_CONTAINERS=PASS", flush=True)

        runtime = ComputerRuntime(db, backend)
        supervisor = Supervisor(WorkerRegistry(((descriptor_for_computer_worker(), runtime),)))
        tasks = TaskService(db, settings, agent_factory=factory, computer_supervisor=supervisor)
        submit = AgentExecutionService(db, factory, tasks)
        resources = AgentSpaceResourceService(db, factory, settings.runtime_dir)
        resource = resources.provision(
            instance_id=parent.id,
            space_id="workroom",
            resource_id="documents",
            operations=("list", "read"),
            files=(("notes.txt", FIXTURE),),
        )
        activations = []
        for instance in (parent, child):
            intent = resources.intents.create(
                instance_id=instance.id,
                space_id="workroom",
                resource_kind="files",
                resource_id="documents",
                operations=("list", "read"),
                ttl_seconds=600,
            )
            activations.append(
                resources.activate(
                    instance_id=instance.id,
                    intent_id=intent.id,
                    resource_record_id=resource.id,
                )
            )
        require(activations[0].id != activations[1].id, "Space approvals collapsed")
        controls = CampaignControlService(db)
        controls.set_budget(campaign.id, "agent_steps", CampaignBudgetSet(limit_units=100))
        budget = controls.reserve_budget(
            campaign.id,
            CampaignBudgetReserve(
                resource="agent_steps",
                owner_id=parent.id,
                idempotency_key="r1-pair-budget",
                units=3,
                ttl_seconds=3600,
            ),
        )
        delegations = AgentDelegationService(db, factory, controls)
        delegation = delegations.create(
            parent.id,
            AgentDelegationCreate(
                child_instance_id=child.id,
                campaign_id=campaign.id,
                budget_reservation_id=budget.id,
                idempotency_key="r1-pair-delegation",
                objective="Read the explicitly shared document",
                allowed_task_kinds=["computer_run"],
                required_capabilities=["computer_read"],
                max_depth=1,
                budget_resource="agent_steps",
                budget_units=3,
            ),
        )
        runner = Runner(
            tasks,
            computer_supervisor=supervisor,
            space_file_gateway=SpaceFileReadGateway(
                db, factory, settings.runtime_dir, runtime, task_service=tasks
            ),
        )

        def request(action: str, *, child_activation: str | None = None) -> AgentTaskCreate:
            return AgentTaskCreate.model_validate(
                {
                    "delegation_id": delegation.id,
                    "task": {
                        "kind": "computer_run",
                        "campaign_id": campaign.id,
                        "payload": {
                            "action": action,
                            "path": "notes.txt" if action == "files_read" else "",
                            "space_activation_id": child_activation or activations[1].id,
                            "parent_space_activation_id": activations[0].id,
                        },
                    },
                }
            )

        try:
            submit.submit(child.id, request("files_read", child_activation=activations[0].id), None)
        except DomainError:
            pass
        else:
            raise RuntimeError("parent approval substituted for child")
        print("PARENT_APPROVAL_SUBSTITUTION_DENIED=PASS", flush=True)
        for action in ("files_read", "files_list"):
            task, created = submit.submit(child.id, request(action), None)
            require(created and task.status == State.PENDING, "Space Task not admitted")
            claimed = tasks.claim()
            require(claimed is not None and claimed.task_id == task.id, "wrong Space Task claimed")
            await runner._run(claimed)
            done = tasks.get(task.id)
            require(done.status == State.SUCCEEDED, f"native Space Task failed: {done.error}")
            if action == "files_read":
                require(done.result["files_read"]["text"] == FIXTURE, "private fixture mismatch")
            else:
                require(
                    [entry["path"] for entry in done.result["files_list"]["entries"]]
                    == ["notes.txt"],
                    "private listing mismatch",
                )
        print("REAL_DELEGATED_SPACE_READ_LIST=PASS", flush=True)
        delegations.revoke(
            delegation.id, AgentDelegationRevoke(reason="End disposable R1 qualification")
        )
        try:
            submit.submit(child.id, request("files_read"), None)
        except DomainError:
            pass
        else:
            raise RuntimeError("revoked delegation admitted a new Space Task")
        print("POST_REVOCATION_TASK_DENIED=PASS", flush=True)
        for instance in (parent, child):
            stopped = await service.stop(instance.id)
            require(stopped.state == "stopped", "Computer stop failed")
        return parent.id, child.id, backend.identity
    finally:
        db.close()


def main() -> None:
    require(__debug__, "optimized mode is unsupported")
    require(os.name == "posix", "native qualification requires the approved Linux host")
    require(not any(key.startswith("ZELO_") for key in os.environ), "ambient Zelo env present")
    sha = source_sha()
    require(
        docker("image", "inspect", "--format", "{{.Id}}", SUPERVISOR_IMAGE) == SUPERVISOR_DIGEST,
        "supervisor image drift",
    )
    require(
        docker("image", "inspect", "--format", "{{.Id}}", CHILD_IMAGE) == CHILD_DIGEST,
        "child image drift",
    )
    tag = "zeloq" + secrets.token_hex(4)
    sandbox, control_net, supervisor_name = (
        tag + "-sandbox",
        tag + "-control",
        tag + "-supervisor",
    )
    for name in (sandbox, control_net):
        require(
            not docker("network", "ls", "--filter", "name=^" + name + "$", "--format", "{{.Name}}"),
            "network name collision",
        )
    require(not owned_containers(tag), "container namespace collision")
    require(not owned_volumes(tag), "volume namespace collision")
    created: list[tuple[str, str]] = []
    print("SOURCE_SHA=" + sha, flush=True)
    print("RUN_NAMESPACE=" + tag, flush=True)
    (ROOT / "runtime").mkdir(exist_ok=True)
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix=tag + "-", dir=ROOT / "runtime") as tmp,
        tempfile.TemporaryDirectory(prefix=tag + "-", dir=ROOT / "workspaces") as workspace,
    ):
        temp = Path(tmp)
        token, master = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        env = temp / "supervisor.env"
        env.write_text(
            "PORT=4300\nSUPERVISOR_TOKEN="
            + token
            + "\nCOMPUTER_TOKEN="
            + master
            + "\nCOMPUTER_NAMESPACE="
            + tag
            + "\nCOMPUTER_IMAGE="
            + CHILD_IMAGE
            + "\nCOMPUTER_BROWSER_MODE=headless\nCOMPUTER_NETWORK="
            + sandbox
            + "\nZELO_COMPUTER_EGRESS_PROXY_HOST="
            + GATEWAY
            + "\nZELO_COMPUTER_EGRESS_PROXY_PORT=4390\n"
        )
        env.chmod(0o600)
        try:
            docker(
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                "--subnet",
                SUBNET,
                "--gateway",
                GATEWAY,
                "--label",
                "zelo.r1.run=" + tag,
                sandbox,
                timeout=40,
            )
            created.append(("network", sandbox))
            docker(
                "network",
                "create",
                "--driver",
                "bridge",
                "--label",
                "zelo.r1.run=" + tag,
                control_net,
                timeout=40,
            )
            created.append(("network", control_net))
            docker(
                "run",
                "-d",
                "--name",
                supervisor_name,
                "--restart",
                "no",
                "--network",
                control_net,
                "-p",
                "127.0.0.1::4300",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--label",
                "zelo.r1.run=" + tag,
                "--env-file",
                str(env),
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                SUPERVISOR_IMAGE,
                timeout=70,
            )
            created.append(("container", supervisor_name))
            docker("network", "connect", sandbox, supervisor_name, timeout=30)
            info = json.loads(docker("inspect", supervisor_name))[0]
            ports = info["NetworkSettings"]["Ports"]["4300/tcp"]
            require(
                len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1",
                "supervisor not loopback only",
            )
            port = int(ports[0]["HostPort"])
            for _ in range(60):
                try:
                    health = httpx.get(
                        f"http://127.0.0.1:{port}/health", timeout=2, trust_env=False
                    )
                    if health.status_code == 200 and health.json().get("docker"):
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("native supervisor never became healthy")
            print("PRIVATE_SUPERVISOR_READY=PASS", flush=True)
            settings = IsolationSettings(
                _env_file=None,
                operator_token=SecretStr(secrets.token_urlsafe(32)),
                runtime_dir=temp / "core",
                workspace_root=Path(workspace),
                computer_supervisor_url=f"http://127.0.0.1:{port}",
                computer_supervisor_token=SecretStr(token),
                computer_token=SecretStr(master),
                computer_namespace=tag,
                computer_internal_network_cidr=SUBNET,
                computer_network_name=sandbox,
                computer_egress_proxy_host=GATEWAY,
                computer_egress_proxy_port=4390,
                computer_request_timeout_seconds=80,
                computer_observer_approved_registry_sha256=computer_read_team_registry().version,
                browser_worker_enabled=False,
                shell_worker_enabled=False,
                skill_publication_external_merge_enabled=False,
                scheduler_enabled=False,
            )
            parent_id, child_id, backend_id = asyncio.run(qualify(settings))
            print("PARENT_AGENT_INSTANCE=" + parent_id, flush=True)
            print("CHILD_AGENT_INSTANCE=" + child_id, flush=True)
            print("CORE_BACKEND_IDENTITY=" + backend_id, flush=True)
        finally:
            for name in owned_containers(tag):
                docker("rm", "-f", "-v", name, timeout=50, allow_failure=True)
            for kind, name in reversed(created):
                if kind == "container":
                    if name in owned_containers(tag):
                        docker("rm", "-f", name, timeout=50, allow_failure=True)
                else:
                    docker("network", "rm", name, timeout=50, allow_failure=True)
            for name in owned_volumes(tag):
                require(
                    not docker("ps", "-aq", "--filter", "volume=" + name),
                    "owned volume still mounted",
                )
                docker("volume", "rm", name, timeout=50)
            require(not owned_containers(tag), "owned container remains")
            require(not owned_volumes(tag), "owned volume remains")
            print("OWNED_DOCKER_RESOURCES_REMOVED=PASS", flush=True)
    print("DISPOSABLE_DB_AND_CREDENTIALS_REMOVED=PASS", flush=True)


if __name__ == "__main__":
    main()
