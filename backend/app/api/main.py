"""HTTP API for the front desk. Auth arrives in issue #6; until then the tenant comes
from the ``X-Tenant-ID`` header so the endpoints can be exercised end to end."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import SessionLocal, set_tenant
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

app = FastAPI(title="Banahaw Reservations API", version="0.1.0")


def tenant_session(x_tenant_id: Annotated[uuid.UUID, Header()]) -> Iterator[Session]:
    with SessionLocal() as session, session.begin():
        set_tenant(session, x_tenant_id)
        session.info["tenant_id"] = x_tenant_id
        yield session


DB = Annotated[Session, Depends(tenant_session)]
Gender = Literal["female", "male"]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class PairOut(BaseModel):
    therapist_id: uuid.UUID
    bed_id: uuid.UUID
    room_id: uuid.UUID


@app.get("/branches/{branch_id}/availability", response_model=list[PairOut])
def availability(
    db: DB,
    branch_id: uuid.UUID,
    variant_id: uuid.UUID,
    starts_at: datetime,
    therapist_id: uuid.UUID | None = None,
    gender: Gender | None = None,
) -> list[Pair]:
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


@app.post("/bookings", response_model=BookingOut, status_code=201)
def post_booking(db: DB, body: BookingIn) -> BookingOut:
    """Book the given therapist + bed per guest, or auto-pick the best pair if omitted."""
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
            tenant_id=db.info["tenant_id"],
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


@app.post("/bookings/{booking_id}/status/{to}", response_model=BookingOut)
def post_status(db: DB, booking_id: uuid.UUID, to: BookingStatus) -> BookingOut:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    try:
        transition(db, booking, to)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    return _out(booking)
