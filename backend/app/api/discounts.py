"""Discount codes (Setup) and putting a code on a booking (front desk).

Who can do what:
  * admins manage every code; a manager manages codes limited to their own branches;
  * the front desk sees the active codes, checks one against a booking and applies it.
A code that was used cannot be deleted, only switched off, so its history stays.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DeskSession, ManagerSession, Staff, StaffSession
from app.discounts import (
    NOT_COUNTED,
    DiscountError,
    apply_code,
    normalise,
    quote,
    remove_code,
)
from app.models import (
    Booking,
    Branch,
    Client,
    DiscountCode,
    DiscountKind,
    DiscountRedemption,
    Service,
    User,
    UserRole,
)

router = APIRouter(tags=["discounts"])


class CodeIn(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    description: str | None = Field(default=None, max_length=200)
    kind: DiscountKind = DiscountKind.percent
    value: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    starts_on: date | None = None
    ends_on: date | None = None
    max_uses: int | None = Field(default=None, gt=0)
    once_per_client: bool = False
    branch_ids: list[uuid.UUID] = []
    service_ids: list[uuid.UUID] = []
    active: bool = True

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = normalise(v)
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Use only letters, numbers, - and _")
        return v

    @model_validator(mode="after")
    def _check(self) -> CodeIn:
        if self.kind == DiscountKind.percent and self.value > 100:
            raise ValueError("A percentage cannot be more than 100")
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("The end date is before the start date")
        return self


class CodeOut(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None
    kind: str
    value: Decimal
    starts_on: date | None
    ends_on: date | None
    max_uses: int | None
    once_per_client: bool
    branch_ids: list[uuid.UUID]
    service_ids: list[uuid.UUID]
    active: bool
    uses: int
    discount_total: Decimal
    created_at: datetime


def _usage(session: Session, code_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, Decimal]]:
    if not code_ids:
        return {}
    rows = session.execute(
        select(
            DiscountRedemption.code_id,
            func.count(),
            func.coalesce(func.sum(DiscountRedemption.amount), 0),
        )
        .join(Booking, Booking.id == DiscountRedemption.booking_id)
        .where(DiscountRedemption.code_id.in_(code_ids), Booking.status.not_in(NOT_COUNTED))
        .group_by(DiscountRedemption.code_id)
    )
    return {cid: (n, Decimal(total)) for cid, n, total in rows}


def _out(c: DiscountCode, usage: dict) -> CodeOut:
    n, total = usage.get(c.id, (0, Decimal(0)))
    return CodeOut(
        id=c.id,
        code=c.code,
        description=c.description,
        kind=c.kind,
        value=c.value,
        starts_on=c.starts_on,
        ends_on=c.ends_on,
        max_uses=c.max_uses,
        once_per_client=c.once_per_client,
        branch_ids=list(c.branch_ids or []),
        service_ids=list(c.service_ids or []),
        active=c.active,
        uses=n,
        discount_total=total,
        created_at=c.created_at,
    )


def _visible(staff: Staff, c: DiscountCode) -> bool:
    if staff.role == UserRole.admin or not c.branch_ids:
        return True
    return any(b in staff.branch_ids for b in c.branch_ids)


def _may_edit(staff: Staff, branch_ids: list[uuid.UUID]) -> bool:
    """Admins edit any code; a manager only codes limited to branches they manage."""
    if staff.role == UserRole.admin:
        return True
    return bool(branch_ids) and all(b in staff.branch_ids for b in branch_ids)


def _code(session: Session, staff: Staff, code_id: uuid.UUID) -> DiscountCode:
    c = session.get(DiscountCode, code_id)
    if c is None or not _visible(staff, c):
        raise HTTPException(404, "Discount code not found")
    return c


def _save(session: Session, staff: Staff, c: DiscountCode, body: CodeIn) -> CodeOut:
    if not _may_edit(staff, body.branch_ids):
        raise HTTPException(
            403, "Managers can only make codes for their own branches. Pick your branch."
        )
    for model, ids, what in (
        (Branch, body.branch_ids, "Branch"),
        (Service, body.service_ids, "Service"),
    ):
        wanted = set(ids)
        found = (
            set(session.scalars(select(model.id).where(model.id.in_(wanted)))) if wanted else set()
        )
        if found != wanted:
            raise HTTPException(422, f"{what} not found")
    for key, value in body.model_dump().items():
        setattr(c, key, value)
    c.branch_ids = list(dict.fromkeys(body.branch_ids))
    c.service_ids = list(dict.fromkeys(body.service_ids))
    session.add(c)
    try:
        session.flush()
    except IntegrityError as e:
        raise HTTPException(409, f"There is already a code {body.code}") from e
    session.refresh(c)
    return _out(c, _usage(session, [c.id]))


@router.get("/discount-codes", response_model=list[CodeOut])
def list_codes(ctx: StaffSession, include_inactive: bool = False) -> list[CodeOut]:
    session, staff = ctx
    q = select(DiscountCode).order_by(DiscountCode.active.desc(), DiscountCode.code)
    if not include_inactive:
        q = q.where(DiscountCode.active)
    codes = [c for c in session.scalars(q) if _visible(staff, c)]
    usage = _usage(session, [c.id for c in codes])
    return [_out(c, usage) for c in codes]


@router.post("/discount-codes", response_model=CodeOut, status_code=201)
def create_code(ctx: ManagerSession, body: CodeIn) -> CodeOut:
    session, staff = ctx
    c = DiscountCode(tenant_id=staff.tenant_id, created_by=staff.user.id)
    return _save(session, staff, c, body)


@router.put("/discount-codes/{code_id}", response_model=CodeOut)
def update_code(ctx: ManagerSession, code_id: uuid.UUID, body: CodeIn) -> CodeOut:
    session, staff = ctx
    c = _code(session, staff, code_id)
    if not _may_edit(staff, list(c.branch_ids or [])):
        raise HTTPException(403, "Only an admin can change a code used at every branch")
    return _save(session, staff, c, body)


@router.delete("/discount-codes/{code_id}", status_code=204)
def delete_code(ctx: ManagerSession, code_id: uuid.UUID) -> Response:
    session, staff = ctx
    c = _code(session, staff, code_id)
    if not _may_edit(staff, list(c.branch_ids or [])):
        raise HTTPException(403, "Only an admin can delete a code used at every branch")
    used = session.scalar(select(func.count()).where(DiscountRedemption.code_id == c.id))
    if used:
        raise HTTPException(409, "This code has been used, so switch it off instead")
    session.delete(c)
    session.flush()
    return Response(status_code=204)


class UseOut(BaseModel):
    booking_id: uuid.UUID
    booking_status: str
    client_name: str
    branch_name: str
    amount: Decimal
    redeemed_by_name: str | None
    redeemed_at: datetime
    counted: bool


@router.get("/discount-codes/{code_id}/uses", response_model=list[UseOut])
def list_uses(ctx: ManagerSession, code_id: uuid.UUID) -> list[UseOut]:
    """Every booking the code was put on, newest first. Cancelled ones don't count."""
    session, staff = ctx
    c = _code(session, staff, code_id)
    rows = session.execute(
        select(DiscountRedemption, Booking.status, Client.name, Branch.name, User.name)
        .join(Booking, Booking.id == DiscountRedemption.booking_id)
        .join(Client, Client.id == DiscountRedemption.client_id)
        .join(Branch, Branch.id == DiscountRedemption.branch_id)
        .outerjoin(User, User.id == DiscountRedemption.redeemed_by)
        .where(DiscountRedemption.code_id == c.id)
        .order_by(DiscountRedemption.redeemed_at.desc())
    )
    return [
        UseOut(
            booking_id=r.booking_id,
            booking_status=status,
            client_name=client,
            branch_name=branch,
            amount=r.amount,
            redeemed_by_name=user,
            redeemed_at=r.redeemed_at,
            counted=status not in NOT_COUNTED,
        )
        for r, status, client, branch, user in rows
        if staff.can_access_branch(r.branch_id)
    ]


