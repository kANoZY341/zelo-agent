"""Offline contract tests only; no Docker daemon or target process is touched."""

import ast
import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from qualification import media_reader_container as m
from qualification import media_reader_seccomp as seccomp

IMAGE = "sha256:" + "a" * 64
BUNDLE = "b" * 64
LEASE = "c" * 64
NAME = "zelo-media-reader-" + LEASE


def contract():
    return m.ReaderContainerContract(IMAGE, BUNDLE)


def image():
    return {
        "Id": IMAGE,
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {
            "Labels": {m.BUNDLE_LABEL: BUNDLE},
            "Entrypoint": None,
            "Cmd": None,
            "Env": None,
            "User": "",
            "WorkingDir": "",
            "Volumes": None,
            "ExposedPorts": None,
            "OnBuild": None,
            "Healthcheck": None,
        },
    }


def container():
    return {
        "Name": "/" + NAME,
        "Image": IMAGE,
        "Config": {
            "User": m.USER,
            "Entrypoint": [m.ENTRYPOINT],
            "Cmd": list(m.COMMAND),
            "Env": list(m.ENV),
            "Labels": {m.LEASE_LABEL: LEASE, m.BUNDLE_LABEL: BUNDLE},
            "OpenStdin": True,
            "Tty": False,
            "WorkingDir": "/",
            "Volumes": None,
            "ExposedPorts": None,
            "Healthcheck": None,
            "StopTimeout": 2,
        },
        "HostConfig": {
            "Memory": m.MEMORY,
            "MemorySwap": m.MEMORY,
            "PidsLimit": m.PIDS,
            "CpuPeriod": m.CPU_PERIOD,
            "CpuQuota": m.CPU_QUOTA,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "ShmSize": m.SHM,
            "Runtime": m.RUNTIME,
            "Init": False,
            "OomKillDisable": False,
            "UsernsMode": "",
            "Ulimits": [{"Name": "nofile", "Hard": m.NOFILE, "Soft": m.NOFILE}],
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": [
                "no-new-privileges",
                "seccomp=" + seccomp.profile_bytes().decode(),
            ],
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {"Type": "none"},
            "AutoRemove": False,
            "PublishAllPorts": False,
            "Binds": None,
            "Devices": None,
            "DeviceRequests": None,
            "PortBindings": None,
            "Links": None,
            "ExtraHosts": None,
            "VolumesFrom": None,
            "Dns": None,
            "DnsOptions": None,
            "DnsSearch": None,
            "GroupAdd": None,
            "Sysctls": None,
            "DeviceCgroupRules": None,
            "CgroupParent": "",
            "StorageOpt": None,
            "Tmpfs": None,
        },
        "Mounts": [],
        "State": {"Running": False},
    }


def test_contract_is_pure_and_has_no_execution_imports():
    path = Path(m.__file__)
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".", 1)[0])
    assert imported == {"dataclasses", "json", "pathlib", "re"}
    text = path.read_text()
    for token in ("subprocess", "docker.sock", "asyncio", "socket.", "os.system", "Popen"):
        assert token not in text


def test_fixed_command_has_no_shell_path_or_host_search():
    assert m.ENTRYPOINT == "/reader/ld.so"
    assert m.COMMAND == (
        "--inhibit-cache",
        "--library-path",
        "/reader/lib",
        "/reader/dpkg-deb",
        "--threads-max=1",
        "--fsys-tarfile",
        "-",
    )
    joined = "\0".join(m.COMMAND)
    assert "/bin/sh" not in joined and "/usr/bin" not in joined
    assert "tar" in m.COMMAND[-2]
    assert "PATH=/nonexistent" in m.ENV
    assert "HOME=/nonexistent" in m.ENV
    assert "TMPDIR=/nonexistent" in m.ENV
    assert all("DPKG_" not in item and "COMPRESS" not in item for item in m.ENV)


