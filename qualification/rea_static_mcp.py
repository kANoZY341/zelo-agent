"""Operator-only, non-authoritative REA MCP static-analysis qualification bridge.

This module is deliberately NOT registered as a Core task, Worker, Tool, API
mutation, Agent permission, or Mind action. A separate authority-bound runtime
integration and review are required before normal Zelo use.
"""

import hashlib
import json
import os
import re
import secrets
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "integrations" / "rea" / "integration.json"
PROFILE = ROOT / "integrations" / "rea" / "static-mcp-v6-profile.json"
SOURCE_LOCK = ROOT / "third_party" / "rea" / "package-lock.json"
SOURCE_ENTRYPOINT = ROOT / "third_party" / "rea" / "scripts" / "rea.mjs"
NATIVE_RUNTIME = ROOT / "workspaces" / "rea-qualification" / "runtime-v6"
SOURCE_CATALOG = ROOT / "integrations" / "rea" / "tool-catalog-v6.0.0.json"
NATIVE_FIXTURE_ROOT = ROOT / "workspaces" / "rea-qualification"
DOCKER_BIN = Path("/usr/bin/docker")
DOCKER_SOCKET = "unix:///var/run/docker.sock"
TOOL = "analyze_javascript_application"
PROTOCOL = "2025-11-25"
MAX_FILE_COUNT = 128
MAX_FILE_BYTES = 512 * 1024
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 5
MAX_RPC_BYTES = 1_500_000
MAX_STDERR_BYTES = 16_384
MAX_RUNTIME_SECONDS = 45
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OWNER_TOKEN = re.compile(r"^[0-9a-f]{32}$")
OWNER_LABEL = "zelo.rea.qualification.run"
EVIDENCE_ID = re.compile(r"^ev_[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[a-zA-Z0-9_.-]{1,120}$")
FILE_EXTENSIONS = frozenset({".js", ".mjs", ".cjs", ".json", ".html", ".css", ".map"})


class ReaStaticRefusal(ValueError):
    """A fail-closed static analysis boundary or qualification error."""


