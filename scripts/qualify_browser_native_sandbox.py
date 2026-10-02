"""Opt-in, fixed trusted native-browser fixture; no production runtime activation.

Run as a module. Requires existing Docker/GCC and an independently trusted digest
for a prepared browser/libs/loader bundle. Never pulls images or visits a website.
"""

import argparse
import hashlib
import json
import platform
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from qualification.artifacts import ArtifactPolicy, capture_manifest, verify_manifest
from qualification.browser_seccomp import build_profile, validate_profile
from qualification.runtime_contracts import validate_digest
from scripts.qualify_browser_resources import command

ROOT = Path(__file__).resolve().parents[1]
LABEL = "app.zelo.native-sandbox-qualification"
BROWSER_SHA256 = "ded93a9c9a53a1ae040f08124badcca95c938e9d5015ff340c3b5538c41bf39e"
ROLES = (("browser", "browser/chrome-headless-shell"), ("loader", "loader"))
MEMORY = 536870912
TMPFS = "rw,nosuid,nodev,noexec,size=33554432"
DOCKERFILE = """FROM scratch
COPY bundle/browser /opt/browser
COPY bundle/libs /opt/libs
COPY bundle/loader /lib64/ld-linux-x86-64.so.2
COPY native-probe /native-probe
USER 65534:65534
ENV LD_LIBRARY_PATH=/opt/libs HOME=/tmp
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent PATH=/opt/browser:/opt/libs
ENTRYPOINT ["/native-probe"]
"""


def verify_container(data: dict, image: str, run_id: str, profile: bytes) -> None:
    """Inspect exact ownership/resource/security settings before starting the fixture."""
    validate_profile(profile, hashlib.sha256(profile).hexdigest())
    config, host = data["Config"], data["HostConfig"]
    required = {
        "Memory": MEMORY,
        "MemorySwap": MEMORY,
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
    }
    if any(type(host.get(k)) is not type(v) or host[k] != v for k, v in required.items()):
        raise ValueError("native_container_limits_mismatch")
    security = host.get("SecurityOpt", [])
    if (
        data["Image"] != image
        or config.get("User") != "65534:65534"
        or config.get("Entrypoint") != ["/native-probe"]
        or config.get("Cmd")
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
        or host.get("Tmpfs") != {"/tmp": TMPFS}
        or len(security) != 2
        or "no-new-privileges" not in security
    ):
        raise ValueError("native_container_boundary_mismatch")
    entries = config.get("Env", [])
    environment = dict(item.split("=", 1) for item in entries)
    if len(environment) != len(entries) or environment != {
        "LD_LIBRARY_PATH": "/opt/libs",
        "HOME": "/tmp",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent",
        "PATH": "/opt/browser:/opt/libs",
    }:
        raise ValueError("native_container_environment_mismatch")
    seccomp = [item[8:] for item in security if item.startswith("seccomp=")]
    if len(seccomp) != 1 or json.loads(seccomp[0]) != json.loads(profile):
        raise ValueError("native_container_profile_mismatch")


def verify_kernel_output(output: str) -> dict[str, str]:
    expected = {
        "cgroup_memory.max": str(MEMORY),
        "cgroup_memory.swap.max": "0",
        "cgroup_pids.max": "128",
        "cgroup_cpu.max": "50000 100000",
        "initial_forbidden_syscalls": "denied",
        "renderer_seccomp": "2",
        "renderer_nnp": "1",
        "renderer_caps": "0",
        "native_renderer_probe": "PASS",
    }
    fields = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in fields:
            raise ValueError("duplicate_kernel_evidence")
        fields[key] = value
    if any(fields.get(k) != v for k, v in expected.items()):
        raise ValueError("native_kernel_boundary_unproven")
    for child, parent in (
        ("renderer_filters", "container_filters"),
        ("renderer_pid_depth", "container_pid_depth"),
    ):
        if not 1 <= int(fields[parent]) < int(fields[child]) <= 64:
            raise ValueError("native_nested_boundary_unproven")
    return fields


