# ruff: noqa: F811  (the `world` fixture is imported from test_api)
"""Therapist pay: clock in/out, booking only clocked-in therapists today, commission,
daily allowance, weekly quota bonus, and who may see or change what."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import pay
from tests.conftest import DAY, at
from tests.test_api import _book, world  # noqa: F401


@pytest.fixture()
def today_is_day(monkeypatch):
    """Pretend it is 09:30 on the test day, so DAY is "today" at the branch."""
    monkeypatch.setattr(pay, "now", lambda: at(9, 30))


def test_commission_defaults_by_length_and_can_be_changed(world):
    a = world.a
    h = world.login("owner@banahaw.ph")
    body = {"label": "60 min", "duration_min": 60, "default_price": "800"}
    r = world.client.put(f"/variants/{a.swedish60.id}", json=body, headers=h)
    assert Decimal(r.json()["commission"]) == 120
    r = world.client.put(
        f"/variants/{a.swedish60.id}", json={**body, "commission": "150"}, headers=h
    )
    assert Decimal(r.json()["commission"]) == 150
    r = world.client.post(
        f"/services/{a.swedish60.service_id}/variants",
        json={"label": "180 min", "duration_min": 180, "default_price": "2000"},
        headers=h,
    )
    assert Decimal(r.json()["commission"]) == 368
    # The front desk cannot change it.
    desk = world.login("desk@banahaw.ph")
    r = world.client.put(f"/variants/{a.swedish60.id}", json=body, headers=desk)
    assert r.status_code == 403


def test_only_clocked_in_therapists_can_be_booked_today(world, today_is_day):
    a = world.a
    h = world.login("desk@banahaw.ph")
    slots = f"/branches/{a.branch.id}/slots"
    search = {"variant_id": str(a.swedish60.id), "date": DAY.isoformat(), "guests": [{}]}

    # Nobody clocked in: no free times today, even though everyone has a shift.
    assert world.client.post(slots, json=search, headers=h).json() == []

    r = world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=h)
    assert r.status_code == 200 and r.json()["clocked_in"]
    # Clicking twice is harmless.
    assert world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=h).status_code == 200

    therapists = world.client.get(f"/branches/{a.branch.id}/therapists", headers=h).json()
    assert {t["name"]: t["clocked_in"] for t in therapists} == {
        "Ana": True,
        "Ben": False,
        "Cora": False,
    }
    board = world.client.get(
        f"/branches/{a.branch.id}/board", params={"date": DAY.isoformat()}, headers=h
    ).json()
    assert [t["name"] for t in board["therapists"] if t["clocked_in_at"]] == ["Ana"]

    found = world.client.post(slots, json=search, headers=h).json()
    assert found and {p["therapist_id"] for s in found for p in s["pairs"]} == {str(a.ana.id)}

    r = world.client.post(
        "/bookings",
        json={
            "branch_id": str(a.branch.id),
            "client_id": str(a.client.id),
            "channel_id": str(a.phone.id),
            "variant_id": str(a.swedish60.id),
            "starts_at": at(12).isoformat(),
            "guests": [{"therapist_id": str(a.ben.id)}],
        },
        headers=h,
    )
    assert r.status_code == 422  # Ben is on shift but not clocked in

    assert world.client.post(f"/therapists/{a.ana.id}/clock-out", headers=h).status_code == 200
    assert world.client.post(slots, json=search, headers=h).json() == []

    # Other days still go by the shift schedule.
    tomorrow = {**search, "date": (DAY + timedelta(days=1)).isoformat()}
    assert world.client.post(slots, json=tomorrow, headers=h).json() == []  # no shifts there


def test_weekly_pay_report(world, today_is_day):
    a = world.a
    owner = world.login("owner@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    world.client.put(
        f"/variants/{a.swedish60.id}",
        json={"label": "60 min", "duration_min": 60, "default_price": "800"},
        headers=owner,
    )
    world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=desk)
    world.client.post(f"/therapists/{a.ben.id}/clock-in", headers=desk)
    for hour in (10, 12):
        b = _book(
            world,
            desk,
            starts_at=at(hour).isoformat(),
            guests=[{"therapist_id": str(a.ana.id)}],
        )
        for to in ("checked_in", "in_service", "completed"):
            r = world.client.post(f"/bookings/{b['id']}/status/{to}", headers=desk)
            assert r.status_code == 200, r.text
    # A cancelled booking earns nothing.
    c = _book(world, desk, starts_at=at(15).isoformat(), guests=[{"therapist_id": str(a.ben.id)}])
    world.client.post(f"/bookings/{c['id']}/status/cancelled", headers=desk)

    # A later commission change does not rewrite completed services.
    world.client.put(
        f"/variants/{a.swedish60.id}",
        json={"label": "60 min", "duration_min": 60, "default_price": "800", "commission": "999"},
        headers=owner,
    )

    r = world.client.put(
        "/pay-settings",
        json={
            "daily_allowance": "250",
            "quota_basis": "sales",
            "week_starts_on": 0,
            "quota_tiers": [
                {"threshold": "3000", "bonus": "800"},
                {"threshold": "1500", "bonus": "300"},
            ],
        },
        headers=owner,
    )
    assert r.status_code == 200, r.text
    assert [t["threshold"] for t in r.json()["quota_tiers"]] == ["1500", "3000"]

    mgr = world.login("mgr@banahaw.ph")
    r = world.client.get(
        f"/branches/{a.branch.id}/pay", params={"week": DAY.isoformat()}, headers=mgr
    )
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["week_start"] == "2026-11-02" and rep["week_end"] == "2026-11-08"  # Mon-Sun
    rows = {t["name"]: t for t in rep["therapists"]}
    ana, ben, cora = rows["Ana"], rows["Ben"], rows["Cora"]
    assert ana["services"] == 2 and Decimal(ana["sales"]) == 1600
    assert Decimal(ana["commission"]) == 240
    assert ana["days_worked"] == 1 and Decimal(ana["allowance"]) == 250
    assert Decimal(ana["bonus"]) == 300 and Decimal(ana["bonus_threshold"]) == 1500
    assert Decimal(ana["total"]) == 790
    assert ben["services"] == 0 and Decimal(ben["total"]) == 250
    assert cora["days_worked"] == 0 and Decimal(cora["total"]) == 0
    assert Decimal(rep["total"]) == 1040
    day = next(d for d in ana["days"] if d["date"] == DAY.isoformat())
    assert day["clocked_in"] and day["services"] == 2

    # Counting services instead of pesos.
    world.client.put(
        "/pay-settings",
        json={
            "daily_allowance": "250",
            "quota_basis": "services",
            "week_starts_on": 0,
            "quota_tiers": [{"threshold": "2", "bonus": "100"}],
        },
        headers=owner,
    )
    rep = world.client.get(
        f"/branches/{a.branch.id}/pay", params={"week": DAY.isoformat()}, headers=mgr
    ).json()
    assert Decimal(next(t for t in rep["therapists"] if t["name"] == "Ana")["bonus"]) == 100


def test_pay_permissions(world):
    a = world.a
    desk = world.login("desk@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    mgr = world.login("mgr@banahaw.ph")
    north_mgr = world.login("north.mgr@banahaw.ph")
    path = f"/branches/{a.branch.id}/pay"
    params = {"week": DAY.isoformat()}
    assert world.client.get(path, params=params, headers=desk).status_code == 403
    assert world.client.get(path, params=params, headers=acct).status_code == 200
    assert world.client.get(path, params=params, headers=north_mgr).status_code == 403
    assert world.client.get("/pay-settings", headers=desk).status_code == 403
    settings = world.client.get("/pay-settings", headers=mgr).json()
    assert settings["daily_allowance"] == "250.00" and settings["quota_tiers"] == []
    assert world.client.put("/pay-settings", json=settings, headers=mgr).status_code == 403
    # The accountant does not clock therapists in; another branch's manager cannot either.
    assert world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=acct).status_code == 403
    r = world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=north_mgr)
    assert r.status_code == 403