@dataclass(frozen=True, slots=True)
class ReaStaticReceipt:
    source_snapshot_sha256: str
    rea_subject_sha256: str
    evidence_id: str
    evidence_sha256: str
    provider_id: str
    provider_version: str
    module_stats: tuple[tuple[str, int], ...]
    limitations: tuple[str, ...]
    input_files: int
    input_bytes: int

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _CopyBudget:
    files: int = 0
    size: int = 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_modules(root: Path) -> str:
    """Content-address installed, already-reviewed modules (including symlinks)."""

    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ReaStaticRefusal("rea_runtime_unavailable")
    digest = hashlib.sha256()
    entries = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        entries += 1
        if entries > 20000:
            raise ReaStaticRefusal("rea_runtime_entry_limit")
        rel = path.relative_to(root).as_posix().encode("utf-8")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ReaStaticRefusal("rea_runtime_symlink_escape")
            digest.update(b"L" + rel + b"\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif stat.S_ISDIR(mode):
            digest.update(b"D" + rel + b"\0")
        elif stat.S_ISREG(mode):
            size = path.stat().st_size
            digest.update(b"F" + rel + b"\0" + str(size).encode("ascii") + b"\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        else:
            raise ReaStaticRefusal("rea_runtime_special_file")
    return digest.hexdigest()


def trusted_profile(runtime: Path) -> dict[str, Any]:
    """Verify source ownership, exact offline build and inert Core authority."""

    from qualification.rea_source_vendor import (  # local source; no external client
        EXPECTED_COMMIT,
        EXPECTED_MANIFEST_SHA256,
        verify_vendored_rea_source,
    )

    try:
        config = json.loads(MANIFEST.read_text(encoding="utf-8"))
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
        package = json.loads((runtime / "package.json").read_text(encoding="utf-8"))
        locked = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
        if (
            config["source"]["commit"] != EXPECTED_COMMIT
            or config["source"]["tag"] != "rea-agents-6.0.0"
            or config["source"]["path"] != "third_party/rea"
            or config["source_integrity_qualified"] is not True
            or config["execution_enabled"] is not False
            or config["managed_install_enabled"] is not False
            or config["network_access_enabled"] is not False
            or config["target_execution_enabled"] is not False
            or config["runtime_capture_enabled"] is not False
            or config["credential_access_enabled"] is not False
            or config["workspace_access_enabled"] is not False
            or config["mcp_tool_grants"] != []
            or config["core_task_kind"] is not None
            or config["worker_authority_profile"] is not None
            or profile["source_commit"] != EXPECTED_COMMIT
            or profile["source_manifest_sha256"] != EXPECTED_MANIFEST_SHA256
            or profile["schema"] != "zelo-rea-source-v6-static-mcp-proof-v1"
            or profile["mcp_protocol_version"] != PROTOCOL
            or profile["mcp_server_version"] != "6.0.0"
            or profile["allowed_tool"] != TOOL
            or profile["manual_fixed_fixture_only"] is not True
            or profile["execution_auto_enabled"] is not False
            or profile["network_enabled"] is not False
            or profile["target_execution_enabled"] is not False
            or profile["zelo_runtime_worker_enabled"] is not False
            or package["name"] != "rea-agents"
            or package["version"] != "6.0.0"
            or locked["packages"][""]["version"] != "6.0.0"
            or catalog["execution_enabled"] is not False
            or catalog["zelo_tool_grants"] != []
            or catalog["upstream_source_commit"] != EXPECTED_COMMIT
            or catalog["mcp_server"] != {"name": "rea", "version": "6.0.0"}
            or catalog["tool_count"] != 139
            or TOOL not in catalog["tool_names"]
            or _sha256_file(SOURCE_LOCK) != profile["package_lock_sha256"]
            or _sha256_file(SOURCE_ENTRYPOINT) != profile["entrypoint_sha256"]
            or _sha256_file(runtime / "package-lock.json") != profile["package_lock_sha256"]
            or _sha256_file(runtime / "scripts" / "rea.mjs") != profile["entrypoint_sha256"]
            or fingerprint_modules(runtime / "node_modules") != profile["node_modules_sha256"]
            or fingerprint_modules(runtime / "dist") != profile["dist_sha256"]
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", profile["node_image_id"])
        ):
            raise ReaStaticRefusal("rea_source_runtime_identity_mismatch")
        source_identity = verify_vendored_rea_source()
        if source_identity.upstream_commit != EXPECTED_COMMIT:
            raise ReaStaticRefusal("rea_source_runtime_identity_mismatch")
    except (OSError, KeyError, ValueError, TypeError) as error:
        if isinstance(error, ReaStaticRefusal):
            raise
        raise ReaStaticRefusal("rea_runtime_identity_unavailable") from None
    return profile


def _reject_symlink_ancestors(path: Path) -> None:
    if not path.is_absolute():
        raise ReaStaticRefusal("rea_source_not_absolute")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ReaStaticRefusal("rea_source_symlink_ancestor")


def _copy_tree(
    source_fd: int, destination: Path, digest: Any, budget: _CopyBudget, *, prefix: str, depth: int
) -> None:
    if depth > MAX_DEPTH:
        raise ReaStaticRefusal("rea_source_depth_limit")
    with os.scandir(source_fd) as stream:
        names = sorted(entry.name for entry in stream)
    for name in names:
        if not SAFE_NAME.fullmatch(name) or name.startswith("."):
            raise ReaStaticRefusal("rea_source_name_not_allowed")
        rel = f"{prefix}/{name}" if prefix else name
        if len(rel.encode("utf-8")) > 255:
            raise ReaStaticRefusal("rea_source_path_limit")
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=source_fd)
        try:
            initial = os.fstat(descriptor)
            if stat.S_ISDIR(initial.st_mode):
                if name in {"node_modules", "vendor"}:
                    raise ReaStaticRefusal("rea_source_dependency_tree_forbidden")
                output = destination / name
                output.mkdir(mode=0o700)
                digest.update(b"D" + rel.encode("utf-8") + b"\0")
                _copy_tree(descriptor, output, digest, budget, prefix=rel, depth=depth + 1)
            elif stat.S_ISREG(initial.st_mode):
                if Path(name).suffix.lower() not in FILE_EXTENSIONS:
                    raise ReaStaticRefusal("rea_source_extension_not_allowed")
                if initial.st_nlink != 1:
                    raise ReaStaticRefusal("rea_source_hardlink_forbidden")
                if initial.st_size > MAX_FILE_BYTES:
                    raise ReaStaticRefusal("rea_source_file_limit")
                budget.files += 1
                if budget.files > MAX_FILE_COUNT:
                    raise ReaStaticRefusal("rea_source_file_count_limit")
                budget.size += initial.st_size
                if budget.size > MAX_INPUT_BYTES:
                    raise ReaStaticRefusal("rea_source_total_bytes_limit")
                digest.update(
                    b"F"
                    + rel.encode("utf-8")
                    + b"\0"
                    + str(initial.st_size).encode("ascii")
                    + b"\0"
                )
                with (destination / name).open("xb") as written:
                    remaining = initial.st_size
                    while remaining:
                        chunk = os.read(descriptor, min(65536, remaining))
                        if not chunk:
                            raise ReaStaticRefusal("rea_source_changed")
                        written.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                (destination / name).chmod(0o600)
                final = os.fstat(descriptor)
                if (
                    initial.st_size,
                    initial.st_mtime_ns,
                    initial.st_ctime_ns,
                    initial.st_ino,
                    initial.st_dev,
                ) != (
                    final.st_size,
                    final.st_mtime_ns,
                    final.st_ctime_ns,
                    final.st_ino,
                    final.st_dev,
                ):
                    raise ReaStaticRefusal("rea_source_changed")
            else:
                raise ReaStaticRefusal("rea_source_not_regular")
        finally:
            os.close(descriptor)


def stage_source(source: Path, destination: Path) -> tuple[str, int, int]:
    """Copy one approved small JS tree into a private, no-symlink snapshot."""

    _reject_symlink_ancestors(source)
    if not destination.is_dir() or destination.is_symlink():
        raise ReaStaticRefusal("rea_staging_unavailable")
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ReaStaticRefusal("rea_source_not_directory")
        output = destination / "target"
        output.mkdir(mode=0o700)
        digest = hashlib.sha256(b"zelo-rea-js-directory-v1\0")
        budget = _CopyBudget()
        _copy_tree(fd, output, digest, budget, prefix="", depth=0)
        if budget.files < 1:
            raise ReaStaticRefusal("rea_source_empty")
        return digest.hexdigest(), budget.files, budget.size
    except OSError:
        raise ReaStaticRefusal("rea_source_unavailable") from None
    finally:
        os.close(fd)


def docker_args(
    runtime: Path,
    staging: Path,
    cidfile: Path,
    image_id: str,
    *,
    uid: int,
    gid: int,
    owner_token: str,
) -> tuple[str, ...]:
    """Immutable Docker policy: no network, capabilities, host workspace or shell."""

    if uid <= 0 or gid <= 0:
        raise ReaStaticRefusal("rea_root_execution_forbidden")
    if not OWNER_TOKEN.fullmatch(owner_token):
        raise ReaStaticRefusal("rea_owner_identity_invalid")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ReaStaticRefusal("rea_unpinned_image")
    if not runtime.is_absolute() or not staging.is_absolute() or not cidfile.is_absolute():
        raise ReaStaticRefusal("rea_untrusted_paths")
    if any(
        character in str(path)
        for path in (runtime, staging, cidfile)
        for character in (",", "\n", "\r", "\0")
    ):
        raise ReaStaticRefusal("rea_untrusted_paths")
    return (
        str(DOCKER_BIN),
        f"--host={DOCKER_SOCKET}",
        "run",
        "-i",
        "--rm",
        "--pull=never",
        f"--cidfile={cidfile}",
        f"--name=zelo-rea-static-{owner_token}",
        f"--label={OWNER_LABEL}={owner_token}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=384m",
        "--memory-swap=384m",
        "--cpus=1",
        f"--user={uid}:{gid}",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=16777216,mode=1777",
        f"--mount=type=bind,source={runtime},target=/rea,readonly",
        f"--mount=type=bind,source={staging / 'target'},target=/target,readonly",
        "--env=HOME=/tmp",
        "--env=NO_COLOR=1",
        "--hostname=rea-static",
        "--entrypoint=/usr/local/bin/node",
        image_id,
        "/rea/scripts/rea.mjs",
        "mcp",
    )


def _clean_env(staging: Path) -> dict[str, str]:
    config = staging / "docker-config"
    config.mkdir(mode=0o700)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(staging),
        "DOCKER_CONFIG": str(config),
        "LANG": "C",
    }


