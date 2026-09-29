"""Storage abstraction boundaries for the final v0.13 slice."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.projects import ProjectService
from app.schemas import ProjectCreate
from app.storage import CoreStorage, SQLITE_STORAGE, SessionStore, build_storage


class DelegatingSessionStore:
    """Test double exposing only the service-level storage contract."""

    def __init__(self, database) -> None:
        self._database = database

    def transaction(self):
        return self._database.transaction()

    def read(self):
        return self._database.read()


def test_storage_factory_builds_declared_sqlite_backend(settings):
    storage = build_storage(settings)
    try:
        assert isinstance(storage, CoreStorage)
        assert isinstance(storage, SessionStore)
        assert storage.descriptor == SQLITE_STORAGE
        assert storage.descriptor.backend == "sqlite"
        assert storage.descriptor.relational is True
        assert storage.descriptor.vector_search is False
    finally:
        storage.close()


def test_unimplemented_storage_backend_fails_closed(runtime_path):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            runtime_dir=runtime_path,
            storage_backend="postgres",
        )


def test_core_service_accepts_session_store_without_database_concrete_type(database):
    storage = DelegatingSessionStore(database)
    assert isinstance(storage, SessionStore)

    projects = ProjectService(storage)
    created = projects.create(ProjectCreate(name="Portable storage", summary="contract proof"))
    loaded = projects.get(created.id)

    assert loaded.id == created.id
    assert loaded.name == "Portable storage"
    assert loaded.summary == "contract proof"


def test_application_exposes_canonical_storage_state(client):
    assert client.app.state.storage is client.app.state.database
    assert client.app.state.storage.descriptor == SQLITE_STORAGE
