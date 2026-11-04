"""A passing Developer validation must include every Core preflight phase."""

import asyncio
import time

import pytest

from app.config import Settings
from app.developer_contracts import DeveloperRequest
from app.developer_process import DeveloperError, ProcessResult
from app.developer_validation import (
    VALIDATION_VERSION,
    run_pytest_profile,
    use_partitioned_pytest,
)
from app.promotion_review import validate_manifest_path
from app.schemas import DeveloperRunInput, PromotionRunInput


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", ".github/workflows/alternate.yaml"])
def test_promotion_rejects_candidate_workflow_paths(path):
    with pytest.raises(DeveloperError, match="promotion_workflow_forbidden"):
        validate_manifest_path(path)


def test_profile_fails_when_final_boundary_check_exceeds_deadline(tmp_path, monkeypatch):
    import app.developer_validation as module

    async def fake_run(argv, **kwargs):
        return ProcessResult(0, b"passed\n", b"", 0)

    checks = 0

    def verify_boundary():
        nonlocal checks
        checks += 1
        if checks == 2:
            time.sleep(0.05)

    monkeypatch.setattr(module, "sandbox", lambda _repo, command, **_kwargs: command)
    monkeypatch.setattr(module, "run_process", fake_run)
    with pytest.raises(DeveloperError, match="process_timeout"):
        asyncio.run(
            run_pytest_profile(
                tmp_path,
                venv=tmp_path,
                timeout=0.02,
                stdout_limit=32768,
                stderr_limit=16384,
                verify_boundary=verify_boundary,
                partitioned=False,
            )
        )


def test_profile_boundary_hash_does_not_block_event_loop(tmp_path, monkeypatch):
    import app.developer_validation as module

    async def fake_run(argv, **kwargs):
        return ProcessResult(0, b"passed\n", b"", 0)

    def verify_boundary():
        time.sleep(0.05)

    async def observe():
        profile = asyncio.create_task(
            run_pytest_profile(
                tmp_path,
                venv=tmp_path,
                timeout=1,
                stdout_limit=32768,
                stderr_limit=16384,
                verify_boundary=verify_boundary,
                partitioned=False,
            )
        )
        await asyncio.sleep(0.01)
        assert not profile.done()
        await profile

    monkeypatch.setattr(module, "sandbox", lambda _repo, command, **_kwargs: command)
    monkeypatch.setattr(module, "run_process", fake_run)
    asyncio.run(observe())


def test_full_source_runs_fixed_core_preflight(tmp_path, monkeypatch):
    import app.developer_validation as module

    source = tmp_path / "source"
    repo = tmp_path / "candidate"
    for root in (source, repo):
        tests = root / "tests"
        tests.mkdir(parents=True)
        (tests / "test_developer.py").write_text("pass\n")
        (tests / "test_developer_validation_profile.py").write_text("pass\n")
        (tests / "test_developer_boundary.py").write_text("pass\n")
        (tests / "test_worker_concurrency.py").write_text("pass\n")
    assert use_partitioned_pytest(source)
    seen = []
    checks = []

    async def fake_run(argv, **kwargs):
        seen.append(argv)
        return ProcessResult(0, b"passed\n", b"", 0)

    monkeypatch.setattr(module, "sandbox", lambda _repo, command, **_kwargs: command)
    monkeypatch.setattr(module, "run_process", fake_run)
    result = asyncio.run(
        run_pytest_profile(
            repo,
            venv=tmp_path / "venv",
            timeout=600,
            stdout_limit=32768,
            stderr_limit=16384,
            verify_boundary=lambda: checks.append(True),
            partitioned=use_partitioned_pytest(source),
        )
    )
    assert result.exit_code == 0
    assert len(seen) == 3 and len(checks) == 6
    assert "-n" not in seen[0]
    assert seen[0][-2:] == ["tests/test_developer.py", "tests/test_developer_validation_profile.py"]
    assert "tests/test_developer_boundary.py" in seen[1]
    assert any(arg.startswith("--deselect=tests/test_developer_boundary.py::") for arg in seen[1])
    assert seen[2][-1] == "tests/test_worker_concurrency.py"
    assert result.stdout.count(b"passed") == 3


def test_failed_serial_boundary_stops_validation_before_later_phase(tmp_path, monkeypatch):
    import app.developer_validation as module

    seen = []

    async def fake_run(argv, **kwargs):
        seen.append(argv)
        return ProcessResult(7 if len(seen) == 2 else 0, b"phase result\n", b"", 0)

    monkeypatch.setattr(module, "sandbox", lambda _repo, command, **_kwargs: command)
    monkeypatch.setattr(module, "run_process", fake_run)
    result = asyncio.run(
        run_pytest_profile(
            tmp_path,
            venv=tmp_path,
            timeout=600,
            stdout_limit=32768,
            stderr_limit=16384,
            verify_boundary=lambda: None,
            partitioned=True,
        )
    )
    assert result.exit_code == 7
    assert len(seen) == 2
    assert b"worker_concurrency" not in result.stdout


def test_tiny_fixture_keeps_single_pytest_step(tmp_path, monkeypatch):
    import app.developer_validation as module

    assert not use_partitioned_pytest(tmp_path)
    seen = []

    async def fake_run(argv, **kwargs):
        seen.append(argv)
        return ProcessResult(0, b"passed\n", b"", 0)

    monkeypatch.setattr(module, "sandbox", lambda _repo, command, **_kwargs: command)
    monkeypatch.setattr(module, "run_process", fake_run)
    result = asyncio.run(
        run_pytest_profile(
            tmp_path,
            venv=tmp_path,
            timeout=10,
            stdout_limit=32768,
            stderr_limit=16384,
            verify_boundary=lambda: None,
            partitioned=False,
        )
    )
    assert result.exit_code == 0 and len(seen) == 1
    assert "-n" not in seen[0]
    assert DeveloperRequest.model_fields["validation_version"].default == VALIDATION_VERSION


def test_default_task_deadlines_cover_the_full_validation_profile():
    assert Settings.model_fields["developer_validation_timeout_seconds"].default == 900.0
    assert Settings.model_fields["developer_timeout_seconds"].default == 1800.0
    assert DeveloperRunInput.model_fields["timeout_seconds"].default == 1800.0
    assert PromotionRunInput.model_fields["timeout_seconds"].default == 1800.0