def _list_docker_containers(filter_name: str, env: dict[str, str]) -> tuple[str, ...]:
    """Get exact complete Docker IDs or fail; never trust a truncated list."""

    result = subprocess.run(
        [
            str(DOCKER_BIN),
            f"--host={DOCKER_SOCKET}",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            filter_name,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=8,
    )
    if result.returncode != 0 or len(result.stdout) > 1024:
        raise ReaStaticRefusal("rea_docker_reconciliation_failed")
    lines = result.stdout.decode("ascii").splitlines()
    if len(lines) > 1 or any(not HEX64.fullmatch(item) for item in lines):
        raise ReaStaticRefusal("rea_docker_reconciliation_invalid")
    return tuple(lines)


def _close_owned_process(
    process: subprocess.Popen[bytes],
    cidfile: Path,
    env: dict[str, str],
    owner_token: str,
) -> None:
    """Reconcile one random ownership label, never remove an unverified Docker ID."""

    if not OWNER_TOKEN.fullmatch(owner_token):
        raise ReaStaticRefusal("rea_owner_identity_invalid")
    cleanup_uncertain = False
    reconciliation_completed = False
    client_reaped = process.poll() is not None
    try:
        if not client_reaped:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
                client_reaped = True
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                    client_reaped = True
                except subprocess.TimeoutExpired:
                    cleanup_uncertain = True

        try:
            cid = cidfile.read_text(encoding="ascii").strip() if cidfile.exists() else None
            if not isinstance(cid, str) or not HEX64.fullmatch(cid):
                # Even an empty daemon result cannot establish that an
                # interrupted create with no CID was never launched.
                cleanup_uncertain = True
                cid = None

            label_filter = f"label={OWNER_LABEL}={owner_token}"
            owned = _list_docker_containers(label_filter, env)
            if owned:
                # Never delete a CID copied into our staging directory unless
                # the daemon independently proves that exact owner label.
                if cid is not None and owned != (cid,):
                    cleanup_uncertain = True
                else:
                    subprocess.run(
                        [str(DOCKER_BIN), f"--host={DOCKER_SOCKET}", "rm", "-f", owned[0]],
                        check=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        timeout=8,
                    )
                if _list_docker_containers(label_filter, env):
                    cleanup_uncertain = True
            if cid is not None and _list_docker_containers(f"id={cid}", env):
                cleanup_uncertain = True
            # Set only after every owned-container daemon query has returned.
            # KeyboardInterrupt/SystemExit during reconciliation must retain
            # the ownership file for an operator to verify/delete later.
            reconciliation_completed = not cleanup_uncertain and client_reaped
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ReaStaticRefusal):
            cleanup_uncertain = True
    finally:
        if reconciliation_completed:
            cidfile.unlink(missing_ok=True)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    if cleanup_uncertain or not client_reaped:
        raise ReaStaticRefusal("rea_owned_container_cleanup_unproven")