# --- On a booking --------------------------------------------------------------------


class ApplyIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


class QuoteOut(BaseModel):
    code: str
    description: str | None
    eligible: Decimal
    amount: Decimal


def _booking(session: Session, staff: Staff, booking_id: uuid.UUID) -> Booking:
    b = session.get(Booking, booking_id)
    if b is None or not staff.can_access_branch(b.branch_id):
        raise HTTPException(404, "Booking not found")
    return b


@router.post("/bookings/{booking_id}/discount/check", response_model=QuoteOut)
def check_code(ctx: DeskSession, booking_id: uuid.UUID, body: ApplyIn) -> QuoteOut:
    """What the code would take off, without applying it."""
    session, staff = ctx
    b = _booking(session, staff, booking_id)
    try:
        q = quote(session, b, body.code)
    except DiscountError as e:
        raise HTTPException(422, str(e)) from e
    return QuoteOut(
        code=q.code.code, description=q.code.description, eligible=q.eligible, amount=q.amount
    )


def booking_out(session: Session, b: Booking):
    from app.api.bookings import _out  # noqa: PLC0415  (avoid an import cycle)

    return _out(session, b, with_events=True)


@router.put("/bookings/{booking_id}/discount")
def put_discount(ctx: DeskSession, booking_id: uuid.UUID, body: ApplyIn):
    """Put a code on the booking (replacing any other). Returns the booking."""
    session, staff = ctx
    b = _booking(session, staff, booking_id)
    try:
        apply_code(session, b, body.code, user_id=staff.user.id)
    except DiscountError as e:
        raise HTTPException(422, str(e)) from e
    return booking_out(session, b)


@router.delete("/bookings/{booking_id}/discount")
def delete_discount(ctx: DeskSession, booking_id: uuid.UUID):
    session, staff = ctx
    b = _booking(session, staff, booking_id)
    try:
        remove_code(session, b, user_id=staff.user.id)
    except DiscountError as e:
        raise HTTPException(422, str(e)) from e
    return booking_out(session, b)
