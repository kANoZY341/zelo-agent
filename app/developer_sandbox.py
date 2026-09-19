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
    args = [str(BWRAP), "--unshare-user", "--unshare-pid", "--unshare-ipc", "--die-with-parent"]
    if not service_network:
        args.append("--unshare-net")
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    # No /etc tree, host /home, /run, source checkout or sibling workspaces.
    for path in ("/etc/ld.so.cache", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts"):
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    args.extend(["--tmpfs", "/tmp", "--dir", "/tmp/zelo-home", "--proc", "/proc", "--dev", "/dev"])
    if repo is None:
        args.extend(["--dir", REPO])
    else:
        args.extend(["--bind" if writable else "--ro-bind", str(repo), REPO])
    git = repo / ".git" if repo is not None else None
    if git is not None and git.exists():
        if git.is_symlink() or not git.is_dir():
            raise DeveloperError("git_metadata_invalid")
        # Commands may edit source, but cannot replace config, refs, hooks or objects.
        args.extend(["--ro-bind", str(git), REPO + "/.git"])
        if git_inspection:
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
    if venv is not None:
        if repo is None:
            raise DeveloperError("validation_environment_invalid")
        # Project tests may resolve ROOT/.venv (including nested boundary tests).
        # Both aliases refer to trusted tools, never a worker-created environment.
        local_venv = repo / ".venv"
        if local_venv.is_symlink() or (local_venv.exists() and not local_venv.is_dir()):
            raise DeveloperError("validation_environment_invalid")
        args.extend(["--ro-bind", str(venv), VENV, "--ro-bind", str(venv), REPO + "/.venv"])
    if codex_fd is not None:
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
    if auth is not None:
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
