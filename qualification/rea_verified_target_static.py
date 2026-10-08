"""Manual no-network REA static proof on one sealed, operator-selected text snapshot.

NOT a Core Worker, Task, approval, Agent action, or live MCP tool. This path
qualifies an *explicitly opted-in* operator-selected text target with a
separate pinned local policy; it never widens the older fixed-fixture proof.
All target data and analyzer output are untrusted; output is a bounded receipt.
"""

import fcntl
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from app.rea_candidate import rea_candidate_snapshot
from app.rea_tool_inventory import ReaCatalogRefusal, rea_tool_inventory
from qualification import rea_static_mcp as fixed
from qualification import rea_target_admission as admission
from qualification.rea_source_vendor import EXPECTED_COMMIT, EXPECTED_MANIFEST_SHA256

ROOT = Path(__file__).resolve().parents[1]
OPERATOR_PROFILE = ROOT / "integrations" / "rea" / "offline-verified-target-v1.json"
EXPECTED_PROFILE_SHA256 = "c489d44be9e37444fa45709ec81922685bf01009cb574cb178918d0bfe19b808"
PROOF_ROOT = ROOT / "workspaces" / "rea-qualification"
RUNTIME = fixed.NATIVE_RUNTIME
MAX_PROFILE_BYTES = 2048


