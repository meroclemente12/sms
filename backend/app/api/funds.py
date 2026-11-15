"""Accounting API: fund accounts, categories, the ledger, petty cash and transfers.

Who can do what:
  * admins set up accounts and categories;
  * admins see every account and entry, record any money in or out, move money
    between accounts, and void mistakes;
  * accountants see every account and entry, but record nothing;
  * the front desk and managers see only the petty cash of their branches and log
    expenses out of it (in categories marked for the desk), dated today. They can
    void their own entry on the same day.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminSession, Staff, StaffSession
from app.funds import (
    BRANCH_ONLY_KINDS,
    Entry,
    FundError,
    balances,
    ensure_categories,
    entries,
    record,
    sync_methods,
    transfer,
    void,
)
from app.models import (
    AccountKind,
    Branch,
    FundAccount,
    FundCategory,
    FundDirection,
    FundTransaction,
    User,
    UserRole,
)
from app.pay import branch_today

router = APIRouter(tags=["accounting"])


def _sees_all(staff: Staff) -> bool:
    return staff.role in (UserRole.admin, UserRole.accountant)


def _visible(staff: Staff, a: FundAccount) -> bool:
    if _sees_all(staff):
        return True
    return a.kind == AccountKind.petty_cash and a.branch_id in staff.branch_ids


def _account(session: Session, staff: Staff, account_id: uuid.UUID) -> FundAccount:
    a = session.get(FundAccount, account_id)
    if a is None or not _visible(staff, a):
        raise HTTPException(404, "Account not found")
    return a


def _err(e: FundError) -> HTTPException:
    return HTTPException(422, str(e))


# --- Accounts --------------------------------------------------------------------------


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: AccountKind = AccountKind.bank
    # Every account belongs to one branch.
    branch_id: uuid.UUID
    account_number: str | None = Field(default=None, max_length=100)
    opening_balance: Decimal = Field(default=Decimal(0), max_digits=12, decimal_places=2)
    opening_date: date | None = None
    accepts_payments: bool = False
    needs_reference: bool = True
    sort_order: int = 0
    active: bool = True


class AccountOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    branch_id: uuid.UUID
    branch_name: str
    account_number: str | None
    opening_balance: Decimal
    opening_date: date
    accepts_payments: bool
    needs_reference: bool
    sort_order: int
    active: bool
    balance: Decimal
    unverified: Decimal


def _accounts_out(session: Session, accounts: list[FundAccount]) -> list[AccountOut]:
    names = dict(session.execute(select(Branch.id, Branch.name)).all())
    bal = balances(session, accounts)
    return [
        AccountOut(
            id=a.id,
            name=a.name,
            kind=a.kind,
            branch_id=a.branch_id,
            branch_name=names.get(a.branch_id, ""),
            account_number=a.account_number,
            opening_balance=a.opening_balance,
            opening_date=a.opening_date,
            accepts_payments=a.accepts_payments,
            needs_reference=a.needs_reference,
            sort_order=a.sort_order,
            active=a.active,
            balance=bal[a.id].balance,
            unverified=bal[a.id].unverified,
        )
        for a in accounts
    ]


def _ordered(session: Session, staff: Staff, branch_id: uuid.UUID | None) -> list[FundAccount]:
    q = select(FundAccount)
    if branch_id:
        q = q.where(FundAccount.branch_id == branch_id)
    rows = [a for a in session.scalars(q) if _visible(staff, a)]
    names = dict(session.execute(select(Branch.id, Branch.name)).all())
    # By branch, then the order set in Setup.
    return sorted(
        rows,
        key=lambda a: (
            names.get(a.branch_id, ""),
            a.sort_order,
            a.name.lower(),
        ),
    )


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(ctx: StaffSession, branch_id: uuid.UUID | None = None) -> list[AccountOut]:
    """Accounts with their balance now, optionally only one branch's."""
    session, staff = ctx
    return _accounts_out(session, _ordered(session, staff, branch_id))


