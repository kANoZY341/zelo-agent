"""Explicit, optional no-network resource diagnostic; not a Browser Worker launcher.

Builds a scratch image from the repository's own static C fixture. Only labelled
containers created by this invocation are started/removed. No image pull, user
mount, Docker socket mount, privileged mode, system configuration change or
production data is needed. Requires an existing trusted Docker daemon and GCC.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
LABEL = "app.zelo.resource-qualification"
MEMORY = 32 * 1024 * 1024
TMPFS = "/tmp:rw,nosuid,nodev,noexec,size=8388608,mode=1777"


def command(args: list[str], timeout: float = 20) -> str:
    # Credentials are neither accepted by this script nor printed. The existing
    # trusted Docker CLI configuration is used to reach the operator's daemon.
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} {args[1]} exited {result.returncode}: {result.stderr[-2000:]}"
        )
    return result.stdout


def verify_container(data: dict, image: str, run_id: str) -> None:
    """Refuse to run even trusted pressure code without the intended constraints."""
    config, host = data["Config"], data["HostConfig"]
    expected = {
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
    }
    if any(type(host.get(k)) is not type(v) or host[k] != v for k, v in expected.items()):
        raise RuntimeError("container resource/isolation settings do not match")
    if (
        data["Image"] != image
        or config.get("User") != "65534:65534"
        or config.get("Entrypoint") != ["/resource-probe"]
        or config.get("Labels", {}).get(LABEL) != run_id
        or host.get("CapDrop") != ["ALL"]
        or host.get("CapAdd")
        or host.get("Binds")
        or host.get("Devices")
        or host.get("DeviceRequests")
        or host.get("PortBindings")
        or config.get("Volumes")
        or data.get("Mounts")
        or host.get("RestartPolicy", {}).get("Name") != "no"
        or host.get("SecurityOpt") != ["no-new-privileges"]
        or host.get("Tmpfs") != {"/tmp": TMPFS.split(":", 1)[1]}
    ):
        raise RuntimeError("container privilege, mount, identity or environment contract mismatch")


def run_probe() -> Path:
    docker, gcc = shutil.which("docker"), shutil.which("gcc")
    if not docker or not gcc:
        raise RuntimeError("existing Docker and GCC are required; nothing will be installed")
    info = json.loads(command([docker, "info", "--format", "{{json .}}"], 10))
    if info.get("CgroupVersion") != "2" or not all(
        info.get(k) for k in ("MemoryLimit", "SwapLimit", "PidsLimit")
    ):
        raise RuntimeError("cgroup v2 memory, swap and PID enforcement required")
    run_id = uuid4().hex
    parent = ROOT / "runtime" / "browser-runtime-qualification"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(tempfile.mkdtemp(prefix="resources-", dir=parent))
    build = root / "build"
    build.mkdir(mode=0o700)
    source = ROOT / "qualification/fixtures/resource_probe.c"
    shutil.copyfile(ROOT / "qualification/fixtures/Dockerfile.resource-probe", build / "Dockerfile")
    command(
        [
            gcc,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(build / "resource-probe"),
            str(source),
        ],
        30,
    )
    binary_digest = hashlib.sha256((build / "resource-probe").read_bytes()).hexdigest()
    image_file = root / "image-id"
    command(
        [
            docker,
            "build",
            "--network=none",
            "--pull=false",
            "--iidfile",
            str(image_file),
            "--label",
            f"{LABEL}={run_id}",
            str(build),
        ],
        60,
    )
    image = image_file.read_text().strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RuntimeError("image not pinned by local content identity")
    owned: dict[str, str | None] = {}
    evidence = {
        "run_id": run_id,
        "image_id": image,
        "fixture_sha256": binary_digest,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "worker_qualified": False,
        "external_network": False,
        "cases": [],
    }
    try:
        for mode in ("boundary", "pids", "cpu", "tmpfs", "memory", "cleanup"):
            name = f"zelo-resource-{run_id}-{mode}"
            owned[name] = None
            cid = command(
                [
                    docker,
                    "create",
                    "--name",
                    name,
                    "--label",
                    f"{LABEL}={run_id}",
                    "--network=none",
                    "--read-only",
                    "--user=65534:65534",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--pids-limit=12",
                    f"--memory={MEMORY}",
                    f"--memory-swap={MEMORY}",
                    "--cpu-period=100000",
                    "--cpu-quota=25000",
                    "--tmpfs",
                    TMPFS,
                    "--ipc=private",
                    "--shm-size=1048576",
                    "--cgroupns=private",
                    "--restart=no",
                    "--stop-timeout=2",
                    "--log-driver=local",
                    "--log-opt=max-size=1m",
                    "--log-opt=max-file=2",
                    image,
                    mode,
                ]
            ).strip()
            if not re.fullmatch(r"[0-9a-f]{64}", cid):
                raise RuntimeError("container id invalid")
            owned[name] = cid
            data = json.loads(command([docker, "inspect", cid]))[0]
            verify_container(data, image, run_id)
            command([docker, "start", cid])
            if mode == "cleanup":
                deadline = time.monotonic() + 5
                while "CHILDREN_READY" not in command([docker, "logs", cid]):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("controlled children never became ready")
                    time.sleep(0.05)
                command([docker, "kill", "--signal=KILL", cid])
            exit_code = int(command([docker, "wait", cid], 18).strip())
            data = json.loads(command([docker, "inspect", cid]))[0]
            output = command([docker, "logs", cid])
            state = data["State"]
            if state["Running"] or state["Pid"] != 0 or "LIMITS_VERIFIED" not in output:
                raise RuntimeError("resource not proven stopped or in-container limits unverified")
            if mode in {"memory", "cleanup"}:
                if exit_code != 137 or (mode == "memory" and not state["OOMKilled"]):
                    raise RuntimeError("expected controlled cgroup kill was not observed")
            elif exit_code != 0:
                raise RuntimeError(f"{mode} fixture failed: {exit_code}: {output[-1500:]}")
            if mode == "boundary":
                interfaces = re.findall(r"^\s*([\w.-]+):", output, re.M)
                if interfaces != ["lo"] or "BOUNDARY_OK" not in output:
                    raise RuntimeError("unexpected network interface or writable root")
            if mode == "cpu":
                throttle = re.search(r"^nr_throttled (\d+)$", output, re.M)
                if not throttle or int(throttle[1]) < 1:
                    raise RuntimeError("CPU quota throttling not observed")
            evidence["cases"].append(
                {
                    "mode": mode,
                    "exit_code": exit_code,
                    "oom_killed": state["OOMKilled"],
                    "output": output,
                }
            )
            command([docker, "rm", cid])
            del owned[name]
        evidence["all_owned_containers_removed"] = True
    finally:
        # Look up only our unpredictable names/IDs and verify label before cleanup.
        for name, cid in list(owned.items()):
            try:
                data = json.loads(command([docker, "inspect", cid or name]))[0]
            except RuntimeError:
                continue
            if data.get("Config", {}).get("Labels", {}).get(LABEL) != run_id:
                raise RuntimeError(
                    "refusing to clean a container without this run's ownership label"
                )
            command([docker, "rm", "-f", data["Id"]])
        # This run-specific labelled image was built by us, not an operator image.
        image_data = json.loads(command([docker, "image", "inspect", image]))[0]
        if image_data.get("Config", {}).get("Labels", {}).get(LABEL) == run_id:
            command([docker, "image", "rm", image])
            evidence["owned_image_removed"] = True
        (root / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return root / "result.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="explicitly build/run the owned limited no-network fixtures",
    )
    if not parser.parse_args().execute:
        parser.error("no action taken: --execute is required")
    result = run_probe()
    print(result)
    print(json.dumps(json.loads(result.read_text()), indent=2))


if __name__ == "__main__":
    main()
