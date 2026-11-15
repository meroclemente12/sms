"""Accounting: fund accounts, the ledger, petty cash, transfers."""

from datetime import timedelta

from app.pay import branch_today
from tests.test_api import world as world  # noqa: F401  (pytest fixture)
from tests.test_payments import _checkout, _in_service, _methods


def _accounts(world, h, **params) -> dict[str, dict]:
    r = world.client.get("/accounts", params=params, headers=h)
    assert r.status_code == 200, r.text
    return {f"{a['branch_name'] or 'All'}/{a['name']}": a for a in r.json()}


def _categories(world, h) -> dict[str, dict]:
    r = world.client.get("/fund-categories", headers=h)
    assert r.status_code == 200, r.text
    return {c["name"]: c for c in r.json()}


def test_branch_starts_with_accounts_linked_to_payment_methods(world):
    owner = world.login("owner@banahaw.ph")
    accts = _accounts(world, owner)
    assert {"Main/Cash", "Main/GCash", "Main/BPI bank transfer", "Main/Petty cash"} <= set(accts)
    assert accts["Main/Cash"]["kind"] == "cash" and accts["Main/Cash"]["accepts_payments"]
    assert accts["Main/Petty cash"]["kind"] == "petty_cash"
    assert not accts["Main/Petty cash"]["accepts_payments"]
    assert accts["Main/Cash"]["balance"] == "0.00"

    # Another spa's accounts are never listed.
    other = _accounts(world, world.login("owner@other.ph", spa="other-spa"))
    assert not set(a["id"] for a in other.values()) & set(a["id"] for a in accts.values())


def test_shared_account_becomes_a_payment_method_at_every_branch(world):
    owner = world.login("owner@banahaw.ph")
    body = {
        "name": "BDO Savings",
        "kind": "bank",
        "account_number": "0012-3456",
        "opening_balance": "50000",
        "accepts_payments": True,
    }
    r = world.client.post("/accounts", json=body, headers=owner)
    assert r.status_code == 201, r.text
    bdo = r.json()
    assert bdo["branch_id"] is None and bdo["balance"] == "50000.00"
    for bid in (world.a.branch.id, world.north.id):
        m = _methods(world, owner, bid)["BDO Savings"]
        assert m["active"] and m["kind"] == "cashless" and m["needs_reference"]

    # No longer for guests: the methods are switched off, not deleted.
    r = world.client.put(
        f"/accounts/{bdo['id']}", json={**body, "accepts_payments": False}, headers=owner
    )
    assert r.status_code == 200, r.text
    assert not _methods(world, owner)["BDO Savings"]["active"]

    # Petty cash and cash drawers need a branch; desks and managers cannot set up.
    r = world.client.post("/accounts", json={"name": "Vault", "kind": "cash"}, headers=owner)
    assert r.status_code == 422
    r = world.client.post("/accounts", json=body, headers=world.login("mgr@banahaw.ph"))
    assert r.status_code == 403


