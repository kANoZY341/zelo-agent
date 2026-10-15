"""Read-only, owner-only effective Space file Task eligibility projection."""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agent_instances import AgentInstanceService
from app.agent_space_status import SpecialistSpaceAccessService
from app.memory import MemoryService
from app.models import (
    AgentComputerPermission,
    AgentSpaceActivationRecord,
    AuditEvent,
    Task,
    utcnow,
)
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import AgentInstanceCreate, AgentInstanceTransition, ProjectCreate
from app.skills import default_skill_registry
from tests.conftest import AUTH
from tests.test_space_file_gateway import _fixture


def _view(database, settings, env, *, backend=True):
    return SpecialistSpaceAccessService(
        database,
        env["factory"],
        default_skill_registry(),
        settings.runtime_dir,
        computer_backend_available=backend,
    ).get(env["instance"].id)


def test_active_approved_file_route_is_read_only_and_metadata_only(database, settings):
    env = _fixture(database, settings)
    before = _counts(database)
    status = _view(database, settings, env)
    assert status.schema_version == "zelo-specialist-space-access-v1"
    assert status.agent_instance_id == env["instance"].id
    assert status.inspection_state == "current"
    assert status.projection_is_authority is False
    assert status.upstream_dot_runtime_loaded is False
    assert len(status.routes) == 1
    route = status.routes[0]
    assert route.activation_id == env["activation"].id
    assert route.intent_id == env["intent"].id
    assert route.resource_record_id == env["resource"].id
    assert route.space_id == "workroom"
    assert route.resource_id == "documents"
    assert route.owner_confirmation_recorded is True
    assert route.eligible_for_scoped_file_task is True
    assert route.reason == "eligible_for_scoped_file_task"
    assert route.automatic_worker_authority is False
    assert route.model_context_admitted is False
    assert "root_path" not in route.model_dump()
    assert "text" not in route.model_dump()
    assert before == _counts(database)


def test_specialist_space_view_reports_list_only_permission_without_implying_read(
    database, settings
):
    env = _fixture(database, settings)
    intent = env["intents"].create(
        instance_id=env["instance"].id,
        space_id="workroom",
        resource_kind="files",
        resource_id="documents",
        operations=("list",),
        ttl_seconds=600,
    )
    listing = env["resources"].activate(
        instance_id=env["instance"].id,
        intent_id=intent.id,
        resource_record_id=env["resource"].id,
    )
    status = _view(database, settings, env)
    list_route = next(route for route in status.routes if route.activation_id == listing.id)
    read_route = next(
        route for route in status.routes if route.activation_id == env["activation"].id
    )
    assert list_route.eligible_operation == "list"
    assert list_route.declared_operations == ("list",)
    assert list_route.eligible_for_scoped_file_task
    assert read_route.eligible_operation == "read"
    assert read_route.declared_operations == ("read",)


def _counts(database):
    with database.read() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in (Task, AuditEvent, AgentSpaceActivationRecord)
        )


def test_missing_computer_backend_and_disabled_file_permission_report_denials(database, settings):
    env = _fixture(database, settings)
    route = _view(database, settings, env, backend=False).routes[0]
    assert route.reason == "computer_backend_unavailable"
    assert not route.eligible_for_scoped_file_task

    with database.transaction() as session:
        permission = session.get(AgentComputerPermission, env["instance"].id)
        permission.files = False
        permission.updated_at = utcnow()
    route = _view(database, settings, env).routes[0]
    assert route.reason == "computer_file_permission_denied"
    assert not route.eligible_for_scoped_file_task
    assert (
        env["resources"]
        .get_activation(instance_id=env["instance"].id, activation_id=env["activation"].id)
        .eligible_for_mediation
        is True
    )  # Owner ledger alone is insufficient.


def test_revocation_pause_and_root_replacement_produce_denials(database, settings):
    env = _fixture(database, settings)
    registered = env["resource"]
    (env["root"] / "notes.txt").write_text("irrelevant source content")
    env["resources"].revoke(instance_id=env["instance"].id, activation_id=env["activation"].id)
    revoked = _view(database, settings, env).routes[0]
    assert revoked.reason == "owner_revoked" and not revoked.eligible_for_scoped_file_task

    env2 = _fixture(database, settings)
    env2["agents"].transition(env2["instance"].id, AgentInstanceTransition(action="pause"))
    paused = _view(database, settings, env2)
    assert paused.inspection_state == "instance_inactive"
    assert paused.routes[0].reason == "agent_or_project_inactive"

    env3 = _fixture(database, settings)
    root = env3["root"]
    root.rename(root.with_name("moved"))
    root.mkdir()
    swapped = _view(database, settings, env3).routes[0]
    assert swapped.reason == "resource_root_changed"
    assert not swapped.eligible_for_scoped_file_task
    assert registered.id != env3["resource"].id


