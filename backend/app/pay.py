"""Therapist pay: commission per service, daily allowance for days clocked in, and the
weekly sales quota bonus.

  * Commission: each completed booking item pays the therapist the commission of its
    service option (set by length in Services setup). It is copied onto the item when
    the booking is completed.
  * Allowance: every branch day a therapist was clocked in at least once.
  * Quota bonus: per week, the bonus of the highest tier the therapist reached, counted
    either in pesos of completed services (menu price, before discounts) or in number of
    completed services.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Booking,
    BookingItem,
    BookingStatus,
    Branch,
    PaySettings,
    QuotaBasis,
    ServiceVariant,
    Therapist,
    TherapistAttendance,
)

# Banahaw's commission by service length, used for new service options and the demo.
DEFAULT_COMMISSION_BY_MINUTES: dict[int, Decimal] = {
    60: Decimal(120),
    75: Decimal(140),
    90: Decimal(192),
    100: Decimal(224),
    130: Decimal(272),
    165: Decimal(296),
    180: Decimal(368),
}
DEFAULT_DAILY_ALLOWANCE = Decimal(250)


def default_commission(duration_min: int) -> Decimal:
    return DEFAULT_COMMISSION_BY_MINUTES.get(duration_min, Decimal(0))


def now() -> datetime:
    """The current moment; a function so tests can move the clock."""
    return datetime.now(ZoneInfo("UTC"))


def branch_today(branch: Branch) -> date:
    return now().astimezone(ZoneInfo(branch.timezone)).date()


# --- Settings ------------------------------------------------------------------------


def settings_for(session: Session, tenant_id: uuid.UUID) -> PaySettings:
    """This spa's pay settings, created with the defaults the first time they're read."""
    row = session.scalar(select(PaySettings).where(PaySettings.tenant_id == tenant_id))
    if row is None:
        row = PaySettings(
            tenant_id=tenant_id,
            daily_allowance=DEFAULT_DAILY_ALLOWANCE,
            quota_basis=QuotaBasis.sales,
            quota_tiers=[],
            week_starts_on=0,
        )
        session.add(row)
        session.flush()
    return row


def week_start(day: date, week_starts_on: int) -> date:
    return day - timedelta(days=(day.weekday() - week_starts_on) % 7)


def bonus_for(tiers: list[dict], reached: Decimal) -> tuple[Decimal, Decimal | None]:
    """(bonus, threshold reached) for the highest tier at or below ``reached``."""
    best: tuple[Decimal, Decimal | None] = (Decimal(0), None)
    for t in sorted(tiers, key=lambda t: Decimal(str(t["threshold"]))):
        threshold = Decimal(str(t["threshold"]))
        if reached >= threshold:
            best = (Decimal(str(t["bonus"])), threshold)
    return best


# --- Clock in / out ------------------------------------------------------------------


class ClockError(Exception):
    pass


def open_attendance(session: Session, therapist_id: uuid.UUID) -> TherapistAttendance | None:
    return session.scalar(
        select(TherapistAttendance).where(
            TherapistAttendance.therapist_id == therapist_id,
            TherapistAttendance.clock_out_at.is_(None),
        )
    )


def _close_stale(session: Session, row: TherapistAttendance, branch: Branch) -> None:
    """A clock-in left open from an earlier day ends at that day's closing time."""
    tz = ZoneInfo(branch.timezone)
    closes = datetime.combine(row.work_date, branch.closes_at, tzinfo=tz)
    if branch.closes_at <= branch.opens_at:
        closes += timedelta(days=1)
    row.clock_out_at = max(row.clock_in_at, min(closes, now()))


def clocked_in_today(session: Session, branch: Branch) -> dict[uuid.UUID, datetime]:
    """Therapists of this branch clocked in right now, with when they clocked in."""
    rows = session.scalars(
        select(TherapistAttendance).where(
            TherapistAttendance.branch_id == branch.id,
            TherapistAttendance.clock_out_at.is_(None),
            TherapistAttendance.work_date == branch_today(branch),
        )
    )
    return {r.therapist_id: r.clock_in_at for r in rows}


def clock_in(
    session: Session, therapist: Therapist, *, user_id: uuid.UUID | None = None
) -> TherapistAttendance:
    branch = session.get(Branch, therapist.branch_id)
    if not therapist.active:
        raise ClockError("This therapist is inactive")
    today = branch_today(branch)
    current = open_attendance(session, therapist.id)
    if current is not None:
        if current.work_date == today:
            return current  # already in: clicking twice is harmless
        _close_stale(session, current, branch)
        session.flush()
    row = TherapistAttendance(
        tenant_id=therapist.tenant_id,
        therapist_id=therapist.id,
        branch_id=therapist.branch_id,
        work_date=today,
        clock_in_at=now(),
        clocked_in_by=user_id,
    )
    session.add(row)
    session.flush()
    return row


