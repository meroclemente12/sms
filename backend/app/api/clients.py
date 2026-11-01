"""Client lookup: search by mobile or name, profile, notes, allergies and history.

Clients belong to the whole spa (every branch sees the same guest), so any signed-in
staff member can find, add and update them.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from app.api.deps import StaffSession
from app.models import (
    Booking,
    BookingItem,
    Branch,
    Client,
    Service,
    ServiceVariant,
    Therapist,
)

router = APIRouter(tags=["clients"])


def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _mobile_key(mobile: str | None) -> str:
    """Last 10 digits: +63 917 123 4567, 0917-123-4567 and 9171234567 are one number."""
    return digits(mobile)[-10:]


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mobile: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    gender: Literal["female", "male", "other"] | None = None
    notes: str | None = None
    allergies: str | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    mobile: str | None
    email: str | None
    gender: str | None
    notes: str | None
    allergies: str | None


class VisitOut(BaseModel):
    booking_id: uuid.UUID
    branch_name: str
    starts_at: datetime
    service_name: str
    variant_label: str
    therapist_name: str
    status: str
    price: Decimal


class ClientDetail(ClientOut):
    visits: list[VisitOut]


def _mobile_match(key: str):
    return func.right(func.regexp_replace(Client.mobile, r"\D", "", "g"), 10) == key


def _check_duplicate(session, mobile: str | None, own_id: uuid.UUID | None = None) -> None:
    key = _mobile_key(mobile)
    if len(key) < 7:
        return
    q = select(Client.name).where(_mobile_match(key))
    if own_id is not None:
        q = q.where(Client.id != own_id)
    other = session.scalar(q.limit(1))
    if other:
        raise HTTPException(409, f"This mobile number already belongs to {other}")


def _clean(body: ClientIn) -> dict:
    data = body.model_dump()
    for k, v in data.items():
        if isinstance(v, str):
            data[k] = v.strip() or None
    if not data["name"]:
        raise HTTPException(422, "name: Enter the client's name")
    return data


@router.get("/clients", response_model=list[ClientOut])
def search_clients(ctx: StaffSession, q: str = "", limit: int = 20) -> list[Client]:
    """Search by part of the name or mobile number. Empty = most recently added."""
    session, _ = ctx
    q = q.strip()
    stmt = select(Client)
    if q:
        conds = [Client.name.ilike(f"%{q}%")]
        d = digits(q)
        if len(d) >= 3:
            # Match "0917 123", "917123" and "+63917123" alike.
            conds.append(func.regexp_replace(Client.mobile, r"\D", "", "g").contains(d.lstrip("0")))
        stmt = stmt.where(or_(*conds)).order_by(Client.name)
    else:
        stmt = stmt.order_by(Client.created_at.desc())
    return list(session.scalars(stmt.limit(min(max(limit, 1), 50))))


@router.post("/clients", response_model=ClientOut, status_code=201)
def create_client(ctx: StaffSession, body: ClientIn) -> Client:
    session, staff = ctx
    data = _clean(body)
    _check_duplicate(session, data["mobile"])
    client = Client(tenant_id=staff.tenant_id, **data)
    session.add(client)
    session.flush()
    return client


def _get(session, client_id: uuid.UUID) -> Client:
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Client not found")
    return client


@router.get("/clients/{client_id}", response_model=ClientDetail)
def get_client(ctx: StaffSession, client_id: uuid.UUID) -> ClientDetail:
    """The client with their last 50 visits, newest first, at every branch."""
    session, _ = ctx
    client = _get(session, client_id)
    rows = session.execute(
        select(
            BookingItem, Booking, Branch.name, Service.name, ServiceVariant.label, Therapist.name
        )
        .join(Booking, Booking.id == BookingItem.booking_id)
        .join(Branch, Branch.id == Booking.branch_id)
        .join(ServiceVariant, ServiceVariant.id == BookingItem.service_variant_id)
        .join(Service, Service.id == ServiceVariant.service_id)
        .join(Therapist, Therapist.id == BookingItem.therapist_id)
        .where(
            or_(
                BookingItem.guest_client_id == client.id,
                (Booking.client_id == client.id) & BookingItem.guest_client_id.is_(None),
            )
        )
        .order_by(BookingItem.starts_at.desc())
        .limit(50)
    )
    visits: dict[uuid.UUID, VisitOut] = {}
    for item, booking, branch, service, label, therapist in rows:
        # A couples booking made by this client is one visit at the booking's full price.
        if booking.id in visits:
            visits[booking.id].price += item.price
            continue
        visits[booking.id] = VisitOut(
            booking_id=booking.id,
            branch_name=branch,
            starts_at=item.starts_at,
            service_name=service,
            variant_label=label,
            therapist_name=therapist,
            status=item.status,
            price=item.price,
        )
    return ClientDetail(
        **ClientOut.model_validate(client).model_dump(), visits=list(visits.values())
    )


@router.put("/clients/{client_id}", response_model=ClientOut)
def update_client(ctx: StaffSession, client_id: uuid.UUID, body: ClientIn) -> Client:
    session, _ = ctx
    client = _get(session, client_id)
    data = _clean(body)
    _check_duplicate(session, data["mobile"], own_id=client.id)
    for k, v in data.items():
        setattr(client, k, v)
    session.flush()
    return client
