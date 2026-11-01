import uuid
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

APP_ROLE = "banahaw_app"


def set_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Scope this transaction to one tenant; row-level security reads this setting."""
    session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})


def use_app_role(session: Session) -> None:
    """Run the rest of this transaction as the non-owner app role, so row-level
    security applies even if the connection user owns the tables (migration 0003)."""
    session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