def clock_out(
    session: Session, therapist: Therapist, *, user_id: uuid.UUID | None = None
) -> TherapistAttendance | None:
    current = open_attendance(session, therapist.id)
    if current is None:
        return None
    branch = session.get(Branch, therapist.branch_id)
    if current.work_date != branch_today(branch):
        _close_stale(session, current, branch)
    else:
        current.clock_out_at = now()
    current.clocked_out_by = user_id
    session.flush()
    return current


# --- Commission ----------------------------------------------------------------------


def fix_commission(session: Session, booking: Booking) -> None:
    """Copy each item's commission from its service option (called on completion)."""
    for item in booking.items:
        if item.commission is None:
            variant = session.get(ServiceVariant, item.service_variant_id)
            item.commission = variant.commission if variant else Decimal(0)


# --- Weekly report -------------------------------------------------------------------


@dataclass
class DayLine:
    day: date
    clocked_in: bool = False
    services: int = 0
    sales: Decimal = Decimal(0)
    commission: Decimal = Decimal(0)


@dataclass
class TherapistPay:
    therapist_id: uuid.UUID
    name: str
    active: bool
    days: dict[date, DayLine]
    services: int = 0
    sales: Decimal = Decimal(0)
    commission: Decimal = Decimal(0)
    days_worked: int = 0
    allowance: Decimal = Decimal(0)
    quota_progress: Decimal = Decimal(0)
    bonus: Decimal = Decimal(0)
    bonus_threshold: Decimal | None = None

    @property
    def total(self) -> Decimal:
        return self.commission + self.allowance + self.bonus


@dataclass
class WeekPay:
    week_start: date
    week_end: date
    settings: PaySettings
    therapists: list[TherapistPay] = field(default_factory=list)


def week_pay(session: Session, branch: Branch, any_day: date) -> WeekPay:
    settings = settings_for(session, branch.tenant_id)
    start = week_start(any_day, settings.week_starts_on)
    end = start + timedelta(days=6)
    tz = ZoneInfo(branch.timezone)
    from_ = datetime.combine(start, time(0), tzinfo=tz)
    until = datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz)
    days = [start + timedelta(days=i) for i in range(7)]

    people: dict[uuid.UUID, TherapistPay] = {}

    def person(t: Therapist) -> TherapistPay:
        if t.id not in people:
            people[t.id] = TherapistPay(
                therapist_id=t.id,
                name=t.name,
                active=t.active,
                days={d: DayLine(day=d) for d in days},
            )
        return people[t.id]

    for t in session.scalars(
        select(Therapist).where(Therapist.branch_id == branch.id, Therapist.active.is_(True))
    ):
        person(t)

    items = session.execute(
        select(BookingItem, Therapist, ServiceVariant.commission)
        .join(Booking, Booking.id == BookingItem.booking_id)
        .join(Therapist, Therapist.id == BookingItem.therapist_id)
        .join(ServiceVariant, ServiceVariant.id == BookingItem.service_variant_id)
        .where(
            Booking.branch_id == branch.id,
            BookingItem.status == BookingStatus.completed,
            BookingItem.starts_at >= from_,
            BookingItem.starts_at < until,
        )
    )
    for item, t, variant_commission in items:
        p = person(t)
        line = p.days[item.starts_at.astimezone(tz).date()]
        commission = item.commission if item.commission is not None else variant_commission
        line.services += 1
        line.sales += item.price
        line.commission += commission

    for row, t in session.execute(
        select(TherapistAttendance, Therapist)
        .join(Therapist, Therapist.id == TherapistAttendance.therapist_id)
        .where(
            TherapistAttendance.branch_id == branch.id,
            TherapistAttendance.work_date >= start,
            TherapistAttendance.work_date <= end,
        )
    ):
        person(t).days[row.work_date].clocked_in = True

    for p in people.values():
        for line in p.days.values():
            p.services += line.services
            p.sales += line.sales
            p.commission += line.commission
            p.days_worked += int(line.clocked_in)
        p.allowance = settings.daily_allowance * p.days_worked
        p.quota_progress = (
            p.sales if settings.quota_basis == QuotaBasis.sales else Decimal(p.services)
        )
        p.bonus, p.bonus_threshold = bonus_for(settings.quota_tiers or [], p.quota_progress)

    out = WeekPay(week_start=start, week_end=end, settings=settings)
    out.therapists = sorted(people.values(), key=lambda p: p.name.lower())
    return out
