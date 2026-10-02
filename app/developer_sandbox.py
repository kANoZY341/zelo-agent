"""Minimal Linux filesystem views shared by validation, Git and the Codex parent."""

import hashlib
import json
from pathlib import Path

from app.developer_process import DeveloperError

BWRAP = Path("/usr/bin/bwrap")
REPO = "/workspace"
VENV = "/opt/zelo-venv"
ENGINE = "/tmp/zelo-codex-engine"
CODEX = "/opt/zelo-codex/codex"
CODE_MODE_HOST = "/opt/zelo-codex/codex-code-mode-host"
WORKER_ROOT = "/opt/zelo-worker"
WORKER_EXECUTABLE = WORKER_ROOT + "/worker"
PYTHON_EXECUTABLE = "/usr/bin/python3.12"
PYTHON_STDLIB = "/usr/lib/python3.12"
PYTHON_ARCH_LIB = "/usr/lib/x86_64-linux-gnu"
PYTHON_LOADER = "/usr/lib64/ld-linux-x86-64.so.2"
SYSTEM_READONLY_BINDS = ("/usr", "/bin", "/lib", "/lib64")
HOST_READONLY_BINDS = ("/etc/ld.so.cache", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts")
SANDBOX_POLICY_SCHEMA = "zelo-linux-sandbox-v1"
_REPO_SOURCE = "$REPO"
_GIT_SOURCE = "$GIT"
_VENV_SOURCE = "$VENV"
_AUTH_SOURCE = "$AUTH"
_GITCONFIG_SOURCE = "$DEVELOPER_GITCONFIG"
_CODEX_FD = "$CODEX_FD"
_CODE_MODE_HOST_FD = "$CODE_MODE_HOST_FD"
_WORKER_FD = "$WORKER_FD"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _bind(source: str, dest: str, *, mode: str, when: str | None = None) -> dict[str, str]:
    mount = {"op": mode, "source": source, "dest": dest}
    if when is not None:
        mount["when"] = when
    return mount


def sandbox_plan(
    *,
    repo_present: bool,
    workspace_write: bool,
    git_metadata_present: bool,
    include_venv: bool,
    include_codex: bool,
    include_code_mode_host: bool,
    include_auth: bool,
    service_network: bool,
    git_inspection: bool,
) -> dict[str, object]:
    operations: list[dict[str, object]] = [
        _bind(path, path, mode="--ro-bind", when="exists") for path in SYSTEM_READONLY_BINDS
    ]
    operations.extend(
        _bind(path, path, mode="--ro-bind", when="exists") for path in HOST_READONLY_BINDS
    )
    operations.extend(
        (
            {"op": "--tmpfs", "dest": "/tmp"},
            {"op": "--dir", "dest": "/tmp/zelo-home"},
            {"op": "--proc", "dest": "/proc"},
            {"op": "--dev", "dest": "/dev"},
        )
    )
    if repo_present:
        operations.append(
            _bind(_REPO_SOURCE, REPO, mode="--bind" if workspace_write else "--ro-bind")
        )
    else:
        operations.append({"op": "--dir", "dest": REPO})
    if git_metadata_present:
        operations.append(_bind(_GIT_SOURCE, REPO + "/.git", mode="--ro-bind"))
        if git_inspection:
            operations.append(
                _bind(
                    _GITCONFIG_SOURCE,
                    REPO + "/.git/config",
                    mode="--ro-bind",
                )
            )
    if include_venv:
        operations.extend(
            (
                _bind(_VENV_SOURCE, VENV, mode="--ro-bind"),
                _bind(_VENV_SOURCE, REPO + "/.venv", mode="--ro-bind"),
            )
        )
    if include_codex:
        operations.extend(
            (
                {"op": "--tmpfs", "dest": "/opt/zelo-codex"},
                {"op": "--perms", "value": "0500"},
                {"op": "--file", "fd_ref": _CODEX_FD, "dest": CODEX},
            )
        )
        if include_code_mode_host:
            operations.extend(
                (
                    {"op": "--perms", "value": "0500"},
                    {"op": "--file", "fd_ref": _CODE_MODE_HOST_FD, "dest": CODE_MODE_HOST},
                )
            )
        operations.extend(
            (
                {"op": "--remount-ro", "dest": "/opt/zelo-codex"},
                {"op": "--dir", "dest": ENGINE},
            )
        )
    if include_auth:
        operations.append(_bind(_AUTH_SOURCE, ENGINE + "/auth.json", mode="--ro-bind"))
    return {
        "namespaces": {
            "user": True,
            "pid": True,
            "ipc": True,
            "network": not service_network,
        },
        "lifecycle": {"die_with_parent": True},
        "workspace": {"path": REPO, "present": repo_present, "writable": workspace_write},
        "git_metadata": {
            "path": REPO + "/.git",
            "present": git_metadata_present,
            "writable": False,
            "git_inspection": git_inspection,
        },
        "venv": {"present": include_venv, "paths": (VENV, REPO + "/.venv"), "writable": False},
        "codex": {
            "present": include_codex,
            "path": "/opt/zelo-codex",
            "sealed": True,
            "code_mode_host": include_code_mode_host,
            "engine_dir": ENGINE,
        },
        "auth": {"present": include_auth, "path": ENGINE + "/auth.json", "writable": False},
        "operations": tuple(operations),
        "cwd": REPO,
    }


def _render_operation(
    operation: dict[str, object],
    *,
    repo: Path | None,
    git: Path | None,
    venv: Path | None,
    auth: Path | None,
    codex_fd: int | None,
    code_mode_host_fd: int | None,
    worker_fd: int | None = None,
) -> list[str]:
    def resolve_source(source: str) -> str:
        if source == _REPO_SOURCE:
            assert repo is not None
            return str(repo)
        if source == _GIT_SOURCE:
            assert git is not None
            return str(git)
        if source == _VENV_SOURCE:
            assert venv is not None
            return str(venv)
        if source == _AUTH_SOURCE:
            assert auth is not None
            return str(auth)
        if source == _GITCONFIG_SOURCE:
            return str(Path(__file__).with_name("developer_gitconfig"))
        return source

    op = operation["op"]
    if op in {"--bind", "--ro-bind"}:
        source = resolve_source(str(operation["source"]))
        when = operation.get("when")
        if when == "exists" and not Path(source).exists():
            return []
        return [str(op), source, str(operation["dest"])]
    if op == "--file":
        fd_ref = operation["fd_ref"]
        if fd_ref == _CODEX_FD:
            fd = codex_fd
        elif fd_ref == _CODE_MODE_HOST_FD:
            fd = code_mode_host_fd
        elif fd_ref == _WORKER_FD:
            fd = worker_fd
        else:
            raise DeveloperError("worker_sandbox_plan_invalid")
        if fd is None:
            raise DeveloperError("worker_artifact_not_pinned")
        return [str(op), str(fd), str(operation["dest"])]
    if op == "--perms":
        return [str(op), str(operation["value"])]
    return [str(op), str(operation["dest"])]


def _render_switches(plan: dict[str, object]) -> list[str]:
    args = [str(BWRAP)]
    namespace_flags = {
        "user": "--unshare-user",
        "pid": "--unshare-pid",
        "ipc": "--unshare-ipc",
        "network": "--unshare-net",
        "uts": "--unshare-uts",
    }
    for name, flag in namespace_flags.items():
        if plan["namespaces"].get(name, False):
            args.append(flag)
    if plan["namespaces"].get("uts", False):
        args.extend(("--hostname", str(plan.get("hostname", "zelo-sandbox"))))
    if plan.get("disable_userns", False):
        args.append("--disable-userns")
    if plan["lifecycle"]["die_with_parent"]:
        args.append("--die-with-parent")
    return args


def worker_sandbox_plans(workspace_write: bool) -> dict[str, dict[str, object]]:
    return {
        "worker_preflight": sandbox_plan(
            repo_present=False,
            workspace_write=workspace_write,
            git_metadata_present=False,
            include_venv=False,
            include_codex=True,
            include_code_mode_host=True,
            include_auth=False,
            service_network=False,
            git_inspection=False,
        ),
        "worker_execution": sandbox_plan(
            repo_present=True,
            workspace_write=workspace_write,
            git_metadata_present=True,
            include_venv=False,
            include_codex=True,
            include_code_mode_host=True,
            include_auth=True,
            service_network=True,
            git_inspection=False,
        ),
    }


def external_worker_sandbox_plan(
    *,
    repo_present: bool,
    workspace_write: bool,
    service_network: bool,
) -> dict[str, object]:
    """Canonical outer sandbox for non-Codex worker adapters.

    The trusted worker parent may use service networking for its model API while
    the adapter's own policy must separately confine model-invoked subprocesses.
    No credential file is mounted into this namespace.
    """
    plan = sandbox_plan(
        repo_present=repo_present,
        workspace_write=workspace_write,
        git_metadata_present=repo_present,
        include_venv=False,
        include_codex=False,
        include_code_mode_host=False,
        include_auth=False,
        service_network=service_network,
        git_inspection=False,
    )
    operations = list(plan["operations"])
    if repo_present:
        operations.append(
            _bind(
                _GITCONFIG_SOURCE,
                REPO + "/.git/config",
                mode="--ro-bind",
            )
        )
    operations.extend(
        (
            {"op": "--tmpfs", "dest": WORKER_ROOT},
            {"op": "--perms", "value": "0500"},
            {"op": "--file", "fd_ref": _WORKER_FD, "dest": WORKER_EXECUTABLE},
            {"op": "--remount-ro", "dest": WORKER_ROOT},
        )
    )
    return {
        **plan,
        "git_metadata": {
            **plan["git_metadata"],
            "trusted_config": repo_present,
        },
        "external_worker": {
            "present": True,
            "path": WORKER_EXECUTABLE,
            "sealed": True,
        },
        "operations": tuple(operations),
    }


def external_worker_sandbox_plans(workspace_write: bool) -> dict[str, dict[str, object]]:
    return {
        "worker_preflight": external_worker_sandbox_plan(
            repo_present=False,
            workspace_write=workspace_write,
            service_network=False,
        ),
        "worker_execution": external_worker_sandbox_plan(
            repo_present=True,
            workspace_write=workspace_write,
            service_network=True,
        ),
    }


def python_compute_sandbox_plan() -> dict[str, object]:
    """Minimal no-workspace/no-network filesystem for bounded Python compute."""
    operations: tuple[dict[str, object], ...] = (
        {"op": "--tmpfs", "dest": "/"},
        {"op": "--tmpfs", "dest": "/usr"},
        {"op": "--dir", "dest": "/usr/bin"},
        {"op": "--dir", "dest": "/usr/lib"},
        {"op": "--tmpfs", "dest": "/lib"},
        {"op": "--tmpfs", "dest": "/lib64"},
        _bind(PYTHON_EXECUTABLE, PYTHON_EXECUTABLE, mode="--ro-bind"),
        _bind(PYTHON_STDLIB, PYTHON_STDLIB, mode="--ro-bind"),
        _bind(PYTHON_ARCH_LIB, PYTHON_ARCH_LIB, mode="--ro-bind"),
        _bind(PYTHON_ARCH_LIB, "/lib/x86_64-linux-gnu", mode="--ro-bind"),
        _bind(PYTHON_LOADER, "/lib64/ld-linux-x86-64.so.2", mode="--ro-bind"),
        {"op": "--proc", "dest": "/proc"},
        {"op": "--dev", "dest": "/dev"},
        {"op": "--tmpfs", "dest": REPO},
        {"op": "--tmpfs", "dest": "/tmp"},
        {"op": "--remount-ro", "dest": "/proc"},
        {"op": "--remount-ro", "dest": "/dev"},
        {"op": "--remount-ro", "dest": "/usr"},
        {"op": "--remount-ro", "dest": "/lib"},
        {"op": "--remount-ro", "dest": "/lib64"},
        {"op": "--remount-ro", "dest": REPO},
        {"op": "--remount-ro", "dest": "/tmp"},
        {"op": "--remount-ro", "dest": "/"},
    )
    return {
        "namespaces": {
            "user": True,
            "pid": True,
            "ipc": True,
            "network": True,
            "uts": True,
        },
        "hostname": "zelo-python",
        "disable_userns": True,
        "lifecycle": {"die_with_parent": True},
        "workspace": {"path": REPO, "present": False, "writable": False},
        "network": {"enabled": False},
        "credentials": {"present": False},
        "python": {
            "executable": PYTHON_EXECUTABLE,
            "stdlib": PYTHON_STDLIB,
            "arch_lib": PYTHON_ARCH_LIB,
            "isolated": True,
            "site_import": False,
        },
        "operations": operations,
        "cwd": REPO,
    }


def python_compute_sandbox(command: list[str]) -> list[str]:
    if not BWRAP.is_file():
        raise DeveloperError("bubblewrap_unavailable")
    if not command or command[0] != PYTHON_EXECUTABLE:
        raise DeveloperError("python_sandbox_command_invalid")
    for path in (PYTHON_EXECUTABLE, PYTHON_STDLIB, PYTHON_ARCH_LIB, PYTHON_LOADER):
        if not Path(path).exists():
            raise DeveloperError("python_runtime_unavailable")
    plan = python_compute_sandbox_plan()
    args = _render_switches(plan)
    for operation in plan["operations"]:
        args.extend(
            _render_operation(
                operation,
                repo=None,
                git=None,
                venv=None,
                auth=None,
                codex_fd=None,
                code_mode_host_fd=None,
            )
        )
    return [*args, "--chdir", str(plan["cwd"]), *command]


def trusted_boundary_sandbox_plans() -> dict[str, dict[str, object]]:
    return {
        "validation": sandbox_plan(
            repo_present=True,
            workspace_write=True,
            git_metadata_present=True,
            include_venv=True,
            include_codex=False,
            include_code_mode_host=False,
            include_auth=False,
            service_network=False,
            git_inspection=False,
        ),
        "git_inspection": sandbox_plan(
            repo_present=True,
            workspace_write=False,
            git_metadata_present=True,
            include_venv=False,
            include_codex=False,
            include_code_mode_host=False,
            include_auth=False,
            service_network=False,
            git_inspection=True,
        ),
        "review_inventory": sandbox_plan(
            repo_present=True,
            workspace_write=False,
            git_metadata_present=True,
            include_venv=False,
            include_codex=False,
            include_code_mode_host=False,
            include_auth=False,
            service_network=False,
            git_inspection=False,
        ),
    }


def sandbox_policy_fingerprint(policies: dict[str, object]) -> str:
    payload = {"schema": SANDBOX_POLICY_SCHEMA, "policies": policies}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def sandbox_policy_version() -> str:
    return sandbox_policy_fingerprint(trusted_boundary_sandbox_plans())


def sandbox(
    repo: Path | None,
    command: list[str],
    *,
    writable: bool = True,
    venv: Path | None = None,
    codex_fd: int | None = None,
    code_mode_host_fd: int | None = None,
    auth: Path | None = None,
    service_network: bool = False,
    git_inspection: bool = False,
) -> list[str]:
    if not BWRAP.is_file():
        raise DeveloperError("bubblewrap_unavailable")
    if repo is not None and (repo.is_symlink() or not repo.is_dir()):
        raise DeveloperError("workspace_escape")
    git = repo / ".git" if repo is not None else None
    plan = sandbox_plan(
        repo_present=repo is not None,
        workspace_write=writable,
        git_metadata_present=git is not None and git.exists(),
        include_venv=venv is not None,
        include_codex=codex_fd is not None,
        include_code_mode_host=code_mode_host_fd is not None,
        include_auth=auth is not None,
        service_network=service_network,
        git_inspection=git_inspection,
    )
    args = _render_switches(plan)
    if git is not None and git.exists():
        if git.is_symlink() or not git.is_dir():
            raise DeveloperError("git_metadata_invalid")
        if git_inspection and (git / "config").is_symlink():
            raise DeveloperError("git_metadata_invalid")
    # Only the Codex parent treats project Codex configuration as forbidden.
    # Validation and Git inspection do not load it; rejecting it there prevented
    # trusted validation of an otherwise valid isolated Zelo checkout.
    if codex_fd is not None:
        for name in (".codex", ".agents"):
            if repo is not None and (repo / name).exists():
                raise DeveloperError("project_worker_configuration_forbidden")
    if plan["venv"]["present"]:
        if repo is None:
            raise DeveloperError("validation_environment_invalid")
        # Project tests may resolve ROOT/.venv (including nested boundary tests).
        # Both aliases refer to trusted tools, never a worker-created environment.
        local_venv = repo / ".venv"
        if local_venv.is_symlink() or (local_venv.exists() and not local_venv.is_dir()):
            raise DeveloperError("validation_environment_invalid")
    if plan["codex"]["present"]:
        if command and command[0] == CODEX and code_mode_host_fd is None:
            raise DeveloperError("code_mode_host_unavailable")
    if plan["auth"]["present"]:
        if auth.is_symlink() or not auth.is_file():
            raise DeveloperError("codex_auth_unavailable")
    for operation in plan["operations"]:
        args.extend(
            _render_operation(
                operation,
                repo=repo,
                git=git,
                venv=venv,
                auth=auth,
                codex_fd=codex_fd,
                code_mode_host_fd=code_mode_host_fd,
            )
        )
    return [*args, "--chdir", str(plan["cwd"]), *command]


def external_worker_sandbox(
    repo: Path | None,
    command: list[str],
    *,
    worker_fd: int,
    writable: bool,
    service_network: bool,
) -> list[str]:
    """Render the canonical outer sandbox for a sealed external worker binary."""
    if not BWRAP.is_file():
        raise DeveloperError("bubblewrap_unavailable")
    if repo is not None and (repo.is_symlink() or not repo.is_dir()):
        raise DeveloperError("workspace_escape")
    git = repo / ".git" if repo is not None else None
    if git is not None and git.exists():
        if git.is_symlink() or not git.is_dir() or (git / "config").is_symlink():
            raise DeveloperError("git_metadata_invalid")
    plan = external_worker_sandbox_plan(
        repo_present=repo is not None,
        workspace_write=writable,
        service_network=service_network,
    )
    args = _render_switches(plan)
    for operation in plan["operations"]:
        args.extend(
            _render_operation(
                operation,
                repo=repo,
                git=git,
                venv=None,
                auth=None,
                codex_fd=None,
                code_mode_host_fd=None,
                worker_fd=worker_fd,
            )
        )
    return [*args, "--chdir", str(plan["cwd"]), *command]


def git_argv(*arguments: str) -> list[str]:
    return [
        "/usr/bin/git",
        "--no-pager",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "credential.helper=",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "diff.external=",
        "-c",
        "protocol.allow=never",
        "-c",
        "safe.directory=/workspace",
        *arguments,
    ]
