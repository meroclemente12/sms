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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

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


MAX_GROUP = 12


def find_group(
    session: Session,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    wishes: list[GuestWish],
    *,
    prefer: list[Pair] | None = None,
) -> tuple[Pair, ...]:
    """A therapist and a bed for every guest of a group, all starting together.

    For a one-guest service booked for several people (a family, a barkada). Each guest
    gets their own therapist and bed; wishes such as "2 male, 1 female, rest any" are
    honoured. The most specific wishes are served first (a named therapist, then a gender,
    then anyone), so the "any" guests take whoever is left.
    """
    variant = session.get(ServiceVariant, variant_id)
    if variant is None or not variant.active:
        raise NotAvailable("Unknown service")
    service = variant.service
    w = window_for(variant, service, starts_at)
    therapists = free_therapists(session, branch_id, service, w)
    beds = sorted(
        free_beds(session, branch_id, service, w),
        key=lambda b: b.room.room_type == RoomType.couples,
    )
    n = len(wishes)
    if len(beds) < n:
        raise NotAvailable(f"Only {len(beds)} free bed(s) at that time for {n} guests")

    def preferred(i: int) -> Pair | None:
        return prefer[i] if prefer and i < len(prefer) else None

    order = sorted(
        range(n), key=lambda i: (wishes[i].therapist_id is None, wishes[i].gender is None)
    )
    # A therapist a guest asked for (or already has, when moving) is kept for that guest.
    claimed = {wishes[i].therapist_id for i in range(n) if wishes[i].therapist_id}
    claimed |= {p.therapist_id for i in range(n) if (p := preferred(i))}
    used_t: set[uuid.UUID] = set()
    chosen_t: dict[int, Therapist] = {}
    for i in order:
        wish, mine = wishes[i], preferred(i)
        options = [
            t
            for t in therapists
            if t.id not in used_t
            and (wish.therapist_id is None or t.id == wish.therapist_id)
            and (wish.gender is None or t.gender == wish.gender)
        ]
        if not options:
            if wish.therapist_id:
                raise NotAvailable("The requested therapist is not free at that time")
            if wish.gender:
                want = sum(1 for x in wishes if x.gender == wish.gender and not x.therapist_id)
                raise NotAvailable(f"Not enough free {wish.gender} therapists for {want} guest(s)")
            raise NotAvailable(f"Not enough free therapists for {n} guests")
        options.sort(
            key=lambda t: (
                not (mine and t.id == mine.therapist_id),
                t.id in claimed and not (mine and t.id == mine.therapist_id),
            )
        )
        chosen_t[i] = options[0]
        used_t.add(options[0].id)

    used_b: set[uuid.UUID] = set()
    chosen_b: dict[int, Bed] = {}
    bed_order = sorted(range(n), key=lambda i: wishes[i].bed_id is None)
    claimed_b = {wishes[i].bed_id for i in range(n) if wishes[i].bed_id}
    claimed_b |= {p.bed_id for i in range(n) if (p := preferred(i))}
    for i in bed_order:
        wish, mine = wishes[i], preferred(i)
        options = [
            b for b in beds if b.id not in used_b and (wish.bed_id is None or b.id == wish.bed_id)
        ]
        if not options:
            raise NotAvailable("The requested bed is not free at that time")
        options.sort(
            key=lambda b: (
                not (mine and b.id == mine.bed_id),
                b.id in claimed_b and not (mine and b.id == mine.bed_id),
            )
        )
        chosen_b[i] = options[0]
        used_b.add(options[0].id)
    return tuple(Pair(chosen_t[i].id, chosen_b[i].id, chosen_b[i].room_id) for i in range(n))


def guest_prices(service: Service, total: Decimal, guests: int) -> list[Decimal]:
    """A couples price covers both guests; a one-guest service is charged per guest."""
    if service.guest_count == 1:
        return [total] * guests
    return split_price(total, guests)


