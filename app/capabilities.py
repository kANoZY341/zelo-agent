"""Trusted capability vocabulary for the v0.15 capability ecosystem."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.developer_contracts import WorkerDescriptor

CAPABILITY_SPEC_SCHEMA = "zelo-capability-spec-v1"
CAPABILITY_REGISTRY_SCHEMA = "zelo-capability-registry-v1"

CapabilityId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
CapabilitySubject = Literal["agent", "worker"]
CapabilityLifecycle = Literal["draft", "active", "retired"]


class CapabilityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilitySpec(CapabilityContract):
    """Trusted versioned capability definition; metadata, never authority."""

    schema_version: Literal["zelo-capability-spec-v1"] = CAPABILITY_SPEC_SCHEMA
    capability_id: CapabilityId
    version: int = Field(default=1, ge=1, le=1_000_000)
    description: str = Field(min_length=1, max_length=512)
    subjects: tuple[CapabilitySubject, ...] = Field(min_length=1, max_length=2)
    lifecycle: CapabilityLifecycle = "draft"

    @model_validator(mode="after")
    def _canonicalize(self):
        if self.subjects != tuple(sorted(set(self.subjects))):
            raise ValueError("capability_subjects_not_canonical")
        return self


class CapabilityRegistrySnapshot(CapabilityContract):
    registry_schema: Literal["zelo-capability-registry-v1"] = CAPABILITY_REGISTRY_SCHEMA
    registry_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: tuple[CapabilitySpec, ...]


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class CapabilityRegistry:
    """Immutable trusted vocabulary assembled by Zelo Core."""

    def __init__(self, capabilities: Iterable[CapabilitySpec] = ()) -> None:
        trusted: dict[str, CapabilitySpec] = {}
        for spec in capabilities:
            item = CapabilitySpec.model_validate(spec.model_dump(mode="json"))
            if item.capability_id in trusted:
                raise ValueError("duplicate_capability_id")
            trusted[item.capability_id] = item
        self._capabilities: Mapping[str, CapabilitySpec] = MappingProxyType(trusted)
        self._version = _canonical_digest(
            {
                "schema": CAPABILITY_REGISTRY_SCHEMA,
                "capabilities": [trusted[item].model_dump(mode="json") for item in sorted(trusted)],
            }
        )

    @property
    def version(self) -> str:
        return self._version

    def list(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._capabilities[item] for item in sorted(self._capabilities))

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._capabilities.get(capability_id)

    def require(self, capability_id: str, *, subject: CapabilitySubject) -> CapabilitySpec:
        spec = self.get(capability_id)
        if spec is None:
            raise ValueError(f"capability_not_registered:{capability_id}")
        if spec.lifecycle != "active":
            raise ValueError(f"capability_not_active:{capability_id}")
        if subject not in spec.subjects:
            raise ValueError(f"capability_subject_mismatch:{capability_id}:{subject}")
        return spec

    def validate(
        self,
        capability_ids: Iterable[str],
        *,
        subject: CapabilitySubject,
    ) -> None:
        for capability_id in sorted(set(capability_ids)):
            self.require(capability_id, subject=subject)

    def validate_workers(self, descriptors: Iterable[WorkerDescriptor]) -> None:
        for descriptor in descriptors:
            trusted = WorkerDescriptor.model_validate(descriptor.model_dump(mode="json"))
            self.validate(trusted.capabilities, subject="worker")

    def snapshot(self) -> CapabilityRegistrySnapshot:
        return CapabilityRegistrySnapshot(
            registry_version=self.version,
            capabilities=self.list(),
        )


def default_capability_registry() -> CapabilityRegistry:
    """Describe capabilities already published through the current trusted runtime slices."""

    return CapabilityRegistry(
        (
            CapabilitySpec(
                capability_id="code_change",
                description="Plan and produce bounded software changes through trusted Core paths.",
                subjects=("agent",),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="code_planning",
                description=(
                    "Plan bounded software implementation work without granting authority."
                ),
                subjects=("agent",),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="code_review",
                description=(
                    "Perform or require bounded software review through trusted review paths."
                ),
                subjects=("agent", "worker"),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="evidence_synthesis",
                description=(
                    "Synthesize bounded evidence gathered through approved research surfaces."
                ),
                subjects=("agent",),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="browser_document",
                description=("Render bounded untrusted DOM text from approved document origins."),
                subjects=("agent", "worker"),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="api_read",
                description=(
                    "Perform bounded GET requests against trusted configured HTTPS endpoints."
                ),
                subjects=("agent", "worker"),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="shell_execution",
                description=(
                    "Run built-in Shell text compute without workspace, writes, network or forks."
                ),
                subjects=("agent", "worker"),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="python_execution",
                description=(
                    "Execute bounded Python compute inside Zelo's isolated no-network sandbox."
                ),
                subjects=("agent", "worker"),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="research",
                version=2,
                description=(
                    "Conduct bounded evidence-grounded research using approved retrieval surfaces."
                ),
                subjects=("agent", "worker"),
                lifecycle="active",
            ),
            CapabilitySpec(
                capability_id="zelo_core_development",
                description="Implement bounded Zelo Core changes through the Developer Runtime.",
                subjects=("worker",),
                lifecycle="active",
            ),
        )
    )
