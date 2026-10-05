"""Agent-scoped persistent-computer egress authorization tests."""

import hashlib
import hmac
import json

import pytest

from app.agent_factory import AgentComputerSpec, AgentFactory, AgentRegistry, AgentSpec
from app.agent_instances import AgentInstanceService
from app.api_contracts import ApiEndpointRegistry
from app.computer_egress import ComputerEgressError, ComputerEgressPolicy
from app.config import Settings
from app.memory import MemoryService
from app.models import AgentComputerPermission, AgentInstance, utcnow
from app.projects import ProjectService
from app.schemas import AgentInstanceCreate, ProjectCreate


def spec(
    agent_id: str = "navigator",
    *,
    version: int = 1,
    endpoint_ids: tuple[str, ...] = ("public_site",),
    navigate: bool = True,
) -> AgentSpec:
    capabilities = ("computer_navigate", "computer_read") if navigate else ("computer_read",)
    return AgentSpec(
        agent_id=agent_id,
        version=version,
        role=agent_id,
        objective="Use bounded persistent-computer egress.",
        capabilities=capabilities,
        allowed_task_kinds=("computer_run",),
        memory_namespace=f"agent/{agent_id}",
        computer=AgentComputerSpec(
            browser=True,
            files=True,
            human_takeover=True,
            browser_endpoint_ids=endpoint_ids,
        ),
        lifecycle="active",
    )


def settings(**overrides) -> Settings:
    values = {
        "computer_supervisor_url": "http://127.0.0.1:4312",
        "computer_supervisor_token": "s" * 24,
        "computer_token": "c" * 24,
        "computer_browser_endpoints": (
            "public_site=https://example.com",
            "second_site=https://example.org",
        ),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def setup_policy(database, *, agents=None):
    agents = tuple(agents or (spec(),))
    factory = AgentFactory(AgentRegistry(agents))
    instances = AgentInstanceService(database, factory, MemoryService(database))
    endpoints = ApiEndpointRegistry.from_entries(settings().computer_browser_endpoints)
    policy = ComputerEgressPolicy(database, factory, endpoints, settings())
    project = ProjectService(database).create(
        ProjectCreate(name="Egress", summary="Persistent-computer egress test.")
    )
    created = {
        item.agent_id: instances.create(
            AgentInstanceCreate(agent_id=item.agent_id, project_id=project.id)
        )
        for item in agents
    }
    return factory, policy, created


def grant_browser(database, instance_id: str) -> None:
    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance_id)
        assert row is not None
        row.enabled = True
        row.browser = True
        row.updated_at = utcnow()
        session.flush()


def test_endpoint_grants_are_canonical_and_require_browser_and_capability():
    with pytest.raises(ValueError, match="computer_browser_endpoints_not_canonical"):
        AgentComputerSpec(
            browser=True,
            browser_endpoint_ids=("z", "a"),
        )

    with pytest.raises(ValueError, match="computer_browser_endpoints_require_browser"):
        AgentComputerSpec(
            files=True,
            browser_endpoint_ids=("public_site",),
        )

    missing_capability = spec(navigate=False)
    policy = ComputerEgressPolicy(
        database=object(),
        factory=AgentFactory(AgentRegistry((missing_capability,))),
        endpoints=ApiEndpointRegistry.from_entries(("public_site=https://example.com",)),
        settings=settings(),
    )
    with pytest.raises(ValueError, match="computer_navigation_capability_required"):
        policy.validate_agents((missing_capability,))


