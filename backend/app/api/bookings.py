"""Availability and bookings for the front desk."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import StaffSession, ensure_branch
from app.lifecycle import InvalidTransition, transition
from app.models import Booking, BookingStatus
from app.scheduling import (
    GuestRequest,
    NotAvailable,
    Pair,
    SlotTaken,
    create_booking,
    find_couples,
    find_pairs,
)

router = APIRouter(tags=["bookings"])

Gender = Literal["female", "male"]


class PairOut(BaseModel):
    therapist_id: uuid.UUID
    bed_id: uuid.UUID
    room_id: uuid.UUID


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


class GuestIn(BaseModel):
    therapist_id: uuid.UUID | None = None
    bed_id: uuid.UUID | None = None
    guest_client_id: uuid.UUID | None = None
    guest_name: str | None = None
    therapist_requested: bool = False
    preferred_gender: Gender | None = None


class BookingIn(BaseModel):
    branch_id: uuid.UUID
    client_id: uuid.UUID
    channel_id: uuid.UUID
    variant_id: uuid.UUID
    starts_at: datetime
    guests: list[GuestIn]
    notes: str | None = None
    check_in_now: bool = False


class BookingOut(BaseModel):
    id: uuid.UUID
    status: str
    items: list[PairOut]


def _out(b: Booking) -> BookingOut:
    return BookingOut(
        id=b.id,
        status=b.status,
        items=[
            PairOut(therapist_id=i.therapist_id, bed_id=i.bed_id, room_id=i.bed.room_id)
            for i in b.items
        ],
    )


@router.post("/bookings", response_model=BookingOut, status_code=201)
def post_booking(ctx: StaffSession, body: BookingIn) -> BookingOut:
    """Book the given therapist + bed per guest, or auto-pick the best pair if omitted."""
    db, staff = ctx
    ensure_branch(db, staff, body.branch_id)
    try:
        if len(body.guests) == 2:
            g1, g2 = body.guests
            auto = find_couples(
                db,
                body.branch_id,
                body.variant_id,
                body.starts_at,
                therapist_ids=(g1.therapist_id, g2.therapist_id),
                genders=(g1.preferred_gender, g2.preferred_gender),
            )
        else:
            g = body.guests[0]
            pairs = find_pairs(
                db,
                body.branch_id,
                body.variant_id,
                body.starts_at,
                therapist_id=g.therapist_id,
                gender=g.preferred_gender,
            )
            if g.bed_id:
                pairs = [p for p in pairs if p.bed_id == g.bed_id]
            if not pairs:
                raise NotAvailable("No free therapist and bed at that time")
            auto = (pairs[0],)
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
                for p, g in zip(auto, body.guests, strict=True)
            ],
            notes=body.notes,
            status=BookingStatus.checked_in if body.check_in_now else BookingStatus.booked,
        )
    except SlotTaken as e:
        raise HTTPException(409, str(e)) from e
    except (NotAvailable, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    return _out(booking)


@router.post("/bookings/{booking_id}/status/{to}", response_model=BookingOut)
def post_status(ctx: StaffSession, booking_id: uuid.UUID, to: BookingStatus) -> BookingOut:
    db, staff = ctx
    booking = db.get(Booking, booking_id)
    if booking is None or not staff.can_access_branch(booking.branch_id):
        raise HTTPException(404, "Booking not found")
    try:
        transition(db, booking, to)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    return _out(booking)
