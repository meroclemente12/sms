"""Accounting API, part 2: recurring transactions, allocations, salary payouts.

Admins do everything here. Accountants can look (lists, due items, history) but not
change anything. Nobody else sees these screens.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminSession, Staff, ensure_branch, require_role
from app.api.funds import EntryOut, _err, _one_entry
from app.fund_plans import (
    allocated_by_account,
    allocation_totals,
    close_allocation,
    due_items,
    next_due,
    pay_out,
    post_occurrence,
    put_aside,
    skip_occurrence,
    spend,
    week_payouts,
)
from app.funds import FundError, balances, entries
from app.models import (
    AccountKind,
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
    User,
    UserRole,
)
from app.pay import branch_today

router = APIRouter(tags=["accounting"])

ViewerSession = Annotated[tuple[Session, Staff], Depends(require_role(UserRole.accountant))]


def _names(session: Session, model, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(session.execute(select(model.id, model.name).where(model.id.in_(ids))).all())


def _branch(session: Session, staff: Staff, branch_id: uuid.UUID) -> Branch:
    ensure_branch(session, staff, branch_id)
    return session.get(Branch, branch_id)


def _branch_account(session: Session, account_id: uuid.UUID, branch_id: uuid.UUID) -> FundAccount:
    a = session.get(FundAccount, account_id)
    if a is None:
        raise HTTPException(404, "Account not found")
    if a.branch_id != branch_id:
        raise HTTPException(422, f"{a.name} belongs to another branch")
    return a


# --- Recurring -------------------------------------------------------------------------


class ScheduleIn(BaseModel):
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    kind: ScheduleKind = ScheduleKind.expense
    account_id: uuid.UUID
    to_account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    frequency: Frequency = Frequency.monthly
    weekday: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=31)
    starts_on: date | None = None
    ends_on: date | None = None
    description: str | None = Field(default=None, max_length=500)
    active: bool = True


class ScheduleOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    name: str
    kind: str
    account_id: uuid.UUID
    account_name: str
    to_account_id: uuid.UUID | None
    to_account_name: str | None
    category_id: uuid.UUID | None
    category_name: str | None
    amount: Decimal
    frequency: str
    weekday: int
    day_of_month: int
    starts_on: date
    ends_on: date | None
    description: str | None
    active: bool
    next_due: date | None


def _schedules_out(session: Session, rows: list[FundSchedule]) -> list[ScheduleOut]:
    accounts = _names(
        session, FundAccount, [r.account_id for r in rows] + [r.to_account_id for r in rows]
    )
    cats = _names(session, FundCategory, [r.category_id for r in rows])
    branches = {b.id: b for b in session.scalars(select(Branch))}
    return [
        ScheduleOut(
            id=r.id,
            branch_id=r.branch_id,
            branch_name=branches[r.branch_id].name,
            name=r.name,
            kind=r.kind,
            account_id=r.account_id,
            account_name=accounts.get(r.account_id, ""),
            to_account_id=r.to_account_id,
            to_account_name=accounts.get(r.to_account_id) if r.to_account_id else None,
            category_id=r.category_id,
            category_name=cats.get(r.category_id) if r.category_id else None,
            amount=r.amount,
            frequency=r.frequency,
            weekday=r.weekday,
            day_of_month=r.day_of_month,
            starts_on=r.starts_on,
            ends_on=r.ends_on,
            description=r.description,
            active=r.active,
            next_due=next_due(session, r, branch_today(branches[r.branch_id])),
        )
        for r in rows
    ]


def _schedules(session: Session, staff: Staff, branch_id: uuid.UUID | None) -> list[FundSchedule]:
    q = select(FundSchedule).order_by(FundSchedule.name)
    if branch_id:
        ensure_branch(session, staff, branch_id)
        q = q.where(FundSchedule.branch_id == branch_id)
    return list(session.scalars(q))


@router.get("/fund-schedules", response_model=list[ScheduleOut])
def list_schedules(ctx: ViewerSession, branch_id: uuid.UUID | None = None) -> list[ScheduleOut]:
    session, staff = ctx
    return _schedules_out(session, _schedules(session, staff, branch_id))


def _save_schedule(
    session: Session, staff: Staff, s: FundSchedule, body: ScheduleIn
) -> ScheduleOut:
    branch = _branch(session, staff, body.branch_id)
    if s.branch_id and s.branch_id != body.branch_id:
        raise HTTPException(422, "A recurring entry cannot move to another branch")
    _branch_account(session, body.account_id, branch.id)
    if body.kind == ScheduleKind.transfer:
        if not body.to_account_id or body.to_account_id == body.account_id:
            raise HTTPException(422, "Pick the account the money goes to")
        _branch_account(session, body.to_account_id, branch.id)
        category_id = None
    else:
        cat = session.get(FundCategory, body.category_id) if body.category_id else None
        if cat is None:
            raise HTTPException(422, "Pick a category")
        want = (
            FundDirection.money_in if body.kind == ScheduleKind.income else FundDirection.money_out
        )
        if cat.direction != want:
            raise HTTPException(422, f"{cat.name} is not a {body.kind} category")
        category_id = cat.id
    starts = body.starts_on or s.starts_on or branch_today(branch)
    if body.ends_on and body.ends_on < starts:
        raise HTTPException(422, "The end date is before the start date")
    s.branch_id = branch.id
    s.name = body.name.strip()
    s.kind = body.kind
    s.account_id = body.account_id
    s.to_account_id = body.to_account_id if body.kind == ScheduleKind.transfer else None
    s.category_id = category_id
    s.amount = body.amount
    s.frequency = body.frequency
    s.weekday = body.weekday
    s.day_of_month = body.day_of_month
    s.starts_on = starts
    s.ends_on = body.ends_on
    s.description = (body.description or "").strip() or None
    s.active = body.active
    session.add(s)
    session.flush()
    return _schedules_out(session, [s])[0]


@router.post("/fund-schedules", response_model=ScheduleOut, status_code=201)
def create_schedule(ctx: AdminSession, body: ScheduleIn) -> ScheduleOut:
    session, staff = ctx
    return _save_schedule(session, staff, FundSchedule(tenant_id=staff.tenant_id, skipped=[]), body)


@router.put("/fund-schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(ctx: AdminSession, schedule_id: uuid.UUID, body: ScheduleIn) -> ScheduleOut:
    session, staff = ctx
    return _save_schedule(session, staff, _schedule(session, staff, schedule_id), body)


def _schedule(session: Session, staff: Staff, schedule_id: uuid.UUID) -> FundSchedule:
    s = session.get(FundSchedule, schedule_id)
    if s is None or not staff.can_access_branch(s.branch_id):
        raise HTTPException(404, "Recurring entry not found")
    return s


class DueOut(BaseModel):
    schedule: ScheduleOut
    due_date: date
    overdue: bool


@router.get("/fund-due", response_model=list[DueOut])
def list_due(
    ctx: ViewerSession,
    branch_id: uuid.UUID | None = None,
    days_ahead: Annotated[int, Query(ge=0, le=60)] = 7,
) -> list[DueOut]:
    """Recurring entries to post: overdue ones, today's, and the next few days'."""
    session, staff = ctx
    rows = _schedules(session, staff, branch_id)
    by_branch: dict[uuid.UUID, list[FundSchedule]] = {}
    for r in rows:
        by_branch.setdefault(r.branch_id, []).append(r)
    out_items = []
    for bid, schedules in by_branch.items():
        today = branch_today(session.get(Branch, bid))
        for d in due_items(session, schedules, today + timedelta(days=days_ahead)):
            out_items.append((d, today))
    outs = {
        s.id: s
        for s in _schedules_out(
            session, list({d.schedule.id: d.schedule for d, _ in out_items}.values())
        )
    }
    result = [
        DueOut(schedule=outs[d.schedule.id], due_date=d.due_date, overdue=d.due_date < today)
        for d, today in out_items
    ]
    result.sort(key=lambda x: (x.due_date, x.schedule.name.lower()))
    return result


class PostIn(BaseModel):
    due_date: date
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    # The day it was actually paid; defaults to today (business day).
    business_date: date | None = None
    reference: str | None = Field(default=None, max_length=100)


@router.post("/fund-schedules/{schedule_id}/post", response_model=list[EntryOut], status_code=201)
def post_schedule(ctx: AdminSession, schedule_id: uuid.UUID, body: PostIn) -> list[EntryOut]:
    session, staff = ctx
    s = _schedule(session, staff, schedule_id)
    day = body.business_date or branch_today(session.get(Branch, s.branch_id))
    try:
        with session.begin_nested():
            rows = post_occurrence(
                session,
                s,
                due_date=body.due_date,
                amount=body.amount or s.amount,
                day=day,
                reference=(body.reference or "").strip() or None,
                user_id=staff.user.id,
            )
    except FundError as e:
        raise _err(e) from e
    except IntegrityError as e:
        raise HTTPException(409, "This one was just posted by someone else") from e
    return [_one_entry(session, staff, r) for r in rows]


class SkipIn(BaseModel):
    due_date: date


@router.post("/fund-schedules/{schedule_id}/skip", response_model=ScheduleOut)
def skip_schedule(ctx: AdminSession, schedule_id: uuid.UUID, body: SkipIn) -> ScheduleOut:
    session, staff = ctx
    s = _schedule(session, staff, schedule_id)
    try:
        skip_occurrence(session, s, body.due_date)
    except FundError as e:
        raise _err(e) from e
    return _schedules_out(session, [s])[0]


# --- Allocations -----------------------------------------------------------------------


class AllocationIn(BaseModel):
    account_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    target_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    target_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    # Money to set aside right away.
    initial_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)


class AllocationOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    account_id: uuid.UUID
    account_name: str
    name: str
    target_amount: Decimal | None
    target_date: date | None
    notes: str | None
    closed: bool
    put_in: Decimal
    used: Decimal
    balance: Decimal


def _allocations_out(session: Session, rows: list[FundAllocation]) -> list[AllocationOut]:
    totals = allocation_totals(session, [r.id for r in rows])
    accounts = _names(session, FundAccount, [r.account_id for r in rows])
    branches = _names(session, Branch, [r.branch_id for r in rows])
    return [
        AllocationOut(
            id=r.id,
            branch_id=r.branch_id,
            branch_name=branches.get(r.branch_id, ""),
            account_id=r.account_id,
            account_name=accounts.get(r.account_id, ""),
            name=r.name,
            target_amount=r.target_amount,
            target_date=r.target_date,
            notes=r.notes,
            closed=r.closed,
            put_in=totals[r.id].put_in,
            used=totals[r.id].used,
            balance=totals[r.id].balance,
        )
        for r in rows
    ]


def _allocation(session: Session, staff: Staff, allocation_id: uuid.UUID) -> FundAllocation:
    a = session.get(FundAllocation, allocation_id)
    if a is None or not staff.can_access_branch(a.branch_id):
        raise HTTPException(404, "Allocation not found")
    return a


@router.get("/fund-allocations", response_model=list[AllocationOut])
def list_allocations(
    ctx: ViewerSession, branch_id: uuid.UUID | None = None, include_closed: bool = False
) -> list[AllocationOut]:
    session, staff = ctx
    q = select(FundAllocation).order_by(FundAllocation.closed, FundAllocation.name)
    if branch_id:
        ensure_branch(session, staff, branch_id)
        q = q.where(FundAllocation.branch_id == branch_id)
    if not include_closed:
        q = q.where(FundAllocation.closed.is_(False))
    return _allocations_out(session, list(session.scalars(q)))


@router.post("/fund-allocations", response_model=AllocationOut, status_code=201)
def create_allocation(ctx: AdminSession, body: AllocationIn) -> AllocationOut:
    session, staff = ctx
    account = session.get(FundAccount, body.account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    if account.kind == AccountKind.petty_cash:
        raise HTTPException(422, "Set money aside in a bank or cash account, not petty cash")
    a = FundAllocation(
        tenant_id=staff.tenant_id,
        branch_id=account.branch_id,
        account_id=account.id,
        name=body.name.strip(),
        target_amount=body.target_amount,
        target_date=body.target_date,
        notes=(body.notes or "").strip() or None,
        closed=False,
    )
    session.add(a)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as e:
        raise HTTPException(409, "This account already has an allocation with that name") from e
    if body.initial_amount:
        try:
            put_aside(
                session,
                a,
                amount=body.initial_amount,
                day=branch_today(session.get(Branch, a.branch_id)),
                note="Set aside when created",
                user_id=staff.user.id,
            )
        except FundError as e:
            raise _err(e) from e
    return _allocations_out(session, [a])[0]


class AllocationEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    target_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


@router.put("/fund-allocations/{allocation_id}", response_model=AllocationOut)
def update_allocation(
    ctx: AdminSession, allocation_id: uuid.UUID, body: AllocationEdit
) -> AllocationOut:
    session, staff = ctx
    a = _allocation(session, staff, allocation_id)
    a.name = body.name.strip()
    a.target_amount = body.target_amount
    a.target_date = body.target_date
    a.notes = (body.notes or "").strip() or None
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as e:
        raise HTTPException(409, "This account already has an allocation with that name") from e
    return _allocations_out(session, [a])[0]


class MoveIn(BaseModel):
    # Positive: set more aside. Negative: release it back to the account.
    amount: Decimal = Field(max_digits=12, decimal_places=2)
    business_date: date | None = None
    note: str | None = Field(default=None, max_length=500)


@router.post("/fund-allocations/{allocation_id}/moves", response_model=AllocationOut)
def move_allocation(ctx: AdminSession, allocation_id: uuid.UUID, body: MoveIn) -> AllocationOut:
    session, staff = ctx
    a = _allocation(session, staff, allocation_id)
    day = body.business_date or branch_today(session.get(Branch, a.branch_id))
    try:
        put_aside(
            session,
            a,
            amount=body.amount,
            day=day,
            note=(body.note or "").strip() or None,
            user_id=staff.user.id,
        )
    except FundError as e:
        raise _err(e) from e
    return _allocations_out(session, [a])[0]


class SpendIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    category_id: uuid.UUID
    business_date: date | None = None
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=100)


@router.post("/fund-allocations/{allocation_id}/spend", response_model=EntryOut, status_code=201)
def spend_allocation(ctx: AdminSession, allocation_id: uuid.UUID, body: SpendIn) -> EntryOut:
    session, staff = ctx
    a = _allocation(session, staff, allocation_id)
    category = session.get(FundCategory, body.category_id)
    if category is None:
        raise HTTPException(404, "Category not found")
    day = body.business_date or branch_today(session.get(Branch, a.branch_id))
    try:
        t = spend(
            session,
            a,
            amount=body.amount,
            day=day,
            category=category,
            description=(body.description or "").strip() or None,
            reference=(body.reference or "").strip() or None,
            user_id=staff.user.id,
        )
    except FundError as e:
        raise _err(e) from e
    return _one_entry(session, staff, t)


@router.post("/fund-allocations/{allocation_id}/close", response_model=AllocationOut)
def close(ctx: AdminSession, allocation_id: uuid.UUID) -> AllocationOut:
    session, staff = ctx
    a = _allocation(session, staff, allocation_id)
    if a.closed:
        raise HTTPException(422, "Already closed")
    close_allocation(
        session, a, day=branch_today(session.get(Branch, a.branch_id)), user_id=staff.user.id
    )
    return _allocations_out(session, [a])[0]


class HistoryLine(BaseModel):
    kind: str  # "set_aside", "released" or "spent"
    business_date: date
    at: datetime
    amount: Decimal
    note: str | None
    by: str | None
    voided: bool = False
    transaction_id: uuid.UUID | None = None


@router.get("/fund-allocations/{allocation_id}/history", response_model=list[HistoryLine])
def allocation_history(ctx: ViewerSession, allocation_id: uuid.UUID) -> list[HistoryLine]:
    """Everything put in, released and spent, newest first."""
    session, staff = ctx
    a = _allocation(session, staff, allocation_id)
    moves = list(
        session.scalars(select(FundAllocationMove).where(FundAllocationMove.allocation_id == a.id))
    )
    spent = list(
        session.scalars(
            select(FundTransaction).where(
                FundTransaction.allocation_id == a.id,
                FundTransaction.direction == FundDirection.money_out,
            )
        )
    )
    users = _names(session, User, [m.recorded_by for m in moves] + [t.recorded_by for t in spent])
    lines = [
        HistoryLine(
            kind="set_aside" if m.amount > 0 else "released",
            business_date=m.business_date,
            at=m.recorded_at,
            amount=abs(m.amount),
            note=m.note,
            by=users.get(m.recorded_by),
        )
        for m in moves
    ] + [
        HistoryLine(
            kind="spent",
            business_date=t.business_date,
            at=t.recorded_at,
            amount=t.amount,
            note=t.description,
            by=users.get(t.recorded_by),
            voided=t.voided_at is not None,
            transaction_id=t.id,
        )
        for t in spent
    ]
    lines.sort(key=lambda x: (x.business_date, x.at), reverse=True)
    return lines


# --- Salary payouts --------------------------------------------------------------------


class PayoutLine(BaseModel):
    therapist_id: uuid.UUID
    name: str
    amount_due: Decimal
    paid: bool
    paid_amount: Decimal | None
    paid_on: date | None
    paid_from: str | None
    transaction_id: uuid.UUID | None


class PayoutList(BaseModel):
    week_start: date
    items: list[PayoutLine]
    to_pay: Decimal


def _payout_list(session: Session, branch: Branch, week: date) -> PayoutList:
    start, payouts = week_payouts(session, branch, week)
    accounts = _names(
        session, FundAccount, [p.transaction.account_id for p in payouts if p.transaction]
    )
    items = [
        PayoutLine(
            therapist_id=p.therapist_id,
            name=p.name,
            amount_due=p.amount_due,
            paid=p.transaction is not None,
            paid_amount=p.transaction.amount if p.transaction else None,
            paid_on=p.transaction.business_date if p.transaction else None,
            paid_from=accounts.get(p.transaction.account_id) if p.transaction else None,
            transaction_id=p.transaction.id if p.transaction else None,
        )
        for p in payouts
    ]
    to_pay = sum((i.amount_due for i in items if not i.paid and i.amount_due > 0), Decimal(0))
    return PayoutList(week_start=start, items=items, to_pay=to_pay)


@router.get("/branches/{branch_id}/pay/payouts", response_model=PayoutList)
def get_payouts(
    ctx: ViewerSession, branch_id: uuid.UUID, week: Annotated[date, Query()]
) -> PayoutList:
    session, staff = ctx
    return _payout_list(session, _branch(session, staff, branch_id), week)


class PayoutIn(BaseModel):
    week: date
    account_id: uuid.UUID
    business_date: date | None = None
    # Empty: everyone not paid yet for the week.
    therapist_ids: list[uuid.UUID] | None = None


@router.post("/branches/{branch_id}/pay/payouts", response_model=PayoutList, status_code=201)
def post_payouts(ctx: AdminSession, branch_id: uuid.UUID, body: PayoutIn) -> PayoutList:
    session, staff = ctx
    branch = _branch(session, staff, branch_id)
    account = _branch_account(session, body.account_id, branch.id)
    try:
        with session.begin_nested():
            pay_out(
                session,
                branch,
                body.week,
                account=account,
                day=body.business_date or branch_today(branch),
                therapist_ids=body.therapist_ids,
                user_id=staff.user.id,
            )
    except FundError as e:
        raise _err(e) from e
    except IntegrityError as e:
        raise HTTPException(409, "Someone else just paid this week") from e
    return _payout_list(session, branch, body.week)


# --- Dashboard -------------------------------------------------------------------------


class DayMoney(BaseModel):
    date: date
    money_in: Decimal
    money_out: Decimal


class CategoryMoney(BaseModel):
    name: str
    amount: Decimal


class AccountMoney(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    branch_name: str
    balance: Decimal
    allocated: Decimal
    available: Decimal


class Dashboard(BaseModel):
    date_from: date
    date_to: date
    # Now
    total_balance: Decimal
    total_available: Decimal
    total_allocated: Decimal
    unverified: Decimal
    accounts: list[AccountMoney]
    # In the period (transfers between accounts are not counted)
    sales: Decimal
    other_income: Decimal
    expenses: Decimal
    net: Decimal
    days: list[DayMoney]
    expenses_by_category: list[CategoryMoney]
    # Recurring
    overdue_count: int
    overdue_amount: Decimal
    due_next_7_count: int
    due_next_7_amount: Decimal
    allocations: list[AllocationOut]


@router.get("/fund-dashboard", response_model=Dashboard)
def dashboard(
    ctx: ViewerSession,
    branch_id: uuid.UUID | None = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Dashboard:
    """Money now and over a period (default: this month so far)."""
    session, staff = ctx
    branches = {b.id: b for b in session.scalars(select(Branch).order_by(Branch.name))}
    if branch_id:
        ensure_branch(session, staff, branch_id)
    visible = [b for b in branches.values() if staff.can_access_branch(b.id)]
    ids = {branch_id} if branch_id else {b.id for b in visible}
    ref = branches[branch_id] if branch_id else (visible[0] if visible else None)
    today = branch_today(ref) if ref else date.today()
    date_to = date_to or today
    date_from = date_from or date_to.replace(day=1)
    if date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    if (date_to - date_from).days > 370:
        raise HTTPException(422, "Pick at most a year at a time")

    accounts = [
        a
        for a in session.scalars(select(FundAccount).order_by(FundAccount.sort_order))
        if a.branch_id in ids
    ]
    bal = balances(session, accounts)
    held = allocated_by_account(session, [a.id for a in accounts])
    account_rows = [
        AccountMoney(
            id=a.id,
            name=a.name,
            kind=a.kind,
            branch_name=branches[a.branch_id].name,
            balance=bal[a.id].balance,
            allocated=held[a.id],
            available=bal[a.id].balance - held[a.id],
        )
        for a in accounts
        if a.active or bal[a.id].balance != 0
    ]

    zero = Decimal(0)
    per_day = {
        date_from + timedelta(days=i): [zero, zero] for i in range((date_to - date_from).days + 1)
    }
    sales = other = spent = zero
    by_cat: dict[uuid.UUID | None, Decimal] = {}
    for e in entries(session, accounts, date_from, date_to):
        if e.voided_at is not None or e.kind == "transfer":
            continue
        slot = per_day.setdefault(e.business_date, [zero, zero])
        if e.direction == FundDirection.money_in:
            slot[0] += e.amount
            if e.kind == "payment":
                sales += e.amount
            else:
                other += e.amount
        else:
            slot[1] += e.amount
            spent += e.amount
            by_cat[e.category_id] = by_cat.get(e.category_id, zero) + e.amount
    cat_names = _names(session, FundCategory, list(by_cat))

    schedules = [s for s in session.scalars(select(FundSchedule)) if s.branch_id in ids]
    overdue = due_items(session, schedules, today - timedelta(days=1))
    upcoming = [
        d for d in due_items(session, schedules, today + timedelta(days=7)) if d.due_date >= today
    ]
    allocs = [
        a
        for a in session.scalars(
            select(FundAllocation)
            .where(FundAllocation.closed.is_(False))
            .order_by(FundAllocation.target_date.nulls_last(), FundAllocation.name)
        )
        if a.branch_id in ids
    ]
    return Dashboard(
        date_from=date_from,
        date_to=date_to,
        total_balance=sum((r.balance for r in account_rows), zero),
        total_available=sum((r.available for r in account_rows), zero),
        total_allocated=sum((r.allocated for r in account_rows), zero),
        unverified=sum((bal[a.id].unverified for a in accounts), zero),
        accounts=account_rows,
        sales=sales,
        other_income=other,
        expenses=spent,
        net=sales + other - spent,
        days=[DayMoney(date=d, money_in=v[0], money_out=v[1]) for d, v in sorted(per_day.items())],
        expenses_by_category=sorted(
            (
                CategoryMoney(
                    name=cat_names.get(k, "Uncategorised") if k else "Uncategorised", amount=v
                )
                for k, v in by_cat.items()
            ),
            key=lambda c: c.amount,
            reverse=True,
        ),
        overdue_count=len(overdue),
        overdue_amount=sum((d.schedule.amount for d in overdue), zero),
        due_next_7_count=len(upcoming),
        due_next_7_amount=sum((d.schedule.amount for d in upcoming), zero),
        allocations=_allocations_out(session, allocs),
    )
