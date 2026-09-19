"""Deterministic, cache-free content manifest of the trusted validation inputs."""

import hashlib
import json
import os
import stat
from pathlib import Path

from app.developer_process import DeveloperError

VALIDATION_VERSION = "trusted-venv-content-manifest-v3"
INPUTS = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "uv.lock", "poetry.lock")
PYCACHE_PREFIX = "/tmp/zelo-python-cache"


def python_command(*arguments: str) -> list[str]:
    """Run validation from source, never from excluded bytecode caches."""
    return ["/opt/zelo-venv/bin/python", "-I", "-X", f"pycache_prefix={PYCACHE_PREFIX}", *arguments]


def validation_manifest(venv: Path, project: Path) -> str:
    """Hash all venv regular-file bytes and paths, excluding only Python byte caches.

    This is change detection at execution boundaries, not a frozen environment:
    trusted host administrators must not mutate the environment during a run.
    Symlink files bind both target identity and bytes; directory aliases must
    resolve within the venv (e.g. lib64 -> lib), never add an external package tree.
    """
    digest = hashlib.sha256(VALIDATION_VERSION.encode())
    count = total = 0

    def add(label: str, path: Path):
        nonlocal count, total
        count += 1
        if count > 50000:
            raise DeveloperError("validation_manifest_limit")
        target = path.resolve(strict=True)
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise DeveloperError("validation_environment_invalid")
            content = hashlib.sha256()
            while block := stream.read(65536):
                total += len(block)
                if total > 512 * 1024 * 1024:
                    raise DeveloperError("validation_manifest_limit")
                content.update(block)
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise DeveloperError("validation_environment_changed")
        digest.update(
            json.dumps(
                [
                    label,
                    str(target) if path.is_symlink() else None,
                    before.st_mode,
                    content.hexdigest(),
                ]
            ).encode()
        )

    try:
        root = venv.resolve(strict=True)
        for required in ("pyvenv.cfg", "bin/python", "bin/ruff", "bin/pytest"):
            if not (venv / required).is_file():
                raise DeveloperError("validation_environment_unavailable")
        for directory, directories, files in os.walk(venv, followlinks=False):
            directories[:] = sorted(d for d in directories if d != "__pycache__")
            for name in directories:
                path = Path(directory) / name
                if path.is_symlink():
                    target = path.resolve(strict=True)
                    if not target.is_relative_to(root):
                        raise DeveloperError("validation_environment_invalid")
                    digest.update(json.dumps([str(path.relative_to(venv)), str(target)]).encode())
            for name in sorted(files):
                path = Path(directory) / name
                if path.parent.name == "__pycache__" and name.endswith((".pyc", ".pyo")):
                    continue
                add("venv/" + str(path.relative_to(venv)), path)
        # Includes distribution METADATA/RECORD as well as actual package code.
        for name in INPUTS:
            path = project / name
            if path.exists():
                add("project/" + name, path)
            else:
                digest.update(json.dumps(["project/" + name, "absent"]).encode())
    except OSError:
        raise DeveloperError("validation_environment_unavailable") from None
    return digest.hexdigest()