class _McpTransport:
    """Bounded newline-delimited MCP JSON-RPC over a single owned process."""

    def __init__(self, process: subprocess.Popen[bytes], deadline: float):
        self.process = process
        self.deadline = deadline
        self.buffer = b""
        self.bytes_read = 0
        self.stderr = 0
        self.notifications = 0

    def call(self, message: dict[str, Any]) -> dict[str, Any]:
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(
                json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("ascii")
                + b"\n"
            )
            self.process.stdin.flush()
        except (OSError, AssertionError):
            raise ReaStaticRefusal("rea_mcp_stdin_failed") from None
        if "id" not in message:
            return {}
        target_id = message["id"]
        while time.monotonic() < self.deadline:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except (ValueError, TypeError):
                    raise ReaStaticRefusal("rea_mcp_invalid_json") from None
                if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
                    raise ReaStaticRefusal("rea_mcp_invalid_rpc")
                if payload.get("id") == target_id:
                    if "error" in payload or "result" not in payload:
                        raise ReaStaticRefusal("rea_mcp_tool_failed")
                    return payload["result"]
                if payload.get("id") is not None:
                    raise ReaStaticRefusal("rea_mcp_unexpected_response")
                self.notifications += 1
                if self.notifications > 8:
                    raise ReaStaticRefusal("rea_mcp_notification_limit")
                continue
            timeout = min(0.5, max(0.0, self.deadline - time.monotonic()))
            streams = [s for s in (self.process.stdout, self.process.stderr) if s is not None]
            readable, _, _ = select.select(streams, [], [], timeout)
            for stream in readable:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    if stream is self.process.stdout:
                        raise ReaStaticRefusal("rea_mcp_server_closed")
                    continue
                if stream is self.process.stderr:
                    self.stderr += len(chunk)
                    if self.stderr > MAX_STDERR_BYTES:
                        raise ReaStaticRefusal("rea_mcp_stderr_limit")
                else:
                    self.bytes_read += len(chunk)
                    if self.bytes_read > MAX_RPC_BYTES:
                        raise ReaStaticRefusal("rea_mcp_output_limit")
                    self.buffer += chunk
        raise ReaStaticRefusal("rea_mcp_timeout")