@dataclass(frozen=True, slots=True)
class ReaManualStaticTargetReceipt:
    schema: Literal["zelo-rea-manual-static-target-proof-v1"]
    snapshot_id: str
    receipt_sha256: str
    content_sha256: str
    copied_source_sha256: str
    source_commit: Literal["6fee42689ae6e95a0182e0d4d0f55644e73e72be"]
    source_manifest_sha256: str
    qualification_profile_sha256: str
    fixed_fixture_runtime_only_previously_qualified: Literal[True]
    execution_mode: Literal["manual_operator_local_offline_static_only"]
    core_approval: Literal[False]
    core_worker_registered: Literal[False]
    core_tool_grants: tuple[()]
    core_task_created: Literal[False]
    evidence: fixed.ReaStaticReceipt

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def _policy() -> dict[str, Any]:
    """Separately pinned manual target scope; never infer from the fixture proof."""

    try:
        info = os.stat(OPERATOR_PROFILE, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROFILE_BYTES:
            raise fixed.ReaStaticRefusal("rea_target_profile_invalid")
        fd = os.open(OPERATOR_PROFILE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            if os.fstat(fd) != info:
                raise fixed.ReaStaticRefusal("rea_target_profile_changed")
            raw = os.read(fd, MAX_PROFILE_BYTES + 1)
            if len(raw) != info.st_size or os.fstat(fd) != info:
                raise fixed.ReaStaticRefusal("rea_target_profile_changed")
        finally:
            os.close(fd)
        if hashlib.sha256(raw).hexdigest() != EXPECTED_PROFILE_SHA256:
            raise fixed.ReaStaticRefusal("rea_target_profile_identity_invalid")
        p = json.loads(raw)
        if (
            type(p) is not dict
            or set(p)
            != {
                "schema",
                "source_commit",
                "source_manifest_sha256",
                "source_runtime_profile_sha256",
                "tool",
                "manual_operator_opt_in_only",
                "require_independently_retained_receipt",
                "require_sealed_target_and_exact_copied_bytes",
                "maximum_files",
                "maximum_total_bytes",
                "maximum_seconds",
                "network_enabled",
                "target_code_execution_enabled",
                "core_task_registered",
                "core_worker_registered",
                "core_tool_grants",
                "core_approval",
                "only_fixed_fixture_is_prior_qualified",
                "exclusive_manual_run",
                "refuse_unreconciled_owned_staging",
            }
            or p["schema"] != "zelo-rea-offline-verified-target-static-v1"
            or p["source_commit"] != EXPECTED_COMMIT
            or p["source_manifest_sha256"] != EXPECTED_MANIFEST_SHA256
            or p["source_runtime_profile_sha256"]
            != hashlib.sha256(fixed.PROFILE.read_bytes()).hexdigest()
            or p["tool"] != fixed.TOOL
            or p["manual_operator_opt_in_only"] is not True
            or p["require_independently_retained_receipt"] is not True
            or p["require_sealed_target_and_exact_copied_bytes"] is not True
            or type(p["maximum_files"]) is not int
            or p["maximum_files"] != fixed.MAX_FILE_COUNT
            or type(p["maximum_total_bytes"]) is not int
            or p["maximum_total_bytes"] != fixed.MAX_INPUT_BYTES
            or type(p["maximum_seconds"]) is not int
            or p["maximum_seconds"] != fixed.MAX_RUNTIME_SECONDS
            or p["network_enabled"] is not False
            or p["target_code_execution_enabled"] is not False
            or p["core_task_registered"] is not False
            or p["core_worker_registered"] is not False
            or p["core_tool_grants"] != []
            or p["core_approval"] is not False
            or p["only_fixed_fixture_is_prior_qualified"] is not True
            or p["exclusive_manual_run"] is not True
            or p["refuse_unreconciled_owned_staging"] is not True
        ):
            raise fixed.ReaStaticRefusal("rea_target_profile_contract_invalid")
        return p
    except fixed.ReaStaticRefusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise fixed.ReaStaticRefusal("rea_target_profile_unavailable") from None


def _require_inert_catalog() -> None:
    try:
        candidate = rea_candidate_snapshot()
        catalog = rea_tool_inventory()
    except (ReaCatalogRefusal, OSError):
        raise fixed.ReaStaticRefusal("rea_target_catalog_unavailable") from None
    if (
        candidate.agent.lifecycle != "draft"
        or candidate.execution_enabled
        or candidate.tool_grants
        or candidate.agent.allowed_task_kinds
        or candidate.agent.allowed_tools
        or catalog.execution_enabled
        or catalog.worker_registered
        or catalog.grants
        or len(catalog.tools) != 139
        or [tool.qualification for tool in catalog.tools if tool.name == fixed.TOOL]
        != ["manual_fixed_fixture_only"]
    ):
        raise fixed.ReaStaticRefusal("rea_target_catalog_authority_changed")


def _change_modes(fd: int, *, make_readonly: bool) -> None:
    """Seal/unseal our own copied tree through nofollow directory descriptors."""

    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            try:
                _change_modes(child, make_readonly=make_readonly)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            file_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=fd,
            )
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise fixed.ReaStaticRefusal("rea_target_stage_special_file")
                os.fchmod(file_fd, 0o400 if make_readonly else 0o600)
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
        else:
            raise fixed.ReaStaticRefusal("rea_target_stage_special_file")
    os.fchmod(fd, 0o500 if make_readonly else 0o700)
    os.fsync(fd)


def _mode_tree(path: Path, *, make_readonly: bool) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _change_modes(fd, make_readonly=make_readonly)
    finally:
        os.close(fd)


def _copy_and_verify(
    identity: str, digest: str, stage: Path
) -> tuple[dict[str, Any], str, int, int]:
    """Bind the copied Docker mount to independently retained offline receipt."""

    initial = admission.verify_target(identity, digest)
    source = admission.STAGING / identity / "files"
    source_sha, count, size = fixed.stage_source(source, stage)
    _mode_tree(stage / "target", make_readonly=True)
    with admission._fixed_root(stage) as staged:
        with admission._directory(staged, "target", private=True) as copied:
            files, stamps = admission._scan(copied, sealed=True)
            if (files, stamps) != admission._scan(copied, sealed=True):
                raise fixed.ReaStaticRefusal("rea_target_stage_changed")
    reconstructed = admission._receipt(identity, files)
    if (
        admission._sha(admission._json(reconstructed)) != digest
        or reconstructed["content_sha256"] != initial["content_sha256"]
        or count != initial["file_count"]
        or size != initial["total_bytes"]
        or reconstructed["file_count"] != count
        or reconstructed["total_bytes"] != size
        or admission.verify_target(identity, digest) != initial
    ):
        raise fixed.ReaStaticRefusal("rea_target_copy_identity_mismatch")
    return initial, source_sha, count, size


def _mcp_static_proof(process: subprocess.Popen[bytes], deadline: float) -> dict[str, Any]:
    transport = fixed._McpTransport(process, deadline)
    initialized = transport.call(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": fixed.PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "zelo-offline-target-qualification", "version": "1"},
            },
        }
    )
    if (
        not isinstance(initialized, dict)
        or initialized.get("protocolVersion") != fixed.PROTOCOL
        or initialized.get("serverInfo") != {"name": "rea", "version": "6.0.0"}
    ):
        raise fixed.ReaStaticRefusal("rea_mcp_identity_mismatch")
    transport.call({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    result = transport.call(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": fixed.TOOL,
                "arguments": {"input_path": "/target", "format": "directory"},
            },
        }
    )
    if not isinstance(result, dict):
        raise fixed.ReaStaticRefusal("rea_mcp_invalid_result")
    return result


