"""Security-review regressions: real local processes, never a model service."""

import asyncio
import errno
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.codex_worker import CodexCliWorker
from app.developer import DeveloperError, tree_digest
from app.developer_artifacts import pin_executable
from app.developer_process import run_process
from app.developer_sandbox import CODEX, sandbox
from app.developer_validation import validation_manifest
from app.policy import State
from app.runner import Runner
from app.schemas import DeveloperRunInput
from app.tasks import TaskService
from tests.developer_helpers import git
from tests.test_developer_boundary import prepared, spec


@pytest.fixture
def pinned_worker(developer_case):
    runtime = developer_case
    binary = runtime.settings.workspace_root / "fake-codex"
    binary.write_text(
        "#!/usr/bin/python3\nimport sys,json\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.154.0')\n"
        "elif '--help' in sys.argv:\n print('--ignore-user-config --ignore-rules "
        "--ephemeral --json --strict-config workspace-write --ask-for-approval "
        "--permission-profile')\n"
        "else:\n"
        " print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'approved artifact executed'}}))\n"
        " print(json.dumps({'type':'turn.completed'}))\n"
    )
    binary.chmod(0o700)
    host = binary.with_name("codex-code-mode-host")
    host.write_text("#!/bin/sh\nexit 0\n")
    host.chmod(0o700)
    auth = runtime.settings.workspace_root / "fake-auth.json"
    auth.write_text("harmless-fake-auth")
    runtime.settings.developer_codex_executable = binary
    runtime.settings.developer_codex_auth_file = auth
    return CodexCliWorker(runtime.settings)


def replacement(binary: Path, marker: Path):
    binary.write_text(
        "#!/usr/bin/python3\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\nraise SystemExit(27)\n"
    )
    binary.chmod(0o700)


def test_approved_binary_replacement_fails_before_worker_execution(
    developer_case, pinned_worker, database
):
    runtime = developer_case
    runtime.worker = pinned_worker
    service = TaskService(database, runtime.settings, developer_runtime=runtime)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "local fixture"}), None
    )
    service.decide(task.approvals[0].id, "approve")
    before = tree_digest(runtime.source_repository)
    marker = runtime.settings.workspace_root / "UNAPPROVED_EXECUTED"
    replacement(runtime.settings.developer_codex_executable, marker)

    execution = service.claim()
    assert execution is not None
    asyncio.run(Runner(service, developer_runtime=runtime)._run(execution))
    result = service.get(task.id)
    assert result.status == State.FAILED
    assert result.error == "worker_configuration_changed"
    assert not marker.exists()
    assert tree_digest(runtime.source_repository) == before


def test_binary_replacement_invalidates_at_operator_approval_without_execution(
    developer_case, pinned_worker, database
):
    runtime = developer_case
    runtime.worker = pinned_worker
    service = TaskService(database, runtime.settings, developer_runtime=runtime)
    task, _ = service.submit(
        DeveloperRunInput(kind="developer_run", payload={"instruction": "local fixture"}), None
    )
    marker = runtime.settings.workspace_root / "UNAPPROVED_APPROVAL_PROBE_EXECUTED"
    replacement(runtime.settings.developer_codex_executable, marker)

    with pytest.raises(Exception, match="no longer pending|conflicts"):
        service.decide(task.approvals[0].id, "approve")
    current = service.get(task.id)

    assert current.status == State.CANCELLED
    assert current.error == "approval_invalidated"
    assert current.approvals[0].status == "invalidated"
    assert not marker.exists()


def test_pin_executable_preserves_oserror_from_caller(pinned_worker):
    with pytest.raises(OSError, match="caller failure"):
        with pin_executable(pinned_worker.settings.developer_codex_executable):
            raise OSError("caller failure")


