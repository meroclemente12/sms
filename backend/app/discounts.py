"""Discount codes: checking a code against a booking, applying it, and counting uses.

A code takes a percentage or a fixed peso amount off the services it covers (all
services when it lists none). Uses on cancelled or no-show bookings do not count
against the code's limits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Booking,
    BookingEvent,
    BookingItem,
    BookingStatus,
    Branch,
    DiscountCode,
    DiscountKind,
    DiscountRedemption,
    ServiceVariant,
)

CENT = Decimal("0.01")
NOT_COUNTED = (BookingStatus.cancelled, BookingStatus.no_show)


class DiscountError(ValueError):
    pass


def normalise(code: str) -> str:
    return "".join(code.split()).upper()


@dataclass
class Quote:
    code: DiscountCode
    eligible: Decimal  # the part of the bill the code applies to
    amount: Decimal


def uses_query(code_id: uuid.UUID):
    """Redemptions of a code that still count (their booking was not cancelled)."""
    return (
        select(DiscountRedemption)
        .join(Booking, Booking.id == DiscountRedemption.booking_id)
        .where(DiscountRedemption.code_id == code_id, Booking.status.not_in(NOT_COUNTED))
    )


def use_count(session: Session, code_id: uuid.UUID) -> int:
    return session.scalar(select(func.count()).select_from(uses_query(code_id).subquery())) or 0


def _visit_date(session: Session, booking: Booking) -> date:
    branch = session.get(Branch, booking.branch_id)
    first = min(i.starts_at for i in booking.items)
    return first.astimezone(ZoneInfo(branch.timezone)).date()


def _eligible_total(session: Session, booking: Booking, code: DiscountCode) -> Decimal:
    if not code.service_ids:
        return sum((i.price for i in booking.items), Decimal(0))
    rows = session.execute(
        select(BookingItem.price)
        .join(ServiceVariant, ServiceVariant.id == BookingItem.service_variant_id)
        .where(
            BookingItem.booking_id == booking.id,
            ServiceVariant.service_id.in_(code.service_ids),
        )
    )
    return sum((p for (p,) in rows), Decimal(0))


def _amount(code: DiscountCode, eligible: Decimal) -> Decimal:
    if code.kind == DiscountKind.percent:
        return (eligible * code.value / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return min(code.value, eligible)


def quote(session: Session, booking: Booking, raw_code: str, *, lock: bool = False) -> Quote:
    """How much ``raw_code`` takes off ``booking``, or a DiscountError saying why not."""
    q = select(DiscountCode).where(DiscountCode.code == normalise(raw_code))
    if lock:  # serialise uses of one code so two desks cannot pass the limit together
        q = q.with_for_update()
    code = session.scalar(q)
    if code is None or not code.active:
        raise DiscountError("That discount code does not exist or is switched off")
    if booking.status in NOT_COUNTED:
        raise DiscountError("This booking was cancelled")
    if code.branch_ids and booking.branch_id not in code.branch_ids:
        raise DiscountError(f"{code.code} cannot be used at this branch")
    day = _visit_date(session, booking)
    if code.starts_on and day < code.starts_on:
        raise DiscountError(f"{code.code} starts on {code.starts_on:%b %-d, %Y}")
    if code.ends_on and day > code.ends_on:
        raise DiscountError(f"{code.code} ended on {code.ends_on:%b %-d, %Y}")

    others = uses_query(code.id).where(DiscountRedemption.booking_id != booking.id)
    if code.max_uses is not None:
        used = session.scalar(select(func.count()).select_from(others.subquery())) or 0
        if used >= code.max_uses:
            raise DiscountError(f"{code.code} has been used up ({code.max_uses} uses)")
    if code.once_per_client:
        mine = others.where(DiscountRedemption.client_id == booking.client_id)
        if session.scalar(select(func.count()).select_from(mine.subquery())):
            raise DiscountError(f"This client has already used {code.code}")

    eligible = _eligible_total(session, booking, code)
    if eligible <= 0:
        raise DiscountError(f"{code.code} does not cover the services in this booking")
    amount = _amount(code, eligible)
    if amount <= 0:
        raise DiscountError(f"{code.code} takes nothing off this booking")
    return Quote(code=code, eligible=eligible, amount=amount)


def redemption_for(session: Session, booking_id: uuid.UUID) -> DiscountRedemption | None:
    return session.scalar(
        select(DiscountRedemption).where(DiscountRedemption.booking_id == booking_id)
    )


def _event(session, booking, event_type, user_id, r: DiscountRedemption) -> None:
    session.add(
        BookingEvent(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            event_type=event_type,
            payload={"code": r.code.code, "amount": str(r.amount)},
            user_id=user_id,
        )
    )


def apply_code(
    session: Session, booking: Booking, raw_code: str, *, user_id: uuid.UUID | None
) -> DiscountRedemption:
    """Put a code on a booking, replacing any code already on it."""
    if booking.status == BookingStatus.completed:
        raise DiscountError("This booking is already checked out")
    q = quote(session, booking, raw_code, lock=True)
    existing = redemption_for(session, booking.id)
    if existing is not None:
        _event(session, booking, "discount.removed", user_id, existing)
        session.delete(existing)
        session.flush()
    r = DiscountRedemption(
        tenant_id=booking.tenant_id,
        code_id=q.code.id,
        booking_id=booking.id,
        client_id=booking.client_id,
        branch_id=booking.branch_id,
        amount=q.amount,
        redeemed_by=user_id,
    )
    r.code = q.code
    session.add(r)
    session.flush()
    _event(session, booking, "discount.applied", user_id, r)
    session.flush()
    return r


def remove_code(session: Session, booking: Booking, *, user_id: uuid.UUID | None) -> None:
    if booking.status == BookingStatus.completed:
        raise DiscountError("This booking is already checked out")
    existing = redemption_for(session, booking.id)
    if existing is None:
        return
    _event(session, booking, "discount.removed", user_id, existing)
    session.delete(existing)
    session.flush()


def refresh(session: Session, booking: Booking) -> None:
    """Recompute a booking's discount after its services, prices or client changed.
    The code's limits were checked when it was applied, so only the amount moves."""
    r = redemption_for(session, booking.id)
    if r is None:
        return
    amount = _amount(r.code, _eligible_total(session, booking, r.code))
    r.client_id = booking.client_id
    if amount <= 0:
        session.delete(r)
    else:
        r.amount = amount
    session.flush()
