"""Alembic runs against ZELO_RUNTIME_DIR, never a separate hard-coded database."""

from alembic import context

from app.config import Settings
from app.database import Database
from app.models import Base

config = context.config


def _set_sqlite_foreign_keys(connection, enabled: bool) -> None:
    raw_connection = connection.connection.driver_connection
    cursor = raw_connection.cursor()
    try:
        cursor.execute(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    finally:
        cursor.close()


def _run_sqlite_migrations(connection) -> None:
    raw_connection = connection.connection.driver_connection
    original_isolation_level = raw_connection.isolation_level
    raw_connection.isolation_level = None
    try:
        _set_sqlite_foreign_keys(connection, False)
        raw_connection.execute("BEGIN")
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        context.run_migrations()
        violations = raw_connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"SQLite foreign-key check failed during migration: {violations}")
        raw_connection.commit()
    except BaseException:
        if raw_connection.in_transaction:
            raw_connection.rollback()
        raise
    finally:
        _set_sqlite_foreign_keys(connection, True)
        violations = raw_connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"SQLite foreign-key check failed after migration: {violations}")
        raw_connection.isolation_level = original_isolation_level


def run_migrations(connection) -> None:
    if connection.dialect.name == "sqlite":
        _run_sqlite_migrations(connection)
    else:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; use the configured local database")
elif config.attributes.get("connection") is not None:
    run_migrations(config.attributes["connection"])
else:
    database = Database(Settings().database_path)
    try:
        database.acquire_runner()  # Refuse migration while the application owns this DB.
        with database.engine.connect() as connection:
            run_migrations(connection)
    finally:
        database.close()
