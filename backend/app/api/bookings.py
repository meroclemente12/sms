"""Availability, bookings and the day board for the front desk."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Staff, StaffSession, ensure_branch
from app.lifecycle import InvalidTransition, transition
from app.models import (
    Bed,
    Booking,
    BookingChannel,
    BookingEvent,
    BookingItem,
    BookingStatus,
    Branch,
    Client,
    Room,
    Service,
    ServiceVariant,
    Shift,
    Therapist,
    TimeBlock,
    User,
)
from app.scheduling import (
    GuestRequest,
    GuestWish,
    NotAvailable,
    Pair,
    SlotTaken,
    create_booking,
    find_pairs,
    find_slots,
    pick_pairs,
    reschedule,
    without_booking,
)

router = APIRouter(tags=["bookings"])

Gender = Literal["female", "male"]


class PairOut(BaseModel):
    therapist_id: uuid.UUID
    bed_id: uuid.UUID
    room_id: uuid.UUID


def _branch(session: Session, staff: Staff, branch_id: uuid.UUID) -> Branch:
    ensure_branch(session, staff, branch_id)
    return session.get(Branch, branch_id)


def _booking(session: Session, staff: Staff, booking_id: uuid.UUID) -> Booking:
    booking = session.get(Booking, booking_id)
    if booking is None or not staff.can_access_branch(booking.branch_id):
        raise HTTPException(404, "Booking not found")
    return booking


def _day_bounds(branch: Branch, day: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(branch.timezone)
    start = datetime.combine(day, time(0), tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)


@router.get("/branches/{branch_id}/availability", response_model=list[PairOut])
def availability(
    ctx: StaffSession,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    therapist_id: uuid.UUID | None = None,
    gender: Gender | None = None,
) -> list[Pair]:
    db, staff = ctx
    ensure_branch(db, staff, branch_id)
    try:
        return find_pairs(
            db, branch_id, variant_id, starts_at, therapist_id=therapist_id, gender=gender
        )
    except NotAvailable as e:
        raise HTTPException(404, str(e)) from e


# --- Finding free times ----------------------------------------------------------------


class WishIn(BaseModel):
    therapist_id: uuid.UUID | None = None
    preferred_gender: Gender | None = None
    bed_id: uuid.UUID | None = None

    def wish(self) -> GuestWish:
        return GuestWish(self.therapist_id, self.preferred_gender, self.bed_id)


class SlotsIn(BaseModel):
    variant_id: uuid.UUID
    date: dt.date | None = Field(default=None, description="Search this day (branch calendar)")
    after: datetime | None = Field(default=None, description="Or search from this moment")
    guests: list[WishIn] = Field(min_length=1, max_length=2)
    exclude_booking_id: uuid.UUID | None = Field(
        default=None, description="Treat this booking's own slots as free (when moving it)"
    )
    limit: int = Field(default=12, ge=1, le=60)


class SlotOut(BaseModel):
    starts_at: datetime
    pairs: list[PairOut]


@router.post("/branches/{branch_id}/slots", response_model=list[SlotOut])
def slots(ctx: StaffSession, branch_id: uuid.UUID, body: SlotsIn) -> list[SlotOut]:
    """Free start times, earliest first, each with the best therapist + bed per guest.

    Searches one day from opening (or from ``after``) until closing time.
    """
    db, staff = ctx
    branch = _branch(db, staff, branch_id)
    tz = ZoneInfo(branch.timezone)
    if body.after is not None:
        day = body.after.astimezone(tz).date()
    elif body.date is not None:
        day = body.date
    else:
        raise HTTPException(422, "Give a date or a start moment")
    opens = datetime.combine(day, branch.opens_at, tzinfo=tz)
    closes = datetime.combine(day, branch.closes_at, tzinfo=tz)
    if closes <= opens:
        closes += timedelta(days=1)
    after = max(opens, body.after) if body.after else opens
    wishes = [g.wish() for g in body.guests]

    def search():
        return find_slots(
            db, branch_id, body.variant_id, wishes, after=after, until=closes, limit=body.limit
        )

    try:
        if body.exclude_booking_id:
            with without_booking(db, _booking(db, staff, body.exclude_booking_id)):
                found = search()
        else:
            found = search()
    except (NotAvailable, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    return [
        SlotOut(starts_at=s.starts_at, pairs=[PairOut(**p.__dict__) for p in s.pairs])
        for s in found
    ]


# --- Bookings --------------------------------------------------------------------------


class GuestIn(WishIn):
    guest_client_id: uuid.UUID | None = None
    guest_name: str | None = Field(default=None, max_length=200)
    therapist_requested: bool = False


class BookingIn(BaseModel):
    branch_id: uuid.UUID
    client_id: uuid.UUID
    channel_id: uuid.UUID
    variant_id: uuid.UUID
    starts_at: datetime
    guests: list[GuestIn] = Field(min_length=1, max_length=2)
    notes: str | None = None
    check_in_now: bool = False


class ItemOut(BaseModel):
    id: uuid.UUID
    therapist_id: uuid.UUID
    therapist_name: str
    bed_id: uuid.UUID
    bed_label: str
    room_id: uuid.UUID
    room_name: str
    guest_name: str | None
    starts_at: datetime
    ends_at: datetime
    blocked_until: datetime
    price: Decimal
    therapist_requested: bool
    preferred_gender: str | None


class EventOut(BaseModel):
    event_type: str
    payload: dict
    user_name: str | None
    occurred_at: datetime


class ClientRef(BaseModel):
    id: uuid.UUID
    name: str
    mobile: str | None
    notes: str | None
    allergies: str | None


class BookingOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    status: str
    client: ClientRef
    channel_id: uuid.UUID
    channel_name: str
    service_id: uuid.UUID
    service_name: str
    variant_id: uuid.UUID
    variant_label: str
    guest_count: int
    starts_at: datetime
    ends_at: datetime
    total: Decimal
    notes: str | None
    amount_paid: Decimal | None
    cancel_reason: str | None
    created_at: datetime
    items: list[ItemOut]
    events: list[EventOut] = []


def _out(db: Session, b: Booking, *, with_events: bool = False) -> BookingOut:
    items = sorted(b.items, key=lambda i: (i.bed.label, str(i.id)))
    variant = db.get(ServiceVariant, items[0].service_variant_id)
    client = db.get(Client, b.client_id)
    channel = db.get(BookingChannel, b.channel_id)
    therapists = {
        t.id: t.name
        for t in db.scalars(
            select(Therapist).where(Therapist.id.in_([i.therapist_id for i in items]))
        )
    }
    events: list[EventOut] = []
    if with_events:
        rows = db.execute(
            select(BookingEvent, User.name)
            .outerjoin(User, User.id == BookingEvent.user_id)
            .where(BookingEvent.booking_id == b.id)
            .order_by(BookingEvent.occurred_at, BookingEvent.id)
        )
        events = [
            EventOut(
                event_type=e.event_type,
                payload=e.payload,
                user_name=name,
                occurred_at=e.occurred_at,
            )
            for e, name in rows
        ]
    paid = (b.attributes or {}).get("amount_paid")
    return BookingOut(
        id=b.id,
        branch_id=b.branch_id,
        status=b.status,
        client=ClientRef(
            id=client.id,
            name=client.name,
            mobile=client.mobile,
            notes=client.notes,
            allergies=client.allergies,
        ),
        channel_id=channel.id,
        channel_name=channel.name,
        service_id=variant.service_id,
        service_name=variant.service.name,
        variant_id=variant.id,
        variant_label=variant.label,
        guest_count=len(items),
        starts_at=items[0].starts_at,
        ends_at=items[0].ends_at,
        total=sum((i.price for i in items), Decimal(0)),
        notes=b.notes,
        amount_paid=Decimal(paid) if paid is not None else None,
        cancel_reason=(b.attributes or {}).get("cancel_reason"),
        created_at=b.created_at,
        items=[
            ItemOut(
                id=i.id,
                therapist_id=i.therapist_id,
                therapist_name=therapists.get(i.therapist_id, "?"),
                bed_id=i.bed_id,
                bed_label=i.bed.label,
                room_id=i.bed.room_id,
                room_name=i.bed.room.name,
                guest_name=i.guest_name,
                starts_at=i.starts_at,
                ends_at=i.ends_at,
                blocked_until=i.blocked_until,
                price=i.price,
                therapist_requested=i.therapist_requested,
                preferred_gender=i.preferred_gender,
            )
            for i in items
        ],
        events=events,
    )


def _check_refs(db: Session, client_id: uuid.UUID | None, channel_id: uuid.UUID | None) -> None:
    # Row-level security already hides other spas' rows, so a foreign id is "not found".
    if client_id is not None and db.get(Client, client_id) is None:
        raise HTTPException(422, "Client not found")
    if channel_id is not None:
        channel = db.get(BookingChannel, channel_id)
        if channel is None or not channel.active:
            raise HTTPException(422, "Choose an active booking channel")


@router.post("/bookings", response_model=BookingOut, status_code=201)
def post_booking(ctx: StaffSession, body: BookingIn) -> BookingOut:
    """Book the given therapist + bed per guest, or auto-pick the best pair if omitted."""
    db, staff = ctx
    ensure_branch(db, staff, body.branch_id)
    _check_refs(db, body.client_id, body.channel_id)
    try:
        pairs = pick_pairs(
            db, body.branch_id, body.variant_id, body.starts_at, [g.wish() for g in body.guests]
        )
        booking = create_booking(
            db,
            tenant_id=staff.tenant_id,
            branch_id=body.branch_id,
            client_id=body.client_id,
            channel_id=body.channel_id,
            variant_id=body.variant_id,
            starts_at=body.starts_at,
            guests=[
                GuestRequest(
                    pair=p,
                    guest_client_id=g.guest_client_id,
                    guest_name=g.guest_name,
                    therapist_requested=g.therapist_requested,
                    preferred_gender=g.preferred_gender,
                )
                for p, g in zip(pairs, body.guests, strict=True)
            ],
            user_id=staff.user.id,
            notes=body.notes,
            status=BookingStatus.checked_in if body.check_in_now else BookingStatus.booked,
        )
    except SlotTaken as e:
        raise HTTPException(409, str(e)) from e
    except (NotAvailable, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    return _out(db, booking)


@router.get("/bookings/{booking_id}", response_model=BookingOut)
def get_booking(ctx: StaffSession, booking_id: uuid.UUID) -> BookingOut:
    db, staff = ctx
    return _out(db, _booking(db, staff, booking_id), with_events=True)


class BookingEdit(BaseModel):
    client_id: uuid.UUID | None = None
    channel_id: uuid.UUID | None = None
    notes: str | None = None


@router.patch("/bookings/{booking_id}", response_model=BookingOut)
def edit_booking(ctx: StaffSession, booking_id: uuid.UUID, body: BookingEdit) -> BookingOut:
    """Change the booking's client, channel or notes. Only the fields sent are changed."""
    db, staff = ctx
    booking = _booking(db, staff, booking_id)
    changes = body.model_dump(exclude_unset=True)
    _check_refs(db, changes.get("client_id"), changes.get("channel_id"))
    if changes.get("client_id") is None:
        changes.pop("client_id", None)
    if changes.get("channel_id") is None:
        changes.pop("channel_id", None)
    before = {k: getattr(booking, k) for k in changes}
    for k, v in changes.items():
        setattr(booking, k, v)
    if changes:
        db.add(
            BookingEvent(
                tenant_id=booking.tenant_id,
                booking_id=booking.id,
                event_type="booking.edited",
                payload={
                    "from": {k: str(v) if v is not None else None for k, v in before.items()},
                    "to": {k: str(v) if v is not None else None for k, v in changes.items()},
                },
                user_id=staff.user.id,
            )
        )
        db.flush()
    return _out(db, booking, with_events=True)


