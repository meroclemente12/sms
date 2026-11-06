"""Discount codes: setup, limits, applying at the desk, and usage."""

from tests.conftest import DAY, at
from tests.test_api import _book
from tests.test_api import world as world  # noqa: F401  (pytest fixture)
from tests.test_payments import _checkout, _in_service, _methods


def _code(world, h, **over):
    body = {"code": "bday20", "kind": "percent", "value": "20", **over}
    return world.client.post("/discount-codes", json=body, headers=h)


def _apply(world, h, booking_id, code):
    return world.client.put(f"/bookings/{booking_id}/discount", json={"code": code}, headers=h)


def test_crud_and_who_may_manage(world):
    owner = world.login("owner@banahaw.ph")
    mgr = world.login("mgr@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    main = str(world.a.branch.id)

    assert _code(world, desk).status_code == 403
    # A manager must limit the code to their own branch.
    assert _code(world, mgr).status_code == 403
    assert _code(world, mgr, branch_ids=[str(world.north.id)]).status_code == 403
    r = _code(world, mgr, branch_ids=[main], description="Birthday month")
    assert r.status_code == 201, r.text
    code = r.json()
    assert code["code"] == "BDAY20" and code["uses"] == 0
    assert _code(world, owner, code=" Bday20 ").status_code == 409
    assert _code(world, owner, code="BIG", value="150").status_code == 422
    r = _code(world, owner, code="LESS100", kind="fixed", value="100")
    assert r.status_code == 201
    spa_wide = r.json()

    # The desk can see active codes; the north manager does not see Main's code.
    assert {c["code"] for c in world.client.get("/discount-codes", headers=desk).json()} == {
        "BDAY20",
        "LESS100",
    }
    north = world.login("north.mgr@banahaw.ph")
    assert [c["code"] for c in world.client.get("/discount-codes", headers=north).json()] == [
        "LESS100"
    ]
    # Managers cannot change a spa-wide code.
    r = world.client.put(
        f"/discount-codes/{spa_wide['id']}",
        json={"code": "LESS100", "kind": "fixed", "value": "200"},
        headers=mgr,
    )
    assert r.status_code == 403
    r = world.client.put(
        f"/discount-codes/{code['id']}",
        json={"code": "BDAY20", "kind": "percent", "value": "25", "branch_ids": [main]},
        headers=mgr,
    )
    assert r.status_code == 200 and r.json()["value"] == "25.00"
    assert world.client.delete(f"/discount-codes/{code['id']}", headers=mgr).status_code == 204
    # Another spa never sees these codes.
    other = world.login("owner@other.ph", spa="other-spa")
    assert world.client.get("/discount-codes", headers=other).json() == []


def test_apply_at_checkout_and_usage(world):
    owner = world.login("owner@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    code = _code(world, owner, max_uses=2, once_per_client=True).json()

    b = _in_service(world, desk)
    r = world.client.post(
        f"/bookings/{b['id']}/discount/check", json={"code": "bday20"}, headers=desk
    )
    assert r.status_code == 200 and r.json()["amount"] == "160.00"
    assert _apply(world, desk, b["id"], "nope").status_code == 422
    r = _apply(world, desk, b["id"], "bday20")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["subtotal"], out["total"], out["discount"]["amount"]) == (
        "800.00",
        "640.00",
        "160.00",
    )
    assert out["events"][-1]["event_type"] == "discount.applied"

    cash = _methods(world, desk)["Cash"]["id"]
    r = _checkout(world, desk, b["id"], [{"method_id": cash, "amount": "640"}])
    assert r.status_code == 200 and r.json()["total"] == "640.00"
    # After check-out the discount is locked.
    r = world.client.delete(f"/bookings/{b['id']}/discount", headers=desk)
    assert r.status_code == 422

    # Same client again: refused (once per client).
    b2 = _book(world, desk, starts_at=at(12).isoformat())
    r = _apply(world, desk, b2["id"], "BDAY20")
    assert r.status_code == 422 and "already used" in r.json()["detail"]

    uses = world.client.get(f"/discount-codes/{code['id']}/uses", headers=owner).json()
    assert len(uses) == 1 and uses[0]["client_name"] == "Maria Santos"
    listed = {c["code"]: c for c in world.client.get("/discount-codes", headers=owner).json()}
    assert listed["BDAY20"]["uses"] == 1 and listed["BDAY20"]["discount_total"] == "160.00"
    # A used code cannot be deleted, only switched off.
    assert world.client.delete(f"/discount-codes/{code['id']}", headers=owner).status_code == 409


def test_limits_dates_branch_and_services(world):
    owner = world.login("owner@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    a = world.a

    _code(world, owner, code="ONCE", max_uses=1)
    b1 = _book(world, desk)
    b2 = _book(world, desk, starts_at=at(12).isoformat())
    assert _apply(world, desk, b1["id"], "ONCE").status_code == 200
    r = _apply(world, desk, b2["id"], "ONCE")
    assert r.status_code == 422 and "used up" in r.json()["detail"]
    # Cancelling the first booking frees the use.
    r = world.client.post(f"/bookings/{b1['id']}/status/cancelled", headers=desk)
    assert r.status_code == 200
    assert _apply(world, desk, b2["id"], "ONCE").status_code == 200

    _code(world, owner, code="LATER", starts_on=str(DAY.replace(day=DAY.day + 1)))
    r = _apply(world, desk, b2["id"], "LATER")
    assert r.status_code == 422 and "starts on" in r.json()["detail"]

    _code(world, owner, code="NORTH", branch_ids=[str(world.north.id)])
    assert "branch" in _apply(world, desk, b2["id"], "NORTH").json()["detail"]

    # Only couples massage: does not cover a Swedish booking.
    couples_service = str(a.couples60.service_id)
    _code(world, owner, code="DUO", kind="fixed", value="2000", service_ids=[couples_service])
    assert "does not cover" in _apply(world, desk, b2["id"], "DUO").json()["detail"]
    duo = _book(world, desk, variant_id=str(a.couples60.id), guests=[{}, {}])
    out = _apply(world, desk, duo["id"], "duo").json()
    # A fixed amount never takes off more than the bill.
    assert (out["discount"]["amount"], out["total"]) == ("1500.00", "0.00")

    # Replacing a code on the same booking keeps one code per booking.
    assert _code(world, owner).status_code == 201
    out = _apply(world, desk, b2["id"], "BDAY20").json()
    assert out["discount"]["code"] == "BDAY20"
    assert [e["event_type"] for e in out["events"]][-2:] == ["discount.removed", "discount.applied"]


def test_reschedule_keeps_discount(world):
    owner = world.login("owner@banahaw.ph")
    desk = world.login("desk@banahaw.ph")
    _code(world, owner)
    b = _book(world, desk)
    _apply(world, desk, b["id"], "BDAY20")
    # Moving the booking keeps the discount, recomputed from the items.
    r = world.client.post(
        f"/bookings/{b['id']}/reschedule",
        json={"starts_at": at(14).isoformat()},
        headers=desk,
    )
    assert r.status_code == 200, r.text
    assert r.json()["discount"]["amount"] == "160.00"
