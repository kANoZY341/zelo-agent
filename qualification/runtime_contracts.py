"""Trusted qualification contracts, NOT cgroup or native-sandbox enforcement."""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field


def validate_digest(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid_digest")


def _integer(value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_resource_bound")


@dataclass(frozen=True)
class ResourcePolicy:
    # All ceilings aggregate the entire owned renderer tree, not individual processes.
    memory_bytes: int
    pids: int
    cpu_quota_us: int
    cpu_period_us: int
    swap_bytes: int
    tmpfs_bytes: int
    deadline_seconds: float

    def __post_init__(self) -> None:
        _integer(self.memory_bytes, 16 * 1024**2, 2 * 1024**3)
        _integer(self.pids, 1, 256)
        _integer(self.cpu_period_us, 1000, 1000000)
        _integer(self.cpu_quota_us, 1000, 2000000)
        if self.cpu_quota_us > 2 * self.cpu_period_us:
            raise ValueError("cpu_ceiling_exceeded")
        _integer(self.swap_bytes, 0, 0)
        _integer(self.tmpfs_bytes, 1, min(self.memory_bytes, 256 * 1024**2))
        if (
            type(self.deadline_seconds) not in (int, float)
            or not 0 < self.deadline_seconds <= 30
            or not math.isfinite(self.deadline_seconds)
        ):
            raise ValueError("invalid_deadline")
        object.__setattr__(self, "deadline_seconds", float(self.deadline_seconds))


@dataclass(frozen=True)
class ExecutionPolicy:
    resources: ResourcePolicy
    image_identity: str
    browser_digest: str
    controller_digest: str
    broker_digest: str
    renderer_policy_digest: str
    approved_origins_digest: str
    version: str = field(default="zelo-browser-qualification-v1", init=False)
    no_network: bool = field(default=True, init=False)
    read_only: bool = field(default=True, init=False)
    no_new_privileges: bool = field(default=True, init=False)
    native_sandbox_required: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if type(self.resources) is not ResourcePolicy:
            raise ValueError("invalid_resources")
        if type(self.image_identity) is not str or not self.image_identity.startswith("sha256:"):
            raise ValueError("unpinned_image")
        validate_digest(self.image_identity.removeprefix("sha256:"))
        for value in (
            self.browser_digest,
            self.controller_digest,
            self.broker_digest,
            self.renderer_policy_digest,
            self.approved_origins_digest,
        ):
            validate_digest(value)

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
