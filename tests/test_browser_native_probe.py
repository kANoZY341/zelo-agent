"""Offline proof-gate tests; never launch Docker or a browser in automatic CI."""

import copy
import json

import pytest

from qualification.browser_seccomp import build_profile
from scripts import qualify_browser_native_sandbox as probe

IMAGE = "sha256:" + "a" * 64
RUN = "b" * 32
CID = "c" * 64


def inspected():
    return {
        "Image": IMAGE,
        "Mounts": [],
        "Config": {
            "User": "65534:65534",
            "Entrypoint": ["/native-probe"],
            "Cmd": None,
            "Labels": {probe.LABEL: RUN},
            "Env": [
                "HOME=/tmp",
                "LD_LIBRARY_PATH=/opt/libs",
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent",
                "PATH=/opt/browser:/opt/libs",
            ],
        },
        "HostConfig": {
            "Memory": probe.MEMORY,
            "MemorySwap": probe.MEMORY,
            "PidsLimit": 128,
            "CpuPeriod": 100000,
            "CpuQuota": 50000,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "ShmSize": 16777216,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": [
                "no-new-privileges",
                "seccomp=" + build_profile().profile_bytes.decode(),
            ],
            "RestartPolicy": {"Name": "no"},
            "Tmpfs": {"/tmp": probe.TMPFS},
        },
    }


def test_exact_container_configuration_and_no_shared_mutation():
    data = inspected()
    before = copy.deepcopy(data)
    probe.verify_container(data, IMAGE, RUN, build_profile().profile_bytes)
    assert data == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("Memory", 0),
        ("MemorySwap", -1),
        ("PidsLimit", -1),
        ("CpuQuota", 0),
        ("NetworkMode", "host"),
        ("ReadonlyRootfs", False),
        ("Privileged", True),
        ("IpcMode", "host"),
        ("PidMode", "host"),
        ("CgroupnsMode", "host"),
        ("CapAdd", ["SYS_ADMIN"]),
        ("CapDrop", []),
        ("Binds", ["/:/host"]),
        ("PortBindings", {"80/tcp": [{"HostPort": "8080"}]}),
        ("SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
        ("Tmpfs", {"/tmp": "rw,size=9999999999"}),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
    ],
)
def test_dangerous_container_changes_rejected(key, value):
    data = inspected()
    data["HostConfig"][key] = value
    with pytest.raises((ValueError, KeyError)):
        probe.verify_container(data, IMAGE, RUN, build_profile().profile_bytes)


def test_identity_environment_and_profile_denials():
    for section, key, value in [
        ("Config", "User", "0"),
        ("Config", "Cmd", ["--no-sandbox"]),
        ("Config", "Entrypoint", ["/bin/sh"]),
        ("Config", "Labels", {probe.LABEL: "other"}),
        ("Config", "Env", ["ZELO_OPERATOR_TOKEN=fixture-only"]),
    ]:
        data = inspected()
        data[section][key] = value
        with pytest.raises(ValueError):
            probe.verify_container(data, IMAGE, RUN, build_profile().profile_bytes)
    data = inspected()
    data["Image"] = "sha256:" + "d" * 64
    with pytest.raises(ValueError):
        probe.verify_container(data, IMAGE, RUN, build_profile().profile_bytes)
    changed = json.loads(build_profile().profile_bytes)
    changed["defaultAction"] = "SCMP_ACT_ALLOW"
    with pytest.raises(ValueError):
        probe.verify_container(inspected(), IMAGE, RUN, json.dumps(changed).encode())


OUTPUT = """cgroup_memory.max=536870912
cgroup_memory.swap.max=0
cgroup_pids.max=128
cgroup_cpu.max=50000 100000
initial_forbidden_syscalls=denied
renderer_seccomp=2
renderer_filters=2
container_filters=1
renderer_nnp=1
renderer_caps=0
renderer_pid_depth=3
container_pid_depth=1
native_renderer_probe=PASS
"""


def test_kernel_observations_require_native_layer_plus_container_layer():
    assert probe.verify_kernel_output(OUTPUT)["renderer_filters"] == "2"
    for old, new in [
        ("renderer_filters=2", "renderer_filters=1"),
        ("renderer_seccomp=2", "renderer_seccomp=0"),
        ("renderer_nnp=1", "renderer_nnp=0"),
        ("renderer_caps=0", "renderer_caps=200000"),
        ("renderer_pid_depth=3", "renderer_pid_depth=1"),
        ("cgroup_memory.swap.max=0", "cgroup_memory.swap.max=max"),
        ("cgroup_pids.max=128", "cgroup_pids.max=max"),
        ("native_renderer_probe=PASS", "native_renderer_probe=FAIL"),
    ]:
        with pytest.raises(ValueError):
            probe.verify_kernel_output(OUTPUT.replace(old, new))
    with pytest.raises(ValueError):
        probe.verify_kernel_output(OUTPUT + "renderer_filters=2\n")
    with pytest.raises(ValueError):
        probe.verify_kernel_output("native_renderer_probe=PASS\n")


def test_cleanup_does_not_remove_unowned_resources_and_continues(monkeypatch):
    calls = []

    def command(args, timeout=20):
        calls.append(args)
        if "--format" in args:
            return CID if "ps" in args else IMAGE
        if "inspect" in args:
            label = "wrong" if args[-1] == CID else RUN
            return json.dumps([{"Config": {"Labels": {probe.LABEL: label}}}])
        return ""

    monkeypatch.setattr(probe, "command", command)
    errors = probe.cleanup_owned("docker", RUN)
    assert len(errors) == 1
    assert not any("rm" in c and CID in c for c in calls)
    assert ["docker", "image", "rm", IMAGE] in calls


def test_cleanup_listing_failure_is_not_proof_of_absence(monkeypatch):
    calls = []

    def command(args, timeout=20):
        calls.append(args)
        if "ps" in args:
            raise RuntimeError("fixture unavailable")
        return ""

    monkeypatch.setattr(probe, "command", command)
    assert len(probe.cleanup_owned("docker", RUN)) == 1
    assert any("image" in args for args in calls)
    with pytest.raises(ValueError):
        probe.cleanup_owned("docker", "*")


def test_diagnostic_requires_explicit_opt_in_and_has_no_network_build(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["probe", "--bundle", "/fixture", "--expected-bundle-digest", "a" * 64]
    )
    monkeypatch.setattr(probe, "run_probe", lambda *args: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2
    assert probe.DOCKERFILE.startswith("FROM scratch\n")
    assert "RUN " not in probe.DOCKERFILE and "ADD " not in probe.DOCKERFILE
