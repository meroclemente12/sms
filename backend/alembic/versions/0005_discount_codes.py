"""Discount codes and their use on bookings.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels = None
depends_on = None

ROLE = "banahaw_app"
TABLES = ("discount_codes", "discount_redemptions")


def upgrade() -> None:
    op.create_table(
        "discount_codes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("description", sa.String(200)),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("starts_on", sa.Date()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("once_per_client", sa.Boolean(), nullable=False),
        sa.Column("branch_ids", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("service_ids", postgresql.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.UniqueConstraint("tenant_id", "code"),
        sa.CheckConstraint("kind IN ('percent', 'fixed')", name="kind_allowed"),
        sa.CheckConstraint("value > 0", name="discount_positive"),
        sa.CheckConstraint("kind <> 'percent' OR value <= 100", name="percent_at_most_100"),
        sa.CheckConstraint("max_uses IS NULL OR max_uses > 0", name="max_uses_positive"),
        sa.CheckConstraint(
            "starts_on IS NULL OR ends_on IS NULL OR ends_on >= starts_on", name="dates_in_order"
        ),
    )
    op.create_index("ix_discount_codes_tenant_id", "discount_codes", ["tenant_id"])

    op.create_table(
        "discount_redemptions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_id", sa.UUID(), sa.ForeignKey("discount_codes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("booking_id", sa.UUID(), sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.UUID(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("redeemed_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("booking_id"),
        sa.CheckConstraint("amount > 0", name="redemption_positive"),
    )
    op.create_index("ix_discount_redemptions_tenant_id", "discount_redemptions", ["tenant_id"])
    op.create_index("ix_discount_redemptions_code_id", "discount_redemptions", ["code_id"])
    op.create_index("ix_discount_redemptions_client_id", "discount_redemptions", ["client_id"])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}")


def downgrade() -> None:
    op.drop_table("discount_redemptions")
    op.drop_table("discount_codes")
