"""Close SQLite REPLACE bypasses in the append-only channel ledgers."""

from alembic import op

revision = "0050_channel_ledger_replace_guards"
down_revision = "0049_skill_publication_promotion_receipt_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The 0036 UPDATE/DELETE guards do not fire for SQLite's implicit delete
    # during INSERT OR REPLACE when recursive_triggers is off. Reject every
    # existing PK, alternate unique identity, or explicit rowid collision
    # before SQLite can remove an older row.
    op.execute(
        "CREATE TRIGGER channel_thread_bindings_no_duplicate_insert "
        "BEFORE INSERT ON channel_thread_bindings "
        "WHEN EXISTS (SELECT 1 FROM channel_thread_bindings WHERE "
        "thread_fingerprint=NEW.thread_fingerprint OR "
        "(channel=NEW.channel AND account_key=NEW.account_key AND "
        "sender_key=NEW.sender_key AND thread_key=NEW.thread_key) OR "
        "rowid=NEW.rowid) "
        "BEGIN SELECT RAISE(ABORT, 'Channel ledger is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER channel_ingress_observations_no_duplicate_insert "
        "BEFORE INSERT ON channel_ingress_observations "
        "WHEN EXISTS (SELECT 1 FROM channel_ingress_observations WHERE "
        "message_fingerprint=NEW.message_fingerprint OR "
        "(thread_fingerprint=NEW.thread_fingerprint AND message_key=NEW.message_key) OR "
        "rowid=NEW.rowid) "
        "BEGIN SELECT RAISE(ABORT, 'Channel ledger is append-only'); END"
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a reviewed backup instead")