def _save_account(session: Session, a: FundAccount, body: AccountIn) -> AccountOut:
    branch = session.get(Branch, body.branch_id)
    if branch is None:
        raise HTTPException(404, "Branch not found")
    if a.branch_id and a.branch_id != body.branch_id:
        raise HTTPException(
            422, "An account cannot move to another branch. Add a new account there instead."
        )
    if body.kind == AccountKind.petty_cash and body.accepts_payments:
        raise HTTPException(422, "Guests cannot pay into petty cash")
    a.name = body.name.strip()
    a.kind = body.kind
    a.branch_id = body.branch_id
    a.account_number = (body.account_number or "").strip() or None
    a.opening_balance = body.opening_balance.quantize(Decimal("0.01"))
    if body.opening_date:
        a.opening_date = body.opening_date
    elif a.opening_date is None:
        a.opening_date = branch_today(branch)
    a.accepts_payments = body.accepts_payments
    a.needs_reference = body.needs_reference and body.kind not in BRANCH_ONLY_KINDS
    a.sort_order = body.sort_order
    a.active = body.active
    session.add(a)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as e:
        raise HTTPException(409, "There is already an account with that name there") from e
    first = session.scalar(
        select(FundTransaction.business_date)
        .where(FundTransaction.account_id == a.id, FundTransaction.voided_at.is_(None))
        .order_by(FundTransaction.business_date)
        .limit(1)
    )
    if first and first < a.opening_date:
        raise HTTPException(
            422, f"This account already has entries from {first:%d %b %Y}; open it on or before"
        )
    try:
        sync_methods(session, a)
    except FundError as e:
        raise _err(e) from e
    return _accounts_out(session, [a])[0]


def _today_any(session: Session) -> date:
    branch = session.scalar(select(Branch).order_by(Branch.created_at).limit(1))
    if branch:
        return branch_today(branch)
    return datetime.now().date()


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(ctx: AdminSession, body: AccountIn) -> AccountOut:
    session, staff = ctx
    return _save_account(session, FundAccount(tenant_id=staff.tenant_id), body)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(ctx: AdminSession, account_id: uuid.UUID, body: AccountIn) -> AccountOut:
    session, staff = ctx
    return _save_account(session, _account(session, staff, account_id), body)


# --- Categories ------------------------------------------------------------------------


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    direction: FundDirection = FundDirection.money_out
    desk_can_use: bool = False
    sort_order: int = 0
    active: bool = True


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    direction: str
    desk_can_use: bool
    sort_order: int
    active: bool


@router.get("/fund-categories", response_model=list[CategoryOut])
def list_categories(ctx: StaffSession) -> list[FundCategory]:
    session, staff = ctx
    ensure_categories(session, staff.tenant_id)
    q = select(FundCategory).order_by(FundCategory.sort_order, FundCategory.name)
    rows = list(session.scalars(q))
    if not _sees_all(staff):
        rows = [c for c in rows if c.desk_can_use and c.active]
    return rows


def _save_category(session: Session, c: FundCategory, body: CategoryIn) -> FundCategory:
    c.name = body.name.strip()
    c.direction = body.direction
    c.desk_can_use = body.desk_can_use and body.direction == FundDirection.money_out
    c.sort_order = body.sort_order
    c.active = body.active
    session.add(c)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as e:
        raise HTTPException(409, "There is already a category with that name") from e
    return c


@router.post("/fund-categories", response_model=CategoryOut, status_code=201)
def create_category(ctx: AdminSession, body: CategoryIn) -> FundCategory:
    session, staff = ctx
    return _save_category(session, FundCategory(tenant_id=staff.tenant_id), body)


@router.put("/fund-categories/{category_id}", response_model=CategoryOut)
def update_category(ctx: AdminSession, category_id: uuid.UUID, body: CategoryIn) -> FundCategory:
    session, _ = ctx
    c = session.get(FundCategory, category_id)
    if c is None:
        raise HTTPException(404, "Category not found")
    return _save_category(session, c, body)


# --- Ledger ----------------------------------------------------------------------------


class EntryOut(BaseModel):
    kind: str
    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    branch_id: uuid.UUID | None
    branch_name: str | None
    business_date: date
    at: datetime
    direction: str
    amount: Decimal
    category_id: uuid.UUID | None
    category_name: str | None
    description: str | None
    reference: str | None
    recorded_by_name: str | None
    other_account_name: str | None
    booking_id: uuid.UUID | None
    client_name: str | None
    payment_status: str | None
    voided: bool
    void_reason: str | None
    can_void: bool


class EntryList(BaseModel):
    date_from: date
    date_to: date
    items: list[EntryOut]
    money_in: Decimal
    money_out: Decimal
    # Only when one account is picked: its balance before and after the range.
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None


def _may_void(staff: Staff, e: Entry, today: dict[uuid.UUID | None, date]) -> bool:
    if e.kind == "payment" or e.voided_at is not None or staff.role == UserRole.accountant:
        return False
    if staff.role == UserRole.admin:
        return True
    return e.recorded_by == staff.user.id and e.business_date == today.get(e.branch_id)


