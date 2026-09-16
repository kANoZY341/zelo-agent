"""Isolated, migrated test databases live only under ignored runtime/."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.database import Database
from app.main import create_app

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
