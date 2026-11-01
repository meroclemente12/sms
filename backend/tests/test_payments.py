"""Cashless payments: methods per branch, logging at check-out, the accountant's check."""

from tests.conftest import at
from tests.test_api import _book
from tests.test_api import world as world  # noqa: F401  (pytest fixture)


def _methods(world, h, branch_id=None) -> dict[str, dict]:
    bid = branch_id or world.a.branch.id
    r = world.client.get(f"/branches/{bid}/payment-methods", headers=h)
    assert r.status_code == 200, r.text
    return {m["name"]: m for m in r.json()}


def _in_service(world, h, **over) -> dict:
    b = _book(world, h, **over)
    for to in ("checked_in", "in_service"):
        r = world.client.post(f"/bookings/{b['id']}/status/{to}", headers=h)
        assert r.status_code == 200, r.text
    return b


def _checkout(world, h, booking_id, payments):
    return world.client.post(
        f"/bookings/{booking_id}/status/completed", json={"payments": payments}, headers=h
    )


def test_payment_methods_per_branch(world):
    desk = world.login("desk@banahaw.ph")
    mgr = world.login("mgr@banahaw.ph")
    methods = _methods(world, desk)
    assert list(methods) == ["Cash", "GCash", "BPI bank transfer"]
    assert methods["Cash"]["kind"] == "cash" and not methods["Cash"]["needs_reference"]
    assert methods["GCash"]["needs_reference"]

    body = {"name": "Maya", "kind": "cashless", "sort_order": 3}
    bid = world.a.branch.id
    r = world.client.post(f"/branches/{bid}/payment-methods", json=body, headers=desk)
    assert r.status_code == 403
    r = world.client.post(f"/branches/{bid}/payment-methods", json=body, headers=mgr)
    assert r.status_code == 201, r.text
    r = world.client.post(f"/branches/{bid}/payment-methods", json=body, headers=mgr)
    assert r.status_code == 409
    # A manager of another branch cannot touch this one.
    north = world.login("north.mgr@banahaw.ph")
    maya = r.json() if r.status_code == 201 else _methods(world, mgr)["Maya"]
    r = world.client.put(
        f"/payment-methods/{maya['id']}", json={**body, "active": False}, headers=north
    )
    assert r.status_code == 403
    # Cash never asks for a reference, whatever is sent.
    r = world.client.put(
        f"/payment-methods/{maya['id']}",
        json={"name": "Maya", "kind": "cash", "needs_reference": True},
        headers=mgr,
    )
    assert r.json()["needs_reference"] is False


