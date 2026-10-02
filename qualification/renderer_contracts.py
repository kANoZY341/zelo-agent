"""Immutable qualification attachment contract; operator provenance is explicit.

PreparedImage is an operator attestation of image contents, not a signature or a
scanner result. The preparing operator must independently verify that attachment.
Runtime checks immutable Docker image identity; it never builds or pulls images.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.api_contracts import ApiEndpointBinding, ApiEndpointRegistry
from qualification.artifacts import ArtifactManifest, verify_manifest
from qualification.broker import Policy
from qualification.browser_seccomp import SeccompProfile, validate_profile
from qualification.runtime_contracts import ExecutionPolicy

ROLES = (
    ("bootstrap", "browser-pipe"),
    ("browser", "browser/chrome-headless-shell"),
    ("loader", "loader"),
)
CONTROLLER_DIGEST = hashlib.sha256(
    Path(__file__).with_name("renderer_protocol.py").read_bytes()
).hexdigest()
RENDERER_DIGEST = hashlib.sha256(
    b"zelo-text-v1;blank;script-disabled;csp-none;network-none;dom-depth16;nodes4096;"
    b"text65536;chromium-text-preview-utf16-10000-mark-incomplete;message524288;total2097152;events32;stderr16384;untrusted"
).hexdigest()


def origins_digest(registry: ApiEndpointRegistry, bindings: tuple[ApiEndpointBinding, ...]) -> str:
    if any(not registry.matches(binding) for binding in bindings):
        raise ValueError("binding_inactive")
    values = sorted(binding.endpoint.endpoint_id for binding in bindings)
    if len(values) != len(set(values)):
        raise ValueError("duplicate_binding")
    return hashlib.sha256(json.dumps([registry.version, values]).encode()).hexdigest()


@dataclass(frozen=True)
class PreparedImage:
    image_identity: str
    manifest_digest: str
    # A trusted offline preparation receipt must bind these exact two values.
    # Passing this object is an operator assertion, never task authority.


@dataclass(frozen=True)
class RendererContract:
    execution: ExecutionPolicy
    manifest: ArtifactManifest
    profile: SeccompProfile
    prepared: PreparedImage

    def validate(self) -> None:
        p = self.execution
        r = p.resources
        if (
            type(p) is not ExecutionPolicy
            or type(self.manifest) is not ArtifactManifest
            or type(self.profile) is not SeccompProfile
            or type(self.prepared) is not PreparedImage
            or self.prepared.image_identity != p.image_identity
            or self.prepared.manifest_digest != self.manifest.digest
            or self.manifest.roles != ROLES
            or p.controller_digest != CONTROLLER_DIGEST
            or p.renderer_policy_digest != RENDERER_DIGEST
            or (
                r.memory_bytes,
                r.pids,
                r.cpu_quota_us,
                r.cpu_period_us,
                r.swap_bytes,
                r.tmpfs_bytes,
            )
            != (536870912, 128, 50000, 100000, 0, 33554432)
        ):
            raise ValueError("renderer_contract_mismatch")
        inventory = {item.path: item for item in self.manifest.files}
        for _, path in ROLES:
            if path not in inventory or not inventory[path].mode & 0o111:
                raise ValueError("renderer_executable_missing")
        if inventory["browser/chrome-headless-shell"].sha256 != p.browser_digest:
            raise ValueError("browser_digest_mismatch")
        validate_profile(self.profile.profile_bytes, self.profile.digest)

    def verify_bundle(self, root: Path) -> None:
        self.validate()
        verify_manifest(root, ROLES, self.manifest.policy, self.manifest, self.manifest.digest)

    def verify_broker(
        self,
        registry: ApiEndpointRegistry,
        bindings: tuple[ApiEndpointBinding, ...],
        policy: Policy,
    ) -> None:
        self.validate()
        if (
            policy.digest != self.execution.broker_digest
            or origins_digest(registry, bindings) != self.execution.approved_origins_digest
        ):
            raise ValueError("broker_contract_mismatch")

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, default=lambda x: x.hex(), separators=(",", ":")
            ).encode()
        ).hexdigest()
