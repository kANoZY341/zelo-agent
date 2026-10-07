"""Add inert, operator-selected WhatsApp account and final revocation records."""

import sqlalchemy as sa
from alembic import op

revision = "0037_channel_operator_account_selection"
down_revision = "0036_channel_thread_observations"
branch_labels = None
depends_on = None


def _hex(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*' "
        f"AND {name} != lower(hex(zeroblob(32)))",
        name=f"ck_channel_selection_hex_{name}",
    )


def upgrade() -> None:
    op.create_table(
        "channel_operator_account_selections",
        sa.Column("account_key", sa.String(64), primary_key=True),
        sa.Column("app_secret_identity", sa.String(64), nullable=False),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("selected_at", sa.DateTime(), nullable=False),
        sa.Column(
            "provider_authenticated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "mind_admission_authority", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("delivery_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        _hex("account_key"),
        _hex("app_secret_identity"),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_channel_selection_operator"),
        sa.CheckConstraint(
            "provider_authenticated=0", name="ck_channel_selection_not_provider_auth"
        ),
        sa.CheckConstraint("mind_admission_authority=0", name="ck_channel_selection_not_mind"),
        sa.CheckConstraint("delivery_authority=0", name="ck_channel_selection_not_delivery"),
    )
    op.create_table(
        "channel_operator_account_revocations",
        sa.Column(
            "account_key",
            sa.String(64),
            sa.ForeignKey("channel_operator_account_selections.account_key"),
            primary_key=True,
        ),
        sa.Column("operator_id", sa.String(32), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=False),
        _hex("account_key"),
        sa.CheckConstraint("operator_id='ahmad'", name="ck_channel_revocation_operator"),
    )
    for table in (
        "channel_operator_account_selections",
        "channel_operator_account_revocations",
    ):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, 'Channel account selection is append-only'); END"
            )


def downgrade() -> None:
    for table in (
        "channel_operator_account_revocations",
        "channel_operator_account_selections",
    ):
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation.lower()}")
    op.drop_table("channel_operator_account_revocations")
    op.drop_table("channel_operator_account_selections")
