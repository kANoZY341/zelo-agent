"""Alembic runs against ZELO_RUNTIME_DIR, never a separate hard-coded database."""

from alembic import context

from app.config import Settings
from app.database import Database
from app.models import Base

config = context.config


def run_migrations(connection) -> None:
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
        with database.engine.begin() as connection:
            run_migrations(connection)
    finally:
        database.close()
