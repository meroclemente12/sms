"""Therapist pay: commission per service option, clock in/out, pay settings.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels = None
depends_on = None

ROLE = "banahaw_app"
TABLES = ("therapist_attendance", "pay_settings")

# Banahaw's commission by service length (minutes -> pesos). Kept here, not imported,
# so this migration stays the same if the app's defaults change later.
COMMISSION_BY_MINUTES = {60: 120, 75: 140, 90: 192, 100: 224, 130: 272, 165: 296, 180: 368}


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default="{}", nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "service_variants",
        sa.Column("commission", sa.Numeric(10, 2), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "commission_not_negative", "service_variants", "commission >= 0"
    )
    for minutes, amount in COMMISSION_BY_MINUTES.items():
        op.execute(
            f"UPDATE service_variants SET commission = {amount} WHERE duration_min = {minutes}"
        )

    op.add_column("booking_items", sa.Column("commission", sa.Numeric(10, 2)))
    # Services already completed earn the commission of their option.
    op.execute(
        "UPDATE booking_items bi SET commission = sv.commission "
        "FROM service_variants sv "
        "WHERE sv.id = bi.service_variant_id AND bi.status = 'completed'"
    )

    op.create_table(
        "therapist_attendance",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("therapist_id", sa.UUID(), sa.ForeignKey("therapists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("clock_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clock_out_at", sa.DateTime(timezone=True)),
        sa.Column("clocked_in_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("clocked_out_by", sa.UUID(), sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.CheckConstraint(
            "clock_out_at IS NULL OR clock_out_at >= clock_in_at", name="attendance_in_order"
        ),
    )
    op.create_index("ix_therapist_attendance_tenant_id", "therapist_attendance", ["tenant_id"])
    op.create_index("ix_therapist_attendance_therapist_id", "therapist_attendance", ["therapist_id"])
    op.create_index("ix_therapist_attendance_branch_id", "therapist_attendance", ["branch_id"])
    # One open clock-in per therapist: two desks clicking at once cannot both clock in.
    op.create_index(
        "uq_therapist_attendance_open",
        "therapist_attendance",
        ["therapist_id"],
        unique=True,
        postgresql_where=sa.text("clock_out_at IS NULL"),
    )

    op.create_table(
        "pay_settings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("daily_allowance", sa.Numeric(10, 2), nullable=False),
        sa.Column("quota_basis", sa.String(20), nullable=False),
        sa.Column("quota_tiers", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("week_starts_on", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id"),
        sa.CheckConstraint("quota_basis IN ('sales', 'services')", name="quota_basis_allowed"),
        sa.CheckConstraint("daily_allowance >= 0", name="allowance_not_negative"),
        sa.CheckConstraint("week_starts_on BETWEEN 0 AND 6", name="week_day_allowed"),
    )
    op.create_index("ix_pay_settings_tenant_id", "pay_settings", ["tenant_id"])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}")


def downgrade() -> None:
    op.drop_table("pay_settings")
    op.drop_table("therapist_attendance")
    op.drop_column("booking_items", "commission")
    op.drop_constraint("commission_not_negative", "service_variants", type_="check")
    op.drop_column("service_variants", "commission")
