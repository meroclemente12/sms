"""Accounting part 2: recurring transactions, allocations, salary payouts.

Adds three tables (fund_schedules, fund_allocations, fund_allocation_moves) and nullable
columns on fund_transactions linking an entry to a schedule occurrence, an allocation or
a therapist's weekly pay. No existing rows change.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels = None
depends_on = None

ROLE = "banahaw_app"
TABLES = ("fund_schedules", "fund_allocations", "fund_allocation_moves")


def _common() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default="{}", nullable=False),
    ]


def _tenant() -> sa.Column:
    return sa.Column(
        "tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def _branch() -> sa.Column:
    return sa.Column(
        "branch_id", sa.UUID(), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "fund_schedules",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant(),
        _branch(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("account_id", sa.UUID(), sa.ForeignKey("fund_accounts.id"), nullable=False),
        sa.Column("to_account_id", sa.UUID(), sa.ForeignKey("fund_accounts.id")),
        sa.Column("category_id", sa.UUID(), sa.ForeignKey("fund_categories.id")),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date()),
        sa.Column("description", sa.Text()),
        sa.Column("skipped", postgresql.ARRAY(sa.Date()), server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_common(),
        sa.CheckConstraint("kind IN ('expense', 'income', 'transfer')", name="kind_allowed"),
        sa.CheckConstraint("frequency IN ('weekly', 'monthly')", name="frequency_allowed"),
        sa.CheckConstraint("amount > 0", name="schedule_amount_positive"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_allowed"),
        sa.CheckConstraint("day_of_month BETWEEN 1 AND 31", name="day_of_month_allowed"),
    )
    op.create_index("ix_fund_schedules_tenant_id", "fund_schedules", ["tenant_id"])
    op.create_index("ix_fund_schedules_branch_id", "fund_schedules", ["branch_id"])

    op.create_table(
        "fund_allocations",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant(),
        _branch(),
        sa.Column("account_id", sa.UUID(), sa.ForeignKey("fund_accounts.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("target_amount", sa.Numeric(12, 2)),
        sa.Column("target_date", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.Column("closed", sa.Boolean(), nullable=False),
        *_common(),
        sa.UniqueConstraint("account_id", "name"),
    )
    op.create_index("ix_fund_allocations_tenant_id", "fund_allocations", ["tenant_id"])
    op.create_index("ix_fund_allocations_branch_id", "fund_allocations", ["branch_id"])
    op.create_index("ix_fund_allocations_account_id", "fund_allocations", ["account_id"])

    op.create_table(
        "fund_allocation_moves",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant(),
        sa.Column(
            "allocation_id",
            sa.UUID(),
            sa.ForeignKey("fund_allocations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("recorded_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount <> 0", name="move_not_zero"),
    )
    op.create_index("ix_fund_allocation_moves_tenant_id", "fund_allocation_moves", ["tenant_id"])
    op.create_index(
        "ix_fund_allocation_moves_allocation_id", "fund_allocation_moves", ["allocation_id"]
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}")

    op.add_column(
        "fund_transactions",
        sa.Column("schedule_id", sa.UUID(), sa.ForeignKey("fund_schedules.id")),
    )
    op.add_column("fund_transactions", sa.Column("due_date", sa.Date()))
    op.add_column(
        "fund_transactions",
        sa.Column("allocation_id", sa.UUID(), sa.ForeignKey("fund_allocations.id")),
    )
    op.add_column(
        "fund_transactions",
        sa.Column("therapist_id", sa.UUID(), sa.ForeignKey("therapists.id")),
    )
    op.add_column("fund_transactions", sa.Column("pay_week_start", sa.Date()))
    op.create_index("ix_fund_transactions_schedule_id", "fund_transactions", ["schedule_id"])
    op.create_index("ix_fund_transactions_allocation_id", "fund_transactions", ["allocation_id"])
    # One live posting per occurrence (per account, so both halves of a transfer fit),
    # and one live payout per therapist and week.
    op.create_index(
        "uq_fund_transactions_occurrence",
        "fund_transactions",
        ["schedule_id", "due_date", "account_id"],
        unique=True,
        postgresql_where=sa.text("schedule_id IS NOT NULL AND voided_at IS NULL"),
    )
    op.create_index(
        "uq_fund_transactions_payout",
        "fund_transactions",
        ["therapist_id", "pay_week_start"],
        unique=True,
        postgresql_where=sa.text("therapist_id IS NOT NULL AND voided_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_fund_transactions_payout", "fund_transactions")
    op.drop_index("uq_fund_transactions_occurrence", "fund_transactions")
    op.drop_index("ix_fund_transactions_allocation_id", "fund_transactions")
    op.drop_index("ix_fund_transactions_schedule_id", "fund_transactions")
    for col in ("pay_week_start", "therapist_id", "allocation_id", "due_date", "schedule_id"):
        op.drop_column("fund_transactions", col)
    op.drop_table("fund_allocation_moves")
    op.drop_table("fund_allocations")
    op.drop_table("fund_schedules")