def test_active_intent_revocation_shows_owner_denial(database, settings):
    env = _fixture(database, settings)
    env["intents"].revoke(instance_id=env["instance"].id, intent_id=env["intent"].id)
    route = _view(database, settings, env).routes[0]
    assert route.reason == "owner_revoked"
    assert route.eligible_for_scoped_file_task is False


def test_world_writable_space_root_is_not_reported_available(database, settings):
    env = _fixture(database, settings)
    env["root"].chmod(0o777)
    status = _view(database, settings, env).routes[0]
    assert status.reason == "resource_directory_unsafe"
    assert status.eligible_for_scoped_file_task is False
    # The owner registry only binds inode identity; this view additionally
    # reflects the actual file gateway's directory mode restriction.
    assert (
        env["resources"]
        .get_resource(instance_id=env["instance"].id, resource_record_id=env["resource"].id)
        .root_attested
        is True
    )


def test_expired_and_retired_resources_never_present_as_usable(database, settings, monkeypatch):
    env = _fixture(database, settings)
    env["resources"].retire(instance_id=env["instance"].id, resource_record_id=env["resource"].id)
    retired = _view(database, settings, env).routes[0]
    assert retired.reason == "resource_retired"
    assert not retired.eligible_for_scoped_file_task

    env2 = _fixture(database, settings)
    from datetime import timedelta

    from app import agent_space_status as module

    monkeypatch.setattr(module, "utcnow", lambda: utcnow() + timedelta(days=1))
    expired = _view(database, settings, env2).routes[0]
    assert expired.reason == "expired"
    assert not expired.eligible_for_scoped_file_task


def test_empty_default_profile_and_cross_instance_separation(database, settings):
    env = _fixture(database, settings)
    project = ProjectService(database).create(
        ProjectCreate(name="Other", summary="Separate active agent")
    )
    agents = AgentInstanceService(database, env["factory"], MemoryService(database))
    second = agents.create(
        AgentInstanceCreate(
            agent_id=env["instance"].agent_id,
            project_id=project.id,
        )
    )
    view = SpecialistSpaceAccessService(
        database,
        env["factory"],
        default_skill_registry(),
        settings.runtime_dir,
        computer_backend_available=True,
    )
    other = view.get(second.id)
    assert other.routes == ()
    with pytest.raises(DomainError, match="Agent instance not found"):
        view.get("00000000-0000-4000-8000-000000000099")


def test_owner_api_auth_and_no_mutation(client, database, settings):
    env = _fixture(database, settings)
    client.app.state.agent_factory = env["factory"]
    client.app.state.skill_registry = default_skill_registry()
    client.app.state.agent_computers = SimpleNamespace(configured=True)
    before = _counts(database)
    url = f"/agents/{env['instance'].id}/specialist-profile/space-access"
    assert client.get(url, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(url, headers=AUTH).status_code == 405
    result = client.get(url, headers=AUTH)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["inspection_state"] == "current"
    assert body["routes"][0]["eligible_for_scoped_file_task"] is True
    assert body["routes"][0]["automatic_worker_authority"] is False
    assert body["projection_is_authority"] is False
    assert "root_path" not in str(body)
    assert before == _counts(database)


def test_damaged_grant_fails_closed_in_projection(database, settings):
    from sqlalchemy import text

    env = _fixture(database, settings)
    # Raw DB updates must be prohibited by append-only triggers.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.execute(
                text("UPDATE agent_space_resources SET root_inode=root_inode+1 WHERE id=:id"),
                {"id": env["resource"].id},
            )
    status = _view(database, settings, env)
    assert status.routes[0].eligible_for_scoped_file_task is True
    assert (
        env["resources"]
        .get_resource(
            instance_id=env["instance"].id,
            resource_record_id=env["resource"].id,
        )
        .root_attested
        is True
    )
