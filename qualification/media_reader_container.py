"""Pure fixed Media reader-container policy; no Docker or native execution on import."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

LEASE_LABEL = "org.zelo.media-reader.lease"
BUNDLE_LABEL = "org.zelo.media-reader.bundle"
ENTRYPOINT = "/reader/ld.so"
COMMAND = (
    "--inhibit-cache",
    "--library-path",
    "/reader/lib",
    "/reader/dpkg-deb",
    "--threads-max=1",
    "--fsys-tarfile",
    "-",
)
ENV = (
    "LC_ALL=C",
    "LANG=C",
    "PATH=/nonexistent",
    "HOME=/nonexistent",
    "TMPDIR=/nonexistent",
)
USER = "65534:65534"
RUNTIME = "runc"
NOFILE = 32
MEMORY = 536870912
PIDS = 8
CPU_PERIOD = 100000
CPU_QUOTA = 50000
SHM = 1048576
BOOTSTRAP_PROOF_SHA256 = "cc5c463a62ccd73534d88c37b2dbc9ad4b0343161066998343877e469a6601e5"
BOOTSTRAP_LOCK_SHA256 = "753df1ca08e5be5b29ae8b73d220bf6bfea052b8bed9ffdc0f64ac12091a8e4f"


class Refused(ValueError):
    """Fixed contract refusal without daemon or peer diagnostic content."""


class LimitRefused(Refused):
    """Fixed container limit mismatch evidence."""

    def __init__(self, host: dict, expected: dict) -> None:
        super().__init__("reader_container_limits")
        self.differences = tuple(
            {
                "field": field,
                **(
                    _bounded_observation(host[field])
                    if field in host
                    else {"observed_kind": "missing"}
                ),
            }
            for field, value in expected.items()
            if not _same(host.get(field), value)
            and not (field == "OomKillDisable" and _oom_default(host))
        )


def _bounded_observation(value: object) -> dict[str, object]:
    """Classify daemon values without retaining arbitrary text or structures."""
    if value is None:
        return {"observed_kind": "null"}
    if type(value) is bool:
        return {"observed_kind": "boolean", "observed_value": value}
    if type(value) is int:
        if -1 <= value <= 10**12:
            return {"observed_kind": "integer", "observed_value": value}
        return {"observed_kind": "integer_out_of_range"}
    if type(value) is str:
        if value in ("", "none", "private", "runc", "host", "default"):
            return {"observed_kind": "known_string", "observed_value": value}
        return {"observed_kind": "other_string"}
    return {"observed_kind": "other"}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused("reader_container_" + code)


def _sha(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value) is not None


def _same(actual: object, expected: object) -> bool:
    return type(actual) is type(expected) and actual == expected


def _oom_default(host: dict) -> bool:
    # The nullable Docker setting does not disable the OOM killer when unset.
    # On unsupported hosts (including cgroup v2), inspect may report null.
    return host.get("OomKillDisable") is False or (
        "OomKillDisable" in host and host["OomKillDisable"] is None
    )


@dataclass(frozen=True)
class ReaderContainerContract:
    """Identity supplied only by a separately pinned future image manifest."""

    image_identity: str
    bundle_sha256: str
    bootstrap_proof_sha256: str = BOOTSTRAP_PROOF_SHA256
    bootstrap_lock_sha256: str = BOOTSTRAP_LOCK_SHA256

    def validate(self) -> None:
        _require(
            type(self.image_identity) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_identity) is not None,
            "image_identity",
        )
        _require(
            _sha(self.bundle_sha256) and not self.bundle_sha256.startswith("sha256:"),
            "bundle",
        )
        _require(
            self.bootstrap_proof_sha256 == BOOTSTRAP_PROOF_SHA256
            and self.bootstrap_lock_sha256 == BOOTSTRAP_LOCK_SHA256,
            "bootstrap_binding",
        )


def create_arguments(
    contract: ReaderContainerContract, name: str, identity: str, profile: Path
) -> tuple[str, ...]:
    contract.validate()
    _require(
        type(name) is str and re.fullmatch(r"zelo-media-reader-[0-9a-f]{64}", name) is not None,
        "name",
    )
    _require(type(identity) is str and re.fullmatch(r"[0-9a-f]{64}", identity) is not None, "lease")
    _require(isinstance(profile, Path) and profile.is_absolute(), "profile")
    return (
        "create",
        "--pull=never",
        "--interactive",
        "--name",
        name,
        "--label",
        f"{LEASE_LABEL}={identity}",
        "--label",
        f"{BUNDLE_LABEL}={contract.bundle_sha256}",
        "--network=none",
        "--read-only",
        f"--user={USER}",
        f"--runtime={RUNTIME}",
        # Unset Init inherits the daemon default; insist on an inspected false.
        "--init=false",
        "--ulimit",
        f"nofile={NOFILE}:{NOFILE}",
        "--workdir=/",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--security-opt=seccomp={profile}",
        f"--memory={MEMORY}",
        f"--memory-swap={MEMORY}",
        f"--pids-limit={PIDS}",
        f"--cpu-period={CPU_PERIOD}",
        f"--cpu-quota={CPU_QUOTA}",
        "--ipc=private",
        "--cgroupns=private",
        f"--shm-size={SHM}",
        "--restart=no",
        "--stop-timeout=2",
        "--log-driver=none",
        f"--entrypoint={ENTRYPOINT}",
        "--env",
        ENV[0],
        "--env",
        ENV[1],
        "--env",
        ENV[2],
        "--env",
        ENV[3],
        "--env",
        ENV[4],
        contract.image_identity,
        *COMMAND,
    )


def verify_image(data: object, contract: ReaderContainerContract, identity: str) -> None:
    contract.validate()
    _require(type(identity) is str and re.fullmatch(r"[0-9a-f]{64}", identity) is not None, "lease")
    _require(type(data) is dict, "image_shape")
    config = data.get("Config")
    _require(type(config) is dict, "image_shape")
    _require(
        data.get("Id") == contract.image_identity
        and data.get("Architecture") == "amd64"
        and data.get("Os") == "linux",
        "image_identity",
    )
    labels = config.get("Labels")
    _require(
        labels == {LEASE_LABEL: identity, BUNDLE_LABEL: contract.bundle_sha256}
        and config.get("Entrypoint") in (None, [])
        and config.get("Cmd") in (None, [])
        and config.get("Env") == ["PATH=/nonexistent"]
        and config.get("User") in (None, "")
        and config.get("WorkingDir") in (None, "")
        and not config.get("Volumes")
        and not config.get("ExposedPorts")
        and not config.get("OnBuild")
        and not config.get("Healthcheck"),
        "image_configuration",
    )


def verify_container(
    data: object,
    contract: ReaderContainerContract,
    name: str,
    identity: str,
    profile_bytes: bytes,
    *,
    running: bool = False,
) -> None:
    contract.validate()
    _require(type(data) is dict, "container_shape")
    config, host = data.get("Config"), data.get("HostConfig")
    _require(type(config) is dict and type(host) is dict, "container_shape")
    expected_host = {
        "Memory": MEMORY,
        "MemorySwap": MEMORY,
        "PidsLimit": PIDS,
        "CpuPeriod": CPU_PERIOD,
        "CpuQuota": CPU_QUOTA,
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "IpcMode": "private",
        "CgroupnsMode": "private",
        "PidMode": "",
        "UTSMode": "",
        "ShmSize": SHM,
        "Runtime": RUNTIME,
        "Init": False,
        "OomKillDisable": False,
        "UsernsMode": "",
    }
    if not _oom_default(host) or not all(
        _same(host.get(key), value)
        for key, value in expected_host.items()
        if key != "OomKillDisable"
    ):
        raise LimitRefused(host, expected_host)
    _require(
        data.get("Name") == "/" + name
        and data.get("Image") == contract.image_identity
        and config.get("User") == USER
        and config.get("Entrypoint") == [ENTRYPOINT]
        and config.get("Cmd") == list(COMMAND)
        and sorted(config.get("Env", [])) == sorted(ENV)
        and config.get("Labels") == {LEASE_LABEL: identity, BUNDLE_LABEL: contract.bundle_sha256}
        and config.get("OpenStdin") is True
        and config.get("Tty") is False
        and config.get("WorkingDir") == "/"
        and config.get("StopTimeout") == 2
        and not config.get("Volumes")
        and not config.get("ExposedPorts")
        and not config.get("Healthcheck")
        and data.get("State", {}).get("Running") is running,
        "configuration",
    )
    _require(host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"), "capabilities")
    _require(
        host.get("Ulimits") == [{"Name": "nofile", "Hard": NOFILE, "Soft": NOFILE}],
        "ulimits",
    )
    security = host.get("SecurityOpt")
    _require(type(security) is list and len(security) == 2, "security")
    _require("no-new-privileges" in security, "security")
    profiles = [item[8:] for item in security if type(item) is str and item.startswith("seccomp=")]
    _require(len(profiles) == 1 and type(profile_bytes) is bytes, "security")
    try:
        actual_profile = json.loads(profiles[0])
        expected_profile = json.loads(profile_bytes)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise Refused("reader_container_security") from None
    _require(actual_profile == expected_profile, "security")
    _require(host.get("RestartPolicy", {}).get("Name") == "no", "restart")
    _require(host.get("LogConfig", {}).get("Type") == "none", "logging")
    _require(host.get("AutoRemove", False) is False, "autoremove")
    _require(host.get("PublishAllPorts", False) is False, "ports")
    for key in (
        "Binds",
        "Devices",
        "DeviceRequests",
        "PortBindings",
        "Links",
        "ExtraHosts",
        "VolumesFrom",
        "Dns",
        "DnsOptions",
        "DnsSearch",
        "GroupAdd",
        "Sysctls",
        "DeviceCgroupRules",
        "CgroupParent",
        "StorageOpt",
        "Tmpfs",
    ):
        _require(not host.get(key), "authority")
    _require(data.get("Mounts") in (None, []), "mounts")


def expected_contract_summary(contract: ReaderContainerContract) -> dict:
    contract.validate()
    return {
        "schema": "zelo-media-native-reader-container-contract-v1",
        "image_identity": contract.image_identity,
        "bundle_sha256": contract.bundle_sha256,
        "bootstrap_proof_sha256": contract.bootstrap_proof_sha256,
        "bootstrap_lock_sha256": contract.bootstrap_lock_sha256,
        "entrypoint": ENTRYPOINT,
        "command": list(COMMAND),
        "environment": list(ENV),
        "memory_bytes": MEMORY,
        "memory_swap_bytes": MEMORY,
        "pids_limit": PIDS,
        "cpu_period": CPU_PERIOD,
        "cpu_quota": CPU_QUOTA,
        "runtime": RUNTIME,
        "nofile": NOFILE,
        "network": "none",
        "read_only_rootfs": True,
        "runtime_authority": False,
        "package_member_provenance_verified": False,
        "actual_package_compatibility_verified": False,
    }