def test_empty_endpoint_extension_preserves_legacy_computer_registry_fingerprint():
    legacy_shape = spec(endpoint_ids=(), navigate=False)
    registry = AgentRegistry((legacy_shape,))

    payload = legacy_shape.model_dump(mode="json")
    payload.pop("organizational_level", None)
    payload.pop("instructions", None)
    payload.pop("skill_ids", None)
    payload.pop("space_permissions", None)
    payload.pop("skill_learning", None)
    payload["computer"].pop("browser_endpoint_ids", None)
    expected = hashlib.sha256(
        json.dumps(
            {
                "schema": "zelo-agent-registry-v1",
                "agents": [payload],
                "departments": [],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()

    assert registry.version == expected


def test_material_endpoint_grant_changes_agent_registry_identity():
    baseline = AgentRegistry((spec(endpoint_ids=(), navigate=False),))
    changed = AgentRegistry((spec(endpoint_ids=("public_site",), navigate=True),))
    assert baseline.version != changed.version


def test_settings_validate_computer_browser_endpoint_registry():
    configured = settings()
    assert configured.computer_browser_endpoints == (
        "public_site=https://example.com",
        "second_site=https://example.org",
    )

    with pytest.raises(ValueError):
        settings(computer_browser_endpoints=("bad=http://example.com",))


def test_policy_authenticates_agent_specific_hmac_and_rejects_cross_agent(database):
    first = spec("first")
    second = spec("second")
    _factory, policy, instances = setup_policy(database, agents=(first, second))
    grant_browser(database, instances["first"].id)
    grant_browser(database, instances["second"].id)

    first_password = policy.derive_password(instances["first"].id)
    expected = hmac.new(
        ("c" * 24).encode("ascii"),
        f"zelo-egress:{instances['first'].id}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert first_password == expected

    policy.authenticate(instances["first"].id, first_password)
    with pytest.raises(ComputerEgressError, match="computer_egress_auth_denied"):
        policy.authenticate(instances["second"].id, first_password)


def test_authorize_binds_current_agent_permission_and_endpoint(database):
    factory, policy, instances = setup_policy(database)
    instance = instances["navigator"]
    grant_browser(database, instance.id)

    authorization = policy.authorize(
        instance.id,
        host="example.com",
        port=443,
    )

    assert authorization.agent_instance_id == instance.id
    assert authorization.agent_registry_version == factory.registry.version
    assert authorization.endpoint.endpoint.endpoint_id == "public_site"
    assert authorization.endpoint.endpoint.origin == "https://example.com"
    assert len(authorization.permission_version) == 64
    assert authorization.policy_version == policy.policy_version

    with pytest.raises(ComputerEgressError, match="computer_egress_target_denied"):
        policy.authorize(instance.id, host="example.org", port=443)


def test_authorize_credentials_combines_auth_and_policy(database):
    _factory, policy, instances = setup_policy(database)
    instance = instances["navigator"]
    grant_browser(database, instance.id)

    result = policy.authorize_credentials(
        instance.id,
        policy.derive_password(instance.id),
        host="example.com",
        port=443,
    )
    assert result.endpoint.endpoint.endpoint_id == "public_site"

    with pytest.raises(ComputerEgressError, match="computer_egress_auth_denied"):
        policy.authorize_credentials(
            instance.id,
            "0" * 64,
            host="example.com",
            port=443,
        )


def test_permission_revocation_and_agent_pause_fail_closed(database):
    _factory, policy, instances = setup_policy(database)
    instance = instances["navigator"]
    grant_browser(database, instance.id)
    policy.authorize(instance.id, host="example.com", port=443)

    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance.id)
        assert row is not None
        row.browser = False
        row.updated_at = utcnow()
    with pytest.raises(ComputerEgressError, match="computer_egress_permission_denied"):
        policy.authorize(instance.id, host="example.com", port=443)

    with database.transaction() as session:
        row = session.get(AgentComputerPermission, instance.id)
        assert row is not None
        row.browser = True
        row.updated_at = utcnow()
        stored = session.get(AgentInstance, instance.id)
        assert stored is not None
        stored.status = "paused"
        stored.updated_at = utcnow()

    with pytest.raises(ComputerEgressError, match="computer_egress_agent_inactive"):
        policy.authorize(instance.id, host="example.com", port=443)


def test_stale_agent_registry_fails_closed(database):
    _factory, policy, instances = setup_policy(database)
    instance = instances["navigator"]
    grant_browser(database, instance.id)
    policy.authorize(instance.id, host="example.com", port=443)

    changed = spec(version=2)
    policy.factory = AgentFactory(AgentRegistry((changed,)))
    with pytest.raises(ComputerEgressError, match="computer_egress_agent_stale"):
        policy.authorize(instance.id, host="example.com", port=443)


def test_policy_validation_rejects_missing_grants_and_registry_entries():
    endpoints = ApiEndpointRegistry.from_entries(("public_site=https://example.com",))

    missing_grants = spec(endpoint_ids=(), navigate=True)
    policy = ComputerEgressPolicy(
        database=object(),
        factory=AgentFactory(AgentRegistry((missing_grants,))),
        endpoints=endpoints,
        settings=settings(),
    )
    with pytest.raises(ValueError, match="computer_navigation_grant_required"):
        policy.validate_agents((missing_grants,))

    missing_endpoint = spec(endpoint_ids=("missing",), navigate=True)
    policy = ComputerEgressPolicy(
        database=object(),
        factory=AgentFactory(AgentRegistry((missing_endpoint,))),
        endpoints=endpoints,
        settings=settings(),
    )
    with pytest.raises(ValueError, match="computer_navigation_endpoint_unavailable"):
        policy.validate_agents((missing_endpoint,))


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("Example.com", 443),
        (" example.com", 443),
        ("example.com ", 443),
        ("", 443),
        ("example.com", 0),
        ("example.com", 65536),
    ],
)
def test_authorize_rejects_noncanonical_target(database, host, port):
    _factory, policy, instances = setup_policy(database)
    instance = instances["navigator"]
    grant_browser(database, instance.id)

    with pytest.raises(ComputerEgressError, match="computer_egress_target_invalid"):
        policy.authorize(instance.id, host=host, port=port)


def test_application_exposes_inert_egress_policy_without_enabling_navigation(client):
    policy = client.app.state.computer_egress_policy
    registry = client.app.state.computer_browser_endpoint_registry

    assert isinstance(policy, ComputerEgressPolicy)
    assert registry.list() == ()
    assert policy.endpoints is registry

    with pytest.raises(ComputerEgressError, match="computer_egress_not_configured"):
        policy.derive_password("00000000-0000-4000-8000-000000000001")


def test_settings_require_rfc1918_proxy_bind_and_complete_backend():
    with pytest.raises(ValueError, match="canonical RFC1918"):
        Settings(
            _env_file=None,
            computer_egress_proxy_host="8.8.8.8",
        )

    with pytest.raises(ValueError, match="complete computer backend"):
        Settings(
            _env_file=None,
            computer_egress_proxy_host="172.30.250.1",
        )

    configured = Settings(
        _env_file=None,
        computer_supervisor_url="http://127.0.0.1:4312",
        computer_supervisor_token="s" * 24,
        computer_token="c" * 24,
        computer_egress_proxy_host="172.30.250.1",
        computer_egress_proxy_port=4390,
    )
    assert configured.computer_egress_proxy_host == "172.30.250.1"
    assert configured.computer_egress_proxy_port == 4390
