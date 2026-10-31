"""Availability engine: find valid (therapist, bed) pairs and book them safely.

Hard rules (never overridden):
  * a therapist does one booking item at a time, and only services they are skilled in;
  * a bed hosts one booking item at a time (cleanup buffer included), and only services
    its bed type allows;
  * the therapist's shift covers the whole service (buffers excluded: cleanup is bed work);
  * no overlapping time block on the therapist or the bed.

The final guard against two desks booking the same slot at once is the pair of
exclusion constraints on ``booking_items`` (see migration 0001).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ACTIVE_STATUSES,
    Bed,
    Booking,
    BookingEvent,
    BookingItem,
    BookingStatus,
    BranchServicePrice,
    Room,
    RoomType,
    Service,
    ServiceVariant,
    Shift,
    Therapist,
    TherapistSkill,
    TimeBlock,
)


class SlotTaken(Exception):
    """The therapist or bed was taken between search and booking."""


class NotAvailable(Exception):
    """No valid therapist and bed for the requested slot."""


@dataclass(frozen=True)
class Window:
    starts_at: datetime
    ends_at: datetime
    blocked_from: datetime
    blocked_until: datetime


@dataclass(frozen=True)
class Pair:
    therapist_id: uuid.UUID
    bed_id: uuid.UUID
    room_id: uuid.UUID


def window_for(variant: ServiceVariant, service: Service, starts_at: datetime) -> Window:
    ends_at = starts_at + timedelta(minutes=variant.duration_min)
    return Window(
        starts_at=starts_at,
        ends_at=ends_at,
        blocked_from=starts_at - timedelta(minutes=service.buffer_before_min),
        blocked_until=ends_at + timedelta(minutes=service.buffer_after_min),
    )


def _overlaps(col_from, col_until, start: datetime, end: datetime):
    return and_(col_from < end, col_until > start)


def branch_price(session: Session, branch_id: uuid.UUID, variant: ServiceVariant) -> Decimal:
    """Price at this branch: the branch override if set, otherwise the default."""
    override = session.scalar(
        select(BranchServicePrice).where(
            BranchServicePrice.branch_id == branch_id,
            BranchServicePrice.service_variant_id == variant.id,
        )
    )
    if override is not None and not override.offered:
        raise NotAvailable("This service is not offered at this branch")
    if override is not None and override.price is not None:
        return override.price
    return variant.default_price


def free_therapists(
    session: Session,
    branch_id: uuid.UUID,
    service: Service,
    w: Window,
    *,
    therapist_id: uuid.UUID | None = None,
    gender: str | None = None,
) -> list[Therapist]:
    """Skilled, on-shift, unblocked, unbooked therapists of this branch.

    Ordered least-busy first (fewest active items that day) so work spreads evenly.
    """
    day_start = w.starts_at - timedelta(hours=24)
    day_end = w.starts_at + timedelta(hours=24)
    load = (
        select(func.count(BookingItem.id))
        .where(
            BookingItem.therapist_id == Therapist.id,
            BookingItem.status.in_(ACTIVE_STATUSES),
            _overlaps(BookingItem.starts_at, BookingItem.ends_at, day_start, day_end),
        )
        .correlate(Therapist)
        .scalar_subquery()
    )
    q = (
        select(Therapist)
        .where(
            Therapist.branch_id == branch_id,
            Therapist.active.is_(True),
            exists().where(
                TherapistSkill.therapist_id == Therapist.id,
                TherapistSkill.service_id == service.id,
            ),
            exists().where(
                Shift.therapist_id == Therapist.id,
                Shift.branch_id == branch_id,
                Shift.starts_at <= w.starts_at,
                Shift.ends_at >= w.ends_at,
            ),
            not_(
                exists().where(
                    BookingItem.therapist_id == Therapist.id,
                    BookingItem.status.in_(ACTIVE_STATUSES),
                    _overlaps(BookingItem.starts_at, BookingItem.ends_at, w.starts_at, w.ends_at),
                )
            ),
            not_(
                exists().where(
                    TimeBlock.therapist_id == Therapist.id,
                    _overlaps(TimeBlock.starts_at, TimeBlock.ends_at, w.starts_at, w.ends_at),
                )
            ),
        )
        .order_by(load, Therapist.name)
    )
    if therapist_id is not None:
        q = q.where(Therapist.id == therapist_id)
    if gender is not None:
        q = q.where(Therapist.gender == gender)
    return list(session.scalars(q))


def free_beds(
    session: Session,
    branch_id: uuid.UUID,
    service: Service,
    w: Window,
    *,
    room_type: str | None = None,
) -> list[Bed]:
    """Active beds of the right type, in open rooms, free for the window incl. buffers."""
    q = (
        select(Bed)
        .join(Room, Room.id == Bed.room_id)
        .where(
            Room.branch_id == branch_id,
            Room.active.is_(True),
            Bed.active.is_(True),
            Bed.bed_type_id == service.bed_type_id,
            not_(
                exists().where(
                    BookingItem.bed_id == Bed.id,
                    BookingItem.status.in_(ACTIVE_STATUSES),
                    _overlaps(
                        BookingItem.blocked_from,
                        BookingItem.blocked_until,
                        w.blocked_from,
                        w.blocked_until,
                    ),
                )
            ),
            not_(
                exists().where(
                    TimeBlock.bed_id == Bed.id,
                    _overlaps(
                        TimeBlock.starts_at, TimeBlock.ends_at, w.blocked_from, w.blocked_until
                    ),
                )
            ),
        )
        .order_by(Room.name, Bed.label)
    )
    if room_type is not None:
        q = q.where(Room.room_type == room_type)
    return list(session.scalars(q))


def find_pairs(
    session: Session,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    *,
    therapist_id: uuid.UUID | None = None,
    gender: str | None = None,
) -> list[Pair]:
    """Valid (therapist, bed) pairs for a one-guest service, best first."""
    variant = session.get(ServiceVariant, variant_id)
    if variant is None or not variant.active:
        raise NotAvailable("Unknown service")
    service = variant.service
    w = window_for(variant, service, starts_at)
    therapists = free_therapists(
        session, branch_id, service, w, therapist_id=therapist_id, gender=gender
    )
    # Single-guest services keep couples rooms free when a single room will do.
    beds = sorted(
        free_beds(session, branch_id, service, w),
        key=lambda b: b.room.room_type == RoomType.couples,
    )
    return [Pair(t.id, b.id, b.room_id) for t in therapists for b in beds]


def find_couples(
    session: Session,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    *,
    therapist_ids: tuple[uuid.UUID | None, uuid.UUID | None] = (None, None),
    genders: tuple[str | None, str | None] = (None, None),
) -> tuple[Pair, Pair]:
    """Two therapists and two beds in the same couples room, same start and end."""
    variant = session.get(ServiceVariant, variant_id)
    if variant is None or not variant.active:
        raise NotAvailable("Unknown service")
    service = variant.service
    w = window_for(variant, service, starts_at)

    beds_by_room: dict[uuid.UUID, list[Bed]] = {}
    for bed in free_beds(session, branch_id, service, w, room_type=RoomType.couples):
        beds_by_room.setdefault(bed.room_id, []).append(bed)
    room_beds = next((beds for beds in beds_by_room.values() if len(beds) >= 2), None)
    if room_beds is None:
        raise NotAvailable("No couples room with two free beds at that time")

    first = free_therapists(
        session, branch_id, service, w, therapist_id=therapist_ids[0], gender=genders[0]
    )
    second = free_therapists(
        session, branch_id, service, w, therapist_id=therapist_ids[1], gender=genders[1]
    )
    for t1 in first:
        for t2 in second:
            if t1.id != t2.id:
                return (
                    Pair(t1.id, room_beds[0].id, room_beds[0].room_id),
                    Pair(t2.id, room_beds[1].id, room_beds[1].room_id),
                )
    raise NotAvailable("Not enough free therapists for a couples booking")


@dataclass(frozen=True)
class GuestRequest:
    """One guest's line in a booking."""

    pair: Pair
    guest_client_id: uuid.UUID | None = None
    guest_name: str | None = None
    therapist_requested: bool = False
    preferred_gender: str | None = None