def check_guest_count(service: Service, guests: int) -> None:
    if service.guest_count == 1:
        if not 1 <= guests <= MAX_GROUP:
            raise ValueError(f"Book 1 to {MAX_GROUP} guests for {service.name}")
    elif guests != service.guest_count:
        raise ValueError(f"{service.name} needs {service.guest_count} guest(s)")


def split_price(total: Decimal, guests: int) -> list[Decimal]:
    """The menu price is for the whole booking; a couples price is shared by both guests
    (any odd centavo goes to the first guest), so the booking total equals the menu."""
    share = (total / guests).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return [total - share * (guests - 1)] + [share] * (guests - 1)


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
    check_guest_count(service, len(guests))
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
    for g, item_price in zip(guests, guest_prices(service, price, len(guests)), strict=True):
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
                price=item_price,
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


# --- Picking, searching and moving -------------------------------------------------------


@dataclass(frozen=True)
class GuestWish:
    """What the desk asked for one guest: a therapist, a gender, a bed (all optional)."""

    therapist_id: uuid.UUID | None = None
    gender: str | None = None
    bed_id: uuid.UUID | None = None


def pick_pairs(
    session: Session,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    wishes: list[GuestWish],
    *,
    prefer: list[Pair] | None = None,
) -> tuple[Pair, ...]:
    """Best therapist + bed per guest, honouring each guest's wishes.

    ``prefer`` (one pair per guest) keeps a moved booking on its current therapist and
    bed when they are still free, so a reschedule changes as little as possible.
    """
    variant = session.get(ServiceVariant, variant_id)
    if variant is None or not variant.active:
        raise NotAvailable("Unknown service")
    check_guest_count(variant.service, len(wishes))
    if variant.service.guest_count == 1 and len(wishes) > 1:
        return find_group(session, branch_id, variant_id, starts_at, wishes, prefer=prefer)
    if len(wishes) == 2:
        w1, w2 = wishes
        return find_couples(
            session,
            branch_id,
            variant_id,
            starts_at,
            therapist_ids=(w1.therapist_id, w2.therapist_id),
            genders=(w1.gender, w2.gender),
        )
    wish = wishes[0]
    pairs = find_pairs(
        session,
        branch_id,
        variant_id,
        starts_at,
        therapist_id=wish.therapist_id,
        gender=wish.gender,
    )
    if wish.bed_id:
        pairs = [p for p in pairs if p.bed_id == wish.bed_id]
    if not pairs:
        raise NotAvailable("No free therapist and bed at that time")
    if prefer:
        current = prefer[0]
        pairs.sort(
            key=lambda p: (p.therapist_id != current.therapist_id, p.bed_id != current.bed_id)
        )
    return (pairs[0],)


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    pairs: tuple[Pair, ...]


