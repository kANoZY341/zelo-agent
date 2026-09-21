"""Trusted, deterministic developer worker registration and selection."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.developer_contracts import (
    DeveloperWorker,
    WorkerDescriptor,
    WorkerSelectionSnapshot,
)
from app.developer_process import DeveloperError
from app.worker_authority import (
    WorkerAuthorityProfile,
    authority_policy_version,
    authority_profile,
    authority_snapshot_matches,
)

REGISTRY_SCHEMA_VERSION = "zelo-worker-registry-v2"


def _registry_value(value: str, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError(f"{name}_invalid")
    return value


def _registry_version(workers: Mapping[str, "RegisteredWorker"]) -> str:
    payload = {
        "schema": REGISTRY_SCHEMA_VERSION,
        "authority_policy_version": authority_policy_version(),
        "workers": [
            {
                "descriptor": workers[worker_id].descriptor.model_dump(mode="json"),
                "authority_profile": workers[worker_id].authority_profile.name,
            }
            for worker_id in sorted(workers)
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RegisteredWorker:
    descriptor: WorkerDescriptor
    worker: DeveloperWorker
    authority_profile: WorkerAuthorityProfile


class WorkerRegistry:
    """A process-local registry populated only by trusted application assembly."""

    def __init__(
        self,
        registrations: Iterable[tuple[WorkerDescriptor, DeveloperWorker]] = (),
        *,
        routing_policy: str = "priority-then-worker-id-v1",
    ) -> None:
        workers: dict[str, RegisteredWorker] = {}
        for descriptor, worker in registrations:
            trusted_descriptor = WorkerDescriptor.model_validate(descriptor.model_dump(mode="json"))
            if trusted_descriptor.worker_id in workers:
                raise ValueError("duplicate_worker_id")
            profile = authority_profile(trusted_descriptor.authority)
            workers[trusted_descriptor.worker_id] = RegisteredWorker(
                trusted_descriptor, worker, profile
            )
        self._version = _registry_version(workers)
        self._routing_policy = _registry_value(routing_policy, "routing_policy")
        self._workers: Mapping[str, RegisteredWorker] = MappingProxyType(workers)

    @property
    def version(self) -> str:
        return self._version

    @property
    def routing_policy(self) -> str:
        return self._routing_policy

    def list(self) -> tuple[WorkerDescriptor, ...]:
        return tuple(
            registration.descriptor
            for registration in sorted(
                self._workers.values(), key=lambda item: item.descriptor.worker_id
            )
        )

    def get(self, worker_id: str) -> RegisteredWorker | None:
        return self._workers.get(worker_id)

    def eligible(self, role: str, capabilities: Iterable[str]) -> tuple[RegisteredWorker, ...]:
        required = frozenset(capabilities)
        return tuple(
            registration
            for registration in self._workers.values()
            if registration.descriptor.enabled
            and registration.descriptor.role == role
            and required.issubset(registration.descriptor.capabilities)
        )


class Supervisor:
    def __init__(self, registry: WorkerRegistry) -> None:
        self.registry = registry

    def select(
        self, *, role: str, capabilities: Iterable[str]
    ) -> tuple[RegisteredWorker, WorkerSelectionSnapshot]:
        eligible = self.registry.eligible(role, capabilities)
        if not eligible:
            raise DeveloperError("no_eligible_worker")
        selected = min(
            eligible,
            key=lambda item: (-item.descriptor.priority, item.descriptor.worker_id),
        )
        return selected, WorkerSelectionSnapshot(
            descriptor=selected.descriptor,
            registry_version=self.registry.version,
            routing_policy=self.registry.routing_policy,
            authority_profile=selected.authority_profile.name,
            authority_policy_version=authority_policy_version(),
        )

    def matches(self, snapshot: WorkerSelectionSnapshot) -> bool:
        current = self.registry.get(snapshot.descriptor.worker_id)
        return (
            current is not None
            and snapshot.registry_version == self.registry.version
            and snapshot.routing_policy == self.registry.routing_policy
            and current.descriptor == snapshot.descriptor
            and current.descriptor.enabled
            and current.authority_profile.name == snapshot.authority_profile
            and authority_snapshot_matches(
                current.descriptor.authority,
                snapshot.authority_profile,
                snapshot.authority_policy_version,
            )
        )


def descriptor_for_worker(
    worker: DeveloperWorker, implementation: str, worker_id: str = "codex_core"
) -> WorkerDescriptor:
    return WorkerDescriptor(
        worker_id=worker_id,
        role="developer",
        capabilities=("zelo_core_development",),
        authority=("workspace-write",),
        implementation=implementation,
        priority=100,
        max_concurrency=1,
    )
