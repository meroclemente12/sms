"""Accounting part 2: recurring transactions, allocations (money set aside) and paying
therapists their weekly pay out of an account.

Recurring: a schedule never posts by itself. Each occurrence shows as Due on its date
until an admin posts it (with the real amount) or skips it.

Allocations: money set aside stays in its account. What is set aside = money put in
(moves) minus what was paid out of it (non-voided transactions with allocation_id). The
account's available balance is its balance minus everything set aside in it.
"""

from __future__ import annotations

import calendar
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.funds import ZERO, FundError, balances, ensure_categories, record, transfer
from app.models import (
    Branch,
    Frequency,
    FundAccount,
    FundAllocation,
    FundAllocationMove,
    FundCategory,
    FundDirection,
    FundSchedule,
    FundTransaction,
    ScheduleKind,
)
from app.pay import week_pay

# How far back overdue occurrences are listed, at most.
LOOKBACK = timedelta(days=400)


# --- Recurring -------------------------------------------------------------------------


def occurrences(s: FundSchedule, first: date, last: date) -> list[date]:
    """Due dates of a schedule between first and last (both included)."""
    first = max(first, s.starts_on)
    if s.ends_on:
        last = min(last, s.ends_on)
    out: list[date] = []
    if first > last:
        return out
    if s.frequency == Frequency.weekly:
        d = first + timedelta(days=(s.weekday - first.weekday()) % 7)
        while d <= last:
            out.append(d)
            d += timedelta(days=7)
        return out
    y, m = first.year, first.month
    while True:
        d = date(y, m, min(s.day_of_month, calendar.monthrange(y, m)[1]))
        if d > last:
            return out
        if d >= first:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def posted_dates(session: Session, schedule_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[date]]:
    out: dict[uuid.UUID, set[date]] = defaultdict(set)
    if not schedule_ids:
        return out
    for sid, due in session.execute(
        select(FundTransaction.schedule_id, FundTransaction.due_date).where(
            FundTransaction.schedule_id.in_(schedule_ids), FundTransaction.voided_at.is_(None)
        )
    ):
        out[sid].add(due)
    return out


@dataclass
class Due:
    schedule: FundSchedule
    due_date: date


def due_items(session: Session, schedules: list[FundSchedule], until: date) -> list[Due]:
    """Occurrences up to ``until`` that are neither posted nor skipped, oldest first."""
    active = [s for s in schedules if s.active]
    posted = posted_dates(session, [s.id for s in active])
    out = []
    for s in active:
        skipped = set(s.skipped or [])
        for d in occurrences(s, until - LOOKBACK, until):
            if d not in skipped and d not in posted[s.id]:
                out.append(Due(s, d))
    out.sort(key=lambda x: (x.due_date, x.schedule.name.lower()))
    return out


def next_due(session: Session, s: FundSchedule, today: date) -> date | None:
    """The first open occurrence (overdue included), or the next one after today."""
    if not s.active:
        return None
    pending = due_items(session, [s], today)
    if pending:
        return pending[0].due_date
    upcoming = occurrences(s, today + timedelta(days=1), today + timedelta(days=400))
    skipped = set(s.skipped or [])
    return next((d for d in upcoming if d not in skipped), None)


def _check_open(session: Session, s: FundSchedule, due_date: date) -> None:
    if due_date not in occurrences(s, due_date, due_date):
        raise FundError(f"{s.name} is not due on {due_date:%d %b %Y}")
    if due_date in posted_dates(session, [s.id])[s.id]:
        raise FundError(f"{s.name} for {due_date:%d %b %Y} was already posted")


