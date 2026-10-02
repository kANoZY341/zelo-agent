"""Pure fixture-launch guards; no Docker, pressure load or external network in CI."""

from copy import deepcopy

import pytest

from scripts.qualify_browser_resources import LABEL, MEMORY, TMPFS, verify_container

IMAGE = "sha256:" + "a" * 64
RUN_ID = "b" * 32


def specification():
    return {
        "Image": IMAGE,
        "Config": {
            "User": "65534:65534",
            "Entrypoint": ["/resource-probe"],
            "Labels": {LABEL: RUN_ID},
        },
        "HostConfig": {
            "Memory": MEMORY,
            "MemorySwap": MEMORY,
            "PidsLimit": 12,
            "CpuPeriod": 100000,
            "CpuQuota": 25000,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CgroupnsMode": "private",
            "IpcMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "CapDrop": ["ALL"],
            "RestartPolicy": {"Name": "no"},
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/tmp": TMPFS.split(":", 1)[1]},
        },
    }


def test_owned_resource_fixture_contract():
    data = specification()
    before = deepcopy(data)
    verify_container(data, IMAGE, RUN_ID)
    assert data == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("Memory", 0),
        ("MemorySwap", -1),
        ("PidsLimit", 0),
        ("CpuPeriod", 0),
        ("CpuQuota", -1),
        ("NetworkMode", "host"),
        ("ReadonlyRootfs", False),
        ("ReadonlyRootfs", 1),
        ("Privileged", True),
        ("CgroupnsMode", "host"),
        ("IpcMode", "host"),
        ("PidMode", "host"),
        ("UTSMode", "host"),
        ("CapDrop", []),
        ("CapAdd", ["SYS_ADMIN"]),
        ("Binds", ["/:/host"]),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
        ("DeviceRequests", [{"Capabilities": [["gpu"]]}]),
        ("PortBindings", {"80/tcp": [{"HostPort": "8080"}]}),
        ("RestartPolicy", {"Name": "always"}),
        ("SecurityOpt", ["seccomp=unconfined"]),
        ("Tmpfs", {}),
    ],
)
def test_resource_or_isolation_widening_rejected_before_start(field, value):
    data = specification()
    data["HostConfig"][field] = value
    with pytest.raises(RuntimeError):
        verify_container(data, IMAGE, RUN_ID)


@pytest.mark.parametrize(
    "field,value",
    [("User", "root"), ("Entrypoint", ["/bin/sh"]), ("Labels", {}), ("Volumes", {"/private": {}})],
)
def test_foreign_identity_or_mount_rejected_before_start(field, value):
    data = specification()
    data["Config"][field] = value
    with pytest.raises(RuntimeError):
        verify_container(data, IMAGE, RUN_ID)


def test_replaced_image_and_host_bind_mount_denied():
    data = specification()
    data["Image"] = "latest"
    with pytest.raises(RuntimeError):
        verify_container(data, IMAGE, RUN_ID)
    data = specification()
    data["Mounts"] = [{"Type": "bind", "Source": "/home/user"}]
    with pytest.raises(RuntimeError):
        verify_container(data, IMAGE, RUN_ID)
