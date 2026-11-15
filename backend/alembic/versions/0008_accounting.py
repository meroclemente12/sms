"""Accounting: fund accounts, categories and the transaction ledger.

Only adds tables and one nullable column (payment_methods.account_id). For every existing
payment method it adds a fund account at the same branch, with the same name and a zero
opening balance, and links the method to it; every branch also gets a Petty cash account.
No existing rows are changed except for that link.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels = None
depends_on = None

ROLE = "banahaw_app"
TABLES = ("fund_accounts", "fund_categories", "fund_transactions")

# A copy of app.funds.DEFAULT_CATEGORIES, so this migration never changes.
CATEGORIES = (
    ("Laundry", "out", True),
    ("Supplies", "out", True),
    ("Food and drinks", "out", True),
    ("Transport", "out", True),
    ("Repairs and maintenance", "out", False),
    ("Electricity", "out", False),
    ("Water", "out", False),
    ("Internet and phone", "out", False),
    ("Rent", "out", False),
    ("Salaries", "out", False),
    ("Government and taxes", "out", False),
    ("Other expense", "out", True),
    ("Other income", "in", False),
)


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


def upgrade() -> None:
    op.create_table(
        "fund_accounts",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant(),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("account_number", sa.String(100)),
        sa.Column("opening_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("opening_date", sa.Date(), nullable=False),
        sa.Column("accepts_payments", sa.Boolean(), nullable=False),
        sa.Column("needs_reference", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_common(),
        sa.CheckConstraint("kind IN ('cash', 'bank', 'ewallet', 'petty_cash')", name="kind_allowed"),
        sa.CheckConstraint(
            "branch_id IS NOT NULL OR kind IN ('bank', 'ewallet')", name="local_kinds_need_branch"
        ),
        sa.UniqueConstraint("tenant_id", "branch_id", "name", postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_fund_accounts_tenant_id", "fund_accounts", ["tenant_id"])
    op.create_index("ix_fund_accounts_branch_id", "fund_accounts", ["branch_id"])

    op.create_table(
        "fund_categories",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("desk_can_use", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_common(),
        sa.CheckConstraint("direction IN ('in', 'out')", name="direction_allowed"),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    op.create_index("ix_fund_categories_tenant_id", "fund_categories", ["tenant_id"])

    op.create_table(
        "fund_transactions",
        sa.Column("id", sa.UUID(), primary_key=True),
        _tenant(),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id")),
        sa.Column("account_id", sa.UUID(), sa.ForeignKey("fund_accounts.id"), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("category_id", sa.UUID(), sa.ForeignKey("fund_categories.id")),
        sa.Column("description", sa.Text()),
        sa.Column("reference", sa.String(100)),
        sa.Column("transfer_id", sa.UUID()),
        sa.Column("recorded_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("voided_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("void_reason", sa.Text()),
        *_common(),
        sa.CheckConstraint("amount > 0", name="fund_amount_positive"),
        sa.CheckConstraint("direction IN ('in', 'out')", name="direction_allowed"),
    )
    for col in ("tenant_id", "branch_id", "account_id", "business_date", "transfer_id"):
        op.create_index(f"ix_fund_transactions_{col}", "fund_transactions", [col])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}")

    op.add_column(
        "payment_methods",
        sa.Column(
            "account_id", sa.UUID(), sa.ForeignKey("fund_accounts.id", ondelete="SET NULL")
        ),
    )
    op.create_index("ix_payment_methods_account_id", "payment_methods", ["account_id"])

    # One account per existing payment method, same branch and name, linked to it.
    op.execute(
        """
        CREATE TEMP TABLE _method_accounts ON COMMIT DROP AS
        SELECT pm.id AS method_id, gen_random_uuid() AS account_id
        FROM payment_methods pm
        """
    )
    op.execute(
        """
        INSERT INTO fund_accounts
            (id, tenant_id, branch_id, name, kind, opening_balance, opening_date,
             accepts_payments, needs_reference, sort_order, active)
        SELECT ma.account_id, pm.tenant_id, pm.branch_id, pm.name,
               CASE WHEN pm.kind = 'cash' THEN 'cash'
                    WHEN pm.name ILIKE '%gcash%' OR pm.name ILIKE '%maya%' THEN 'ewallet'
                    ELSE 'bank' END,
               0, (now() AT TIME ZONE 'Asia/Manila')::date,
               true, pm.needs_reference, pm.sort_order, pm.active
        FROM payment_methods pm JOIN _method_accounts ma ON ma.method_id = pm.id
        """
    )
    op.execute(
        """
        UPDATE payment_methods pm SET account_id = ma.account_id
        FROM _method_accounts ma WHERE ma.method_id = pm.id
        """
    )
    op.execute(
        """
        INSERT INTO fund_accounts
            (id, tenant_id, branch_id, name, kind, opening_balance, opening_date,
             accepts_payments, needs_reference, sort_order, active)
        SELECT gen_random_uuid(), b.tenant_id, b.id, 'Petty cash', 'petty_cash', 0,
               (now() AT TIME ZONE 'Asia/Manila')::date, false, false, 100, true
        FROM branches b
        WHERE NOT EXISTS (
            SELECT 1 FROM fund_accounts fa WHERE fa.branch_id = b.id AND fa.name = 'Petty cash'
        )
        """
    )
    values = ", ".join(
        f"('{name}', '{direction}', {str(desk).lower()}, {i})"
        for i, (name, direction, desk) in enumerate(CATEGORIES)
    )
    op.execute(
        f"""
        INSERT INTO fund_categories
            (id, tenant_id, name, direction, desk_can_use, sort_order, active)
        SELECT gen_random_uuid(), t.id, c.name, c.direction, c.desk, c.ord, true
        FROM tenants t CROSS JOIN (VALUES {values}) AS c(name, direction, desk, ord)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_payment_methods_account_id", "payment_methods")
    op.drop_column("payment_methods", "account_id")
    op.drop_table("fund_transactions")
    op.drop_table("fund_categories")
    op.drop_table("fund_accounts")
