"""Isolated, migrated test databases live only under ignored runtime/."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.database import Database
from app.main import create_app
from tests.developer_helpers import developer_case  # noqa: F401


@pytest.fixture(scope="module")
def external_validation_venv(tmp_path_factory):
    source = ROOT / ".venv"
    if not source.is_relative_to(Path("/workspace")):
        return source
    # Nested validation exposes the trusted venv inside /workspace; copy it out.
    target = tmp_path_factory.mktemp("external-validation-venv") / "venv"
    shutil.copytree(
        source, target, symlinks=True, ignore=shutil.ignore_patterns(".venv", "__pycache__")
    )
    # Preserve relative links and rebase absolute links that target this venv.
    for link in target.rglob("*"):
        if not link.is_symlink() or not link.readlink().is_absolute():
            continue
        original_target = (source / link.relative_to(target)).resolve(strict=True)
        for ancestor in (original_target, *original_target.parents):
            if ancestor.samefile(source):
                link.unlink()
                link.symlink_to(
                    target / original_target.relative_to(ancestor),
                    target_is_directory=original_target.is_dir(),
                )
                break
    return target


@pytest.fixture
def nested_validation_host_root(monkeypatch, tmp_path, external_validation_venv):
    if not ROOT.is_relative_to(Path("/workspace")):
        return
    # A test starting another runtime needs a host venv outside the candidate tree.
    host_root = tmp_path / "trusted-host"
    host_root.mkdir()
    (host_root / ".venv").symlink_to(external_validation_venv, target_is_directory=True)
    monkeypatch.setattr("app.developer.ROOT", host_root)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep tests sharing the repository-wide developer capacity lock together."""
    for item in items:
        if "developer_case" in item.fixturenames:
            item.add_marker(pytest.mark.xdist_group("developer_capacity"))


TEST_TOKEN = "test-only-operator-token-not-a-real-credential"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def runtime_path() -> Iterator[Path]:
    (ROOT / "runtime").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="test-", dir=ROOT / "runtime") as directory:
        yield Path(directory)


@pytest.fixture
def settings(runtime_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_name="Zelo Agent",
        operator_token=TEST_TOKEN,
        runtime_dir=runtime_path,
        runner_poll_seconds=0.01,
    )


@pytest.fixture
def database(settings: Settings) -> Iterator[Database]:
    database = Database(settings.database_path)
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def client(settings: Settings, database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(settings), headers=AUTH) as client:
        yield client
