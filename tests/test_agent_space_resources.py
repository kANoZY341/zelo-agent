"""Owner-scoped Space resource roots and separate non-executing confirmations."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent_factory import AgentComputerSpec, AgentFactory, AgentRegistry, coding_agent_spec
from app.agent_instances import AgentInstanceService
from app.agent_space_intents import AgentSpaceIntentService
from app.agent_space_resources import AgentSpaceResourceService
from app.memory import MemoryService
from app.models import Project, new_id
from app.policy import DomainError
from app.projects import ProjectService
from app.schemas import AgentInstanceCreate, AgentInstanceTransition, ProjectCreate
from tests.conftest import AUTH


def _fixture(database, settings, *, prepare_root=True):
    spec = coding_agent_spec().model_copy(
        update={
            "space_permissions": ("workroom",),
            "computer": AgentComputerSpec(files=True),
        }
    )
    factory = AgentFactory(AgentRegistry([spec]))
    project = ProjectService(database).create(
        ProjectCreate(name="Space activation", summary="Bounded files namespace test")
    )
    agents = AgentInstanceService(database, factory, MemoryService(database))
    instance = agents.create(AgentInstanceCreate(agent_id="coding", project_id=project.id))
    root = settings.runtime_dir / "spaces" / project.id / "workroom" / "documents"
    if prepare_root:
        root.mkdir(parents=True)
    resources = AgentSpaceResourceService(database, factory, settings.runtime_dir)
    intents = AgentSpaceIntentService(database, factory)
    return resources, intents, agents, factory, project, instance, root


def _intent(intents, instance, *, resource_id="documents", operations=("list", "read")):
    return intents.create(
        instance_id=instance.id,
        space_id="workroom",
        resource_kind="files",
        resource_id=resource_id,
        operations=operations,
        ttl_seconds=600,
    )


def _resource(resources, instance, *, resource_id="documents", operations=("list", "read")):
    return resources.register(
        instance_id=instance.id,
        space_id="workroom",
        resource_id=resource_id,
        operations=operations,
    )


def test_owner_confirms_exact_attested_resource_without_granting_worker_authority(
    database, settings
):
    resources, intents, _, _, project, instance, root = _fixture(database, settings)
    with pytest.raises(DomainError, match="Space resource not found"):
        resources.get_resource(
            instance_id=instance.id, resource_record_id="00000000-0000-4000-8000-000000000001"
        )
    intent = _intent(intents, instance)
    resource = _resource(resources, instance)
    assert resource.project_id == project.id
    assert resource.root_path == str(root)
    assert resource.root_attested and not resource.retired
    assert resource.effective_resource_access is False
    receipt = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    assert receipt.agent_instance_id == instance.id
    assert receipt.intent_sha256 == intent.intent_sha256
    assert receipt.resource_sha256 == resource.resource_sha256
    assert receipt.eligible_for_mediation is True
    assert receipt.effective_resource_access is False
    assert receipt.worker_authority is False and receipt.tool_authority is False
    assert resources.get_activation(instance_id=instance.id, activation_id=receipt.id) == receipt
    with pytest.raises(DomainError, match="already confirmed"):
        resources.activate(
            instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
        )


def test_missing_or_symlinked_space_roots_fail_closed(database, settings):
    resources, intents, _, _, _, instance, root = _fixture(database, settings, prepare_root=False)
    _intent(intents, instance)
    with pytest.raises(DomainError, match="root unavailable or unsafe"):
        _resource(resources, instance)
    root.parent.mkdir(parents=True)
    outside = settings.runtime_dir / "outside-space"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DomainError, match="root unavailable or unsafe"):
        _resource(resources, instance)
    root.unlink()
    root.mkdir()
    resource = _resource(resources, instance)
    root.rmdir()
    root.symlink_to(outside, target_is_directory=True)
    assert (
        resources.get_resource(
            instance_id=instance.id, resource_record_id=resource.id
        ).root_attested
        is False
    )


def test_symlinked_space_ancestor_and_arbitrary_resource_ids_rejected(database, settings):
    resources, _, _, _, project, instance, root = _fixture(database, settings, prepare_root=False)
    outside = settings.runtime_dir / "outside"
    outside.mkdir()
    root.parent.parent.mkdir(parents=True)
    root.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(DomainError, match="root unavailable or unsafe"):
        _resource(resources, instance)
    for invalid in ("../sibling", "nested/path", "%2e%2e", "UPPER", ".hidden"):
        with pytest.raises(DomainError, match="canonical read-only metadata"):
            _resource(resources, instance, resource_id=invalid)
    with pytest.raises(DomainError, match="canonical read-only metadata"):
        _resource(resources, instance, operations=("write",))
    with pytest.raises(DomainError, match="canonical read-only metadata"):
        _resource(resources, instance, operations=("read", "list"))
    assert project.id in str(root)


def test_activation_requires_exact_resource_identity_and_operation_subset(database, settings):
    resources, intents, _, _, _, instance, root = _fixture(database, settings)
    resource = _resource(resources, instance, operations=("read",))
    intent = _intent(intents, instance)
    with pytest.raises(DomainError, match="matching attested resource"):
        resources.activate(
            instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
        )
    limited_intent = _intent(intents, instance, operations=("read",))
    activated = resources.activate(
        instance_id=instance.id, intent_id=limited_intent.id, resource_record_id=resource.id
    )
    assert activated.eligible_for_mediation
    with pytest.raises(DomainError, match="identity already recorded"):
        _resource(resources, instance)
    root.mkdir(exist_ok=True)


def test_intent_and_activation_revocations_are_terminal(database, settings):
    resources, intents, _, _, _, instance, _ = _fixture(database, settings)
    intent = _intent(intents, instance)
    resource = _resource(resources, instance)
    grant = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    revoked = resources.revoke(instance_id=instance.id, activation_id=grant.id)
    assert revoked.revoked and not revoked.eligible_for_mediation
    assert resources.revoke(instance_id=instance.id, activation_id=grant.id) == revoked
    assert resources.get_activation(instance_id=instance.id, activation_id=grant.id) == revoked
    with pytest.raises(DomainError, match="already confirmed"):
        resources.activate(
            instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
        )

    second_intent = _intent(intents, instance)
    second = resources.activate(
        instance_id=instance.id, intent_id=second_intent.id, resource_record_id=resource.id
    )
    intents.revoke(instance_id=instance.id, intent_id=second_intent.id)
    stale = resources.get_activation(instance_id=instance.id, activation_id=second.id)
    assert stale.revoked and not stale.eligible_for_mediation
    assert not stale.effective_resource_access


def test_resource_retirement_is_permanent_and_invalidates_old_confirmation(database, settings):
    resources, intents, _, _, _, instance, _ = _fixture(database, settings)
    resource = _resource(resources, instance)
    intent = _intent(intents, instance)
    activation = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    retired = resources.retire(instance_id=instance.id, resource_record_id=resource.id)
    assert retired.retired
    assert resources.retire(instance_id=instance.id, resource_record_id=resource.id) == retired
    assert not resources.get_activation(
        instance_id=instance.id, activation_id=activation.id
    ).eligible_for_mediation
    new_intent = _intent(intents, instance)
    with pytest.raises(DomainError, match="matching attested resource"):
        resources.activate(
            instance_id=instance.id, intent_id=new_intent.id, resource_record_id=resource.id
        )


def test_project_and_agent_lifecycle_changes_invalidate_eligibility(database, settings):
    resources, intents, agents, _, project, instance, _ = _fixture(database, settings)
    resource = _resource(resources, instance)
    intent = _intent(intents, instance)
    grant = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    agents.transition(instance.id, AgentInstanceTransition(action="pause"))
    paused = resources.get_activation(instance_id=instance.id, activation_id=grant.id)
    assert not paused.eligible_for_mediation
    revoked = resources.revoke(instance_id=instance.id, activation_id=grant.id)
    assert revoked.revoked and not revoked.eligible_for_mediation
    agents.transition(instance.id, AgentInstanceTransition(action="resume"))
    refreshed = resources.get_activation(instance_id=instance.id, activation_id=grant.id)
    assert not refreshed.eligible_for_mediation
    with database.transaction() as session:
        session.get(Project, project.id).status = "paused"
    with pytest.raises(DomainError, match="project must be active"):
        resources.get_resource(instance_id=instance.id, resource_record_id=resource.id)
    retired = resources.retire(instance_id=instance.id, resource_record_id=resource.id)
    assert retired.retired


def test_pause_resume_between_intent_review_and_confirmation_denied(
    database, settings, monkeypatch
):
    resources, intents, agents, _, _, instance, _ = _fixture(database, settings)
    registered = _resource(resources, instance)
    intent = _intent(intents, instance)
    review = resources.intents.get

    def pause_between_reviews(*, instance_id, intent_id):
        approved = review(instance_id=instance_id, intent_id=intent_id)
        agents.transition(instance.id, AgentInstanceTransition(action="pause"))
        agents.transition(instance.id, AgentInstanceTransition(action="resume"))
        return approved

    monkeypatch.setattr(resources.intents, "get", pause_between_reviews)
    with pytest.raises(DomainError, match="prerequisites changed"):
        resources.activate(
            instance_id=instance.id,
            intent_id=intent.id,
            resource_record_id=registered.id,
        )


def test_bad_binding_does_not_cross_projects(database, settings):
    resources, intents, _, factory, _, instance, _ = _fixture(database, settings)
    record = _resource(resources, instance)
    intent = _intent(intents, instance)
    second = ProjectService(database).create(
        ProjectCreate(name="Other project", summary="Must not see first Space")
    )
    agents = AgentInstanceService(database, factory, MemoryService(database))
    foreign = agents.create(AgentInstanceCreate(agent_id="coding", project_id=second.id))
    with pytest.raises(DomainError, match="Space resource not found"):
        resources.get_resource(instance_id=foreign.id, resource_record_id=record.id)
    with pytest.raises(DomainError, match="Space intent not found"):
        resources.activate(
            instance_id=foreign.id, intent_id=intent.id, resource_record_id=record.id
        )
    with pytest.raises(DomainError, match="Space intent not found"):
        intents.get(instance_id=foreign.id, intent_id=intent.id)


def test_expiration_is_rechecked_and_live_grants_never_returned(database, settings, monkeypatch):
    resources, intents, _, _, _, instance, _ = _fixture(database, settings)
    resource = _resource(resources, instance)
    intent = _intent(intents, instance)
    grant = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    monkeypatch.setattr(
        "app.agent_space_intents.utcnow", lambda: datetime.now(UTC) + timedelta(days=1)
    )
    monkeypatch.setattr(
        "app.agent_space_resources.utcnow", lambda: datetime.now(UTC) + timedelta(days=1)
    )
    stale = resources.get_activation(instance_id=instance.id, activation_id=grant.id)
    assert not stale.eligible_for_mediation and not stale.effective_resource_access
    with pytest.raises(DomainError, match="no longer eligible"):
        resources.activate(
            instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
        )


def test_append_only_schema_blocks_tampering_replace_and_resurrection(database, settings):
    resources, intents, _, _, _, instance, _ = _fixture(database, settings)
    resource = _resource(resources, instance)
    intent = _intent(intents, instance)
    activation = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    resources.revoke(instance_id=instance.id, activation_id=activation.id)
    resources.retire(instance_id=instance.id, resource_record_id=resource.id)
    for table, key, id_ in (
        ("agent_space_resources", "id", resource.id),
        ("agent_space_resource_retirements", "resource_record_id", resource.id),
        ("agent_space_activations", "id", activation.id),
        ("agent_space_activation_revocations", "activation_id", activation.id),
    ):
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(
                    text(f"DELETE FROM {table} WHERE {key}=:identifier"),
                    {"identifier": id_},
                )
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(
                    text(
                        f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} "
                        f"WHERE {key}=:identifier"
                    ),
                    {"identifier": id_},
                )
    assert not resources.get_activation(
        instance_id=instance.id, activation_id=activation.id
    ).eligible_for_mediation


def test_secondary_unique_conflict_cannot_replace_immutable_registry_or_grant(database, settings):
    resources, intents, _, _, _, instance, _ = _fixture(database, settings)
    resource = _resource(resources, instance)
    intent = _intent(intents, instance)
    activation = resources.activate(
        instance_id=instance.id, intent_id=intent.id, resource_record_id=resource.id
    )
    for table, original_id in (
        ("agent_space_resources", resource.id),
        ("agent_space_activations", activation.id),
    ):
        with database.read() as session:
            row = dict(
                session.execute(text(f"SELECT * FROM {table} WHERE id=:id"), {"id": original_id})
                .mappings()
                .one()
            )
        # Fresh PK, identical secondary unique key. Default SQLite REPLACE
        # otherwise deletes the original without executing DELETE triggers.
        replacement_id = new_id()
        row["id"] = replacement_id
        columns = ",".join(row)
        placeholders = ",".join(f":{field}" for field in row)
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(
                    text(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"),
                    row,
                )
        with database.read() as session:
            assert (
                session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE id=:id"), {"id": original_id}
                )
                == 1
            )
            assert (
                session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE id=:id"), {"id": replacement_id}
                )
                == 0
            )
    assert resources.get_activation(
        instance_id=instance.id, activation_id=activation.id
    ).eligible_for_mediation


def test_http_requires_owner_and_never_returns_effective_file_authority(client, database, settings):
    resources, intents, _, factory, _, instance, _ = _fixture(database, settings)
    client.app.state.agent_factory = factory
    instance_url = f"/agents/{instance.id}"
    resource_url = f"{instance_url}/space-resources"
    payload = {
        "space_id": "workroom",
        "resource_id": "documents",
        "resource_kind": "files",
        "operations": ["list", "read"],
    }
    assert (
        client.post(resource_url, headers={"Authorization": "Bearer bad"}, json=payload).status_code
        == 401
    )
    assert (
        client.post(resource_url, headers=AUTH, json={**payload, "root_path": "/"}).status_code
        == 422
    )
    created = client.post(resource_url, headers=AUTH, json=payload)
    assert created.status_code == 201, created.text
    resource = created.json()
    assert resource["effective_resource_access"] is False
    intent = _intent(intents, instance)
    activate = client.post(
        f"{instance_url}/space-intents/{intent.id}/activate",
        headers=AUTH,
        json={"resource_record_id": resource["id"]},
    )
    assert activate.status_code == 201, activate.text
    receipt = activate.json()
    assert receipt["eligible_for_mediation"] is True
    assert receipt["effective_resource_access"] is False
    assert receipt["worker_authority"] is False
    assert receipt["tool_authority"] is False
    assert (
        client.get(
            f"{instance_url}/space-activations/{receipt['id']}",
            headers={"Authorization": "Bearer bad"},
        ).status_code
        == 401
    )
    revoked = client.post(f"{instance_url}/space-activations/{receipt['id']}/revoke", headers=AUTH)
    assert revoked.status_code == 200
    assert revoked.json()["eligible_for_mediation"] is False
    assert resources.get_activation(instance_id=instance.id, activation_id=receipt["id"]).revoked
