import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5433/banahaw_test"
)
os.environ["DATABASE_URL"] = TEST_URL

from app import models as m  # noqa: E402
from app.payments import add_default_methods  # noqa: E402

MNL = ZoneInfo("Asia/Manila")
DAY = date(2026, 11, 2)


def at(hh: int, mm: int = 0) -> datetime:
    return datetime.combine(DAY, time(hh, mm), tzinfo=MNL)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    """Each test runs in a transaction that is rolled back afterwards."""
    with engine.connect() as conn:
        trans = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield s
        s.close()
        trans.rollback()


@dataclass
class Spa:
    tenant: m.Tenant
    branch: m.Branch
    phone: m.BookingChannel
    walk_in: m.BookingChannel
    client: m.Client
    swedish60: m.ServiceVariant
    couples60: m.ServiceVariant
    ana: m.Therapist
    ben: m.Therapist
    cora: m.Therapist
    bed_a: m.Bed
    bed_b: m.Bed
    couples_beds: list[m.Bed]


def make_spa(s: Session, name: str = "Banahaw Heals") -> Spa:
    t = m.Tenant(name=name, slug=name.lower().replace(" ", "-"))
    s.add(t)
    s.flush()
    tid = t.id
    br = m.Branch(tenant_id=tid, name="Main")
    massage_bed = m.BedType(tenant_id=tid, name="Massage bed")
    s.add_all([br, massage_bed])
    s.flush()

    room_a = m.Room(tenant_id=tid, branch_id=br.id, name="Room A", room_type="single")
    room_b = m.Room(tenant_id=tid, branch_id=br.id, name="Room B", room_type="single")
    room_c = m.Room(tenant_id=tid, branch_id=br.id, name="Couples", room_type="couples")
    s.add_all([room_a, room_b, room_c])
    s.flush()
    bed_a = m.Bed(tenant_id=tid, room_id=room_a.id, bed_type_id=massage_bed.id, label="A1")
    bed_b = m.Bed(tenant_id=tid, room_id=room_b.id, bed_type_id=massage_bed.id, label="B1")
    c1 = m.Bed(tenant_id=tid, room_id=room_c.id, bed_type_id=massage_bed.id, label="C1")
    c2 = m.Bed(tenant_id=tid, room_id=room_c.id, bed_type_id=massage_bed.id, label="C2")
    s.add_all([bed_a, bed_b, c1, c2])

    swedish = m.Service(
        tenant_id=tid, name="Swedish massage", bed_type_id=massage_bed.id, buffer_after_min=20
    )
    couples = m.Service(
        tenant_id=tid,
        name="Couples massage",
        bed_type_id=massage_bed.id,
        buffer_after_min=20,
        guest_count=2,
    )
    s.add_all([swedish, couples])
    s.flush()
    sw60 = m.ServiceVariant(
        tenant_id=tid,
        service_id=swedish.id,
        label="60 min",
        duration_min=60,
        default_price=Decimal("800"),
    )
    cp60 = m.ServiceVariant(
        tenant_id=tid,
        service_id=couples.id,
        label="60 min",
        duration_min=60,
        default_price=Decimal("1500"),
    )
    s.add_all([sw60, cp60])

    ana = m.Therapist(tenant_id=tid, branch_id=br.id, name="Ana", gender="female")
    ben = m.Therapist(tenant_id=tid, branch_id=br.id, name="Ben", gender="male")
    cora = m.Therapist(tenant_id=tid, branch_id=br.id, name="Cora", gender="female")
    s.add_all([ana, ben, cora])
    s.flush()
    for th, svc in [
        (ana, swedish),
        (ana, couples),
        (ben, swedish),
        (ben, couples),
        (cora, couples),
    ]:
        s.add(m.TherapistSkill(tenant_id=tid, therapist_id=th.id, service_id=svc.id))
    for th in (ana, ben, cora):
        s.add(
            m.Shift(
                tenant_id=tid,
                therapist_id=th.id,
                branch_id=br.id,
                work_date=DAY,
                starts_at=at(9),
                ends_at=at(18),
            )
        )

    phone = m.BookingChannel(tenant_id=tid, code="phone", name="Phone call")
    email = m.BookingChannel(tenant_id=tid, code="email", name="Email")
    walk = m.BookingChannel(tenant_id=tid, code="walk_in", name="Walk-in")
    client = m.Client(tenant_id=tid, name="Maria Santos", mobile="+639170000000")
    s.add_all([phone, email, walk, client])
    add_default_methods(s, br)
    s.flush()
    return Spa(t, br, phone, walk, client, sw60, cp60, ana, ben, cora, bed_a, bed_b, [c1, c2])


@pytest.fixture()
def spa(session) -> Spa:
    return make_spa(session)


def new_id() -> uuid.UUID:
    return uuid.uuid4()
