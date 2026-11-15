# ruff: noqa: F811  (the `world` fixture is imported from test_api)
"""Accounting part 2: recurring transactions, allocations, salary payouts."""

from datetime import date

from app.fund_plans import occurrences
from app.models import FundSchedule
from tests.conftest import DAY, at
from tests.test_api import _book, world  # noqa: F401
from tests.test_funds import _accounts, _categories
from tests.test_pay import today_is_day  # noqa: F401


def test_occurrences_weekly_and_monthly():
    weekly = FundSchedule(frequency="weekly", weekday=4, starts_on=date(2026, 11, 1), ends_on=None)
    assert occurrences(weekly, date(2026, 11, 1), date(2026, 11, 20)) == [
        date(2026, 11, 6),
        date(2026, 11, 13),
        date(2026, 11, 20),
    ]
    monthly = FundSchedule(
        frequency="monthly", day_of_month=31, starts_on=date(2026, 1, 15), ends_on=date(2026, 4, 30)
    )
    # 31st falls on the last day of shorter months; nothing after ends_on.
    assert occurrences(monthly, date(2025, 1, 1), date(2027, 1, 1)) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_recurring_bill_shows_due_then_posts_or_skips(world, today_is_day):
    owner = world.login("owner@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    bpi = _accounts(world, owner)["Main/BPI bank transfer"]
    petty = _accounts(world, owner)["Main/Petty cash"]
    cats = _categories(world, owner)
    bill = {
        "branch_id": str(world.a.branch.id),
        "name": "Electricity",
        "kind": "expense",
        "account_id": bpi["id"],
        "category_id": cats["Electricity"]["id"],
        "amount": "8000",
        "frequency": "monthly",
        "day_of_month": 1,
        "starts_on": "2026-11-01",
    }
    r = world.client.post("/fund-schedules", json=bill, headers=owner)
    assert r.status_code == 201, r.text
    elec = r.json()
    assert elec["next_due"] == "2026-11-01"
    # Accountants look, but cannot set up.
    assert world.client.post("/fund-schedules", json=bill, headers=acct).status_code == 403
    # Wrong category direction, transfer without a target.
    bad = {**bill, "category_id": cats["Other income"]["id"]}
    assert world.client.post("/fund-schedules", json=bad, headers=owner).status_code == 422
    bad = {**bill, "kind": "transfer"}
    assert world.client.post("/fund-schedules", json=bad, headers=owner).status_code == 422

    refill = {
        "branch_id": str(world.a.branch.id),
        "name": "Petty cash refill",
        "kind": "transfer",
        "account_id": bpi["id"],
        "to_account_id": petty["id"],
        "amount": "3000",
        "frequency": "weekly",
        "weekday": 0,  # Mondays; DAY (Nov 2) is a Monday
        "starts_on": "2026-11-01",
    }
    r = world.client.post("/fund-schedules", json=refill, headers=owner)
    assert r.status_code == 201, r.text
    ref = r.json()

    r = world.client.get("/fund-due", params={"days_ahead": 7}, headers=acct)
    assert r.status_code == 200, r.text
    due = [(d["schedule"]["name"], d["due_date"], d["overdue"]) for d in r.json()]
    assert due == [
        ("Electricity", "2026-11-01", True),
        ("Petty cash refill", "2026-11-02", False),
        ("Petty cash refill", "2026-11-09", False),
    ]

    # Post the bill with the real amount; it lands in the ledger and leaves the due list.
    r = world.client.post(
        f"/fund-schedules/{elec['id']}/post",
        json={"due_date": "2026-11-01", "amount": "8450.75", "reference": "MER-1"},
        headers=owner,
    )
    assert r.status_code == 201, r.text
    (entry,) = r.json()
    assert (entry["amount"], entry["category_name"], entry["business_date"]) == (
        "8450.75",
        "Electricity",
        "2026-11-02",
    )
    again = world.client.post(
        f"/fund-schedules/{elec['id']}/post", json={"due_date": "2026-11-01"}, headers=owner
    )
    assert again.status_code == 422
    # Not a due date of this schedule.
    r = world.client.post(
        f"/fund-schedules/{elec['id']}/post", json={"due_date": "2026-11-05"}, headers=owner
    )
    assert r.status_code == 422

    # The refill posts as a transfer: both halves.
    r = world.client.post(
        f"/fund-schedules/{ref['id']}/post", json={"due_date": "2026-11-02"}, headers=owner
    )
    assert r.status_code == 201 and len(r.json()) == 2
    assert _accounts(world, owner)["Main/Petty cash"]["balance"] == "3000.00"
    # Skip next week's.
    r = world.client.post(
        f"/fund-schedules/{ref['id']}/skip", json={"due_date": "2026-11-09"}, headers=owner
    )
    assert r.status_code == 200 and r.json()["next_due"] == "2026-11-16"
    assert world.client.get("/fund-due", params={"days_ahead": 7}, headers=owner).json() == []

    # Voiding a posted entry puts it back on the due list.
    r = world.client.post(
        f"/fund-transactions/{entry['id']}/void", json={"reason": "Wrong account"}, headers=owner
    )
    assert r.status_code == 200
    due = world.client.get("/fund-due", params={"days_ahead": 0}, headers=owner).json()
    assert [(d["schedule"]["name"], d["due_date"]) for d in due] == [("Electricity", "2026-11-01")]


def test_allocations_set_aside_spend_and_close(world, today_is_day):
    owner = world.login("owner@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    accts = _accounts(world, owner)
    bpi = accts["Main/BPI bank transfer"]
    body = {k: bpi[k] for k in ("name", "kind", "branch_id", "accepts_payments")}
    world.client.put(
        f"/accounts/{bpi['id']}", json={**body, "opening_balance": "50000"}, headers=owner
    )
    cats = _categories(world, owner)

    r = world.client.post(
        "/fund-allocations",
        json={
            "account_id": bpi["id"],
            "name": "Christmas party",
            "target_amount": "30000",
            "target_date": "2026-12-20",
            "initial_amount": "20000",
        },
        headers=owner,
    )
    assert r.status_code == 201, r.text
    party = r.json()
    assert party["balance"] == "20000.00" and party["account_name"] == "BPI bank transfer"
    # Accountants can see but not create; petty cash cannot hold allocations.
    assert (
        world.client.get("/fund-allocations", headers=acct).json()[0]["name"] == "Christmas party"
    )
    r = world.client.post(
        "/fund-allocations", json={"account_id": bpi["id"], "name": "X"}, headers=acct
    )
    assert r.status_code == 403
    r = world.client.post(
        "/fund-allocations",
        json={"account_id": accts["Main/Petty cash"]["id"], "name": "X"},
        headers=owner,
    )
    assert r.status_code == 422

    bal = _accounts(world, owner)["Main/BPI bank transfer"]
    assert (bal["balance"], bal["allocated"], bal["available"]) == (
        "50000.00",
        "20000.00",
        "30000.00",
    )
    # Cannot set aside more than is free in the account.
    r = world.client.post(
        f"/fund-allocations/{party['id']}/moves", json={"amount": "30000.01"}, headers=owner
    )
    assert r.status_code == 422 and "not yet set aside" in r.json()["detail"]
    r = world.client.post(
        f"/fund-allocations/{party['id']}/moves",
        json={"amount": "5000", "note": "November sales"},
        headers=owner,
    )
    assert r.json()["balance"] == "25000.00"

    # Spend from it: an expense from BPI that reduces what is set aside.
    spend = {
        "amount": "12000",
        "category_id": cats["Other expense"]["id"],
        "description": "Venue deposit",
    }
    r = world.client.post(f"/fund-allocations/{party['id']}/spend", json=spend, headers=owner)
    assert r.status_code == 201, r.text
    spent = r.json()
    assert spent["account_name"] == "BPI bank transfer" and spent["direction"] == "out"
    bal = _accounts(world, owner)["Main/BPI bank transfer"]
    assert (bal["balance"], bal["allocated"], bal["available"]) == (
        "38000.00",
        "13000.00",
        "25000.00",
    )
    r = world.client.post(
        f"/fund-allocations/{party['id']}/spend",
        json={**spend, "amount": "13000.01"},
        headers=owner,
    )
    assert r.status_code == 422

    hist = world.client.get(f"/fund-allocations/{party['id']}/history", headers=acct).json()
    assert [(h["kind"], h["amount"]) for h in hist][-1] == ("set_aside", "20000.00")
    assert {h["kind"] for h in hist} == {"set_aside", "spent"}

    # Voiding the spend gives the money back to the allocation.
    world.client.post(
        f"/fund-transactions/{spent['id']}/void", json={"reason": "Cancelled"}, headers=owner
    )
    r = world.client.get("/fund-allocations", headers=owner).json()[0]
    assert r["balance"] == "25000.00"

    # Release some, then close: the rest goes back to the account.
    r = world.client.post(
        f"/fund-allocations/{party['id']}/moves", json={"amount": "-5000"}, headers=owner
    )
    assert r.json()["balance"] == "20000.00"
    r = world.client.post(f"/fund-allocations/{party['id']}/close", headers=owner)
    assert r.json()["closed"] and r.json()["balance"] == "0.00"
    assert _accounts(world, owner)["Main/BPI bank transfer"]["available"] == "50000.00"
    assert world.client.get("/fund-allocations", headers=owner).json() == []
    closed = world.client.get(
        "/fund-allocations", params={"include_closed": True}, headers=owner
    ).json()
    assert [c["name"] for c in closed] == ["Christmas party"]


def test_salary_payout_from_weekly_pay(world, today_is_day):
    a = world.a
    owner = world.login("owner@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    world.client.post(f"/therapists/{a.ana.id}/clock-in", headers=desk)
    b = _book(world, desk, starts_at=at(10).isoformat(), guests=[{"therapist_id": str(a.ana.id)}])
    for to in ("checked_in", "in_service", "completed"):
        assert (
            world.client.post(f"/bookings/{b['id']}/status/{to}", headers=desk).status_code == 200
        )

    url = f"/branches/{a.branch.id}/pay/payouts"
    r = world.client.get(url, params={"week": DAY.isoformat()}, headers=acct)
    assert r.status_code == 200, r.text
    lines = {i["name"]: i for i in r.json()["items"]}
    ana = lines["Ana"]
    assert not ana["paid"] and float(ana["amount_due"]) > 0
    assert float(lines["Ben"]["amount_due"]) == 0

    cash = _accounts(world, owner)["Main/Cash"]
    body = {"week": DAY.isoformat(), "account_id": cash["id"]}
    assert world.client.post(url, json=body, headers=acct).status_code == 403
    r = world.client.post(url, json=body, headers=owner)
    assert r.status_code == 201, r.text
    ana = {i["name"]: i for i in r.json()["items"]}["Ana"]
    assert ana["paid"] and ana["paid_from"] == "Cash" and ana["paid_amount"] == ana["amount_due"]
    assert r.json()["to_pay"] == "0"
    # Nobody left to pay.
    assert world.client.post(url, json=body, headers=owner).status_code == 422

    entries = world.client.get(
        "/fund-entries", params={"date_from": DAY.isoformat(), "show": "out"}, headers=owner
    ).json()["items"]
    (pay_entry,) = entries
    assert pay_entry["category_name"] == "Salaries"
    assert pay_entry["description"].startswith("Pay: Ana, week Nov 2")

    # Voiding the payout makes Ana unpaid again.
    world.client.post(
        f"/fund-transactions/{pay_entry['id']}/void",
        json={"reason": "Paid by GCash"},
        headers=owner,
    )
    r = world.client.get(url, params={"week": DAY.isoformat()}, headers=owner)
    assert not {i["name"]: i for i in r.json()["items"]}["Ana"]["paid"]
