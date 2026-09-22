import copy

import pytest
from pydantic import ValidationError

from app.developer_contracts import (
    WorkerArtifactIdentity,
    WorkerCredentialLocation,
    WorkerIdentity,
)


def canonical_identity() -> WorkerIdentity:
    return WorkerIdentity(
        implementation="example",
        config_version="config-v1",
        cli_version="runtime-v1",
        capability_profile="capabilities-v1",
        artifacts=(
            WorkerArtifactIdentity(name="binary", path="/trusted/example", sha256="a" * 64),
            WorkerArtifactIdentity(name="support", path="/trusted/support", sha256="b" * 64),
        ),
        credential_locations=(
            WorkerCredentialLocation(name="auth", path="/trusted/auth.json"),
        ),
    )


def test_worker_identity_has_no_codex_specific_top_level_fields():
    forbidden = {
        "executable",
        "executable_sha256",
        "code_mode_host",
        "code_mode_host_sha256",
        "auth_location",
    }
    assert forbidden.isdisjoint(WorkerIdentity.model_fields)


def test_worker_identity_artifacts_and_credentials_must_be_canonical():
    with pytest.raises(ValidationError, match="worker_artifacts_not_canonical"):
        WorkerIdentity(
            implementation="example",
            config_version="config-v1",
            cli_version="runtime-v1",
            capability_profile="capabilities-v1",
            artifacts=(
                WorkerArtifactIdentity(name="zeta", path="/zeta", sha256="a" * 64),
                WorkerArtifactIdentity(name="alpha", path="/alpha", sha256="b" * 64),
            ),
        )

    with pytest.raises(ValidationError, match="worker_artifacts_not_canonical"):
        WorkerIdentity(
            implementation="example",
            config_version="config-v1",
            cli_version="runtime-v1",
            capability_profile="capabilities-v1",
            artifacts=(
                WorkerArtifactIdentity(name="binary", path="/one", sha256="a" * 64),
                WorkerArtifactIdentity(name="binary", path="/two", sha256="b" * 64),
            ),
        )

    with pytest.raises(ValidationError, match="worker_credentials_not_canonical"):
        WorkerIdentity(
            implementation="example",
            config_version="config-v1",
            cli_version="runtime-v1",
            capability_profile="capabilities-v1",
            artifacts=(
                WorkerArtifactIdentity(name="binary", path="/one", sha256="a" * 64),
            ),
            credential_locations=(
                WorkerCredentialLocation(name="zeta", path="/zeta"),
                WorkerCredentialLocation(name="alpha", path="/alpha"),
            ),
        )

    with pytest.raises(ValidationError):
        WorkerArtifactIdentity(name="binary", path="/one", sha256="not-a-digest")


def test_v09_worker_identity_is_upgraded_to_canonical_shape():
    legacy = {
        "implementation": "codex",
        "config_version": "codex-zelo-v4-sealed",
        "cli_version": "codex-cli 0.155.1",
        "capability_profile": "legacy-profile",
        "executable": "/trusted/codex",
        "executable_sha256": "a" * 64,
        "code_mode_host": "/trusted/codex-code-mode-host",
        "code_mode_host_sha256": "b" * 64,
        "auth_location": "/trusted/auth.json",
    }
    identity = WorkerIdentity.model_validate(legacy)

    assert identity.schema_version == "zelo-worker-identity-v1"
    assert identity.artifact("executable").path == "/trusted/codex"
    assert identity.artifact("executable").sha256 == "a" * 64
    assert identity.artifact("code_mode_host").path == "/trusted/codex-code-mode-host"
    assert identity.artifact("code_mode_host").sha256 == "b" * 64
    assert identity.credential_location("auth").path == "/trusted/auth.json"

    persisted = identity.model_dump(mode="json")
    for legacy_field in (
        "executable",
        "executable_sha256",
        "code_mode_host",
        "code_mode_host_sha256",
        "auth_location",
    ):
        assert legacy_field not in persisted
    assert [item["name"] for item in persisted["artifacts"]] == [
        "code_mode_host",
        "executable",
    ]


def test_worker_identity_rejects_mixed_legacy_and_canonical_shapes():
    legacy = {
        "implementation": "codex",
        "config_version": "config",
        "cli_version": "runtime",
        "capability_profile": "profile",
        "executable": "/trusted/codex",
        "executable_sha256": "a" * 64,
        "code_mode_host": "/trusted/host",
        "code_mode_host_sha256": "b" * 64,
        "auth_location": "/trusted/auth",
        "artifacts": [
            {"name": "binary", "path": "/trusted/binary", "sha256": "c" * 64},
        ],
    }
    with pytest.raises(ValidationError, match="worker_identity_legacy_invalid"):
        WorkerIdentity.model_validate(legacy)


def test_non_codex_worker_identity_needs_no_codex_fields():
    identity = WorkerIdentity(
        implementation="opencode",
        config_version="opencode-config-v1",
        cli_version="opencode-runtime-v1",
        capability_profile="local-model-v1",
        artifacts=(
            WorkerArtifactIdentity(
                name="binary", path="/trusted/opencode", sha256="c" * 64
            ),
        ),
    )
    assert identity.implementation == "opencode"
    assert identity.credential_locations == ()
    assert identity.artifact("binary").path == "/trusted/opencode"


def test_worker_artifact_and_auth_changes_invalidate_runtime_compatibility(developer_case):
    approved = developer_case.request("test", "zelo_core", 30)

    artifacts = list(approved.worker.artifacts)
    executable_index = next(
        index for index, item in enumerate(artifacts) if item.name == "executable"
    )
    executable = artifacts[executable_index]
    artifacts[executable_index] = executable.model_copy(update={"sha256": "f" * 64})
    changed_worker = approved.worker.model_copy(update={"artifacts": tuple(artifacts)})
    changed_request = approved.model_copy(update={"worker": changed_worker})
    assert not developer_case.approval_compatible(changed_request, 30)

    artifacts = list(approved.worker.artifacts)
    host_index = next(
        index for index, item in enumerate(artifacts) if item.name == "code_mode_host"
    )
    host = artifacts[host_index]
    artifacts[host_index] = host.model_copy(update={"sha256": "e" * 64})
    changed_worker = approved.worker.model_copy(update={"artifacts": tuple(artifacts)})
    changed_request = approved.model_copy(update={"worker": changed_worker})
    assert not developer_case.approval_compatible(changed_request, 30)

    locations = list(approved.worker.credential_locations)
    auth_index = next(index for index, item in enumerate(locations) if item.name == "auth")
    auth = locations[auth_index]
    locations[auth_index] = auth.model_copy(update={"path": auth.path + "-changed"})
    changed_worker = approved.worker.model_copy(
        update={"credential_locations": tuple(locations)}
    )
    changed_request = approved.model_copy(update={"worker": changed_worker})
    assert not developer_case.approval_compatible(changed_request, 30)


def test_canonical_identity_round_trip_is_stable():
    identity = canonical_identity()
    dumped = copy.deepcopy(identity.model_dump(mode="json"))
    assert WorkerIdentity.model_validate(dumped) == identity
