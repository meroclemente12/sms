# ruff: noqa: F811  (the `world` fixture is imported from test_api)
"""Branches open past midnight: everything up to the business-day start belongs to the
day the branch opened (day board, free times, clock-in, pay, payments)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from importlib import util
from pathlib import Path

import pytest
from sqlalchemy import update

from app import models as m
from app import pay
from app.business_day import (
    business_date,
    day_bounds,
    fits_closed_hours,
    opening_hours,
    suggest_day_start,
)
from app.db import engine
from tests.conftest import DAY, MNL, at
from tests.test_api import _book, world  # noqa: F401

NEXT = DAY + timedelta(days=1)


def late(hh: int, mm: int = 0) -> datetime:
    """A time after midnight, on the calendar day after DAY."""
    return datetime.combine(NEXT, time(hh, mm), tzinfo=MNL)


def test_suggested_day_start_falls_in_closed_hours():
    assert suggest_day_start(time(9), time(22)) == time(6)
    assert suggest_day_start(time(13), time(1)) == time(6)
    # Open 5 AM to 11 PM: 6 AM is open, so the middle of 11 PM to 5 AM.
    assert suggest_day_start(time(5), time(23)) == time(2)
    # Open around the clock: the day starts when it "opens".
    assert suggest_day_start(time(8), time(8)) == time(8)
    assert fits_closed_hours(time(1), time(13), time(1))
    assert fits_closed_hours(time(13), time(13), time(1))
    assert not fits_closed_hours(time(0, 30), time(13), time(1))
    assert not fits_closed_hours(time(14), time(13), time(1))


def test_business_date_and_hours():
    branch = m.Branch(
        timezone="Asia/Manila", opens_at=time(13), closes_at=time(1), day_starts_at=time(6)
    )
    assert business_date(branch, late(0, 30)) == DAY
    assert business_date(branch, late(5, 59)) == DAY
    assert business_date(branch, late(6)) == NEXT
    assert day_bounds(branch, DAY) == (at(6), late(6))
    assert opening_hours(branch, DAY) == (at(13), late(1))
    day = m.Branch(
        timezone="Asia/Manila", opens_at=time(9), closes_at=time(22), day_starts_at=time(6)
    )
    assert opening_hours(day, DAY) == (at(9), at(22))


def test_migration_suggestion_matches_the_app():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0007_business_day.py"
    spec = util.spec_from_file_location("mig0007", path)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for opens in range(0, 24):
        for closes in range(0, 24):
            o, c = time(opens), time(closes)
            assert mod._suggest(o, c) == suggest_day_start(o, c), (o, c)


def _set_hours(world, h, opens: str, closes: str, day_starts_at: str | None = None):
    a = world.a
    body = {"name": "Main", "opens_at": opens, "closes_at": closes}
    if day_starts_at is not None:
        body["day_starts_at"] = day_starts_at
    return world.client.put(f"/branches/{a.branch.id}", json=body, headers=h)


def test_branch_day_start_is_picked_and_checked(world):
    owner = world.login("owner@banahaw.ph")
    r = _set_hours(world, owner, "13:00", "01:00")
    assert r.status_code == 200, r.text
    assert r.json()["day_starts_at"] == "06:00:00"
    r = _set_hours(world, owner, "13:00", "01:00", "00:30")
    assert r.status_code == 422 and "01:00 and 13:00" in r.json()["detail"]
    r = _set_hours(world, owner, "13:00", "01:00", "04:00")
    assert r.status_code == 200 and r.json()["day_starts_at"] == "04:00:00"
    # Kept when the hours change and it still fits; re-picked when it no longer does.
    assert _set_hours(world, owner, "12:00", "02:00").json()["day_starts_at"] == "04:00:00"
    assert _set_hours(world, owner, "03:00", "23:00").json()["day_starts_at"] == "01:00:00"
    me = world.client.get("/auth/me", headers=owner).json()
    main = next(b for b in me["branches"] if b["name"] == "Main")
    assert main["day_starts_at"] == "01:00:00" and main["timezone"] == "Asia/Manila"


@pytest.fixture()
def clock(monkeypatch):
    """Move the branch clock: clock.set(moment)."""

    class Clock:
        def set(self, moment: datetime) -> None:
            monkeypatch.setattr(pay, "now", lambda: moment)

    return Clock()


def test_night_after_midnight_belongs_to_the_opening_day(world, clock):
    a = world.a
    owner = world.login("owner@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    assert _set_hours(world, owner, "13:00", "02:00").status_code == 200

    # Ana clocks in at the start of the night.
    clock.set(at(13, 5))
    assert world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=desk).status_code == 200

    # Past midnight it is still the same business day: Ana is still in, and a walk-in
    # finds her free.
    clock.set(late(0, 10))
    r = world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=desk)
    assert r.status_code == 200
    with engine.connect() as conn:
        rows = conn.execute(
            m.TherapistAttendance.__table__.select().where(
                m.TherapistAttendance.therapist_id == a.ana.id
            )
        ).all()
    assert [r.work_date for r in rows] == [DAY]  # no second day worked
    found = world.client.post(
        f"/branches/{a.branch.id}/slots",
        json={
            "variant_id": str(a.swedish60.id),
            "after": late(0, 10).isoformat(),
            "guests": [{}],
        },
        headers=desk,
    ).json()
    assert found, "walk-in after midnight found nobody"
    assert {p["therapist_id"] for s in found for p in s["pairs"]} == {str(a.ana.id)}
    assert datetime.fromisoformat(found[0]["starts_at"]) == late(0, 15)

    b = _book(
        world,
        desk,
        channel_id=str(a.walk_in.id),
        starts_at=late(0, 15).isoformat(),
        guests=[{"therapist_id": str(a.ana.id)}],
    )
    for to in ("checked_in", "in_service"):
        assert world.client.post(f"/bookings/{b['id']}/status/{to}", headers=desk).is_success
    methods = world.client.get(f"/branches/{a.branch.id}/payment-methods", headers=desk).json()
    cash = next(x for x in methods if x["name"] == "Cash")
    r = world.client.post(
        f"/bookings/{b['id']}/status/completed",
        json={"payments": [{"method_id": cash["id"], "amount": "800"}]},
        headers=desk,
    )
    assert r.status_code == 200, r.text
    with engine.begin() as conn:
        conn.execute(
            update(m.Payment).where(m.Payment.booking_id == b["id"]).values(recorded_at=late(1, 20))
        )

    # The day board of the opening day shows it; the next day's does not.
    def board(day: date) -> dict:
        return world.client.get(
            f"/branches/{a.branch.id}/board", params={"date": day.isoformat()}, headers=desk
        ).json()

    tonight = board(DAY)
    assert tonight["day_starts_at"] == "06:00:00"
    assert [i["booking_id"] for i in tonight["items"]] == [b["id"]]
    assert [t["name"] for t in tonight["therapists"] if t["clocked_in_at"]] == ["Ana"]
    assert board(NEXT)["items"] == []

    # Pay counts the sale on the opening day.
    mgr = world.login("mgr@banahaw.ph")
    rep = world.client.get(
        f"/branches/{a.branch.id}/pay", params={"week": DAY.isoformat()}, headers=mgr
    ).json()
    ana = next(t for t in rep["therapists"] if t["name"] == "Ana")
    days = {d["date"]: d for d in ana["days"]}
    assert days[DAY.isoformat()]["services"] == 1
    assert Decimal(days[DAY.isoformat()]["sales"]) == 800
    assert days[NEXT.isoformat()]["services"] == 0
    assert ana["days_worked"] == 1

    # The payment logged at 1:20 AM is in the opening day's list.
    acct = world.login("acct@banahaw.ph")
    url = f"/branches/{a.branch.id}/payments"

    def logged(day: date) -> int:
        r = world.client.get(
            url, params={"date_from": day.isoformat(), "status": "all"}, headers=acct
        )
        assert r.status_code == 200, r.text
        return len(r.json()["items"])

    assert logged(DAY) == 1
    assert logged(NEXT) == 0

    # Clocking out after midnight closes the same day's record.
    clock.set(late(1, 45))
    assert world.client.post(f"/therapists/{a.ana.id}/clock-out", headers=desk).is_success
    with engine.connect() as conn:
        out = conn.execute(
            m.TherapistAttendance.__table__.select().where(
                m.TherapistAttendance.therapist_id == a.ana.id
            )
        ).one()
    assert out.clock_out_at == late(1, 45)
