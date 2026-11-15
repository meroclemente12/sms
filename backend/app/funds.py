"""Accounting: fund accounts (cash drawer, petty cash, bank, GCash), their balances, and
the ledger of money in and out.

Money moves in three ways:
  * guest payments at check-out, read straight from ``payments`` through the payment
    method's ``account_id`` (never copied, so a corrected payment is always right here);
  * transactions logged by hand: an expense, a bill, other income;
  * transfers between two accounts (petty cash refill, cash deposited to the bank),
    stored as an "out" and an "in" row sharing a ``transfer_id``.

An account counts from its ``opening_date``: its opening balance is what it held at the
start of that business day, so payments and transactions before it are left out.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.business_day import business_date, day_bounds, days_bounds
from app.models import (
    AccountKind,
    Booking,
    Branch,
    Client,
    FundAccount,
    FundCategory,
    FundDirection,
    FundTransaction,
    Payment,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
)

ZERO = Decimal("0.00")

# name, direction, the front desk may use it for petty cash
DEFAULT_CATEGORIES: tuple[tuple[str, str, bool], ...] = (
    ("Laundry", "out", True),
    ("Supplies", "out", True),
    ("Food and drinks", "out", True),
    ("Transport", "out", True),
    ("Repairs and maintenance", "out", False),
    ("Electricity", "out", False),
    ("Water", "out", False),
    ("Internet and phone", "out", False),
    ("Rent", "out", False),
    ("Salaries", "out", False),
    ("Government and taxes", "out", False),
    ("Other expense", "out", True),
    ("Other income", "in", False),
)

# name, kind, guests can pay with it, needs a reference number
DEFAULT_BRANCH_ACCOUNTS: tuple[tuple[str, AccountKind, bool, bool], ...] = (
    ("Cash", AccountKind.cash, True, False),
    ("GCash", AccountKind.ewallet, True, True),
    ("BPI bank transfer", AccountKind.bank, True, True),
    ("Petty cash", AccountKind.petty_cash, False, False),
)

BRANCH_ONLY_KINDS = (AccountKind.cash, AccountKind.petty_cash)


class FundError(ValueError):
    pass


# --- Setup -----------------------------------------------------------------------------


def ensure_categories(session: Session, tenant_id: uuid.UUID) -> None:
    """Give a spa the usual categories the first time it opens accounting."""
    if session.scalar(select(FundCategory.id).where(FundCategory.tenant_id == tenant_id).limit(1)):
        return
    for order, (name, direction, desk) in enumerate(DEFAULT_CATEGORIES):
        session.add(
            FundCategory(
                tenant_id=tenant_id,
                name=name,
                direction=direction,
                desk_can_use=desk,
                sort_order=order,
            )
        )
    session.flush()


def _branches_for(session: Session, account: FundAccount) -> list[uuid.UUID]:
    if account.branch_id:
        return [account.branch_id]
    return list(session.scalars(select(Branch.id).where(Branch.tenant_id == account.tenant_id)))


def sync_methods(session: Session, account: FundAccount) -> None:
    """Keep the account's payment methods in step with it: one per branch where guests
    can pay into it, named like the account. Methods are switched off, never deleted,
    because old payments point at them."""
    in_scope = set(_branches_for(session, account))
    wanted = account.accepts_payments and account.active
    linked = {
        m.branch_id: m
        for m in session.scalars(
            select(PaymentMethod).where(PaymentMethod.account_id == account.id)
        )
    }
    for branch_id, method in linked.items():
        if branch_id not in in_scope:
            method.active = False
    for branch_id in in_scope:
        method = linked.get(branch_id)
        if method is None:
            # An older method with the same name (from before accounts) is adopted.
            method = session.scalar(
                select(PaymentMethod).where(
                    PaymentMethod.branch_id == branch_id,
                    PaymentMethod.name == account.name,
                    PaymentMethod.account_id.is_(None),
                )
            )
        if method is None:
            if not wanted:
                continue
            method = PaymentMethod(tenant_id=account.tenant_id, branch_id=branch_id)
            session.add(method)
        method.account_id = account.id
        method.name = account.name
        method.kind = (
            PaymentKind.cash if account.kind in BRANCH_ONLY_KINDS else PaymentKind.cashless
        )
        method.needs_reference = account.needs_reference and method.kind == PaymentKind.cashless
        method.sort_order = account.sort_order
        method.active = wanted
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as e:
        raise FundError(
            f"A branch already has a different payment method called {account.name}. "
            "Rename one of them."
        ) from e


def add_default_accounts(session: Session, branch: Branch, opening_date: date) -> None:
    """A new branch starts with a cash drawer, GCash, BPI and petty cash, and guests can
    also pay into the spa's shared accounts there."""
    for order, (name, kind, pays, needs_ref) in enumerate(DEFAULT_BRANCH_ACCOUNTS):
        account = FundAccount(
            tenant_id=branch.tenant_id,
            branch_id=branch.id,
            name=name,
            kind=kind,
            opening_balance=ZERO,
            opening_date=opening_date,
            accepts_payments=pays,
            needs_reference=needs_ref,
            sort_order=order if pays else 100,
        )
        session.add(account)
        session.flush()
        sync_methods(session, account)
    for shared in session.scalars(
        select(FundAccount).where(
            FundAccount.tenant_id == branch.tenant_id, FundAccount.branch_id.is_(None)
        )
    ):
        sync_methods(session, shared)


