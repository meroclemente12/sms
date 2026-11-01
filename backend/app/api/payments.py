"""Payment methods (Setup), payments the desk logs, and the accountant's check.

Who can do what:
  * every signed-in staff member can read a branch's payment methods;
  * a manager of the branch (or an admin) adds and edits them;
  * the front desk and managers log and correct payments on bookings;
  * accountants, managers and admins see the payments list and verify or flag
    cashless payments. Nobody but an admin checks a payment they logged themselves.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.api.deps import (
    CheckerSession,
    DeskSession,
    ManagerSession,
    Staff,
    StaffSession,
    ensure_branch,
)
from app.models import (
    Booking,
    BookingItem,
    Branch,
    Client,
    Payment,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
    Service,
    ServiceVariant,
    User,
    UserRole,
)
from app.payments import (
    PaymentError,
    PaymentLine,
    check_payment,
    correct_payment,
    record_payment,
)

router = APIRouter(tags=["payments"])


# --- Payment methods (Setup) -----------------------------------------------------------


class MethodIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: PaymentKind = PaymentKind.cashless
    needs_reference: bool = True
    sort_order: int = 0
    active: bool = True


class MethodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    kind: str
    needs_reference: bool
    sort_order: int
    active: bool


def _save_method(session: Session, method: PaymentMethod, body: MethodIn) -> PaymentMethod:
    method.name = body.name.strip()
    method.kind = body.kind
    # Cash never has a reference number.
    method.needs_reference = body.needs_reference and body.kind == PaymentKind.cashless
    method.sort_order = body.sort_order
    method.active = body.active
    session.add(method)
    try:
        session.flush()
    except IntegrityError as e:
        raise HTTPException(409, "This branch already has a method with that name") from e
    return method


@router.get("/branches/{branch_id}/payment-methods", response_model=list[MethodOut])
def list_methods(ctx: StaffSession, branch_id: uuid.UUID) -> list[PaymentMethod]:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    return list(
        session.scalars(
            select(PaymentMethod)
            .where(PaymentMethod.branch_id == branch_id)
            .order_by(PaymentMethod.sort_order, PaymentMethod.name)
        )
    )


@router.post("/branches/{branch_id}/payment-methods", response_model=MethodOut, status_code=201)
def create_method(ctx: ManagerSession, branch_id: uuid.UUID, body: MethodIn) -> PaymentMethod:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    return _save_method(
        session, PaymentMethod(tenant_id=staff.tenant_id, branch_id=branch_id), body
    )


@router.put("/payment-methods/{method_id}", response_model=MethodOut)
def update_method(ctx: ManagerSession, method_id: uuid.UUID, body: MethodIn) -> PaymentMethod:
    session, staff = ctx
    method = session.get(PaymentMethod, method_id)
    if method is None:
        raise HTTPException(404, "Payment method not found")
    ensure_branch(session, staff, method.branch_id)
    return _save_method(session, method, body)


# --- Payments --------------------------------------------------------------------------


class PaymentIn(BaseModel):
    method_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    reference: str | None = Field(default=None, max_length=100)

    def line(self) -> PaymentLine:
        ref = (self.reference or "").strip() or None
        return PaymentLine(method_id=self.method_id, amount=self.amount, reference=ref)


class PaymentOut(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    method_id: uuid.UUID
    method_name: str
    kind: str
    amount: Decimal
    reference: str | None
    status: str
    recorded_by_name: str | None
    recorded_at: datetime
    checked_by_name: str | None
    checked_at: datetime | None
    check_note: str | None


class PaymentRow(PaymentOut):
    """A payment in the accountant's list, with the booking it belongs to."""

    client_name: str
    service_name: str
    booking_starts_at: datetime


def _payment_query():
    recorder, checker = aliased(User), aliased(User)
    return (
        select(Payment, PaymentMethod, recorder.name, checker.name)
        .join(PaymentMethod, PaymentMethod.id == Payment.method_id)
        .outerjoin(recorder, recorder.id == Payment.recorded_by)
        .outerjoin(checker, checker.id == Payment.checked_by)
    )