def create_booking(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    branch_id: uuid.UUID,
    client_id: uuid.UUID,
    channel_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    guests: list[GuestRequest],
    user_id: uuid.UUID | None = None,
    notes: str | None = None,
    status: BookingStatus = BookingStatus.booked,
) -> Booking:
    """Insert a booking with one item per guest, atomically.

    Raises ``SlotTaken`` if another desk took the therapist or bed first.
    """
    variant = session.get(ServiceVariant, variant_id)
    if variant is None:
        raise NotAvailable("Unknown service")
    service = variant.service
    if len(guests) != service.guest_count:
        raise ValueError(f"{service.name} needs {service.guest_count} guest(s)")
    w = window_for(variant, service, starts_at)
    price = branch_price(session, branch_id, variant)

    booking = Booking(
        tenant_id=tenant_id,
        branch_id=branch_id,
        client_id=client_id,
        channel_id=channel_id,
        status=status,
        notes=notes,
        created_by=user_id,
    )
    for g in guests:
        booking.items.append(
            BookingItem(
                tenant_id=tenant_id,
                guest_client_id=g.guest_client_id,
                guest_name=g.guest_name,
                service_variant_id=variant.id,
                therapist_id=g.pair.therapist_id,
                bed_id=g.pair.bed_id,
                starts_at=w.starts_at,
                ends_at=w.ends_at,
                blocked_from=w.blocked_from,
                blocked_until=w.blocked_until,
                price=price,
                status=status,
                therapist_requested=g.therapist_requested,
                preferred_gender=g.preferred_gender,
            )
        )
    try:
        with session.begin_nested():
            session.add(booking)
            session.flush()
            session.add(
                BookingEvent(
                    tenant_id=tenant_id,
                    booking_id=booking.id,
                    event_type="booking.created",
                    payload={"status": str(status), "starts_at": w.starts_at.isoformat()},
                    user_id=user_id,
                )
            )
            session.flush()
    except IntegrityError as exc:
        if "no_overlap_" in str(exc.orig):
            raise SlotTaken("That therapist or bed was just booked by someone else") from exc
        raise
    return booking