def _receipt(
    mcp_result: dict[str, Any], source_sha: str, count: int, total: int
) -> ReaStaticReceipt:
    if mcp_result.get("isError") not in (None, False):
        raise ReaStaticRefusal("rea_analysis_failed")
    structured = mcp_result.get("structuredContent")
    if not isinstance(structured, dict):
        raise ReaStaticRefusal("rea_evidence_missing")
    evidence = structured.get("evidence")
    result = structured.get("result")
    identifier = structured.get("evidence_id")
    if (
        not isinstance(evidence, dict)
        or not isinstance(result, dict)
        or not isinstance(identifier, str)
        or not EVIDENCE_ID.fullmatch(identifier)
        or evidence.get("evidence_id") != identifier
        or evidence.get("operation") != TOOL
        or evidence.get("normalized_result") != result
    ):
        raise ReaStaticRefusal("rea_evidence_identity_invalid")
    subject = evidence.get("subject")
    provider = evidence.get("provider")
    if (
        not isinstance(subject, dict)
        or not isinstance(provider, dict)
        or subject.get("local_path") != "/target"
        or subject.get("format") != "directory"
        or provider.get("id") != "rea-javascript-application"
        or provider.get("version") != "1"
        or result.get("input_path") != "/target"
    ):
        raise ReaStaticRefusal("rea_evidence_binding_invalid")
    source_digest = subject.get("digest")
    subject_sha = source_digest.get("sha256") if isinstance(source_digest, dict) else None
    if (
        not isinstance(subject_sha, str)
        or not HEX64.fullmatch(subject_sha)
        or result.get("root_artifact_sha256") != subject_sha
    ):
        raise ReaStaticRefusal("rea_evidence_digest_invalid")
    stats = result.get("statistics")
    limitations = evidence.get("limitations")
    if (
        not isinstance(stats, dict)
        or len(stats) > 24
        or not all(
            isinstance(k, str) and len(k) <= 64 and type(v) is int and 0 <= v <= 1_000_000_000
            for k, v in stats.items()
        )
        or not isinstance(limitations, list)
        or len(limitations) > 16
        or not all(isinstance(item, str) and len(item) <= 512 for item in limitations)
    ):
        raise ReaStaticRefusal("rea_evidence_bounds_invalid")
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    return ReaStaticReceipt(
        source_snapshot_sha256=source_sha,
        rea_subject_sha256=subject_sha,
        evidence_id=identifier,
        evidence_sha256=digest,
        provider_id=provider["id"],
        provider_version=provider["version"],
        module_stats=tuple(sorted(stats.items())),
        limitations=tuple(limitations),
        input_files=count,
        input_bytes=total,
    )