def _entries_out(
    session: Session, staff: Staff, rows: list[Entry], accounts: dict[uuid.UUID, FundAccount]
) -> list[EntryOut]:
    branches = {b.id: b for b in session.scalars(select(Branch))}
    cats = dict(session.execute(select(FundCategory.id, FundCategory.name)).all())
    user_ids = {e.recorded_by for e in rows if e.recorded_by}
    users = (
        dict(session.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all())
        if user_ids
        else {}
    )
    other_ids = {e.other_account_id for e in rows if e.other_account_id} - set(accounts)
    names = {a.id: a.name for a in accounts.values()}
    if other_ids:
        names |= dict(
            session.execute(
                select(FundAccount.id, FundAccount.name).where(FundAccount.id.in_(other_ids))
            ).all()
        )
    today = {bid: branch_today(b) for bid, b in branches.items()}
    return [
        EntryOut(
            kind=e.kind,
            id=e.id,
            account_id=e.account_id,
            account_name=names.get(e.account_id, ""),
            branch_id=e.branch_id,
            branch_name=branches[e.branch_id].name if e.branch_id in branches else None,
            business_date=e.business_date,
            at=e.at,
            direction=e.direction,
            amount=e.amount,
            category_id=e.category_id,
            category_name=cats.get(e.category_id) if e.category_id else None,
            description=e.description,
            reference=e.reference,
            recorded_by_name=users.get(e.recorded_by),
            other_account_name=names.get(e.other_account_id) if e.other_account_id else None,
            booking_id=e.booking_id,
            client_name=e.client_name,
            payment_status=e.payment_status,
            voided=e.voided_at is not None,
            void_reason=e.void_reason,
            can_void=_may_void(staff, e, today),
        )
        for e in rows
    ]


@router.get("/fund-entries", response_model=EntryList)
def list_entries(
    ctx: StaffSession,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    account_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    show: Literal["all", "in", "out", "transfers", "sales"] = "all",
) -> EntryList:
    """Money in and out on business days date_from..date_to, newest first."""
    session, staff = ctx
    if account_id:
        accounts = [_account(session, staff, account_id)]
    else:
        accounts = _ordered(session, staff, None)
    if branch_id and not staff.can_access_branch(branch_id):
        raise HTTPException(403, "You do not have access to this branch")
    ref = session.get(Branch, branch_id) if branch_id else None
    ref = ref or (
        session.get(Branch, accounts[0].branch_id) if accounts and accounts[0].branch_id else None
    )
    today = branch_today(ref) if ref else _today_any(session)
    date_to = date_to or date_from or today
    date_from = date_from or date_to
    if date_to < date_from:
        raise HTTPException(422, "The end date is before the start date")
    if (date_to - date_from).days > 370:
        raise HTTPException(422, "Pick at most a year at a time")

    rows = entries(session, accounts, date_from, date_to, branch_id=branch_id)
    if show == "in":
        rows = [e for e in rows if e.direction == FundDirection.money_in and e.kind != "transfer"]
    elif show == "out":
        rows = [e for e in rows if e.direction == FundDirection.money_out and e.kind != "transfer"]
    elif show == "transfers":
        rows = [e for e in rows if e.kind == "transfer"]
    elif show == "sales":
        rows = [e for e in rows if e.kind == "payment"]
    counted = [e for e in rows if e.voided_at is None]
    # Moves between two of the listed accounts are not money in or out of the business.
    listed = {a.id for a in accounts}

    def internal(e: Entry) -> bool:
        return e.kind == "transfer" and e.other_account_id in listed and len(listed) > 1

    money_in = sum(
        (e.amount for e in counted if e.direction == "in" and not internal(e)), Decimal(0)
    )
    money_out = sum(
        (e.amount for e in counted if e.direction == "out" and not internal(e)), Decimal(0)
    )
    out = EntryList(
        date_from=date_from,
        date_to=date_to,
        items=list(reversed(_entries_out(session, staff, rows, {a.id: a for a in accounts}))),
        money_in=money_in,
        money_out=money_out,
    )
    if account_id and not branch_id and show == "all":
        a = accounts[0]
        out.opening_balance = balances(session, [a], until=date_from - timedelta(days=1))[
            a.id
        ].balance
        out.closing_balance = balances(session, [a], until=date_to)[a.id].balance
    return out