def _fields(p: Payment, m: PaymentMethod, recorder: str | None, checker: str | None) -> dict:
    return dict(
        id=p.id,
        booking_id=p.booking_id,
        method_id=m.id,
        method_name=m.name,
        kind=m.kind,
        amount=p.amount,
        reference=p.reference,
        status=p.status,
        recorded_by_name=recorder,
        recorded_at=p.recorded_at,
        checked_by_name=checker,
        checked_at=p.checked_at,
        check_note=p.check_note,
    )


def payments_for_booking(session: Session, booking_id: uuid.UUID) -> list[PaymentOut]:
    rows = session.execute(
        _payment_query()
        .where(Payment.booking_id == booking_id)
        .order_by(Payment.recorded_at, Payment.id)
    )
    return [PaymentOut(**_fields(*row)) for row in rows]


def _payment(session: Session, staff: Staff, payment_id: uuid.UUID) -> Payment:
    payment = session.get(Payment, payment_id)
    if payment is None or not staff.can_access_branch(payment.branch_id):
        raise HTTPException(404, "Payment not found")
    return payment


def _one(session: Session, payment_id: uuid.UUID) -> PaymentOut:
    row = session.execute(_payment_query().where(Payment.id == payment_id)).one()
    return PaymentOut(**_fields(*row))


@router.post("/bookings/{booking_id}/payments", response_model=PaymentOut, status_code=201)
def add_payment(ctx: DeskSession, booking_id: uuid.UUID, body: PaymentIn) -> PaymentOut:
    """Log a payment on a booking (for example the second half of a split payment)."""
    session, staff = ctx
    booking = session.get(Booking, booking_id)
    if booking is None or not staff.can_access_branch(booking.branch_id):
        raise HTTPException(404, "Booking not found")
    try:
        payment = record_payment(session, booking, body.line(), user_id=staff.user.id)
    except PaymentError as e:
        raise HTTPException(422, str(e)) from e
    return _one(session, payment.id)


@router.put("/payments/{payment_id}", response_model=PaymentOut)
def update_payment(ctx: DeskSession, payment_id: uuid.UUID, body: PaymentIn) -> PaymentOut:
    """Correct a payment that is not verified yet. It goes back to be checked again."""
    session, staff = ctx
    payment = _payment(session, staff, payment_id)
    try:
        correct_payment(session, payment, body.line(), user_id=staff.user.id)
    except PaymentError as e:
        raise HTTPException(422, str(e)) from e
    return _one(session, payment.id)


# --- The accountant's check ------------------------------------------------------------


class MethodTotal(BaseModel):
    method_id: uuid.UUID
    method_name: str
    kind: str
    count: int
    amount: Decimal


class PaymentList(BaseModel):
    timezone: str
    date_from: date
    date_to: date
    items: list[PaymentRow]
    totals: list[MethodTotal]
    pending: int
    flagged: int
    verified: int


StatusFilter = Literal["pending", "flagged", "verified", "cashless", "cash", "all"]