# --- Balances --------------------------------------------------------------------------


@dataclass
class Balance:
    balance: Decimal = ZERO
    # Cashless guest payments the accountant has not verified yet (included above).
    unverified: Decimal = ZERO


def _payment_starts(session: Session, accounts: list[FundAccount]) -> dict[uuid.UUID, dict]:
    """For each account, the moment (per branch) from which its payments count."""
    branches = {b.id: b for b in session.scalars(select(Branch))}
    out: dict[uuid.UUID, dict] = {}
    for a in accounts:
        ids = [a.branch_id] if a.branch_id else list(branches)
        out[a.id] = {
            bid: day_bounds(branches[bid], a.opening_date)[0] for bid in ids if bid in branches
        }
    return out


def balances(
    session: Session, accounts: list[FundAccount], *, until: date | None = None
) -> dict[uuid.UUID, Balance]:
    """Balance of each account, at the end of ``until`` (default: now)."""
    result = {a.id: Balance(balance=a.opening_balance) for a in accounts}
    if not accounts:
        return result
    ids = list(result)
    tx = select(
        FundTransaction.account_id,
        FundTransaction.direction,
        func.sum(FundTransaction.amount),
    ).where(FundTransaction.account_id.in_(ids), FundTransaction.voided_at.is_(None))
    if until is not None:
        tx = tx.where(FundTransaction.business_date <= until)
    for account_id, direction, total in session.execute(
        tx.group_by(FundTransaction.account_id, FundTransaction.direction)
    ):
        sign = 1 if direction == FundDirection.money_in else -1
        result[account_id].balance += sign * total

    starts = _payment_starts(session, accounts)
    branches = {b.id: b for b in session.scalars(select(Branch))}
    rows = session.execute(
        select(
            PaymentMethod.account_id,
            Payment.branch_id,
            Payment.recorded_at,
            Payment.amount,
            Payment.status,
        )
        .join(PaymentMethod, PaymentMethod.id == Payment.method_id)
        .where(PaymentMethod.account_id.in_(ids))
    )
    for account_id, branch_id, recorded_at, amount, status in rows:
        start = starts[account_id].get(branch_id)
        if start is None or recorded_at < start:
            continue
        if until is not None and recorded_at >= day_bounds(branches[branch_id], until)[1]:
            continue
        result[account_id].balance += amount
        if status in (PaymentStatus.pending, PaymentStatus.flagged):
            result[account_id].unverified += amount
    return result


# --- Recording -------------------------------------------------------------------------


def _check_date(account: FundAccount, day: date) -> None:
    if day < account.opening_date:
        raise FundError(
            f"{account.name} starts on {account.opening_date:%d %b %Y}. "
            "Pick a later date, or change its opening date in Setup."
        )


def _usable(account: FundAccount, branch_id: uuid.UUID | None) -> None:
    if not account.active:
        raise FundError(f"{account.name} is no longer in use")
    if account.branch_id and branch_id and account.branch_id != branch_id:
        raise FundError(f"{account.name} belongs to another branch")


def record(
    session: Session,
    *,
    account: FundAccount,
    direction: FundDirection,
    amount: Decimal,
    day: date,
    branch_id: uuid.UUID | None,
    category: FundCategory | None,
    description: str | None,
    reference: str | None,
    user_id: uuid.UUID | None,
) -> FundTransaction:
    if amount <= 0:
        raise FundError("The amount must be more than zero")
    _usable(account, branch_id)
    _check_date(account, day)
    if category is not None:
        if not category.active:
            raise FundError(f"The category {category.name} is no longer in use")
        if category.direction != direction:
            word = "money in" if category.direction == FundDirection.money_in else "money out"
            raise FundError(f"{category.name} is a {word} category")
    t = FundTransaction(
        tenant_id=account.tenant_id,
        branch_id=branch_id or account.branch_id,
        account_id=account.id,
        direction=direction,
        amount=amount,
        business_date=day,
        category_id=category.id if category else None,
        description=description,
        reference=reference,
        recorded_by=user_id,
    )
    session.add(t)
    session.flush()
    return t


def transfer(
    session: Session,
    *,
    source: FundAccount,
    target: FundAccount,
    amount: Decimal,
    day: date,
    description: str | None,
    reference: str | None,
    user_id: uuid.UUID | None,
) -> tuple[FundTransaction, FundTransaction]:
    if source.id == target.id:
        raise FundError("Pick two different accounts")
    if amount <= 0:
        raise FundError("The amount must be more than zero")
    for a in (source, target):
        _usable(a, None)
        _check_date(a, day)
    group = uuid.uuid4()
    rows = []
    for account, direction in ((source, FundDirection.money_out), (target, FundDirection.money_in)):
        t = FundTransaction(
            tenant_id=account.tenant_id,
            branch_id=account.branch_id,
            account_id=account.id,
            direction=direction,
            amount=amount,
            business_date=day,
            description=description,
            reference=reference,
            transfer_id=group,
            recorded_by=user_id,
        )
        session.add(t)
        rows.append(t)
    session.flush()
    return rows[0], rows[1]