# --- Recording -------------------------------------------------------------------------


class TransactionIn(BaseModel):
    account_id: uuid.UUID
    direction: FundDirection = FundDirection.money_out
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    business_date: date | None = None
    # Which branch it was for; defaults to the account's branch.
    branch_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=100)


def _one_entry(session: Session, staff: Staff, t: FundTransaction) -> EntryOut:
    a = session.get(FundAccount, t.account_id)
    rows = [
        e
        for e in entries(session, [a], t.business_date, t.business_date, include_payments=False)
        if e.id == t.id
    ]
    return _entries_out(session, staff, rows, {a.id: a})[0]


@router.post("/fund-transactions", response_model=EntryOut, status_code=201)
def create_transaction(ctx: StaffSession, body: TransactionIn) -> EntryOut:
    session, staff = ctx
    if staff.role == UserRole.accountant:
        raise HTTPException(403, "Accountants can view the accounts but not record in them")
    account = _account(session, staff, body.account_id)
    branch_id = body.branch_id or account.branch_id
    if branch_id and not staff.can_access_branch(branch_id):
        raise HTTPException(403, "You do not have access to this branch")
    branch = session.get(Branch, branch_id) if branch_id else None
    day = body.business_date or (branch_today(branch) if branch else _today_any(session))
    category = session.get(FundCategory, body.category_id) if body.category_id else None
    if body.category_id and category is None:
        raise HTTPException(404, "Category not found")
    if not _sees_all(staff):
        # The desk: expenses out of their own petty cash, today, in a desk category.
        if body.direction != FundDirection.money_out:
            raise HTTPException(403, "The front desk can only log money taken out")
        if category is None or not category.desk_can_use:
            raise HTTPException(422, "Pick what the money was for")
        if day != branch_today(branch):
            raise HTTPException(422, "The front desk can only log today's expenses")
    elif category is None:
        raise HTTPException(422, "Pick a category")
    description = (body.description or "").strip() or None
    if not _sees_all(staff) and not description:
        raise HTTPException(422, "Add a short note, e.g. what was bought or who was paid")
    try:
        t = record(
            session,
            account=account,
            direction=body.direction,
            amount=body.amount,
            day=day,
            branch_id=branch_id,
            category=category,
            description=description,
            reference=(body.reference or "").strip() or None,
            user_id=staff.user.id,
        )
    except FundError as e:
        raise _err(e) from e
    return _one_entry(session, staff, t)


class TransferIn(BaseModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    business_date: date | None = None
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=100)


@router.post("/fund-transfers", response_model=list[EntryOut], status_code=201)
def create_transfer(ctx: AdminSession, body: TransferIn) -> list[EntryOut]:
    """Move money between two accounts, e.g. refill petty cash from BPI, or deposit
    the cash drawer to the bank."""
    session, staff = ctx
    source = _account(session, staff, body.from_account_id)
    target = _account(session, staff, body.to_account_id)
    ref = source.branch_id or target.branch_id
    day = body.business_date or (
        branch_today(session.get(Branch, ref)) if ref else _today_any(session)
    )
    try:
        out_row, in_row = transfer(
            session,
            source=source,
            target=target,
            amount=body.amount,
            day=day,
            description=(body.description or "").strip() or None,
            reference=(body.reference or "").strip() or None,
            user_id=staff.user.id,
        )
    except FundError as e:
        raise _err(e) from e
    return [_one_entry(session, staff, out_row), _one_entry(session, staff, in_row)]


class VoidIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/fund-transactions/{transaction_id}/void", response_model=list[EntryOut])
def void_transaction(ctx: StaffSession, transaction_id: uuid.UUID, body: VoidIn) -> list[EntryOut]:
    session, staff = ctx
    t = session.get(FundTransaction, transaction_id)
    if t is None:
        raise HTTPException(404, "Entry not found")
    account = _account(session, staff, t.account_id)
    if staff.role == UserRole.accountant:
        raise HTTPException(403, "Accountants can view the accounts but not change them")
    if staff.role != UserRole.admin:
        branch = session.get(Branch, account.branch_id)
        if t.recorded_by != staff.user.id or t.business_date != branch_today(branch):
            raise HTTPException(403, "Only today's entries you logged yourself can be voided")
    try:
        rows = void(session, t, user_id=staff.user.id, reason=body.reason.strip())
    except FundError as e:
        raise _err(e) from e
    return [_one_entry(session, staff, r) for r in rows]