def analyze_static_fixture() -> ReaStaticReceipt:
    """Manual, fixed-fixture proof only; never call from Mind/Task/Worker routes."""

    source = NATIVE_FIXTURE_ROOT / "fixtures" / "tiny-app"
    try:
        runtime = NATIVE_RUNTIME.resolve(strict=True)
    except OSError:
        raise ReaStaticRefusal("rea_source_runtime_unavailable") from None
    state_root = NATIVE_FIXTURE_ROOT
    if os.geteuid() == 0 or os.getegid() == 0:
        raise ReaStaticRefusal("rea_root_execution_forbidden")
    _reject_symlink_ancestors(source)
    profile = trusted_profile(runtime)
    if not DOCKER_BIN.is_file():
        raise ReaStaticRefusal("rea_docker_unavailable")
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_root.is_symlink():
        raise ReaStaticRefusal("rea_state_symlink")
    owner_token = secrets.token_hex(16)
    stage = Path(tempfile.mkdtemp(prefix=f"rea-mcp-{owner_token}-", dir=state_root))
    retained_for_recovery = False
    try:
        (stage / "owner.json").write_text(
            json.dumps(
                {
                    "schema": "zelo-rea-owned-container-v1",
                    "label": OWNER_LABEL,
                    "token": owner_token,
                    "container_name": f"zelo-rea-static-{owner_token}",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="ascii",
        )
        source_sha, count, total = stage_source(source, stage)
        cidfile = stage / "owned.cid"
        env = _clean_env(stage)
        argv = docker_args(
            runtime,
            stage,
            cidfile,
            profile["node_image_id"],
            uid=os.geteuid(),
            gid=os.getegid(),
            owner_token=owner_token,
        )
        deadline = time.monotonic() + MAX_RUNTIME_SECONDS
        # A Docker create can cross into the daemon before Popen returns.
        # Retain owner.json and CID unless reconciliation completes, even
        # on KeyboardInterrupt, SystemExit or other BaseException paths.
        retained_for_recovery = True
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=stage,
                env=env,
                start_new_session=True,
                bufsize=0,
            )
        except OSError:
            raise ReaStaticRefusal("rea_docker_launch_failed") from None
        try:
            transport = _McpTransport(process, deadline)
            initialized = transport.call(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "zelo-static-qualification", "version": "1"},
                    },
                }
            )
            if (
                not isinstance(initialized, dict)
                or initialized.get("protocolVersion") != PROTOCOL
                or initialized.get("serverInfo") != {"name": "rea", "version": "6.0.0"}
            ):
                raise ReaStaticRefusal("rea_mcp_identity_mismatch")
            transport.call(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
            analyzed = transport.call(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": TOOL,
                        "arguments": {
                            "input_path": "/target",
                            "format": "directory",
                        },
                    },
                }
            )
            if not isinstance(analyzed, dict):
                raise ReaStaticRefusal("rea_mcp_invalid_result")
            return _receipt(analyzed, source_sha, count, total)
        finally:
            _close_owned_process(process, cidfile, env, owner_token)
            # Only confirmed cleanup releases the private ownership record.
            retained_for_recovery = False
    finally:
        if not retained_for_recovery:
            try:
                shutil.rmtree(stage)
            except OSError:
                raise ReaStaticRefusal("rea_owned_staging_cleanup_unproven") from None