def void(session: Session, t: FundTransaction, *, user_id: uuid.UUID, reason: str) -> list:
    """Cancel a mistaken entry (both halves of a transfer). It stays in the history."""
    if t.voided_at is not None:
        raise FundError("This entry was already voided")
    if not reason:
        raise FundError("Say why it is being voided")
    rows = [t]
    if t.transfer_id:
        rows = list(
            session.scalars(
                select(FundTransaction).where(FundTransaction.transfer_id == t.transfer_id)
            )
        )
    for r in rows:
        r.voided_at = func.now()
        r.voided_by = user_id
        r.void_reason = reason
    session.flush()
    for r in rows:
        session.refresh(r)
    return rows


# --- Ledger ----------------------------------------------------------------------------


@dataclass
class Entry:
    """One line of an account's statement: a logged transaction or a guest payment."""

    kind: str  # "transaction", "transfer" or "payment"
    id: uuid.UUID
    account_id: uuid.UUID
    branch_id: uuid.UUID | None
    business_date: date
    at: datetime
    direction: str
    amount: Decimal
    category_id: uuid.UUID | None = None
    description: str | None = None
    reference: str | None = None
    recorded_by: uuid.UUID | None = None
    # transfers: the account on the other side
    other_account_id: uuid.UUID | None = None
    transfer_id: uuid.UUID | None = None
    # payments
    booking_id: uuid.UUID | None = None
    client_name: str | None = None
    payment_status: str | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None
    extra: dict = field(default_factory=dict)


def entries(
    session: Session,
    accounts: list[FundAccount],
    first: date,
    last: date,
    *,
    branch_id: uuid.UUID | None = None,
    include_payments: bool = True,
) -> list[Entry]:
    """Everything that moved money in these accounts on business days first..last,
    oldest first. Voided rows are included (marked) so the history is complete."""
    if not accounts:
        return []
    ids = [a.id for a in accounts]
    q = select(FundTransaction).where(
        FundTransaction.account_id.in_(ids),
        FundTransaction.business_date >= first,
        FundTransaction.business_date <= last,
    )
    if branch_id:
        q = q.where(FundTransaction.branch_id == branch_id)
    txs = list(session.scalars(q))
    transfer_ids = {t.transfer_id for t in txs if t.transfer_id}
    other: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}
    if transfer_ids:
        halves = defaultdict(list)
        for tid, aid in session.execute(
            select(FundTransaction.transfer_id, FundTransaction.account_id).where(
                FundTransaction.transfer_id.in_(transfer_ids)
            )
        ):
            halves[tid].append(aid)
        for tid, aids in halves.items():
            for aid in aids:
                rest = [x for x in aids if x != aid]
                if rest:
                    other[(tid, aid)] = rest[0]
    out = [
        Entry(
            kind="transfer" if t.transfer_id else "transaction",
            id=t.id,
            account_id=t.account_id,
            branch_id=t.branch_id,
            business_date=t.business_date,
            at=t.recorded_at,
            direction=t.direction,
            amount=t.amount,
            category_id=t.category_id,
            description=t.description,
            reference=t.reference,
            recorded_by=t.recorded_by,
            transfer_id=t.transfer_id,
            other_account_id=other.get((t.transfer_id, t.account_id)) if t.transfer_id else None,
            voided_at=t.voided_at,
            void_reason=t.void_reason,
        )
        for t in txs
    ]

    if include_payments:
        branches = {b.id: b for b in session.scalars(select(Branch))}
        starts = _payment_starts(session, accounts)
        pq = (
            select(Payment, PaymentMethod.account_id, Client.name)
            .join(PaymentMethod, PaymentMethod.id == Payment.method_id)
            .join(Booking, Booking.id == Payment.booking_id)
            .join(Client, Client.id == Booking.client_id)
            .where(PaymentMethod.account_id.in_(ids))
        )
        if branch_id:
            pq = pq.where(Payment.branch_id == branch_id)
        for bid in {branch_id} if branch_id else set(branches):
            if bid not in branches:
                continue
            start, end = days_bounds(branches[bid], first, last)
            for p, account_id, client_name in session.execute(
                pq.where(
                    Payment.branch_id == bid,
                    Payment.recorded_at >= start,
                    Payment.recorded_at < end,
                )
            ):
                if p.recorded_at < starts[account_id].get(bid, p.recorded_at):
                    continue
                out.append(
                    Entry(
                        kind="payment",
                        id=p.id,
                        account_id=account_id,
                        branch_id=bid,
                        business_date=business_date(branches[bid], p.recorded_at),
                        at=p.recorded_at,
                        direction=FundDirection.money_in,
                        amount=p.amount,
                        reference=p.reference,
                        recorded_by=p.recorded_by,
                        booking_id=p.booking_id,
                        client_name=client_name,
                        payment_status=p.status,
                    )
                )
    out.sort(key=lambda e: (e.business_date, e.at, str(e.id)))
    return out
