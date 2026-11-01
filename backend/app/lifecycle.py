"""Booking status transitions. Every change is written to ``booking_events``."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import Booking, BookingEvent, BookingStatus

S = BookingStatus

ALLOWED: dict[str, set[str]] = {
    S.booked: {S.confirmed, S.checked_in, S.cancelled, S.no_show},
    S.confirmed: {S.checked_in, S.cancelled, S.no_show},
    S.checked_in: {S.in_service, S.cancelled},
    S.in_service: {S.completed},
    S.completed: set(),
    S.cancelled: set(),
    S.no_show: set(),
}


class InvalidTransition(Exception):
    pass


def transition(
    session: Session,
    booking: Booking,
    to: BookingStatus,
    *,
    user_id: uuid.UUID | None = None,
    note: dict[str, Any] | None = None,
) -> Booking:
    """Move a booking and all its items (both guests of a couples booking) together."""
    if to not in ALLOWED[booking.status]:
        raise InvalidTransition(f"Cannot go from {booking.status} to {to}")
    before = booking.status
    booking.status = to
    for item in booking.items:
        item.status = to
    session.add(
        BookingEvent(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            event_type=f"booking.{to}",
            payload={"from": str(before), "to": str(to), **(note or {})},
            user_id=user_id,
        )
    )
    session.flush()
    return booking
