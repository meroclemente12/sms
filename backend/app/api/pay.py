"""Therapist pay: clock in/out from the desk, pay settings, and the weekly pay report.

Who can do what:
  * clock a therapist in or out: front desk, managers and admins of that branch;
  * read pay settings and the weekly report: managers, accountants and admins;
  * change pay settings (spa-wide): admins.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminSession, DeskSession, Staff, ensure_branch, require_role
from app.models import Branch, Therapist, UserRole
from app.pay import ClockError, clock_in, clock_out, settings_for, week_pay

router = APIRouter(tags=["pay"])

PayReaderSession = Annotated[
    tuple[Session, Staff], Depends(require_role(UserRole.manager, UserRole.accountant))
]


# --- Clock in / out ------------------------------------------------------------------


class ClockOut(BaseModel):
    therapist_id: uuid.UUID
    clocked_in: bool
    clock_in_at: datetime | None


def _therapist(session: Session, staff: Staff, therapist_id: uuid.UUID) -> Therapist:
    t = session.get(Therapist, therapist_id)
    if t is None:
        raise HTTPException(404, "Therapist not found")
    ensure_branch(session, staff, t.branch_id)
    return t


@router.post("/therapists/{therapist_id}/clock-in", response_model=ClockOut)
def post_clock_in(ctx: DeskSession, therapist_id: uuid.UUID) -> ClockOut:
    session, staff = ctx
    t = _therapist(session, staff, therapist_id)
    try:
        row = clock_in(session, t, user_id=staff.user.id)
    except ClockError as e:
        raise HTTPException(422, str(e)) from e
    except IntegrityError as e:  # another desk clocked them in at the same moment
        raise HTTPException(409, "This therapist was just clocked in from another desk") from e
    return ClockOut(therapist_id=t.id, clocked_in=True, clock_in_at=row.clock_in_at)


@router.post("/therapists/{therapist_id}/clock-out", response_model=ClockOut)
def post_clock_out(ctx: DeskSession, therapist_id: uuid.UUID) -> ClockOut:
    session, staff = ctx
    t = _therapist(session, staff, therapist_id)
    clock_out(session, t, user_id=staff.user.id)
    return ClockOut(therapist_id=t.id, clocked_in=False, clock_in_at=None)


# --- Settings ------------------------------------------------------------------------


class Tier(BaseModel):
    threshold: Decimal = Field(gt=0, description="Pesos of sales, or number of services")
    bonus: Decimal = Field(ge=0)


class PaySettingsIn(BaseModel):
    daily_allowance: Decimal = Field(ge=0)
    quota_basis: Literal["sales", "services"]
    quota_tiers: list[Tier] = Field(default=[], max_length=20)
    week_starts_on: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")

    @field_validator("quota_tiers")
    @classmethod
    def _distinct(cls, v: list[Tier]) -> list[Tier]:
        if len({t.threshold for t in v}) != len(v):
            raise ValueError("Each quota tier needs a different target")
        return sorted(v, key=lambda t: t.threshold)


class PaySettingsOut(PaySettingsIn):
    pass


def _settings_out(row) -> PaySettingsOut:
    return PaySettingsOut(
        daily_allowance=row.daily_allowance,
        quota_basis=row.quota_basis,
        quota_tiers=[Tier(**t) for t in row.quota_tiers or []],
        week_starts_on=row.week_starts_on,
    )


@router.get("/pay-settings", response_model=PaySettingsOut)
def get_pay_settings(ctx: PayReaderSession) -> PaySettingsOut:
    session, staff = ctx
    return _settings_out(settings_for(session, staff.tenant_id))


@router.put("/pay-settings", response_model=PaySettingsOut)
def put_pay_settings(ctx: AdminSession, body: PaySettingsIn) -> PaySettingsOut:
    session, staff = ctx
    row = settings_for(session, staff.tenant_id)
    row.daily_allowance = body.daily_allowance
    row.quota_basis = body.quota_basis
    row.quota_tiers = [
        {"threshold": str(t.threshold), "bonus": str(t.bonus)} for t in body.quota_tiers
    ]
    row.week_starts_on = body.week_starts_on
    session.flush()
    return _settings_out(row)


# --- Weekly report -------------------------------------------------------------------


class DayOut(BaseModel):
    date: date
    clocked_in: bool
    services: int
    sales: Decimal
    commission: Decimal


class TherapistPayOut(BaseModel):
    therapist_id: uuid.UUID
    name: str
    services: int
    sales: Decimal = Field(description="Menu prices of completed services, before discounts")
    commission: Decimal
    days_worked: int
    allowance: Decimal
    quota_progress: Decimal = Field(description="Pesos or services, per the quota basis")
    bonus: Decimal
    bonus_threshold: Decimal | None
    total: Decimal
    days: list[DayOut]


class WeekPayOut(BaseModel):
    week_start: date
    week_end: date
    settings: PaySettingsOut
    therapists: list[TherapistPayOut]
    total: Decimal


@router.get("/branches/{branch_id}/pay", response_model=WeekPayOut)
def get_week_pay(
    ctx: PayReaderSession,
    branch_id: uuid.UUID,
    week: Annotated[date, Query(description="Any day in the week")],
) -> WeekPayOut:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    report = week_pay(session, session.get(Branch, branch_id), week)
    people = [
        TherapistPayOut(
            therapist_id=p.therapist_id,
            name=p.name,
            services=p.services,
            sales=p.sales,
            commission=p.commission,
            days_worked=p.days_worked,
            allowance=p.allowance,
            quota_progress=p.quota_progress,
            bonus=p.bonus,
            bonus_threshold=p.bonus_threshold,
            total=p.total,
            days=[
                DayOut(
                    date=d.day,
                    clocked_in=d.clocked_in,
                    services=d.services,
                    sales=d.sales,
                    commission=d.commission,
                )
                for d in sorted(p.days.values(), key=lambda d: d.day)
            ],
        )
        for p in report.therapists
    ]
    return WeekPayOut(
        week_start=report.week_start,
        week_end=report.week_end,
        settings=_settings_out(report.settings),
        therapists=people,
        total=sum((p.total for p in people), Decimal(0)),
    )