def cleanup_owned(docker: str, run_id: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid_ownership_label")
    errors = []
    for kind in ("container", "image"):
        listing = [docker, "ps", "-a"] if kind == "container" else [docker, "image", "ls"]
        listing += ["--no-trunc", "--filter", f"label={LABEL}={run_id}", "--format", "{{.ID}}"]
        try:
            identifiers = set(command(listing).splitlines())
        except Exception as exc:
            errors.append(f"{kind} enumeration unavailable: {type(exc).__name__}")
            continue
        for identifier in identifiers:
            try:
                pattern = r"[0-9a-f]{64}" if kind == "container" else r"sha256:[0-9a-f]{64}"
                if not re.fullmatch(pattern, identifier):
                    raise ValueError("invalid_owned_identifier")
                prefix = [docker] if kind == "container" else [docker, "image"]
                data = json.loads(command([*prefix, "inspect", identifier]))[0]
                if data["Config"].get("Labels", {}).get(LABEL) != run_id:
                    raise ValueError("ownership_label_mismatch")
                command([*prefix, "rm", *(["-f"] if kind == "container" else []), identifier])
            except Exception as exc:
                errors.append(f"{kind} cleanup unproven: {type(exc).__name__}")
    return errors


def run_probe(bundle: Path, expected_digest: str) -> Path:
    if platform.machine() != "x86_64":
        raise ValueError("native_qualification_architecture_unsupported")
    validate_digest(expected_digest)
    docker, gcc = shutil.which("docker"), shutil.which("gcc")
    if not docker or not gcc:
        raise RuntimeError("existing Docker and GCC required; no install will be attempted")
    policy = ArtifactPolicy()
    expected = capture_manifest(bundle, ROLES, policy)
    if expected.digest != expected_digest:
        raise ValueError("unapproved_bundle_digest")
    browser = next(item for item in expected.files if item.path == ROLES[0][1])
    if browser.sha256 != BROWSER_SHA256:
        raise ValueError("unqualified_browser_binary")
    profile = build_profile()
    parent = ROOT / "runtime/browser-sandbox"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(tempfile.mkdtemp(prefix="qualified-native-", dir=parent))
    build = root / "build"
    build.mkdir(mode=0o700)
    shutil.copytree(bundle, build / "bundle", symlinks=True)
    verify_manifest(build / "bundle", ROLES, policy, expected, expected_digest)
    (build / "Dockerfile").write_text(DOCKERFILE)
    profile_path = root / "seccomp.json"
    profile_path.write_bytes(profile.profile_bytes)
    source = ROOT / "qualification/fixtures/native_sandbox_probe.c"
    command([gcc, "-static", "-O2", "-o", str(build / "native-probe"), str(source)], 30)
    run_id = uuid4().hex
    report = {
        "run_id": run_id,
        "status": "incomplete",
        "worker_qualified": False,
        "external_network_used": False,
        "native_sandbox_disabled": False,
        "bundle_digest": expected_digest,
        "profile_digest": profile.digest,
        "fixture_sha256": hashlib.sha256((build / "native-probe").read_bytes()).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    try:
        image_file = root / "image-id"
        command(
            [
                docker,
                "build",
                "--network=none",
                "--pull=false",
                "--label",
                f"{LABEL}={run_id}",
                "--iidfile",
                str(image_file),
                str(build),
            ],
            90,
        )
        image = image_file.read_text().strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("image_identity_unproven")
        report["image_identity"] = image
        cid = command(
            [
                docker,
                "create",
                "--label",
                f"{LABEL}={run_id}",
                "--name",
                f"zelo-native-{run_id}",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--user=65534:65534",
                "--security-opt=no-new-privileges",
                f"--security-opt=seccomp={profile_path}",
                f"--memory={MEMORY}",
                f"--memory-swap={MEMORY}",
                "--pids-limit=128",
                "--cpu-period=100000",
                "--cpu-quota=50000",
                "--ipc=private",
                "--cgroupns=private",
                "--tmpfs",
                f"/tmp:{TMPFS}",
                "--shm-size=16777216",
                "--restart=no",
                "--log-driver=local",
                "--log-opt=max-size=1m",
                "--log-opt=max-file=2",
                image,
            ]
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise ValueError("container_identity_unproven")
        verify_container(
            json.loads(command([docker, "inspect", cid]))[0], image, run_id, profile.profile_bytes
        )
        command([docker, "start", cid])
        exit_code = int(command([docker, "wait", cid], 25).strip())
        output = command([docker, "logs", cid])
        report["exit_code"] = exit_code
        report["output"] = output[-16384:]
        if exit_code != 0:
            raise ValueError("native_probe_failed")
        report["kernel_observations"] = verify_kernel_output(output)
        state = json.loads(command([docker, "inspect", cid]))[0]["State"]
        if state["Running"] or state["Pid"] != 0:
            raise ValueError("container_cleanup_unproven")
        report["status"] = "passed"
    finally:
        errors = cleanup_owned(docker, run_id)
        report["cleanup_errors"] = errors
        if errors:
            report["status"] = "cleanup_unproven"
        (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        if errors:
            raise RuntimeError("owned native resources require explicit reconciliation")
    return root / "result.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-local-container-test", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-digest", required=True)
    args = parser.parse_args()
    if not args.acknowledge_local_container_test:
        parser.error("explicit local-container test acknowledgement required")
    print(run_probe(args.bundle, args.expected_bundle_digest))


if __name__ == "__main__":
    main()