class RescheduleIn(BaseModel):
    starts_at: datetime
    variant_id: uuid.UUID | None = Field(default=None, description="Change the length/service")
    guests: list[WishIn] | None = Field(
        default=None, description="Per guest: a therapist, gender or bed. Empty = any"
    )


@router.post("/bookings/{booking_id}/reschedule", response_model=BookingOut)
def post_reschedule(ctx: StaffSession, booking_id: uuid.UUID, body: RescheduleIn) -> BookingOut:
    """Move a booking. Both guests of a couples booking move together."""
    db, staff = ctx
    booking = _booking(db, staff, booking_id)
    try:
        reschedule(
            db,
            booking,
            starts_at=body.starts_at,
            variant_id=body.variant_id,
            wishes=[g.wish() for g in body.guests] if body.guests else None,
            user_id=staff.user.id,
        )
    except SlotTaken as e:
        raise HTTPException(409, str(e)) from e
    except (NotAvailable, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    return _out(db, booking, with_events=True)


class StatusIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500, description="Why it was cancelled")
    amount_paid: Decimal | None = Field(default=None, ge=0, description="Noted at check-out")


@router.post("/bookings/{booking_id}/status/{to}", response_model=BookingOut)
def post_status(
    ctx: StaffSession,
    booking_id: uuid.UUID,
    to: BookingStatus,
    body: StatusIn | None = None,
) -> BookingOut:
    db, staff = ctx
    booking = _booking(db, staff, booking_id)
    body = body or StatusIn()
    note: dict[str, str] = {}
    if body.reason and to in (BookingStatus.cancelled, BookingStatus.no_show):
        note["cancel_reason"] = body.reason
    if body.amount_paid is not None and to == BookingStatus.completed:
        note["amount_paid"] = str(body.amount_paid)
    try:
        transition(db, booking, to, user_id=staff.user.id, note=note)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    if note:
        booking.attributes = {**(booking.attributes or {}), **note}
        db.flush()
    return _out(db, booking, with_events=True)