def test_source_replacement_after_pinning_cannot_change_preflight(pinned_worker):
    approved = pinned_worker.identity()
    source = pinned_worker.settings.developer_codex_executable
    marker = source.parent / "UNAPPROVED_EXECUTED"
    with pinned_worker.bind(approved) as bound:
        artifact = bound.artifact()
        assert (os.fstat(artifact.fd).st_dev, os.fstat(artifact.fd).st_ino) != (
            source.stat().st_dev,
            source.stat().st_ino,
        )
        replacement(source, marker)
        with pytest.raises(OSError) as failure:
            os.pwrite(artifact.fd, b"x", 0)
        assert failure.value.errno == errno.EPERM
        assert asyncio.run(bound.preflight()) == approved
    assert not marker.exists()


@pytest.mark.developer_boundary
def test_replacement_between_probe_and_launch_uses_same_sealed_bytes(
    developer_case, pinned_worker, monkeypatch
):
    repo, request = prepared(developer_case)
    approved = pinned_worker.identity()
    request = request.model_copy(update={"worker": approved})
    marker = repo.parent / "UNAPPROVED_EXECUTED"
    with pinned_worker.bind(approved) as bound:
        descriptor = bound.artifact().fd

        async def local_probe(*args):
            assert await bound.preflight() == approved
            replacement(pinned_worker.settings.developer_codex_executable, marker)
            assert bound.artifact().fd == descriptor

        monkeypatch.setattr(bound, "probe", local_probe)
        result = asyncio.run(bound.run(request, repo))
        assert result.summary == "approved artifact executed"
    assert not marker.exists()


@pytest.mark.developer_boundary
def test_bubblewrap_cannot_write_sealed_worker_artifact(developer_case, pinned_worker):
    repo, _ = prepared(developer_case)
    with pin_executable(pinned_worker.settings.developer_codex_executable) as artifact:
        code = f"""from pathlib import Path
import hashlib
assert hashlib.sha256(Path({CODEX!r}).read_bytes()).hexdigest() == {artifact.digest!r}
try:
    Path({CODEX!r}).write_bytes(b'changed')
except OSError:
    print('DENIED')
else:
    raise SystemExit(25)
"""
        result = asyncio.run(
            run_process(
                sandbox(repo, ["/usr/bin/python3", "-I", "-c", code], codex_fd=artifact.fd),
                cwd=repo,
                timeout=5,
                pass_fds=(artifact.fd,),
            )
        )
        assert result.exit_code == 0 and result.stdout == b"DENIED\n"


def test_snapshot_precedes_every_worker_capability_probe(developer_case, monkeypatch):
    runtime = developer_case
    request = spec(runtime)
    events = []
    snapshot = runtime.primary_snapshot
    preflight = runtime.worker.preflight

    async def capture(*args):
        events.append("snapshot")
        return await snapshot(*args)

    async def probe():
        assert events == ["snapshot"]
        events.append("preflight")
        return await preflight()

    async def stop(*args):
        raise DeveloperError("fixture_stop")

    monkeypatch.setattr(runtime, "primary_snapshot", capture)
    monkeypatch.setattr(runtime.worker, "preflight", probe)
    monkeypatch.setattr(runtime, "prepare", stop)
    result = asyncio.run(runtime.run(str(uuid4()), str(uuid4()), request, lambda *args: None))
    assert result["error"] == "fixture_stop"
    assert events == ["snapshot", "preflight", "snapshot"]


