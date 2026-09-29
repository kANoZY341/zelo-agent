"""Backend-neutral storage contracts and application storage assembly.

v0.13 keeps SQLite as the only enabled backend. Core services depend on the
small session contract below so a later relational backend can be introduced
without making service logic depend on SQLite details.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.config import Settings

@dataclass(frozen=True)
class StorageDescriptor:
    """Stable, non-secret description of one configured storage backend."""

    schema: str
    backend: str
    relational: bool
    vector_search: bool


SQLITE_STORAGE = StorageDescriptor(
    schema="zelo-storage-backend-v1",
    backend="sqlite",
    relational=True,
    vector_search=False,
)


@runtime_checkable
class SessionStore(Protocol):
    """Minimum transactional persistence contract consumed by Core services."""

    def transaction(self) -> AbstractContextManager[Session]:
        """Open one write transaction with backend-appropriate serialization."""
        ...

    def read(self) -> AbstractContextManager[Session]:
        """Open one read-only service session."""
        ...


@runtime_checkable
class CoreStorage(SessionStore, Protocol):
    """Application-lifecycle storage contract owned by Zelo Core."""

    descriptor: StorageDescriptor

    def acquire_runner(self) -> None:
        """Acquire the backend's exclusive runner-ownership boundary."""
        ...

    def check_schema(self) -> None:
        """Verify the configured storage schema is current."""
        ...

    def close(self) -> None:
        """Release backend resources and runner ownership."""
        ...


def build_storage(settings: Settings) -> CoreStorage:
    """Build the configured Core storage backend.

    SQLite remains the only enabled implementation in v0.13. Future relational
    or vector-capable backends must implement the Zelo-owned contracts rather
    than changing service authority or persistence semantics.
    """
    if settings.storage_backend == "sqlite":
        from app.database import Database

        return Database(settings.database_path)
    raise RuntimeError(f"Unsupported storage backend: {settings.storage_backend}")
