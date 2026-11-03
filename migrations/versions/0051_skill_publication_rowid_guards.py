"""Rebuild Skill publication history without replaceable SQLite rowids.

SQLite's INSERT OR REPLACE can delete a row by its hidden rowid without firing
BEFORE DELETE triggers when recursive_triggers is off. Keep every logical row
and its constraints, indexes, and triggers while removing that alternate key.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_skill_publication_rowid_guards"
down_revision = "0050_channel_ledger_replace_guards"
branch_labels = None
depends_on = None

_TABLES = (
    "skill_publication_claims",
    "skill_publication_operator_decisions",
    "skill_publication_dispatch_fences",
    "skill_publication_merge_attempts",
    "skill_publication_merge_receipts",
    "skill_publication_merge_failures",
    "skill_publication_post_attempt_reconciliations",
    "skill_publication_promotion_receipt_links",
)
_LINK_INSERT_GUARD = "skill_publication_promotion_receipt_links_no_duplicate_insert"


def _schema_objects(
    connection: sa.Connection, table: str
) -> tuple[str, tuple[tuple[str, str], ...]]:
    create_sql = connection.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:table"),
        {"table": table},
    ).scalar_one()
    prefix = f"CREATE TABLE {table} ("
    if not create_sql.startswith(prefix) or "WITHOUT ROWID" in create_sql.upper():
        raise RuntimeError(f"Unexpected Skill ledger schema: {table}")
    objects = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE tbl_name=:table AND type IN ('index','trigger') AND sql IS NOT NULL "
            "ORDER BY type,name"
        ),
        {"table": table},
    ).all()
    definitions = tuple((name, sql) for name, sql in objects)
    required = {f"{table}_no_duplicate_insert", f"{table}_no_update", f"{table}_no_delete"}
    names = {name for name, _sql in objects}
    if not required.issubset(names):
        raise RuntimeError(f"Missing Skill ledger protection: {table}")
    return create_sql, definitions


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Skill ledger rowid migration requires SQLite")
    saved: dict[str, tuple[tuple[str, str], ...]] = {}
    for table in _TABLES:
        create_sql, objects = _schema_objects(connection, table)
        temp = f"_v015_{table}"
        prefix = f"CREATE TABLE {table}"
        op.execute(f"CREATE TABLE {temp}" + create_sql[len(prefix) :] + " WITHOUT ROWID")
        op.execute(f"INSERT INTO {temp} SELECT * FROM {table}")
        saved[table] = objects

    # An existing trigger on the older generic link table references the
    # publication table. SQLite validates its SQL during ALTER TABLE, so
    # suspend and restore that exact trigger in the same migration transaction.
    external_name = "skill_learning_promotion_links_no_publication_conflict"
    external_sql = connection.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),
        {"name": external_name},
    ).scalar_one()
    if not external_sql.startswith(f"CREATE TRIGGER {external_name} "):
        raise RuntimeError("Unexpected Skill publication cross-ledger trigger")
    op.execute(f"DROP TRIGGER {external_name}")
    # Foreign keys are disabled only by migrations/env.py's explicit,
    # rollback-capable transaction. It verifies every reference before commit.
    for table in reversed(_TABLES):
        op.drop_table(table)
    for table in _TABLES:
        op.rename_table(f"_v015_{table}", table)
    op.execute(external_sql)

    for table in _TABLES:
        for name, definition in saved[table]:
            if name != _LINK_INSERT_GUARD:
                op.execute(definition)
    # 0049 omitted the link_fingerprint UNIQUE identity from its insert guard.
    # REPLACE can target that alternate key even after rowids are removed.
    table = "skill_publication_promotion_receipt_links"
    op.execute(
        f"CREATE TRIGGER {_LINK_INSERT_GUARD} BEFORE INSERT ON {table} "
        f"WHEN EXISTS (SELECT 1 FROM {table} WHERE id=NEW.id) OR "
        f"EXISTS (SELECT 1 FROM {table} WHERE claim_id=NEW.claim_id) OR "
        f"EXISTS (SELECT 1 FROM {table} WHERE proposal_id=NEW.proposal_id) OR "
        f"EXISTS (SELECT 1 FROM {table} WHERE receipt_id=NEW.receipt_id) OR "
        f"EXISTS (SELECT 1 FROM {table} WHERE link_fingerprint=NEW.link_fingerprint) OR "
        f"EXISTS (SELECT 1 FROM {table} WHERE agent_instance_id=NEW.agent_instance_id "
        "AND source_skill_id=NEW.source_skill_id AND procedure_id=NEW.procedure_id "
        "AND proposed_version=NEW.proposed_version) "
        "BEGIN SELECT RAISE(ABORT, 'Skill publication link is append-only'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
