"""Tenant slug for login, and a lookup that works before the tenant is known.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("slug", sa.String(length=63), nullable=False))
    op.create_unique_constraint("tenants_slug_key", "tenants", ["slug"])
    # Login needs the tenant id before row-level security knows the tenant. This
    # function returns only the id of an active tenant for a slug, nothing else.
    op.execute(
        """
        CREATE FUNCTION tenant_id_for_slug(p_slug text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT id FROM tenants WHERE slug = lower(p_slug) AND active
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION tenant_id_for_slug(text)")
    op.drop_constraint("tenants_slug_key", "tenants", type_="unique")
    op.drop_column("tenants", "slug")