def test_checkout_logs_split_payment_and_needs_reference(world):
    h = world.login("desk@banahaw.ph")
    methods = _methods(world, h)
    b = _in_service(world, h)

    # GCash without a reference is refused, and the booking stays in service.
    r = _checkout(world, h, b["id"], [{"method_id": methods["GCash"]["id"], "amount": "800"}])
    assert r.status_code == 422 and "reference" in r.json()["detail"]
    assert world.client.get(f"/bookings/{b['id']}", headers=h).json()["status"] == "in_service"

    r = _checkout(
        world,
        h,
        b["id"],
        [
            {"method_id": methods["Cash"]["id"], "amount": "300"},
            {"method_id": methods["GCash"]["id"], "amount": "550", "reference": " 1012 345 "},
        ],
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "completed"
    assert out["amount_paid"] == "850.00"
    cash, gcash = out["payments"]
    assert (cash["method_name"], cash["status"]) == ("Cash", "not_needed")
    assert (gcash["method_name"], gcash["status"], gcash["reference"]) == (
        "GCash",
        "pending",
        "1012 345",
    )
    assert gcash["recorded_by_name"] == "Desk"
    types = [e["event_type"] for e in out["events"]]
    assert types[-3:] == ["booking.completed", "payment.recorded", "payment.recorded"]

    # Another payment later (e.g. a tip or the rest of a bill), on the same booking.
    r = world.client.post(
        f"/bookings/{b['id']}/payments",
        json={"method_id": methods["BPI bank transfer"]["id"], "amount": "50", "reference": "X9"},
        headers=h,
    )
    assert r.status_code == 201 and r.json()["status"] == "pending"

    # Another spa's method cannot be used.
    other = _methods(world, world.login("owner@other.ph", spa="other-spa"), world.b.branch.id)
    r = world.client.post(
        f"/bookings/{b['id']}/payments",
        json={"method_id": other["Cash"]["id"], "amount": "50"},
        headers=h,
    )
    assert r.status_code == 422


def test_accountant_verifies_and_flags_and_desk_corrects(world):
    desk = world.login("desk@banahaw.ph")
    acct = world.login("acct@banahaw.ph")
    methods = _methods(world, desk)
    b = _in_service(world, desk)
    r = _checkout(
        world,
        desk,
        b["id"],
        [
            {"method_id": methods["Cash"]["id"], "amount": "100"},
            {"method_id": methods["GCash"]["id"], "amount": "400", "reference": "G1"},
            {"method_id": methods["BPI bank transfer"]["id"], "amount": "300", "reference": "B1"},
        ],
    )
    assert r.status_code == 200, r.text
    _, gcash, bpi = r.json()["payments"]
    url = f"/branches/{world.a.branch.id}/payments"

    # The front desk cannot see the check list or verify.
    assert world.client.get(url, headers=desk).status_code == 403
    assert world.client.post(f"/payments/{gcash['id']}/verify", headers=desk).status_code == 403
    # The accountant cannot book.
    r = world.client.post(
        "/bookings",
        json={
            "branch_id": str(world.a.branch.id),
            "client_id": str(world.a.client.id),
            "channel_id": str(world.a.phone.id),
            "variant_id": str(world.a.swedish60.id),
            "starts_at": at(15).isoformat(),
            "guests": [{}],
        },
        headers=acct,
    )
    assert r.status_code == 403

    listing = world.client.get(url, headers=acct).json()
    assert [p["method_name"] for p in listing["items"]] == ["BPI bank transfer", "GCash"]
    assert listing["items"][0]["client_name"] == "Maria Santos"
    assert listing["items"][0]["service_name"] == "Swedish massage"
    assert listing["pending"] == 2
    assert {t["method_name"]: t["amount"] for t in listing["totals"]} == {
        "Cash": "100.00",
        "GCash": "400.00",
        "BPI bank transfer": "300.00",
    }

    r = world.client.post(f"/payments/{gcash['id']}/verify", headers=acct)
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert r.json()["checked_by_name"] == "Acct" and r.json()["checked_at"]
    # A flag needs a note.
    assert (
        world.client.post(f"/payments/{bpi['id']}/flag", json={}, headers=acct).status_code == 422
    )
    r = world.client.post(
        f"/payments/{bpi['id']}/flag", json={"note": "Not in the BPI statement"}, headers=acct
    )
    assert r.json()["status"] == "flagged"

    # Filters.
    assert [
        p["id"]
        for p in world.client.get(url, params={"status": "flagged"}, headers=acct).json()["items"]
    ] == [bpi["id"]]
    cash = world.client.get(url, params={"status": "cash"}, headers=acct).json()["items"]
    assert [p["method_name"] for p in cash] == ["Cash"]
    r = world.client.post(f"/payments/{cash[0]['id']}/verify", headers=acct)
    assert r.status_code == 422  # cash is not checked

    # The desk sees the flag on the booking and corrects the reference: back to pending.
    booking = world.client.get(f"/bookings/{b['id']}", headers=desk).json()
    flagged = next(p for p in booking["payments"] if p["status"] == "flagged")
    assert flagged["check_note"] == "Not in the BPI statement"
    r = world.client.put(
        f"/payments/{bpi['id']}",
        json={"method_id": methods["BPI bank transfer"]["id"], "amount": "300", "reference": "B2"},
        headers=desk,
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert r.json()["reference"] == "B2"
    # A verified payment is locked for the desk...
    r = world.client.put(
        f"/payments/{gcash['id']}",
        json={"method_id": methods["GCash"]["id"], "amount": "1", "reference": "G1"},
        headers=desk,
    )
    assert r.status_code == 422
    # ...until the accountant reopens it.
    r = world.client.post(f"/payments/{gcash['id']}/reopen", headers=acct)
    assert r.json()["status"] == "pending" and r.json()["checked_by_name"] is None

    events = world.client.get(f"/bookings/{b['id']}", headers=desk).json()["events"]
    assert [e["event_type"] for e in events if e["event_type"].startswith("payment.")][-4:] == [
        "payment.verified",
        "payment.flagged",
        "payment.corrected",
        "payment.reopened",
    ]

    # Other spas and branches see nothing.
    other = world.login("owner@other.ph", spa="other-spa")
    assert world.client.post(f"/payments/{gcash['id']}/verify", headers=other).status_code == 404
    north = world.login("north.mgr@banahaw.ph")
    assert world.client.get(url, headers=north).status_code == 403


def test_manager_cannot_check_own_payment(world):
    mgr = world.login("mgr@banahaw.ph")
    methods = _methods(world, mgr)
    b = _in_service(world, mgr)
    r = _checkout(
        world,
        mgr,
        b["id"],
        [{"method_id": methods["GCash"]["id"], "amount": "800", "reference": "M"}],
    )
    pid = r.json()["payments"][0]["id"]
    r = world.client.post(f"/payments/{pid}/verify", headers=mgr)
    assert r.status_code == 422 and "Someone else" in r.json()["detail"]
    owner = world.login("owner@banahaw.ph")
    assert world.client.post(f"/payments/{pid}/verify", headers=owner).status_code == 200
