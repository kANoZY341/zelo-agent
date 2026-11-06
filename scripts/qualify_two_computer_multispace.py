"""Disposable native two-Space check with a test-only Agent registry.

The trusted production Computer Specialist remains limited to workroom.
This case opts two disposable AgentSpecs into private-room only inside its
isolated Core database and Docker namespace.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.agent_computer_service import AgentComputerService
from app.agent_computers import OpenBotComputerBackend
from app.agent_delegations import AgentDelegationService
from app.agent_execution import AgentExecutionService
from app.agent_factory import AgentFactory, AgentRegistry
from app.agent_instances import AgentInstanceService
from app.agent_space_resources import AgentSpaceResourceService
from app.campaign_control import CampaignControlService
from app.campaigns import CampaignService
from app.computer_observer import computer_observer_spec, space_read_manager_spec
from app.computer_worker import ComputerRuntime
from app.database import Database
from app.memory import MemoryService
from app.policy import DomainError, State
from app.projects import ProjectService
from app.runner import Runner
from app.schemas import (
    AgentComputerPermissionPatch,
    AgentDelegationCreate,
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
from scripts.qualify_two_computer_space import (
    CHILD_DIGEST,
    ROOT,
    IsolationSettings,
    docker,
    require,
    run_qualification,
)

FIXTURES = {
    "workroom": ("notes.txt", "workroom-only native evidence"),
    "private-room": ("private-notes.txt", "private-room-only native evidence"),
}


async def qualify_multispace(
    settings: IsolationSettings, instance_ids: list[str]
) -> tuple[str, str, str]:
    db = Database(settings.database_path)
    try:
        db.migrate()
        backend = OpenBotComputerBackend(settings)
        parent_spec = space_read_manager_spec().model_copy(
            update={"space_permissions": ("private-room", "workroom")}
        )
        child_spec = computer_observer_spec().model_copy(
            update={"space_permissions": ("private-room", "workroom")}
        )
        factory = AgentFactory(AgentRegistry((parent_spec, child_spec)))
        agents = AgentInstanceService(db, factory, MemoryService(db))
        projects = ProjectService(db)
        project = projects.create(
            ProjectCreate(name="Native two-Space pair", summary="Disposable host proof")
        )
        goal = projects.create_goal(
            project.id,
            GoalCreate(
                title="Prove independent Space identity",
                spec=GoalSpec(
                    objective="Read only the matching owner-approved Space fixture",
                    success_criteria=["exact per-Space content and listing"],
                    constraints=["mixed approvals deny before Task creation"],
                ),
            ),
        )
        campaign = CampaignService(db).create(
            goal.id,
            CampaignCreate(title="Native two-Space pair", summary="Disposable qualification"),
        )
        parent = agents.create(
            AgentInstanceCreate(agent_id=parent_spec.agent_id, project_id=project.id)
        )
        child = agents.create(
            AgentInstanceCreate(agent_id=child_spec.agent_id, project_id=project.id)
        )
        require(parent.id != child.id, "AgentInstance identities collapsed")
        instance_ids.extend((parent.id, child.id))
        service = AgentComputerService(db, factory, backend)
        for instance in (parent, child):
            try:
                await service.start(instance.id)
            except DomainError as error:
                require(error.status_code == 409, "Computer ungranted refusal changed")
            else:
                raise RuntimeError("Computer started without owner grant")
            await service.patch_permissions(
                instance.id,
                AgentComputerPermissionPatch(enabled=True, browser=True, files=True),
            )
            started = await service.start(instance.id)
            require(started.state == "running", "native Computer did not start")
            existing = await backend.existing(instance.id)
            require(
                existing is not None and existing.agent_id == instance.id,
                "backend identity drift",
            )
            require((await backend.control(instance.id)).holder == "bot", "Computer not bot-held")
        names = [f"{settings.computer_namespace}-computer-{item.id}" for item in (parent, child)]
        require(len(set(names)) == 2, "Computer child names collapsed")
        for name in names:
            info = json.loads(docker("inspect", name))[0]
            require(info["Image"] == CHILD_DIGEST, "Computer image drift")
            require(info["State"]["Running"], "Computer container stopped")
            require(
                tuple(info["NetworkSettings"]["Networks"]) == (settings.computer_network_name,),
                "Computer escaped private network",
            )
            require(
                not any(info["NetworkSettings"]["Ports"].values()),
                "Computer exposed a host port",
            )
        print("TWO_REAL_PRIVATE_COMPUTERS=PASS", flush=True)

        runtime = ComputerRuntime(db, backend)
        supervisor = Supervisor(WorkerRegistry(((descriptor_for_computer_worker(), runtime),)))
        tasks = TaskService(db, settings, agent_factory=factory, computer_supervisor=supervisor)
        submit = AgentExecutionService(db, factory, tasks)
        resources = AgentSpaceResourceService(db, factory, settings.runtime_dir)
        approvals = {}
        for space_id, (filename, content) in FIXTURES.items():
            resource = resources.provision(
                instance_id=parent.id,
                space_id=space_id,
                resource_id="documents",
                operations=("list", "read"),
                files=((filename, content),),
            )
            receipts = []
            for instance in (parent, child):
                intent = resources.intents.create(
                    instance_id=instance.id,
                    space_id=space_id,
                    resource_kind="files",
                    resource_id="documents",
                    operations=("list", "read"),
                    ttl_seconds=600,
                )
                receipts.append(
                    resources.activate(
                        instance_id=instance.id,
                        intent_id=intent.id,
                        resource_record_id=resource.id,
                    )
                )
            require(receipts[0].id != receipts[1].id, "parent and child approval collapsed")
            approvals[space_id] = tuple(receipts)
        require(
            approvals["workroom"][0].resource_record_id
            != approvals["private-room"][0].resource_record_id,
            "Space resources collapsed",
        )

        controls = CampaignControlService(db)
        controls.set_budget(campaign.id, "agent_steps", CampaignBudgetSet(limit_units=100))
        budget = controls.reserve_budget(
            campaign.id,
            CampaignBudgetReserve(
                resource="agent_steps",
                owner_id=parent.id,
                idempotency_key="native-multispace-budget",
                units=8,
                ttl_seconds=3600,
            ),
        )
        delegation = AgentDelegationService(db, factory, controls).create(
            parent.id,
            AgentDelegationCreate(
                child_instance_id=child.id,
                campaign_id=campaign.id,
                budget_reservation_id=budget.id,
                idempotency_key="native-multispace-delegation",
                objective="Read only owner-approved Space fixtures",
                allowed_task_kinds=["computer_run"],
                required_capabilities=["computer_read"],
                max_depth=1,
                budget_resource="agent_steps",
                budget_units=8,
            ),
        )
        runner = Runner(
            tasks,
            computer_supervisor=supervisor,
            space_file_gateway=SpaceFileReadGateway(
                db, factory, settings.runtime_dir, runtime, task_service=tasks
            ),
        )

        def request(
            action: str, *, parent_space: str, child_space: str, path: str = ""
        ) -> AgentTaskCreate:
            return AgentTaskCreate.model_validate(
                {
                    "delegation_id": delegation.id,
                    "task": {
                        "kind": "computer_run",
                        "campaign_id": campaign.id,
                        "payload": {
                            "action": action,
                            "path": path if action == "files_read" else "",
                            "space_activation_id": approvals[child_space][1].id,
                            "parent_space_activation_id": approvals[parent_space][0].id,
                        },
                    },
                }
            )

        for action in ("files_read", "files_list"):
            for parent_space, child_space in (
                ("workroom", "private-room"),
                ("private-room", "workroom"),
            ):
                before = tasks.list_tasks(limit=100, offset=0).total
                try:
                    submit.submit(
                        child.id,
                        request(
                            action,
                            parent_space=parent_space,
                            child_space=child_space,
                            path=FIXTURES[child_space][0],
                        ),
                        None,
                    )
                except DomainError as error:
                    require(
                        error.status_code == 409
                        and error.message == "computer_space_activation_invalid",
                        "mixed Space approval refused for unrelated reason",
                    )
                else:
                    raise RuntimeError("mixed Space approvals admitted a Task")
                require(
                    tasks.list_tasks(limit=100, offset=0).total == before,
                    "mixed Space approval created a Task",
                )
        print("MIXED_SPACE_READ_LIST_REFUSED_BEFORE_TASK=PASS", flush=True)

        async def run_matched(action: str, space_id: str) -> None:
            filename, content = FIXTURES[space_id]
            task, created = submit.submit(
                child.id,
                request(action, parent_space=space_id, child_space=space_id, path=filename),
                None,
            )
            require(created and task.status == State.PENDING, "matched Space Task not admitted")
            claimed = tasks.claim()
            require(claimed is not None and claimed.task_id == task.id, "wrong Task claimed")
            await runner._run(claimed)
            done = tasks.get(task.id)
            require(done.status == State.SUCCEEDED, f"matched Space Task failed: {done.error}")
            if action == "files_read":
                require(done.result["files_read"]["text"] == content, "Space content crossed")
                event = "space_file_read_completed"
            else:
                paths = [row["path"] for row in done.result["files_list"]["entries"]]
                require(paths == [filename], "Space listing crossed")
                event = "space_file_list_completed"
            require(
                event in [item.event for item in tasks.events(task.id, 100, 0)],
                "matched Space completion audit missing",
            )

        for space_id in FIXTURES:
            for action in ("files_read", "files_list"):
                await run_matched(action, space_id)
        print("MATCHED_NATIVE_TWO_SPACE_READ_LIST=PASS", flush=True)

        queued_private = []
        for action in ("files_read", "files_list"):
            task, created = submit.submit(
                child.id,
                request(
                    action,
                    parent_space="private-room",
                    child_space="private-room",
                    path=FIXTURES["private-room"][0],
                ),
                None,
            )
            require(created and task.status == State.PENDING, "private Task not queued")
            queued_private.append((task.id, action))

        resources.revoke(
            instance_id=parent.id,
            activation_id=approvals["private-room"][0].id,
        )
        for task_id, action in queued_private:
            require(tasks.claim() is None, "withdrawn private approval was claimed")
            denied = tasks.get(task_id)
            require(
                denied.status == State.CANCELLED
                and denied.error == "computer_execution_spec_invalid"
                and denied.attempts == [],
                "withdrawn private approval did not cancel queued Task",
            )
            events = [item.event for item in tasks.events(task_id, 100, 0)]
            completed_event = (
                "space_file_read_completed"
                if action == "files_read"
                else "space_file_list_completed"
            )
            require(
                "execution_denied" in events and completed_event not in events,
                "withdrawn private approval audit changed",
            )
        print("QUEUED_PRIVATE_READ_LIST_CANCELLED_AT_CLAIM=PASS", flush=True)

        before = tasks.list_tasks(limit=100, offset=0).total
        try:
            submit.submit(
                child.id,
                request(
                    "files_read",
                    parent_space="private-room",
                    child_space="private-room",
                    path=FIXTURES["private-room"][0],
                ),
                None,
            )
        except DomainError as error:
            require(
                error.status_code == 409 and error.message == "computer_space_activation_invalid",
                "private Space withdrawal refused for unrelated reason",
            )
        else:
            raise RuntimeError("withdrawn private approval admitted a Task")
        require(tasks.list_tasks(limit=100, offset=0).total == before, "withdrawal created a Task")
        await run_matched("files_read", "workroom")
        print("PRIVATE_WITHDRAWAL_PRESERVED_WORKROOM=PASS", flush=True)
        for instance in (parent, child):
            stopped = await service.stop(instance.id)
            require(stopped.state == "stopped", "Computer stop failed")
        return parent.id, child.id, backend.identity
    finally:
        db.close()


def main() -> None:
    (ROOT / "runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r1-docker-config-", dir=ROOT / "runtime") as path:
        run_qualification(Path(path), qualify_multispace)


if __name__ == "__main__":
    main()
