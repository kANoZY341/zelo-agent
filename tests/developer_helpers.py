"""Local-only developer fixtures. Never invoke a model or mutate the source checkout."""

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.config import ROOT
from app.developer import DeveloperRuntime
from app.developer_contracts import WorkerIdentity, WorkerResult
from app.developer_process import run_sync
from app.developer_sandbox import git_argv


def git(repo, *args, stdin=None):
    if stdin is not None:
        import asyncio

        from app.developer_process import run_process

        result = asyncio.run(run_process(git_argv(*args), cwd=repo, timeout=5, stdin=stdin))
    else:
        result = run_sync(git_argv(*args), cwd=repo)
    assert result.exit_code == 0, result.stderr
    return result.stdout.decode().strip()


def fixture_commit(repo):
    """Build a commit object only in a disposable fixture; no git commit command."""
    git(repo, "add", "--all")
    tree = git(repo, "write-tree")
    content = (
        f"tree {tree}\nauthor Test <test@example.invalid> 1 +0000\n"
        "committer Test <test@example.invalid> 1 +0000\n\nfixture\n"
    )
    sha = git(repo, "hash-object", "-w", "-t", "commit", "--stdin", stdin=content.encode())
    git(repo, "update-ref", "HEAD", sha)
    return sha


class FakeWorker:
    def __init__(self):
        self.calls = []
        self.action = None
        self.nonfatal_error_items = 0
        self.worker_identity = WorkerIdentity(
            implementation="fake",
            config_version="1",
            cli_version="fake-1",
            capability_profile="test",
            executable="test-worker",
            executable_sha256="a" * 64,
            code_mode_host="test-code-mode-host",
            code_mode_host_sha256="b" * 64,
            auth_location="none",
        )

    def identity(self):
        return self.worker_identity

    @contextmanager
    def bind(self, approved):
        yield self

    async def preflight(self):
        return self.identity()

    async def run(self, request, repo):
        self.calls.append(repo)
        started = datetime.now(UTC)
        if self.action:
            await self.action(request, repo)
        else:
            (repo / "DEVELOPER_SMOKE.txt").write_text("Zelo developer worker online\n")
        return WorkerResult(
            exit_code=0,
            summary="Fake worker complete",
            event_count=3,
            nonfatal_error_items=self.nonfatal_error_items,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=0,
        )


@pytest.fixture
def developer_case(settings, monkeypatch):
    # Isolate parallel tests while preserving shared locks across forked children.
    lock_root = settings.runtime_dir / "developer-locks"

    def isolated_lock_root(self):
        root = self.root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root()
        lock_root.mkdir(exist_ok=True, mode=0o700)
        return lock_root

    monkeypatch.setattr(DeveloperRuntime, "_lock_root", isolated_lock_root)
    (ROOT / "workspaces").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="developer-test-", dir=ROOT / "workspaces") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        git(source, "init", "--quiet")
        (source / "test_example.py").write_text("def test_example():\n    assert True\n")
        (source / ".gitignore").write_text(".ruff_cache/\n.pytest_cache/\n__pycache__/\n")
        fixture_commit(source)
        workspace = root / "workspaces"
        workspace.mkdir()
        config = settings.model_copy(update={"workspace_root": workspace})
        worker = FakeWorker()
        yield DeveloperRuntime(config, worker, source)