def test_create_arguments_are_exact_and_no_mounts_or_tmpfs():
    args = m.create_arguments(contract(), NAME, LEASE, Path("/fixed/seccomp.json"))
    assert args[0] == "create"
    assert args.count("--pull=never") == 1
    assert "--network=none" in args and "--read-only" in args
    assert "--cap-drop=ALL" in args and "--security-opt=no-new-privileges" in args
    assert "--memory=536870912" in args and "--memory-swap=536870912" in args
    assert "--pids-limit=8" in args and "--cpu-quota=50000" in args
    assert "--entrypoint=/reader/ld.so" in args
    assert "--runtime=runc" in args
    assert "--ulimit" in args and "nofile=32:32" in args
    assert "--workdir=/" in args
    assert args[-len(m.COMMAND) :] == m.COMMAND
    assert args[-len(m.COMMAND) - 1] == IMAGE
    assert "--mount" not in args and "--volume" not in args and "--tmpfs" not in args
    assert "--env" in args and "LC_ALL=C" in args and "LANG=C" in args
    assert "PATH=/nonexistent" in args
    assert "HOME=/nonexistent" in args
    assert "TMPDIR=/nonexistent" in args


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_identity", "a" * 64),
        ("image_identity", "sha256:" + "A" * 64),
        ("bundle_sha256", "sha256:" + "b" * 64),
        ("bundle_sha256", "b" * 63),
        ("bootstrap_proof_sha256", "0" * 64),
        ("bootstrap_lock_sha256", "0" * 64),
    ],
)
def test_contract_identity_refusals(field, value):
    with pytest.raises(m.Refused):
        replace(contract(), **{field: value}).validate()


@pytest.mark.parametrize("name", ["reader", NAME + "x", "zelo-media-reader-" + "G" * 64])
def test_create_rejects_bad_name(name):
    with pytest.raises(m.Refused, match="reader_container_name"):
        m.create_arguments(contract(), name, LEASE, Path("/fixed/seccomp.json"))


@pytest.mark.parametrize("identity", ["c" * 63, "C" * 64, "x" * 64])
def test_create_rejects_bad_lease(identity):
    with pytest.raises(m.Refused, match="reader_container_lease"):
        m.create_arguments(contract(), NAME, identity, Path("/fixed/seccomp.json"))


def test_valid_image_and_container():
    m.verify_image(image(), contract())
    m.verify_container(container(), contract(), NAME, LEASE, seccomp.profile_bytes())


@pytest.mark.parametrize(
    "path,value",
    [
        (("Id",), "sha256:" + "0" * 64),
        (("Architecture",), "arm64"),
        (("Os",), "windows"),
        (("Config", "Labels"), {}),
        (("Config", "Labels"), {m.BUNDLE_LABEL: BUNDLE, "extra": "x"}),
        (("Config", "Entrypoint"), ["/reader/ld.so"]),
        (("Config", "Cmd"), ["x"]),
        (("Config", "Env"), ["PATH=/bin"]),
        (("Config", "User"), "0"),
        (("Config", "WorkingDir"), "/tmp"),
        (("Config", "Volumes"), {"/tmp": {}}),
        (("Config", "ExposedPorts"), {"80/tcp": {}}),
        (("Config", "OnBuild"), ["RUN x"]),
        (("Config", "Healthcheck"), {"Test": ["CMD", "x"]}),
    ],
)
def test_image_drift_refused(path, value):
    value_dict = copy.deepcopy(image())
    target = value_dict
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(m.Refused):
        m.verify_image(value_dict, contract())


@pytest.mark.parametrize(
    "field,value",
    [
        ("Memory", m.MEMORY - 1),
        ("MemorySwap", m.MEMORY * 2),
        ("PidsLimit", m.PIDS + 1),
        ("CpuPeriod", m.CPU_PERIOD - 1),
        ("CpuQuota", m.CPU_QUOTA + 1),
        ("NetworkMode", "bridge"),
        ("ReadonlyRootfs", False),
        ("Privileged", True),
        ("IpcMode", "host"),
        ("CgroupnsMode", "host"),
        ("PidMode", "host"),
        ("UTSMode", "host"),
        ("ShmSize", m.SHM * 2),
        ("Runtime", "crun"),
        ("Init", True),
        ("OomKillDisable", True),
        ("UsernsMode", "host"),
    ],
)
def test_resource_or_namespace_drift_refused(field, value):
    data = container()
    data["HostConfig"][field] = value
    with pytest.raises(m.Refused, match="reader_container_limits"):
        m.verify_container(data, contract(), NAME, LEASE, seccomp.profile_bytes())