def post_occurrence(
    session: Session,
    s: FundSchedule,
    *,
    due_date: date,
    amount: Decimal,
    day: date,
    reference: str | None,
    user_id: uuid.UUID | None,
) -> list[FundTransaction]:
    _check_open(session, s, due_date)
    if due_date in (s.skipped or []):
        s.skipped = [d for d in s.skipped if d != due_date]
    links = {"schedule_id": s.id, "due_date": due_date}
    account = session.get(FundAccount, s.account_id)
    description = s.description or s.name
    if s.kind == ScheduleKind.transfer:
        target = session.get(FundAccount, s.to_account_id)
        return list(
            transfer(
                session,
                source=account,
                target=target,
                amount=amount,
                day=day,
                description=description,
                reference=reference,
                user_id=user_id,
                links=links,
            )
        )
    direction = FundDirection.money_in if s.kind == ScheduleKind.income else FundDirection.money_out
    category = session.get(FundCategory, s.category_id) if s.category_id else None
    return [
        record(
            session,
            account=account,
            direction=direction,
            amount=amount,
            day=day,
            branch_id=s.branch_id,
            category=category,
            description=description,
            reference=reference,
            user_id=user_id,
            links=links,
        )
    ]


def skip_occurrence(session: Session, s: FundSchedule, due_date: date) -> None:
    _check_open(session, s, due_date)
    if due_date not in (s.skipped or []):
        s.skipped = sorted([*(s.skipped or []), due_date])
    session.flush()


# --- Allocations -----------------------------------------------------------------------


@dataclass
class AllocationTotals:
    put_in: Decimal = ZERO  # net of releases
    used: Decimal = ZERO

    @property
    def balance(self) -> Decimal:
        return self.put_in - self.used


def allocation_totals(
    session: Session, allocation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AllocationTotals]:
    out = {i: AllocationTotals() for i in allocation_ids}
    if not allocation_ids:
        return out
    for aid, total in session.execute(
        select(FundAllocationMove.allocation_id, func.sum(FundAllocationMove.amount))
        .where(FundAllocationMove.allocation_id.in_(allocation_ids))
        .group_by(FundAllocationMove.allocation_id)
    ):
        out[aid].put_in = total
    for aid, total in session.execute(
        select(FundTransaction.allocation_id, func.sum(FundTransaction.amount))
        .where(
            FundTransaction.allocation_id.in_(allocation_ids),
            FundTransaction.voided_at.is_(None),
            FundTransaction.direction == FundDirection.money_out,
        )
        .group_by(FundTransaction.allocation_id)
    ):
        out[aid].used = total
    return out