@contextmanager
def _exclusive_manual_run() -> Iterator[None]:
    """Fail closed on simultaneous or unreconciled manual Docker qualifications.

    One private, nofollow, 0600 lock per operator-owned qualification root.
    A crash releases the kernel lock but keeps its owned staging directory;
    that retained directory blocks all later runs until separate reconciliation.
    """

    directory = None
    lock = None
    try:
        directory = os.open(
            PROOF_ROOT,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        info = os.fstat(directory)
        if (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or not stat.S_ISDIR(info.st_mode)
        ):
            raise fixed.ReaStaticRefusal("rea_target_proof_root_not_private")
        lock = os.open(
            ".rea-target-static.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        detail = os.fstat(lock)
        if (
            not stat.S_ISREG(detail.st_mode)
            or detail.st_nlink != 1
            or detail.st_uid != os.geteuid()
            or stat.S_IMODE(detail.st_mode) != 0o600
        ):
            raise fixed.ReaStaticRefusal("rea_target_qualification_lock_invalid")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise fixed.ReaStaticRefusal("rea_target_qualification_busy") from None
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith("rea-target-"):
                    raise fixed.ReaStaticRefusal("rea_target_unreconciled_prior_run")
        yield
    except OSError:
        raise fixed.ReaStaticRefusal("rea_target_qualification_lock_unavailable") from None
    finally:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)


def analyze_verified_static_target(
    snapshot_id: str, receipt_sha256: str, *, operator_opt_in: bool = False
) -> ReaManualStaticTargetReceipt:
    """One manual bounded probe. No Core execution registry or worker references."""

    if operator_opt_in is not True:
        raise fixed.ReaStaticRefusal("rea_target_explicit_opt_in_required")
    if os.geteuid() == 0 or os.getegid() == 0:
        raise fixed.ReaStaticRefusal("rea_root_execution_forbidden")
    if not isinstance(snapshot_id, str) or admission.IDENTITY.fullmatch(snapshot_id) is None:
        raise fixed.ReaStaticRefusal("rea_target_id_invalid")
    if not isinstance(receipt_sha256, str) or admission.HEX.fullmatch(receipt_sha256) is None:
        raise fixed.ReaStaticRefusal("rea_target_receipt_invalid")
    _policy()
    _require_inert_catalog()
    try:
        runtime = RUNTIME.resolve(strict=True)
    except OSError:
        raise fixed.ReaStaticRefusal("rea_target_runtime_unavailable") from None
    profile = fixed.trusted_profile(runtime)
    if not fixed.DOCKER_BIN.is_file():
        raise fixed.ReaStaticRefusal("rea_docker_unavailable")
    fixed._reject_symlink_ancestors(PROOF_ROOT)
    if not PROOF_ROOT.is_dir() or PROOF_ROOT.is_symlink():
        raise fixed.ReaStaticRefusal("rea_target_proof_root_unavailable")
    if stat.S_IMODE(PROOF_ROOT.stat().st_mode) != 0o700:
        raise fixed.ReaStaticRefusal("rea_target_proof_root_not_private")

    with _exclusive_manual_run():
        return _analyze_locked_target(snapshot_id, receipt_sha256, runtime, profile)


def _analyze_locked_target(
    snapshot_id: str, receipt_sha256: str, runtime: Path, profile: dict[str, Any]
) -> ReaManualStaticTargetReceipt:
    """Only the exclusive lock holder may create an owned Docker probe."""

    token = secrets.token_hex(16)
    stage = Path(tempfile.mkdtemp(prefix=f"rea-target-{token}-", dir=PROOF_ROOT))
    retained_for_recovery = False
    try:
        (stage / "owner.json").write_text(
            json.dumps(
                {
                    "schema": "zelo-rea-owned-container-v1",
                    "label": fixed.OWNER_LABEL,
                    "token": token,
                    "container_name": f"zelo-rea-static-{token}",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="ascii",
        )
        initial, source_sha, count, size = _copy_and_verify(snapshot_id, receipt_sha256, stage)
        _require_inert_catalog()
        if admission.verify_target(snapshot_id, receipt_sha256) != initial:
            raise fixed.ReaStaticRefusal("rea_target_changed_before_run")
        cidfile = stage / "owned.cid"
        env = fixed._clean_env(stage)
        argv = fixed.docker_args(
            runtime,
            stage,
            cidfile,
            profile["node_image_id"],
            uid=os.geteuid(),
            gid=os.getegid(),
            owner_token=token,
        )
        deadline = time.monotonic() + fixed.MAX_RUNTIME_SECONDS
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
            raise fixed.ReaStaticRefusal("rea_target_docker_launch_failed") from None
        try:
            analyzed = _mcp_static_proof(process, deadline)
            evidence = fixed._receipt(analyzed, source_sha, count, size)
            if (
                evidence.input_files != initial["file_count"]
                or evidence.input_bytes != initial["total_bytes"]
            ):
                raise fixed.ReaStaticRefusal("rea_target_evidence_input_mismatch")
            return ReaManualStaticTargetReceipt(
                schema="zelo-rea-manual-static-target-proof-v1",
                snapshot_id=snapshot_id,
                receipt_sha256=receipt_sha256,
                content_sha256=initial["content_sha256"],
                copied_source_sha256=source_sha,
                source_commit=EXPECTED_COMMIT,
                source_manifest_sha256=EXPECTED_MANIFEST_SHA256,
                qualification_profile_sha256=EXPECTED_PROFILE_SHA256,
                fixed_fixture_runtime_only_previously_qualified=True,
                execution_mode="manual_operator_local_offline_static_only",
                core_approval=False,
                core_worker_registered=False,
                core_tool_grants=(),
                core_task_created=False,
                evidence=evidence,
            )
        finally:
            fixed._close_owned_process(process, cidfile, env, token)
            retained_for_recovery = False
    finally:
        if not retained_for_recovery:
            try:
                target = stage / "target"
                if target.exists():
                    _mode_tree(target, make_readonly=False)
                shutil.rmtree(stage)
            except OSError:
                raise fixed.ReaStaticRefusal("rea_target_owned_staging_cleanup_unproven") from None