@router.get("/branches/{branch_id}/payments", response_model=PaymentList)
def list_payments(
    ctx: CheckerSession,
    branch_id: uuid.UUID,
    date_from: Annotated[date | None, Query(description="Logged on or after this day")] = None,
    date_to: Annotated[date | None, Query(description="Logged on or before this day")] = None,
    status: StatusFilter = "cashless",
    method_id: uuid.UUID | None = None,
) -> PaymentList:
    """Payments logged at a branch between two days (branch time), newest first.

    ``totals`` and the counts cover every payment in the date range, whatever the
    status / method filter, so the accountant can match them against the bank and
    GCash statements."""
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    branch = session.get(Branch, branch_id)
    tz = ZoneInfo(branch.timezone)
    today = datetime.now(tz).date()
    date_to = date_to or date_from or today
    date_from = date_from or date_to
    if date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    if (date_to - date_from).days > 92:
        raise HTTPException(422, "Pick at most three months at a time")
    start = datetime.combine(date_from, time(0), tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=tz)

    first_item = (
        select(BookingItem.starts_at, ServiceVariant.service_id)
        .join(ServiceVariant, ServiceVariant.id == BookingItem.service_variant_id)
        .where(BookingItem.booking_id == Booking.id)
        .order_by(BookingItem.starts_at)
        .limit(1)
        .lateral()
    )
    q = (
        _payment_query()
        .add_columns(Client.name, Service.name, first_item.c.starts_at)
        .join(Booking, Booking.id == Payment.booking_id)
        .join(Client, Client.id == Booking.client_id)
        .join(first_item, true())
        .join(Service, Service.id == first_item.c.service_id)
        .where(
            Payment.branch_id == branch_id,
            Payment.recorded_at >= start,
            Payment.recorded_at < end,
        )
        .order_by(Payment.recorded_at.desc(), Payment.id)
    )
    rows = list(session.execute(q))

    totals: dict[uuid.UUID, MethodTotal] = {}
    counts = {s: 0 for s in PaymentStatus}
    items: list[PaymentRow] = []
    for p, m, recorder, checker, client_name, service_name, starts_at in rows:
        t = totals.setdefault(
            m.id,
            MethodTotal(method_id=m.id, method_name=m.name, kind=m.kind, count=0, amount=0),
        )
        t.count += 1
        t.amount += p.amount
        counts[p.status] += 1
        if method_id and m.id != method_id:
            continue
        if status == "cashless" and m.kind != PaymentKind.cashless:
            continue
        if status == "cash" and m.kind != PaymentKind.cash:
            continue
        if status in ("pending", "flagged", "verified") and p.status != status:
            continue
        items.append(
            PaymentRow(
                **_fields(p, m, recorder, checker),
                client_name=client_name,
                service_name=service_name,
                booking_starts_at=starts_at,
            )
        )
    order = {
        mt.id: (mt.sort_order, mt.name)
        for mt in session.scalars(select(PaymentMethod).where(PaymentMethod.branch_id == branch_id))
    }
    return PaymentList(
        timezone=branch.timezone,
        date_from=date_from,
        date_to=date_to,
        items=items,
        totals=sorted(totals.values(), key=lambda t: order.get(t.method_id, (0, ""))),
        pending=counts[PaymentStatus.pending],
        flagged=counts[PaymentStatus.flagged],
        verified=counts[PaymentStatus.verified],
    )


class CheckIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _check(ctx, payment_id: uuid.UUID, to: PaymentStatus, body: CheckIn | None) -> PaymentOut:
    session, staff = ctx
    payment = _payment(session, staff, payment_id)
    note = ((body.note if body else None) or "").strip() or None
    try:
        check_payment(
            session,
            payment,
            to,
            user_id=staff.user.id,
            note=note,
            may_check_own=staff.role == UserRole.admin,
        )
    except PaymentError as e:
        raise HTTPException(422, str(e)) from e
    return _one(session, payment.id)


@router.post("/payments/{payment_id}/verify", response_model=PaymentOut)
def verify_payment(
    ctx: CheckerSession, payment_id: uuid.UUID, body: CheckIn | None = None
) -> PaymentOut:
    """The money arrived (seen in the GCash / bank statement)."""
    return _check(ctx, payment_id, PaymentStatus.verified, body)


@router.post("/payments/{payment_id}/flag", response_model=PaymentOut)
def flag_payment(ctx: CheckerSession, payment_id: uuid.UUID, body: CheckIn) -> PaymentOut:
    """Something is wrong (not found, wrong amount, wrong reference). A note is required."""
    return _check(ctx, payment_id, PaymentStatus.flagged, body)


@router.post("/payments/{payment_id}/reopen", response_model=PaymentOut)
def reopen_payment(ctx: CheckerSession, payment_id: uuid.UUID) -> PaymentOut:
    """Undo a verify or flag: the payment goes back to waiting for a check."""
    return _check(ctx, payment_id, PaymentStatus.pending, None)
