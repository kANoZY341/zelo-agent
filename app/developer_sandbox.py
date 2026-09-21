"""Minimal Linux filesystem views shared by validation, Git and the Codex parent."""

from pathlib import Path

from app.developer_process import DeveloperError

BWRAP = Path("/usr/bin/bwrap")
SANDBOX_VERSION = "zelo-linux-v3-sealed-artifacts"
REPO = "/workspace"
VENV = "/opt/zelo-venv"
ENGINE = "/tmp/zelo-codex-engine"
CODEX = "/opt/zelo-codex/codex"
CODE_MODE_HOST = "/opt/zelo-codex/codex-code-mode-host"
SYSTEM_READONLY_BINDS = ("/usr", "/bin", "/lib", "/lib64")
HOST_READONLY_BINDS = ("/etc/ld.so.cache", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts")


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
    return {
        "argv_prefix": ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--die-with-parent"),
        "unshare_network": not service_network,
        "system_readonly_binds": SYSTEM_READONLY_BINDS,
        "host_readonly_binds": HOST_READONLY_BINDS,
        "tmpfs": ("/tmp",),
        "directories": ("/tmp/zelo-home",),
        "proc": "/proc",
        "dev": "/dev",
        "workspace": {
            "path": REPO,
            "mode": "--bind" if workspace_write else "--ro-bind",
            "present": repo_present,
        },
        "git_metadata": {
            "path": REPO + "/.git",
            "mode": "--ro-bind",
            "present": git_metadata_present,
            "git_inspection": git_inspection,
        },
        "venv": {
            "present": include_venv,
            "paths": (VENV, REPO + "/.venv"),
            "mode": "--ro-bind",
        },
        "codex": {
            "present": include_codex,
            "path": "/opt/zelo-codex",
            "sealed": True,
            "mode": "tmpfs-file-remount-ro",
            "code_mode_host": include_code_mode_host,
            "engine_dir": ENGINE,
        },
        "auth": {
            "present": include_auth,
            "path": ENGINE + "/auth.json",
            "mode": "--ro-bind",
        },
        "cwd": REPO,
    }


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
    args = [str(BWRAP), *plan["argv_prefix"]]
    if plan["unshare_network"]:
        args.append("--unshare-net")
    for path in plan["system_readonly_binds"]:
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    # No /etc tree, host /home, /run, source checkout or sibling workspaces.
    for path in plan["host_readonly_binds"]:
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    args.extend(
        [
            "--tmpfs",
            *plan["tmpfs"],
            "--dir",
            *plan["directories"],
            "--proc",
            plan["proc"],
            "--dev",
            plan["dev"],
        ]
    )
    if repo is None:
        args.extend(["--dir", REPO])
    else:
        args.extend([plan["workspace"]["mode"], str(repo), REPO])
    if git is not None and git.exists():
        if git.is_symlink() or not git.is_dir():
            raise DeveloperError("git_metadata_invalid")
        # Commands may edit source, but cannot replace config, refs, hooks or objects.
        args.extend([plan["git_metadata"]["mode"], str(git), REPO + "/.git"])
        if plan["git_metadata"]["git_inspection"]:
            if (git / "config").is_symlink():
                raise DeveloperError("git_metadata_invalid")
            args.extend(
                [
                    "--ro-bind",
                    str(Path(__file__).with_name("developer_gitconfig")),
                    REPO + "/.git/config",
                ]
            )
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
        args.extend(["--ro-bind", str(venv), VENV, "--ro-bind", str(venv), REPO + "/.venv"])
    if plan["codex"]["present"]:
        if command and command[0] == CODEX and code_mode_host_fd is None:
            raise DeveloperError("code_mode_host_unavailable")
        # Materialize the sealed bytes into a private named tmpfs executable.
        # Descriptor bind mounts cannot resolve anonymous memfd dentries here.
        # Remount before execution: neither parent nor generated code can alter it.
        args.extend(
            [
                "--tmpfs",
                "/opt/zelo-codex",
                "--perms",
                "0500",
                "--file",
                str(codex_fd),
                CODEX,
                *(
                    ["--perms", "0500", "--file", str(code_mode_host_fd), CODE_MODE_HOST]
                    if code_mode_host_fd is not None
                    else []
                ),
                "--remount-ro",
                "/opt/zelo-codex",
                "--dir",
                ENGINE,
            ]
        )
    if plan["auth"]["present"]:
        if auth.is_symlink() or not auth.is_file():
            raise DeveloperError("codex_auth_unavailable")
        args.extend(["--ro-bind", str(auth), ENGINE + "/auth.json"])
    return [*args, "--chdir", REPO, *command]


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