def allocated_by_account(
    session: Session, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Money set aside (and not used yet) in each account."""
    out = {a: ZERO for a in account_ids}
    if not account_ids:
        return out
    rows = list(
        session.execute(
            select(FundAllocation.id, FundAllocation.account_id).where(
                FundAllocation.account_id.in_(account_ids)
            )
        )
    )
    totals = allocation_totals(session, [r[0] for r in rows])
    for aid, account_id in rows:
        out[account_id] += max(totals[aid].balance, ZERO)
    return out


def available(session: Session, account: FundAccount) -> Decimal:
    bal = balances(session, [account])[account.id].balance
    return bal - allocated_by_account(session, [account.id])[account.id]


def put_aside(
    session: Session,
    allocation: FundAllocation,
    *,
    amount: Decimal,
    day: date,
    note: str | None,
    user_id: uuid.UUID | None,
) -> FundAllocationMove:
    """Set money aside (amount > 0) or release it back to the account (amount < 0)."""
    if allocation.closed:
        raise FundError(f"{allocation.name} is closed")
    if amount == 0:
        raise FundError("The amount cannot be zero")
    account = session.get(FundAccount, allocation.account_id)
    if amount > 0:
        free = available(session, account)
        if amount > free:
            raise FundError(
                f"{account.name} has only ₱{free:,.2f} not yet set aside. "
                "Add money to the account first, or set aside less."
            )
    else:
        left = allocation_totals(session, [allocation.id])[allocation.id].balance
        if -amount > left:
            raise FundError(f"Only ₱{left:,.2f} is set aside in {allocation.name}")
    move = FundAllocationMove(
        tenant_id=allocation.tenant_id,
        allocation_id=allocation.id,
        amount=amount,
        business_date=day,
        note=note,
        recorded_by=user_id,
    )
    session.add(move)
    session.flush()
    return move


def spend(
    session: Session,
    allocation: FundAllocation,
    *,
    amount: Decimal,
    day: date,
    category: FundCategory,
    description: str | None,
    reference: str | None,
    user_id: uuid.UUID | None,
) -> FundTransaction:
    """Pay for something out of the money set aside: an expense from the allocation's
    account that also reduces what is set aside."""
    if allocation.closed:
        raise FundError(f"{allocation.name} is closed")
    left = allocation_totals(session, [allocation.id])[allocation.id].balance
    if amount > left:
        raise FundError(
            f"Only ₱{left:,.2f} is set aside in {allocation.name}. "
            "Set more aside first, or pay the rest as a normal expense."
        )
    return record(
        session,
        account=session.get(FundAccount, allocation.account_id),
        direction=FundDirection.money_out,
        amount=amount,
        day=day,
        branch_id=allocation.branch_id,
        category=category,
        description=description or allocation.name,
        reference=reference,
        user_id=user_id,
        links={"allocation_id": allocation.id},
    )


def close_allocation(
    session: Session, allocation: FundAllocation, *, day: date, user_id: uuid.UUID | None
) -> None:
    """Close it: whatever is still set aside goes back to the account."""
    left = allocation_totals(session, [allocation.id])[allocation.id].balance
    if left > 0:
        put_aside(session, allocation, amount=-left, day=day, note="Closed", user_id=user_id)
    allocation.closed = True
    session.flush()


# --- Salary payouts --------------------------------------------------------------------

SALARY_CATEGORY = "Salaries"


@dataclass
class Payout:
    therapist_id: uuid.UUID
    name: str
    amount_due: Decimal
    transaction: FundTransaction | None


def week_payouts(session: Session, branch: Branch, any_day: date) -> tuple[date, list[Payout]]:
    report = week_pay(session, branch, any_day)
    paid = {
        t.therapist_id: t
        for t in session.scalars(
            select(FundTransaction).where(
                FundTransaction.branch_id == branch.id,
                FundTransaction.pay_week_start == report.week_start,
                FundTransaction.therapist_id.is_not(None),
                FundTransaction.voided_at.is_(None),
            )
        )
    }
    return report.week_start, [
        Payout(p.therapist_id, p.name, p.total, paid.get(p.therapist_id)) for p in report.therapists
    ]


def pay_out(
    session: Session,
    branch: Branch,
    any_day: date,
    *,
    account: FundAccount,
    day: date,
    therapist_ids: list[uuid.UUID] | None,
    user_id: uuid.UUID | None,
) -> list[FundTransaction]:
    """Pay each therapist's weekly total (commission + allowance + bonus) out of one
    account, one entry per therapist. Therapists already paid for the week are left."""
    if account.branch_id != branch.id:
        raise FundError(f"{account.name} belongs to another branch")
    ensure_categories(session, branch.tenant_id)
    category = session.scalar(select(FundCategory).where(FundCategory.name == SALARY_CATEGORY))
    week_start, payouts = week_payouts(session, branch, any_day)
    week_end = week_start + timedelta(days=6)
    label = f"{week_start:%b} {week_start.day}–{week_end:%b} {week_end.day}"
    wanted = set(therapist_ids) if therapist_ids else None
    out = []
    for p in payouts:
        if p.transaction or p.amount_due <= 0:
            continue
        if wanted is not None and p.therapist_id not in wanted:
            continue
        out.append(
            record(
                session,
                account=account,
                direction=FundDirection.money_out,
                amount=p.amount_due,
                day=day,
                branch_id=branch.id,
                category=category if category and category.active else None,
                description=f"Pay: {p.name}, week {label}",
                reference=None,
                user_id=user_id,
                links={"therapist_id": p.therapist_id, "pay_week_start": week_start},
            )
        )
    if not out:
        raise FundError("Nobody is left to pay for this week")
    return out
