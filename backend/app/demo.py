"""Sample spa for trying the app locally. Safe to run more than once: it does nothing
if the demo spa already exists.

    uv run python -m app.cli seed-demo
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.cli import create_tenant
from app.db import SessionLocal, set_tenant
from app.lifecycle import transition
from app.models import (
    Bed,
    BedType,
    BookingChannel,
    BookingStatus,
    Branch,
    Client,
    Room,
    Service,
    ServiceVariant,
    Shift,
    Tenant,
    Therapist,
    TherapistSkill,
    User,
    UserBranch,
    UserRole,
)
from app.payments import add_default_methods
from app.scheduling import GuestRequest, GuestWish, create_booking, pick_pairs
from app.security import hash_password

DEMO_SLUG = "banahaw"
DEMO_PASSWORD = "banahaw-demo"
TZ = "Asia/Manila"

# name, category, bed type, guests, [(label, minutes, price)]
SERVICES = [
    ("Swedish massage", "Massage", "Massage bed", 1, [("60 min", 60, 800), ("90 min", 90, 1100)]),
    ("Hilot", "Massage", "Massage bed", 1, [("60 min", 60, 900), ("90 min", 90, 1250)]),
    ("Foot spa", "Foot care", "Foot spa chair", 1, [("45 min", 45, 500)]),
    ("Couples massage", "Massage", "Massage bed", 2, [("60 min", 60, 1500), ("90 min", 90, 2100)]),
]

# name, gender, colour, services they can do
THERAPISTS = [
    ("Ana", "female", "#e07a5f", ["Swedish massage", "Hilot", "Couples massage"]),
    ("Ben", "male", "#3d85c6", ["Swedish massage", "Couples massage"]),
    ("Carla", "female", "#81b29a", ["Hilot", "Foot spa"]),
    ("Dante", "male", "#f2a541", ["Hilot", "Swedish massage"]),
    ("Elena", "female", "#9b5de5", ["Foot spa", "Swedish massage", "Couples massage"]),
    ("Faye", "female", "#00a6a6", ["Swedish massage", "Hilot", "Foot spa", "Couples massage"]),
]


def seed_demo(password: str = DEMO_PASSWORD) -> bool:
    """Create the demo spa. Returns False if it already existed."""
    with SessionLocal() as session:
        existing = session.scalar(select(Tenant.id).where(Tenant.slug == DEMO_SLUG))
    if existing:
        _add_accountant(existing, password)
        return False

    tenant = create_tenant("Banahaw Heals", DEMO_SLUG, "owner@banahaw.ph", "Owner", password)
    tz = ZoneInfo(TZ)
    with SessionLocal() as session, session.begin():
        tid = tenant.id
        set_tenant(session, tid)

        main = Branch(tenant_id=tid, name="Main", address="Lucena City, Quezon", timezone=TZ)
        session.add(main)
        session.flush()
        add_default_methods(session, main)
        bed_types = {n: BedType(tenant_id=tid, name=n) for n in ("Massage bed", "Foot spa chair")}
        session.add_all(bed_types.values())
        session.flush()

        rooms = [
            ("Room 1", "single", "Massage bed", 1),
            ("Room 2", "single", "Massage bed", 1),
            ("Room 3", "single", "Massage bed", 1),
            ("Room 4", "single", "Massage bed", 1),
            ("Couples Suite", "couples", "Massage bed", 2),
            ("Foot Spa Lounge", "single", "Foot spa chair", 3),
        ]
        for name, kind, bed_type, beds in rooms:
            room = Room(tenant_id=tid, branch_id=main.id, name=name, room_type=kind)
            session.add(room)
            session.flush()
            for i in range(1, beds + 1):
                label = f"{name} bed {i}" if beds > 1 else f"{name} bed"
                if bed_type == "Foot spa chair":
                    label = f"Chair {i}"
                session.add(
                    Bed(
                        tenant_id=tid,
                        room_id=room.id,
                        bed_type_id=bed_types[bed_type].id,
                        label=label,
                    )
                )

        services: dict[str, Service] = {}
        for name, category, bed_type, guests, variants in SERVICES:
            svc = Service(
                tenant_id=tid,
                name=name,
                category=category,
                bed_type_id=bed_types[bed_type].id,
                guest_count=guests,
                buffer_after_min=20,
            )
            session.add(svc)
            session.flush()
            services[name] = svc
            for label, minutes, price in variants:
                session.add(
                    ServiceVariant(
                        tenant_id=tid,
                        service_id=svc.id,
                        label=label,
                        duration_min=minutes,
                        default_price=Decimal(price),
                    )
                )

        today = datetime.now(tz).date()
        for name, gender, colour, skills in THERAPISTS:
            t = Therapist(
                tenant_id=tid, branch_id=main.id, name=name, gender=gender, display_colour=colour
            )
            session.add(t)
            session.flush()
            for s in skills:
                session.add(
                    TherapistSkill(tenant_id=tid, therapist_id=t.id, service_id=services[s].id)
                )
            for d in range(14):
                day: date = today + timedelta(days=d)
                session.add(
                    Shift(
                        tenant_id=tid,
                        therapist_id=t.id,
                        branch_id=main.id,
                        work_date=day,
                        starts_at=datetime.combine(day, time(10, 0), tzinfo=tz),
                        ends_at=datetime.combine(day, time(20, 0), tzinfo=tz),
                    )
                )

        for email, name, role in [
            ("manager@banahaw.ph", "Maria Manager", UserRole.manager),
            ("desk@banahaw.ph", "Dana Desk", UserRole.front_desk),
            ("accounts@banahaw.ph", "Alma Accountant", UserRole.accountant),
        ]:
            u = User(
                tenant_id=tid,
                email=email,
                name=name,
                role=role,
                password_hash=hash_password(password),
            )
            session.add(u)
            session.flush()
            session.add(UserBranch(tenant_id=tid, user_id=u.id, branch_id=main.id))

        session.add_all(
            [
                Client(tenant_id=tid, name="Liza Santos", mobile="+639171234567"),
                Client(tenant_id=tid, name="Paolo Reyes", mobile="+639181234567"),
            ]
        )
    _sample_bookings(tenant.id, tz)
    return True


def _add_accountant(tenant_id, password: str) -> None:
    """Demo spas made before the accountant role existed get the accountant user too."""
    with SessionLocal() as session, session.begin():
        set_tenant(session, tenant_id)
        if session.scalar(select(User.id).where(User.email == "accounts@banahaw.ph")):
            return
        branch = session.scalar(select(Branch).order_by(Branch.created_at))
        u = User(
            tenant_id=tenant_id,
            email="accounts@banahaw.ph",
            name="Alma Accountant",
            role=UserRole.accountant,
            password_hash=hash_password(password),
        )
        session.add(u)
        session.flush()
        if branch:
            session.add(UserBranch(tenant_id=tenant_id, user_id=u.id, branch_id=branch.id))


# day offset, hh:mm, client, service, variant, channel, therapist (or None), status
SAMPLE_BOOKINGS = [
    (0, "10:00", "Liza Santos", "Swedish massage", "60 min", "phone", "Ana", "confirmed"),
    (0, "10:30", "Paolo Reyes", "Hilot", "90 min", "walk_in", None, "booked"),
    (0, "11:00", "Liza Santos", "Couples massage", "60 min", "email", None, "booked"),
    (0, "13:00", "Paolo Reyes", "Foot spa", "45 min", "phone", "Carla", "booked"),
    (1, "10:00", "Paolo Reyes", "Swedish massage", "90 min", "phone", "Dante", "booked"),
]


def _sample_bookings(tenant_id, tz: ZoneInfo) -> None:
    """A few bookings today and tomorrow so the day board is not empty."""
    with SessionLocal() as session, session.begin():
        set_tenant(session, tenant_id)
        branch = session.scalar(select(Branch))
        clients = {c.name: c for c in session.scalars(select(Client))}
        channels = {c.code: c for c in session.scalars(select(BookingChannel))}
        therapists = {t.name: t for t in session.scalars(select(Therapist))}
        variants = {(v.service.name, v.label): v for v in session.scalars(select(ServiceVariant))}
        today = datetime.now(tz).date()
        for offset, hhmm, client, service, label, channel, therapist, status in SAMPLE_BOOKINGS:
            day = today + timedelta(days=offset)
            starts = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz)
            variant = variants[(service, label)]
            wish = GuestWish(therapist_id=therapists[therapist].id if therapist else None)
            wishes = [wish] * variant.service.guest_count
            pairs = pick_pairs(session, branch.id, variant.id, starts, wishes)
            booking = create_booking(
                session,
                tenant_id=tenant_id,
                branch_id=branch.id,
                client_id=clients[client].id,
                channel_id=channels[channel].id,
                variant_id=variant.id,
                starts_at=starts,
                guests=[
                    GuestRequest(
                        pair=p,
                        guest_name="Guest" if i else None,
                        therapist_requested=therapist is not None,
                    )
                    for i, p in enumerate(pairs)
                ],
            )
            if status != BookingStatus.booked:
                transition(session, booking, BookingStatus(status))
