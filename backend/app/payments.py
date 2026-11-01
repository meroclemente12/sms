"""Payments: what the front desk logs at check-out, and the accountant's check of the
cashless ones. Every change is also written to the booking's history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Booking,
    BookingEvent,
    BookingStatus,
    Branch,
    Payment,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
)

# name, kind, needs a reference number
DEFAULT_METHODS: tuple[tuple[str, PaymentKind, bool], ...] = (
    ("Cash", PaymentKind.cash, False),
    ("GCash", PaymentKind.cashless, True),
    ("BPI bank transfer", PaymentKind.cashless, True),
)


class PaymentError(ValueError):
    pass


def add_default_methods(session: Session, branch: Branch) -> None:
    for order, (name, kind, needs_ref) in enumerate(DEFAULT_METHODS):
        session.add(
            PaymentMethod(
                tenant_id=branch.tenant_id,
                branch_id=branch.id,
                name=name,
                kind=kind,
                needs_reference=needs_ref,
                sort_order=order,
            )
        )


@dataclass
class PaymentLine:
    method_id: uuid.UUID
    amount: Decimal
    reference: str | None = None


def _event(session: Session, payment: Payment, event_type: str, user_id, **extra) -> None:
    session.add(
        BookingEvent(
            tenant_id=payment.tenant_id,
            booking_id=payment.booking_id,
            event_type=event_type,
            payload={
                "payment_id": str(payment.id),
                "method": payment.method.name,
                "amount": str(payment.amount),
                **({"reference": payment.reference} if payment.reference else {}),
                **extra,
            },
            user_id=user_id,
        )
    )


def _checked_line(session: Session, branch_id: uuid.UUID, line: PaymentLine) -> PaymentMethod:
    method = session.get(PaymentMethod, line.method_id)
    if method is None or method.branch_id != branch_id:
        raise PaymentError("That payment method is not used at this branch")
    if line.amount <= 0:
        raise PaymentError("The amount must be more than zero")
    if method.kind == PaymentKind.cashless and method.needs_reference and not line.reference:
        raise PaymentError(f"Enter the {method.name} reference number")
    return method


def _initial_status(method: PaymentMethod) -> PaymentStatus:
    if method.kind == PaymentKind.cashless:
        return PaymentStatus.pending
    return PaymentStatus.not_needed


def record_payment(
    session: Session, booking: Booking, line: PaymentLine, *, user_id: uuid.UUID | None
) -> Payment:
    if booking.status in (BookingStatus.cancelled, BookingStatus.no_show):
        raise PaymentError("This booking was cancelled, so it cannot take a payment")
    method = _checked_line(session, booking.branch_id, line)
    if not method.active:
        raise PaymentError(f"{method.name} is no longer in use at this branch")
    payment = Payment(
        id=uuid.uuid4(),
        tenant_id=booking.tenant_id,
        branch_id=booking.branch_id,
        booking_id=booking.id,
        method_id=method.id,
        amount=line.amount,
        reference=(line.reference or None),
        status=_initial_status(method),
        recorded_by=user_id,
    )
    payment.method = method
    session.add(payment)
    session.flush()
    _event(session, payment, "payment.recorded", user_id)
    session.flush()
    return payment


def correct_payment(
    session: Session, payment: Payment, line: PaymentLine, *, user_id: uuid.UUID | None
) -> Payment:
    """The desk fixes a payment (usually one the accountant flagged). It goes back in
    the queue to be checked again. A verified payment is locked."""
    if payment.status == PaymentStatus.verified:
        raise PaymentError("This payment was already verified. Ask the accountant to reopen it.")
    method = _checked_line(session, payment.branch_id, line)
    payment.method_id = method.id
    payment.method = method
    payment.amount = line.amount
    payment.reference = line.reference or None
    payment.status = _initial_status(method)
    payment.checked_by = None
    payment.checked_at = None
    session.flush()
    _event(session, payment, "payment.corrected", user_id)
    session.flush()
    return payment


def check_payment(
    session: Session,
    payment: Payment,
    to: PaymentStatus,
    *,
    user_id: uuid.UUID,
    note: str | None = None,
    may_check_own: bool = False,
) -> Payment:
    """Verify, flag or reopen a cashless payment."""
    if payment.status == PaymentStatus.not_needed:
        raise PaymentError("Cash payments are not checked here")
    if to == PaymentStatus.flagged and not note:
        raise PaymentError("Say what is wrong so the front desk can fix it")
    if to in (PaymentStatus.verified, PaymentStatus.flagged):
        if payment.recorded_by == user_id and not may_check_own:
            raise PaymentError("Someone else must check a payment you logged yourself")
        payment.checked_by = user_id
        payment.checked_at = func.now()
    elif to == PaymentStatus.pending:
        payment.checked_by = None
        payment.checked_at = None
    else:
        raise PaymentError(f"Cannot set a payment to {to}")
    before = payment.status
    payment.status = to
    payment.check_note = note
    session.flush()
    event = {
        PaymentStatus.verified: "payment.verified",
        PaymentStatus.flagged: "payment.flagged",
        PaymentStatus.pending: "payment.reopened",
    }[to]
    _event(session, payment, event, user_id, **({"note": note} if note else {}), **{"from": before})
    session.flush()
    session.refresh(payment)
    return payment


def booking_payments(session: Session, booking_id: uuid.UUID) -> list[Payment]:
    return list(
        session.scalars(
            select(Payment)
            .where(Payment.booking_id == booking_id)
            .order_by(Payment.recorded_at, Payment.id)
        )
    )