@pytest.mark.parametrize(
    "path,value",
    [
        (("Name",), "/foreign"),
        (("Image",), "sha256:" + "0" * 64),
        (("Config", "User"), "0:0"),
        (("Config", "Entrypoint"), ["/bin/sh"]),
        (("Config", "Cmd"), list(m.COMMAND[:-1])),
        (("Config", "Env"), [*m.ENV, "PATH=/bin"]),
        (("Config", "Labels"), {m.LEASE_LABEL: LEASE}),
        (("Config", "OpenStdin"), False),
        (("Config", "Tty"), True),
        (("Config", "WorkingDir"), "/tmp"),
        (("Config", "Volumes"), {"/x": {}}),
        (("Config", "ExposedPorts"), {"80/tcp": {}}),
        (("Config", "Healthcheck"), {"Test": ["NONE"]}),
        (("State", "Running"), True),
    ],
)
def test_container_configuration_drift_refused(path, value):
    data = copy.deepcopy(container())
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(m.Refused, match="reader_container_configuration"):
        m.verify_container(data, contract(), NAME, LEASE, seccomp.profile_bytes())


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("CapDrop", [], "capabilities"),
        ("CapAdd", ["SYS_ADMIN"], "capabilities"),
        ("SecurityOpt", [], "security"),
        ("Ulimits", [], "ulimits"),
        ("SecurityOpt", ["no-new-privileges", "seccomp=unconfined"], "security"),
        ("RestartPolicy", {"Name": "always"}, "restart"),
        ("LogConfig", {"Type": "json-file"}, "logging"),
        ("AutoRemove", True, "autoremove"),
        ("PublishAllPorts", True, "ports"),
    ],
)
def test_privilege_and_lifecycle_drift_refused(field, value, code):
    data = container()
    data["HostConfig"][field] = value
    with pytest.raises(m.Refused, match=f"reader_container_{code}"):
        m.verify_container(data, contract(), NAME, LEASE, seccomp.profile_bytes())


@pytest.mark.parametrize(
    "field,value",
    [
        ("Binds", ["/:/host"]),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
        ("DeviceRequests", [{}]),
        ("PortBindings", {"80/tcp": []}),
        ("Links", ["x:y"]),
        ("ExtraHosts", ["x:127.0.0.1"]),
        ("VolumesFrom", ["x"]),
        ("Dns", ["8.8.8.8"]),
        ("DnsOptions", ["use-vc"]),
        ("DnsSearch", ["example.test"]),
        ("GroupAdd", ["0"]),
        ("Sysctls", {"kernel.shm_rmid_forced": "0"}),
        ("DeviceCgroupRules", ["c 1:3 rwm"]),
        ("CgroupParent", "/foreign"),
        ("StorageOpt", {"size": "1G"}),
        ("Tmpfs", {"/tmp": "rw"}),
    ],
)
def test_extra_authority_refused(field, value):
    data = container()
    data["HostConfig"][field] = value
    with pytest.raises(m.Refused, match="reader_container_authority"):
        m.verify_container(data, contract(), NAME, LEASE, seccomp.profile_bytes())


@pytest.mark.parametrize("mounts", [[{"Type": "bind", "Destination": "/x"}], [{}]])
def test_any_mount_refused(mounts):
    data = container()
    data["Mounts"] = mounts
    with pytest.raises(m.Refused, match="reader_container_mounts"):
        m.verify_container(data, contract(), NAME, LEASE, seccomp.profile_bytes())


def test_summary_is_non_authoritative_and_bound_to_bootstrap():
    summary = m.expected_contract_summary(contract())
    assert summary["schema"] == "zelo-media-native-reader-container-contract-v1"
    assert summary["bootstrap_proof_sha256"] == m.BOOTSTRAP_PROOF_SHA256
    assert summary["bootstrap_lock_sha256"] == m.BOOTSTRAP_LOCK_SHA256
    assert summary["runtime_authority"] is False
    assert summary["package_member_provenance_verified"] is False
    assert summary["actual_package_compatibility_verified"] is False
    assert summary["memory_bytes"] == summary["memory_swap_bytes"] == 536870912
    assert summary["pids_limit"] == 8
    assert summary["runtime"] == "runc"
    assert summary["nofile"] == 32


def test_contract_dataclass_is_immutable():
    with pytest.raises(FrozenInstanceError):
        contract().image_identity = "x"
