"""Business day start per branch, for branches open past midnight.

Adds branches.day_starts_at. Only adds a column with a default and fills it in for the
existing branches; no other data changes.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence
from datetime import time

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels = None
depends_on = None


def _m(t: time) -> int:
    return t.hour * 60 + t.minute


def _suggest(opens: time, closes: time) -> time:
    """6 AM when the branch is closed then, otherwise the middle of its closed hours.

    A copy of app.business_day.suggest_day_start, so this migration never changes."""
    if _m(opens) == _m(closes):
        return opens
    closed = (_m(opens) - _m(closes)) % 1440
    if (360 - _m(closes)) % 1440 <= closed:
        return time(6, 0)
    mid = (_m(closes) + closed // 2) % 1440
    return time(mid // 60, (mid % 60) // 15 * 15)


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column("day_starts_at", sa.Time(), server_default=sa.text("'06:00'"), nullable=False),
    )
    conn = op.get_bind()
    for row in conn.execute(sa.text("SELECT id, opens_at, closes_at FROM branches")):
        start = _suggest(row.opens_at, row.closes_at)
        if start != time(6, 0):
            conn.execute(
                sa.text("UPDATE branches SET day_starts_at = :t WHERE id = :id"),
                {"t": start, "id": row.id},
            )


def downgrade() -> None:
    op.drop_column("branches", "day_starts_at")