@pytest.fixture
def validation_tree(developer_case):
    root = developer_case.settings.workspace_root / "validation-env"
    for name in (
        "pyvenv.cfg",
        "bin/python",
        "bin/ruff",
        "bin/pytest",
        "lib/site-packages/_pytest/runner.py",
        "lib/site-packages/dependency.py",
        "lib/site-packages/dependency.dist-info/METADATA",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial fixture bytes\n")
    return root


@pytest.mark.parametrize(
    "name",
    [
        "bin/python",
        "bin/ruff",
        "bin/pytest",
        "lib/site-packages/_pytest/runner.py",
        "lib/site-packages/dependency.py",
        "lib/site-packages/dependency.dist-info/METADATA",
    ],
)
def test_validator_bytes_change_invalidates_approval(
    developer_case, validation_tree, monkeypatch, name
):
    runtime = developer_case
    monkeypatch.setattr(
        runtime,
        "validation_identity",
        lambda: validation_manifest(validation_tree, runtime.source_repository),
    )
    request = spec(runtime)
    (validation_tree / name).write_text("changed after approval\n")
    result = asyncio.run(runtime.run(str(uuid4()), str(uuid4()), request, lambda *args: None))
    assert result["error"] == "worker_configuration_changed"
    assert runtime.worker.calls == []


@pytest.mark.parametrize(
    "name", ["requirements.txt", "requirements-dev.txt", "pyproject.toml", "uv.lock", "poetry.lock"]
)
def test_dependency_inputs_bind_validation_manifest(developer_case, validation_tree, name):
    source = developer_case.source_repository
    before = validation_manifest(validation_tree, source)
    (source / name).write_text("fixture dependency change")
    assert validation_manifest(validation_tree, source) != before


def test_validation_manifest_excludes_only_byte_caches(developer_case, validation_tree):
    source = developer_case.source_repository
    before = validation_manifest(validation_tree, source)
    cache = validation_tree / "lib/site-packages/__pycache__"
    cache.mkdir()
    (cache / "dependency.pyc").write_bytes(b"cache")
    assert validation_manifest(validation_tree, source) == before


@pytest.mark.developer_boundary
def test_dependency_change_during_worker_stops_validation(
    developer_case, validation_tree, monkeypatch
):
    runtime = developer_case
    monkeypatch.setattr(
        runtime,
        "validation_identity",
        lambda: validation_manifest(validation_tree, runtime.source_repository),
    )
    request = spec(runtime)

    async def change(*args):
        (validation_tree / "lib/site-packages/dependency.py").write_text("changed")

    runtime.worker.action = change
    result = asyncio.run(runtime.run(str(uuid4()), str(uuid4()), request, lambda *args: None))
    assert result["error"] == "validation_environment_changed"
    assert result["validation"] == []
    assert not result["validation_passed"]


@pytest.mark.developer_boundary
@pytest.mark.parametrize(
    "case", ["new_rule", "existing_rule", "binary", "oversized", "nested_unicode"]
)
def test_ignored_generated_files_cannot_hide(developer_case, case):
    repo, request = prepared(developer_case)
    name = "concealed.py"
    if case == "existing_rule":
        name = ".ruff_cache/hidden.txt"  # Already ignored by the approved fixture.
    elif case == "nested_unicode":
        name = 'hidden dir/é/new "file".txt'
    if case != "existing_rule":
        with (repo / ".gitignore").open("a") as stream:
            stream.write("concealed.py\nhidden dir/\n")
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = b"DO_NOT_PERSIST_IGNORED_CONTENT"
    if case == "binary":
        contents += b"\0\xff"
    if case == "oversized":
        contents *= 100000
    path.write_bytes(contents)
    result = asyncio.run(developer_case.review(repo, request))
    assert name in result["changed_files"]
    assert {"path": name, "type": "file", "size": len(contents)} in result[
        "ignored_generated_files"
    ]
    assert "DO_NOT_PERSIST_IGNORED_CONTENT" not in json.dumps(result)


@pytest.mark.developer_boundary
def test_ignored_files_count_toward_review_limit(developer_case):
    repo, request = prepared(developer_case)
    folder = repo / ".ruff_cache"
    folder.mkdir()
    for i in range(3):
        (folder / str(i)).write_text("fixture")
    request = request.model_copy(
        update={"limits": request.limits.model_copy(update={"changed_files": 2})}
    )
    with pytest.raises(DeveloperError, match="review_inventory_failed"):
        asyncio.run(developer_case.review(repo, request))


@pytest.mark.developer_boundary
@pytest.mark.parametrize(
    "parents",
    [
        ["nested"],
        ["ignored"],
        ["first", "second"],
        ["parent with spaces"],
        ["unicodé"],
    ],
)
def test_nested_git_artifacts_are_rejected(developer_case, parents):
    repo, request = prepared(developer_case)
    if parents == ["ignored"]:
        with (repo / ".gitignore").open("a") as stream:
            stream.write("ignored/\n")
    for parent in parents:
        nested = repo / parent / ".git"
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("must not escape review")
    with pytest.raises(DeveloperError, match="nested_git_artifact_forbidden"):
        asyncio.run(developer_case.review(repo, request))


@pytest.mark.developer_boundary
def test_nested_git_artifacts_count_toward_review_limit(developer_case):
    repo, request = prepared(developer_case)
    nested = repo / "nested" / ".git"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("must count")
    request = request.model_copy(
        update={"limits": request.limits.model_copy(update={"changed_files": 1})}
    )
    with pytest.raises(DeveloperError, match="review_inventory_failed"):
        asyncio.run(developer_case.review(repo, request))


@pytest.mark.developer_boundary
@pytest.mark.parametrize("prior", [None, "worker_fixture_failed"])
def test_verification_failure_is_not_mismatch(developer_case, monkeypatch, prior):
    runtime = developer_case
    request = spec(runtime)
    original = runtime.primary_snapshot
    calls = 0
    events = []

    async def snapshot(*args):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise DeveloperError("git_operation_failed")
        return await original(*args)

    if prior:

        async def fail(*args):
            raise DeveloperError(prior)

        runtime.worker.action = fail
    monkeypatch.setattr(runtime, "primary_snapshot", snapshot)
    result = asyncio.run(
        runtime.run(str(uuid4()), str(uuid4()), request, lambda name, details: events.append(name))
    )
    assert result["boundary_error"] == "developer_boundary_verification_failed"
    assert result["error"] == (prior or "developer_boundary_verification_failed")
    assert result["original_error"] == prior
    assert not result["validation_passed"]
    assert "developer_boundary_verification_failed" in events
    assert "developer_boundary_violation" not in events


@pytest.mark.developer_boundary
@pytest.mark.parametrize(
    "setting",
    [
        "core.fsmonitor",
        "core.hooksPath",
        "core.pager",
        "pager.diff",
        "diff.external",
        "diff.attack.textconv",
        "credential.helper",
        "include.path",
        "includeIf",
    ],
)
def test_executable_git_configuration_is_neutralized(developer_case, setting):
    repo, request = prepared(developer_case)
    marker = repo.parent / "HOST_CALLBACK_EXECUTED"
    callback = repo / "marker-hook"
    callback.write_text(
        f"#!/bin/sh\n/usr/bin/touch '{marker}'\n/usr/bin/touch /workspace/INSIDE_CALLBACK\n"
    )
    callback.chmod(0o700)
    command = "/workspace/marker-hook"
    if setting == "core.hooksPath":
        hooks = repo / "hooks"
        hooks.mkdir()
        (hooks / "post-index-change").write_bytes(callback.read_bytes())
        (hooks / "post-index-change").chmod(0o700)
        value = "/workspace/hooks"
    elif setting in {"include.path", "includeIf"}:
        config = repo / "attack.config"
        config.write_text(
            "[core]\n fsmonitor = /workspace/marker-hook\n"
            "[diff]\n external = /workspace/marker-hook\n"
        )
        value = "/workspace/attack.config"
        if setting == "includeIf":
            setting = "includeIf.gitdir:/workspace/.git.path"
    else:
        value = command
    git(repo, "config", setting, value)
    (repo / ".gitattributes").write_text("*.py diff=attack\n")
    (repo / "test_example.py").write_text("def test_example():\n    assert False\n")
    result = asyncio.run(developer_case.review(repo, request))
    assert "test_example.py" in result["changed_files"]
    assert not marker.exists() and not (repo / "INSIDE_CALLBACK").exists()
