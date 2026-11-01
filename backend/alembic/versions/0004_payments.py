"""Payment methods per branch, payments logged at check-out, and the accountant role.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels = None
depends_on = None

ROLE = "banahaw_app"
TABLES = ("payment_methods", "payments")


def _common() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default="{}", nullable=False),
    ]


def upgrade() -> None:
    op.drop_constraint("role_allowed", "users", type_="check")
    op.create_check_constraint(
        "role_allowed", "users", "role IN ('front_desk', 'manager', 'admin', 'accountant')"
    )

    op.create_table(
        "payment_methods",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("needs_reference", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_common(),
        sa.CheckConstraint("kind IN ('cash', 'cashless')", name="kind_allowed"),
        sa.UniqueConstraint("branch_id", "name"),
    )
    op.create_index("ix_payment_methods_tenant_id", "payment_methods", ["tenant_id"])
    op.create_index("ix_payment_methods_branch_id", "payment_methods", ["branch_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.UUID(), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("booking_id", sa.UUID(), sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method_id", sa.UUID(), sa.ForeignKey("payment_methods.id"), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("recorded_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("checked_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("check_note", sa.Text()),
        *_common(),
        sa.CheckConstraint("amount > 0", name="payment_positive"),
        sa.CheckConstraint(
            "status IN ('not_needed', 'pending', 'verified', 'flagged')", name="status_allowed"
        ),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_booking_id", "payments", ["booking_id"])
    op.create_index("ix_payments_branch_recorded", "payments", ["branch_id", "recorded_at"])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}")

    # Every existing branch starts with the usual Philippine methods; edit them in Setup.
    op.execute(
        """
        INSERT INTO payment_methods
            (id, tenant_id, branch_id, name, kind, needs_reference, sort_order, active)
        SELECT gen_random_uuid(), b.tenant_id, b.id, m.name, m.kind, m.needs_reference,
               m.sort_order, true
        FROM branches b
        CROSS JOIN (VALUES ('Cash', 'cash', false, 0),
                           ('GCash', 'cashless', true, 1),
                           ('BPI bank transfer', 'cashless', true, 2))
             AS m(name, kind, needs_reference, sort_order)
        """
    )


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("payment_methods")
    op.execute("UPDATE users SET role = 'front_desk' WHERE role = 'accountant'")
    op.drop_constraint("role_allowed", "users", type_="check")
    op.create_check_constraint("role_allowed", "users", "role IN ('front_desk', 'manager', 'admin')")