def test_sales_post_to_the_account_of_the_payment_method(world):
    desk = world.login("desk@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    methods = _methods(world, desk)
    b = _in_service(world, desk)
    r = _checkout(
        world,
        desk,
        b["id"],
        [
            {"method_id": methods["Cash"]["id"], "amount": "300"},
            {"method_id": methods["GCash"]["id"], "amount": "500", "reference": "777"},
        ],
    )
    assert r.status_code == 200, r.text
    accts = _accounts(world, acct)
    assert accts["Main/Cash"]["balance"] == "300.00"
    gcash = accts["Main/GCash"]
    assert (gcash["balance"], gcash["unverified"]) == ("500.00", "500.00")

    r = world.client.get("/fund-entries", params={"show": "sales"}, headers=acct)
    assert r.status_code == 200, r.text
    sales = r.json()
    assert sales["money_in"] == "800.00"
    assert {e["client_name"] for e in sales["items"]} == {"Maria Santos"}
    assert all(not e["can_void"] for e in sales["items"])

    # Once verified it no longer counts as unverified.
    gpay = next(p for p in r.json()["items"] if p["account_name"] == "GCash")
    assert world.client.post(f"/payments/{gpay['id']}/verify", headers=acct).status_code == 200
    assert _accounts(world, acct)["Main/GCash"]["unverified"] == "0.00"


def test_desk_logs_petty_cash_expense(world):
    desk = world.login("desk@banahaw.ph")
    owner = world.login("owner@banahaw.ph")
    mine = _accounts(world, desk)
    assert list(mine) == ["Main/Petty cash"]  # nothing else is visible to the desk
    petty = mine["Main/Petty cash"]
    cats = _categories(world, desk)
    assert "Laundry" in cats and "Salaries" not in cats

    # Refill first (admin), from the bank.
    bpi = _accounts(world, owner)["Main/BPI bank transfer"]
    r = world.client.post(
        "/fund-transfers",
        json={
            "from_account_id": bpi["id"],
            "to_account_id": petty["id"],
            "amount": "2000",
            "description": "Weekly refill",
        },
        headers=owner,
    )
    assert r.status_code == 201, r.text
    out_row, in_row = r.json()
    assert (out_row["direction"], in_row["direction"]) == ("out", "in")
    assert out_row["other_account_name"] == "Petty cash"
    # Only admins and accountants move money.
    r = world.client.post(
        "/fund-transfers",
        json={"from_account_id": petty["id"], "to_account_id": bpi["id"], "amount": "1"},
        headers=desk,
    )
    assert r.status_code == 403

    laundry = {
        "account_id": petty["id"],
        "amount": "350",
        "category_id": cats["Laundry"]["id"],
        "description": "Towels, 2 bags",
    }
    r = world.client.post("/fund-transactions", json=laundry, headers=desk)
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["category_name"], e["recorded_by_name"], e["can_void"]) == ("Laundry", "Desk", True)
    assert _accounts(world, desk)["Main/Petty cash"]["balance"] == "1650.00"

    # The desk cannot add money, backdate, skip the note, or use admin categories.
    bad = [
        {**laundry, "direction": "in"},
        {**laundry, "business_date": str(branch_today(world.a.branch) - timedelta(days=1))},
        {**laundry, "description": " "},
        {**laundry, "account_id": bpi["id"]},
    ]
    codes = [world.client.post("/fund-transactions", json=b, headers=desk).status_code for b in bad]
    assert codes == [403, 422, 422, 404]
    salaries = _categories(world, owner)["Salaries"]
    r = world.client.post(
        "/fund-transactions", json={**laundry, "category_id": salaries["id"]}, headers=desk
    )
    assert r.status_code == 422

    # A desk at another branch cannot see Main's petty cash.
    r = world.client.get("/fund-entries", params={"account_id": petty["id"]}, headers=desk)
    ledger = r.json()
    assert (ledger["opening_balance"], ledger["closing_balance"]) == ("0.00", "1650.00")
    assert ledger["money_in"] == "2000.00" and ledger["money_out"] == "350.00"
    north = world.login("north.mgr@banahaw.ph")
    r = world.client.get("/fund-entries", params={"account_id": petty["id"]}, headers=north)
    assert r.status_code == 404

    # Void: the desk can void their own entry today; it stops counting but stays listed.
    r = world.client.post(
        f"/fund-transactions/{e['id']}/void", json={"reason": "Wrong amount"}, headers=desk
    )
    assert r.status_code == 200 and r.json()[0]["voided"]
    assert _accounts(world, desk)["Main/Petty cash"]["balance"] == "2000.00"
    r = world.client.post(
        f"/fund-transactions/{e['id']}/void", json={"reason": "again"}, headers=owner
    )
    assert r.status_code == 422

    # Voiding one half of a transfer voids both.
    r = world.client.post(
        f"/fund-transactions/{in_row['id']}/void", json={"reason": "Never happened"}, headers=owner
    )
    assert r.status_code == 200 and len(r.json()) == 2
    assert _accounts(world, owner)["Main/BPI bank transfer"]["balance"] == "0.00"


def test_admin_records_bill_and_dates_and_opening(world):
    owner = world.login("owner@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    cats = _categories(world, owner)
    bpi = _accounts(world, owner)["Main/BPI bank transfer"]
    bill = {
        "account_id": bpi["id"],
        "amount": "8450.75",
        "category_id": cats["Electricity"]["id"],
        "description": "Meralco October",
        "reference": "REF-1",
        "business_date": "2026-11-05",
    }
    # The accountant can record bills too.
    r = world.client.post("/fund-transactions", json=bill, headers=acct)
    assert r.status_code == 201, r.text
    assert r.json()["business_date"] == "2026-11-05"
    # Category direction must match; a category is required; not before the opening date.
    assert (
        world.client.post(
            "/fund-transactions", json={**bill, "direction": "in"}, headers=owner
        ).status_code
        == 422
    )
    no_cat = {k: v for k, v in bill.items() if k != "category_id"}
    assert world.client.post("/fund-transactions", json=no_cat, headers=owner).status_code == 422
    early = {**bill, "business_date": "2026-10-30"}
    r = world.client.post("/fund-transactions", json=early, headers=owner)
    assert r.status_code == 422 and "starts on" in r.json()["detail"]

    r = world.client.get(
        "/fund-entries",
        params={"date_from": "2026-11-01", "date_to": "2026-11-05", "show": "out"},
        headers=owner,
    )
    assert r.status_code == 200
    assert [e["description"] for e in r.json()["items"]] == ["Meralco October"]
    assert _accounts(world, owner)["Main/BPI bank transfer"]["balance"] == "-8450.75"

    # Moving the opening date past an existing entry is refused.
    body = {k: bpi[k] for k in ("name", "kind", "branch_id", "accepts_payments")}
    r = world.client.put(
        f"/accounts/{bpi['id']}", json={**body, "opening_date": "2026-11-10"}, headers=owner
    )
    assert r.status_code == 422
    r = world.client.put(
        f"/accounts/{bpi['id']}", json={**body, "opening_balance": "20000"}, headers=owner
    )
    assert r.status_code == 200 and r.json()["balance"] == "11549.25"
    # Desks and managers never see the bank.
    mgr = world.login("mgr@banahaw.ph")
    assert list(_accounts(world, mgr)) == ["Main/Petty cash"]