def find_slots(
    session: Session,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    wishes: list[GuestWish],
    *,
    after: datetime,
    until: datetime,
    step_min: int = 15,
    limit: int = 12,
) -> list[Slot]:
    """Start times from ``after`` (rounded up to the step) whose service ends by ``until``,
    each with the best pairs, earliest first."""
    variant = session.get(ServiceVariant, variant_id)
    if variant is None or not variant.active:
        raise NotAvailable("Unknown service")
    branch_price(session, branch_id, variant)  # refuses services the branch does not offer
    step = timedelta(minutes=step_min)
    epoch = datetime(2000, 1, 1, tzinfo=after.tzinfo)
    t = epoch + -((epoch - after) // step) * step  # ceil to the step
    duration = timedelta(minutes=variant.duration_min)
    out: list[Slot] = []
    while t + duration <= until and len(out) < limit:
        try:
            out.append(Slot(t, pick_pairs(session, branch_id, variant_id, t, wishes)))
        except NotAvailable:
            pass
        t += step
    return out


@contextmanager
def without_booking(session: Session, booking: Booking) -> Iterator[dict]:
    """While inside, ``booking``'s own slots count as free.

    Used to search or move a booking without it blocking itself. Everything done inside
    is rolled back unless the caller sets ``handle["keep"] = True`` (after restoring the
    items' statuses) and the block exits normally.
    """
    nested = session.begin_nested()
    handle = {"keep": False, "statuses": [i.status for i in booking.items]}
    for item in booking.items:
        item.status = BookingStatus.cancelled
    session.flush()
    try:
        yield handle
    except BaseException:
        nested.rollback()
        raise
    if handle["keep"]:
        nested.commit()
    else:
        nested.rollback()


MOVABLE: tuple[str, ...] = (BookingStatus.booked, BookingStatus.confirmed, BookingStatus.checked_in)


def reschedule(
    session: Session,
    booking: Booking,
    *,
    starts_at: datetime,
    variant_id: uuid.UUID | None = None,
    wishes: list[GuestWish] | None = None,
    user_id: uuid.UUID | None = None,
) -> Booking:
    """Move a booking (every guest together) to a new time, therapist, bed or length.

    Raises ``NotAvailable`` if nothing fits, ``SlotTaken`` if another desk won the race.
    """
    if booking.status not in MOVABLE:
        raise ValueError(f"A {booking.status.replace('_', ' ')} booking cannot be moved")
    items = list(booking.items)
    old_variant = session.get(ServiceVariant, items[0].service_variant_id)
    variant = session.get(ServiceVariant, variant_id or old_variant.id)
    if variant is None or not variant.active:
        raise NotAvailable("Unknown service")
    if variant.service.guest_count != old_variant.service.guest_count:
        raise ValueError("Change a couples booking only to another couples service")
    current = [Pair(i.therapist_id, i.bed_id, i.bed.room_id) for i in items]
    if wishes is None:
        wishes = [GuestWish() for _ in items]
    # A guest who asked for a male or female therapist keeps that wish when moved,
    # unless the desk picked a therapist by name.
    if len(wishes) == len(items):
        wishes = [
            w if w.therapist_id else GuestWish(None, w.gender or i.preferred_gender, w.bed_id)
            for w, i in zip(wishes, items, strict=True)
        ]
    before = {
        "starts_at": items[0].starts_at.isoformat(),
        "variant_id": str(old_variant.id),
        "therapists": [str(i.therapist_id) for i in items],
        "beds": [str(i.bed_id) for i in items],
    }
    w = window_for(variant, variant.service, starts_at)
    prices = (
        [i.price for i in items]
        if variant.id == old_variant.id
        else guest_prices(
            variant.service, branch_price(session, booking.branch_id, variant), len(items)
        )
    )
    try:
        with without_booking(session, booking) as handle:
            pairs = pick_pairs(
                session, booking.branch_id, variant.id, starts_at, wishes, prefer=current
            )
            for item, pair, status, wish, price in zip(
                items, pairs, handle["statuses"], wishes, prices, strict=True
            ):
                item.therapist_id = pair.therapist_id
                item.bed_id = pair.bed_id
                item.service_variant_id = variant.id
                item.starts_at, item.ends_at = w.starts_at, w.ends_at
                item.blocked_from, item.blocked_until = w.blocked_from, w.blocked_until
                item.price = price
                item.status = status
                if wish.gender:
                    item.preferred_gender = wish.gender
            session.flush()
            session.add(
                BookingEvent(
                    tenant_id=booking.tenant_id,
                    booking_id=booking.id,
                    event_type="booking.rescheduled",
                    payload={
                        "from": before,
                        "to": {
                            "starts_at": w.starts_at.isoformat(),
                            "variant_id": str(variant.id),
                            "therapists": [str(p.therapist_id) for p in pairs],
                            "beds": [str(p.bed_id) for p in pairs],
                        },
                    },
                    user_id=user_id,
                )
            )
            session.flush()
            handle["keep"] = True
    except IntegrityError as exc:
        if "no_overlap_" in str(exc.orig):
            raise SlotTaken("That therapist or bed was just booked by someone else") from exc
        raise
    for item in items:
        session.refresh(item, ["bed"])
    return booking
