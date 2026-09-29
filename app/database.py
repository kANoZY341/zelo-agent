"""SQLite configuration, explicit transactions, migrations, and runner ownership."""

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.orm import Session

from app.config import ROOT
from app.storage import SQLITE_STORAGE


def migration_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


class Database:
    descriptor = SQLITE_STORAGE

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create privately before SQLite opens it. Also preserve protection on an existing DB.
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(path, 0o600)
        self.path = path
        self.engine = create_engine(
            URL.create("sqlite", database=str(path)),
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        event.listen(self.engine, "connect", self._configure)
        self._lock: IO[bytes] | None = None

    @staticmethod
    def _configure(connection: sqlite3.Connection, record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            # Serialize writers before reading policy state, including duplicate requests.
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session

    def migrate(self) -> None:
        config = migration_config()
        with self.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def check_schema(self) -> None:
        with self.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_heads()
        expected = tuple(ScriptDirectory.from_config(migration_config()).get_heads())
        if current != expected:
            raise RuntimeError("Database migration required: run python -m alembic upgrade head")

    def acquire_runner(self) -> None:
        self._lock = self.path.with_suffix(".runner.lock").open("ab")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            self._lock = None
            raise RuntimeError("Database already has a runner; use ONE Uvicorn worker") from None

    def close(self) -> None:
        self.engine.dispose()
        if self._lock:
            self._lock.close()
            self._lock = None