# --- Day board -------------------------------------------------------------------------


class Span(BaseModel):
    starts_at: datetime
    ends_at: datetime


class BoardTherapist(BaseModel):
    id: uuid.UUID
    name: str
    gender: str
    colour: str | None
    shifts: list[Span]


class BoardBed(BaseModel):
    id: uuid.UUID
    label: str
    room_id: uuid.UUID
    room_name: str
    room_type: str


class BoardBlock(BaseModel):
    id: uuid.UUID
    therapist_id: uuid.UUID | None
    bed_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime
    reason: str


class BoardItem(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    therapist_id: uuid.UUID
    bed_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    blocked_from: datetime
    blocked_until: datetime
    status: str
    client_name: str
    guest_name: str | None
    service_name: str
    variant_label: str
    channel_name: str
    therapist_requested: bool
    preferred_gender: str | None
    guest_count: int
    has_notes: bool


class BoardOut(BaseModel):
    date: dt.date
    timezone: str
    opens_at: time
    closes_at: time
    therapists: list[BoardTherapist]
    beds: list[BoardBed]
    blocks: list[BoardBlock]
    items: list[BoardItem]


@router.get("/branches/{branch_id}/board", response_model=BoardOut)
def board(
    ctx: StaffSession,
    branch_id: uuid.UUID,
    day: Annotated[date, Query(alias="date")],
    include_cancelled: bool = False,
) -> BoardOut:
    """Everything the day board shows for one branch and one day."""
    db, staff = ctx
    branch = _branch(db, staff, branch_id)
    start, end = _day_bounds(branch, day)

    shifts: dict[uuid.UUID, list[Span]] = {}
    for s in db.scalars(
        select(Shift)
        .where(Shift.branch_id == branch_id, Shift.starts_at < end, Shift.ends_at > start)
        .order_by(Shift.starts_at)
    ):
        shifts.setdefault(s.therapist_id, []).append(Span(starts_at=s.starts_at, ends_at=s.ends_at))
    therapists = [
        BoardTherapist(
            id=t.id,
            name=t.name,
            gender=t.gender,
            colour=t.display_colour,
            shifts=shifts.get(t.id, []),
        )
        for t in db.scalars(
            select(Therapist)
            .where(Therapist.branch_id == branch_id, Therapist.active.is_(True))
            .order_by(Therapist.name)
        )
    ]
    beds = [
        BoardBed(id=b.id, label=b.label, room_id=r.id, room_name=r.name, room_type=r.room_type)
        for b, r in db.execute(
            select(Bed, Room)
            .join(Room, Room.id == Bed.room_id)
            .where(Room.branch_id == branch_id, Room.active.is_(True), Bed.active.is_(True))
            .order_by(Room.name, Bed.label)
        )
    ]
    t_ids = [t.id for t in therapists]
    b_ids = [b.id for b in beds]
    blocks = [
        BoardBlock(
            id=tb.id,
            therapist_id=tb.therapist_id,
            bed_id=tb.bed_id,
            starts_at=tb.starts_at,
            ends_at=tb.ends_at,
            reason=tb.reason,
        )
        for tb in db.scalars(
            select(TimeBlock).where(
                (TimeBlock.therapist_id.in_(t_ids)) | (TimeBlock.bed_id.in_(b_ids)),
                TimeBlock.starts_at < end,
                TimeBlock.ends_at > start,
            )
        )
    ]

    q = (
        select(BookingItem, Booking, Client.name, Service, ServiceVariant, BookingChannel.name)
        .join(Booking, Booking.id == BookingItem.booking_id)
        .join(Client, Client.id == Booking.client_id)
        .join(ServiceVariant, ServiceVariant.id == BookingItem.service_variant_id)
        .join(Service, Service.id == ServiceVariant.service_id)
        .join(BookingChannel, BookingChannel.id == Booking.channel_id)
        .where(
            Booking.branch_id == branch_id,
            BookingItem.starts_at < end,
            BookingItem.ends_at > start,
        )
        .order_by(BookingItem.starts_at)
    )
    if not include_cancelled:
        q = q.where(BookingItem.status.notin_([BookingStatus.cancelled, BookingStatus.no_show]))
    items = [
        BoardItem(
            id=i.id,
            booking_id=b.id,
            therapist_id=i.therapist_id,
            bed_id=i.bed_id,
            starts_at=i.starts_at,
            ends_at=i.ends_at,
            blocked_from=i.blocked_from,
            blocked_until=i.blocked_until,
            status=i.status,
            client_name=client_name,
            guest_name=i.guest_name,
            service_name=svc.name,
            variant_label=v.label,
            channel_name=channel_name,
            therapist_requested=i.therapist_requested,
            preferred_gender=i.preferred_gender,
            guest_count=svc.guest_count,
            has_notes=bool(b.notes),
        )
        for i, b, client_name, svc, v, channel_name in db.execute(q)
    ]
    return BoardOut(
        date=day,
        timezone=branch.timezone,
        opens_at=branch.opens_at,
        closes_at=branch.closes_at,
        therapists=therapists,
        beds=beds,
        blocks=blocks,
        items=items,
    )
