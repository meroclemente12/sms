"""API tests: sign-in, roles, branch access, tenant isolation, setup screens, bookings.

These run against committed data (the API opens its own connections), so each test gets
two fresh spas and deletes them afterwards.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import models as m
from app.api.main import app
from app.security import hash_password
from tests.conftest import MNL, Spa, at, make_spa

PW = "correct-horse-1"


@dataclass
class World:
    a: Spa
    b: Spa
    north: m.Branch
    client: TestClient

    def login(self, email: str, spa: str = "banahaw-heals", password: str = PW) -> dict:
        r = self.client.post("/auth/login", json={"spa": spa, "email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user(s: Session, spa: Spa, email: str, role: str, branches=(), active=True) -> m.User:
    u = m.User(
        tenant_id=spa.tenant.id,
        email=email,
        name=email.split("@")[0].title(),
        role=role,
        password_hash=hash_password(PW),
        active=active,
    )
    s.add(u)
    s.flush()
    for b in branches:
        s.add(m.UserBranch(tenant_id=spa.tenant.id, user_id=u.id, branch_id=b.id))
    return u


@pytest.fixture()
def world(engine):
    with Session(engine, expire_on_commit=False) as s:
        a = make_spa(s)
        b = make_spa(s, name="Other Spa")
        north = m.Branch(tenant_id=a.tenant.id, name="North")
        s.add(north)
        s.flush()
        _user(s, a, "owner@banahaw.ph", "admin")
        _user(s, a, "mgr@banahaw.ph", "manager", [a.branch])
        _user(s, a, "desk@banahaw.ph", "front_desk", [a.branch])
        _user(s, a, "acct@banahaw.ph", "accountant", [a.branch])
        _user(s, a, "north.mgr@banahaw.ph", "manager", [north])
        _user(s, a, "gone@banahaw.ph", "front_desk", [a.branch], active=False)
        _user(s, b, "owner@other.ph", "admin")
        s.commit()
        yield World(a, b, north, TestClient(app))
        s.execute(delete(m.Tenant).where(m.Tenant.id.in_([a.tenant.id, b.tenant.id])))
        s.commit()


# --- Sign-in -------------------------------------------------------------------------


def test_login_and_me(world):
    h = world.login("Desk@Banahaw.ph", spa="BANAHAW-HEALS")  # case-insensitive
    me = world.client.get("/auth/me", headers=h).json()
    assert me["role"] == "front_desk"
    assert me["tenant_name"] == "Banahaw Heals"
    assert [b["name"] for b in me["branches"]] == ["Main"]


def test_admin_sees_all_branches(world):
    me = world.client.get("/auth/me", headers=world.login("owner@banahaw.ph")).json()
    assert {b["name"] for b in me["branches"]} == {"Main", "North"}


@pytest.mark.parametrize(
    "spa,email,password",
    [
        ("banahaw-heals", "desk@banahaw.ph", "wrong-password"),
        ("banahaw-heals", "nobody@banahaw.ph", PW),
        ("no-such-spa", "desk@banahaw.ph", PW),
        ("other-spa", "desk@banahaw.ph", PW),  # right password, wrong spa
        ("banahaw-heals", "gone@banahaw.ph", PW),  # deactivated
    ],
)
def test_bad_logins_are_refused_with_one_message(world, spa, email, password):
    r = world.client.post("/auth/login", json={"spa": spa, "email": email, "password": password})
    assert r.status_code == 401
    assert r.json()["detail"] == "Spa code, email or password is wrong"


def test_endpoints_need_a_valid_token(world):
    assert world.client.get("/auth/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert world.client.get("/auth/me", headers=bad).status_code == 401
    assert world.client.get("/services", headers=bad).status_code == 401


def test_change_password(world):
    h = world.login("desk@banahaw.ph")
    r = world.client.post(
        "/auth/password", headers=h, json={"current_password": "nope", "new_password": "x" * 10}
    )
    assert r.status_code == 422
    r = world.client.post(
        "/auth/password", headers=h, json={"current_password": PW, "new_password": "short"}
    )
    assert r.status_code == 422
    r = world.client.post(
        "/auth/password",
        headers=h,
        json={"current_password": PW, "new_password": "a-new-password"},
    )
    assert r.status_code == 204
    world.login("desk@banahaw.ph", password="a-new-password")


# --- Staff accounts ------------------------------------------------------------------


def test_admin_manages_users(world):
    h = world.login("owner@banahaw.ph")
    body = {
        "email": "New.Desk@banahaw.ph",
        "name": "New Desk",
        "role": "front_desk",
        "password": "welcome-123",
        "branch_ids": [str(world.north.id)],
    }
    r = world.client.post("/users", headers=h, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "new.desk@banahaw.ph"
    assert world.client.post("/users", headers=h, json=body).status_code == 409
    me = world.client.get(
        "/auth/me", headers=world.login("new.desk@banahaw.ph", password="welcome-123")
    ).json()
    assert [b["name"] for b in me["branches"]] == ["North"]
    users = world.client.get("/users", headers=h).json()
    assert "owner@other.ph" not in {u["email"] for u in users}  # other spa's staff hidden


def test_only_admin_manages_users_and_cannot_lock_themselves_out(world):
    assert world.client.get("/users", headers=world.login("mgr@banahaw.ph")).status_code == 403
    h = world.login("owner@banahaw.ph")
    me = world.client.get("/auth/me", headers=h).json()
    r = world.client.put(
        f"/users/{me['id']}",
        headers=h,
        json={"email": me["email"], "name": "Owner", "role": "manager"},
    )
    assert r.status_code == 422


# --- Roles and branch access ---------------------------------------------------------


def test_front_desk_reads_setup_but_cannot_change_it(world):
    h = world.login("desk@banahaw.ph")
    a = world.a
    assert world.client.get(f"/branches/{a.branch.id}/rooms", headers=h).status_code == 200
    r = world.client.post(f"/branches/{a.branch.id}/rooms", headers=h, json={"name": "Room Z"})
    assert r.status_code == 403
    assert world.client.get(f"/branches/{world.north.id}/rooms", headers=h).status_code == 403


def test_manager_changes_only_their_branch(world):
    h = world.login("mgr@banahaw.ph")
    ok = world.client.post(f"/branches/{world.a.branch.id}/rooms", headers=h, json={"name": "Z"})
    assert ok.status_code == 201
    other = world.client.post(f"/branches/{world.north.id}/rooms", headers=h, json={"name": "Z"})
    assert other.status_code == 403
    # Spa-wide lists are admin only.
    assert world.client.post("/bed-types", headers=h, json={"name": "Chair"}).status_code == 403


def test_one_spa_cannot_see_another(world):
    h = world.login("owner@other.ph", spa="other-spa")
    a = world.a
    assert world.client.get(f"/branches/{a.branch.id}/rooms", headers=h).status_code == 404
    assert world.client.get(f"/therapists/{a.ana.id}", headers=h).status_code in (404, 405)
    r = world.client.put(
        f"/therapists/{a.ana.id}", headers=h, json={"name": "Hacked", "gender": "female"}
    )
    assert r.status_code == 404
    names = {s["name"] for s in world.client.get("/services", headers=h).json()}
    assert names == {"Swedish massage", "Couples massage"}  # its own menu only
    branches = world.client.get("/branches", headers=h).json()
    assert {b["id"] for b in branches} == {str(world.b.branch.id)}


# --- Setup screens -------------------------------------------------------------------


def test_branch_room_bed_setup(world):
    h = world.login("owner@banahaw.ph")
    r = world.client.post("/branches", headers=h, json={"name": "South", "timezone": "Asia/Manila"})
    assert r.status_code == 201
    south = r.json()["id"]
    bad_tz = world.client.post("/branches", headers=h, json={"name": "X", "timezone": "Mars/Base"})
    assert bad_tz.status_code == 422
    bt = world.client.post("/bed-types", headers=h, json={"name": "Foot spa chair"}).json()
    assert (
        world.client.post("/bed-types", headers=h, json={"name": "Foot spa chair"}).status_code
        == 409
    )
    room = world.client.post(
        f"/branches/{south}/rooms", headers=h, json={"name": "Couples 1", "room_type": "couples"}
    ).json()
    for label in ("C1", "C2"):
        r = world.client.post(
            f"/rooms/{room['id']}/beds", headers=h, json={"label": label, "bed_type_id": bt["id"]}
        )
        assert r.status_code == 201
    rooms = world.client.get(f"/branches/{south}/rooms", headers=h).json()
    assert [b["label"] for b in rooms[0]["beds"]] == ["C1", "C2"]
    bed = rooms[0]["beds"][0]
    r = world.client.put(
        f"/beds/{bed['id']}",
        headers=h,
        json={"label": "C1", "bed_type_id": bt["id"], "active": False},
    )
    assert r.json()["active"] is False


def test_services_and_branch_prices(world):
    h = world.login("owner@banahaw.ph")
    a = world.a
    bed_type = world.client.get("/bed-types", headers=h).json()[0]["id"]
    svc = world.client.post(
        "/services",
        headers=h,
        json={"name": "Hilot", "category": "Massage", "bed_type_id": bed_type},
    ).json()
    assert svc["buffer_after_min"] == 20  # default cleanup
    v = world.client.post(
        f"/services/{svc['id']}/variants",
        headers=h,
        json={"label": "90 min", "duration_min": 90, "default_price": "1200"},
    ).json()

    mgr = world.login("mgr@banahaw.ph")
    r = world.client.put(
        f"/branches/{a.branch.id}/prices/{v['id']}", headers=mgr, json={"price": "1350"}
    )
    assert r.status_code == 204
    r = world.client.put(
        f"/branches/{a.branch.id}/prices/{a.swedish60.id}", headers=mgr, json={"offered": False}
    )
    assert r.status_code == 204
    prices = {
        p["variant_id"]: p
        for p in world.client.get(f"/branches/{a.branch.id}/prices", headers=mgr).json()
    }
    assert prices[v["id"]]["branch_price"] == "1350.00"
    assert prices[v["id"]]["default_price"] == "1200.00"
    assert prices[str(a.swedish60.id)]["offered"] is False
    north = world.client.get(f"/branches/{world.north.id}/prices", headers=h).json()
    assert all(p["branch_price"] is None and p["offered"] for p in north)


def test_therapists_and_skills(world):
    h = world.login("mgr@banahaw.ph")
    a = world.a
    services = {s["name"]: s["id"] for s in world.client.get("/services", headers=h).json()}
    r = world.client.post(
        f"/branches/{a.branch.id}/therapists",
        headers=h,
        json={
            "name": "Dina",
            "gender": "female",
            "display_colour": "#7a5cff",
            "skill_service_ids": [services["Swedish massage"]],
        },
    )
    assert r.status_code == 201, r.text
    dina = r.json()
    assert dina["skill_service_ids"] == [services["Swedish massage"]]
    r = world.client.put(
        f"/therapists/{dina['id']}",
        headers=h,
        json={
            "name": "Dina",
            "gender": "female",
            "skill_service_ids": [services["Couples massage"]],
        },
    )
    assert r.json()["skill_service_ids"] == [services["Couples massage"]]
    listed = world.client.get(f"/branches/{a.branch.id}/therapists", headers=h).json()
    assert {t["name"] for t in listed} == {"Ana", "Ben", "Cora", "Dina"}


def test_shifts_overlap_overnight_and_copy_week(world):
    h = world.login("mgr@banahaw.ph")
    a = world.a
    base = f"/branches/{a.branch.id}/shifts"
    mon = "2026-11-09"
    r = world.client.post(
        base,
        headers=h,
        json={"therapist_id": str(a.ana.id), "work_date": mon, "start": "09:00", "end": "18:00"},
    )
    assert r.status_code == 201
    assert datetime.fromisoformat(r.json()["starts_at"]) == datetime(2026, 11, 9, 1, tzinfo=UTC)
    clash = world.client.post(
        base,
        headers=h,
        json={"therapist_id": str(a.ana.id), "work_date": mon, "start": "17:00", "end": "20:00"},
    )
    assert clash.status_code == 409
    night = world.client.post(
        base,
        headers=h,
        json={"therapist_id": str(a.ben.id), "work_date": mon, "start": "20:00", "end": "02:00"},
    ).json()
    assert datetime.fromisoformat(night["ends_at"]) == datetime(2026, 11, 10, 2, tzinfo=MNL)
    copied = world.client.post(
        f"{base}/copy-week",
        headers=h,
        json={"from_week_start": mon, "to_week_start": "2026-11-16"},
    ).json()
    assert len(copied) == 2
    again = world.client.post(
        f"{base}/copy-week",
        headers=h,
        json={"from_week_start": mon, "to_week_start": "2026-11-16"},
    ).json()
    assert again == []  # already there, nothing doubled
    week = world.client.get(base, headers=h, params={"from": "2026-11-16", "to": "2026-11-22"})
    assert len(week.json()) == 2
    sid = week.json()[0]["id"]
    assert world.client.delete(f"/shifts/{sid}", headers=h).status_code == 204
    north_mgr = world.login("north.mgr@banahaw.ph")
    assert (
        world.client.delete(f"/shifts/{week.json()[1]['id']}", headers=north_mgr).status_code == 403
    )


def test_time_blocks(world):
    h = world.login("mgr@banahaw.ph")
    a = world.a
    both = {
        "therapist_id": str(a.ana.id),
        "bed_id": str(a.bed_a.id),
        "starts_at": at(12).isoformat(),
        "ends_at": at(13).isoformat(),
        "reason": "break",
    }
    assert world.client.post("/time-blocks", headers=h, json=both).status_code == 422
    brk = dict(both, bed_id=None)
    r = world.client.post("/time-blocks", headers=h, json=brk)
    assert r.status_code == 201
    maint = dict(both, therapist_id=None, reason="maintenance")
    assert world.client.post("/time-blocks", headers=h, json=maint).status_code == 201
    listed = world.client.get(
        f"/branches/{a.branch.id}/time-blocks",
        headers=h,
        params={"from": at(0).isoformat(), "to": at(23).isoformat()},
    ).json()
    assert {b["reason"] for b in listed} == {"break", "maintenance"}
    # The break now hides Ana from availability at noon.
    avail = world.client.get(
        f"/branches/{a.branch.id}/availability",
        headers=h,
        params={"variant_id": str(a.swedish60.id), "starts_at": at(12).isoformat()},
    ).json()
    assert str(a.ana.id) not in {p["therapist_id"] for p in avail}
    assert world.client.delete(f"/time-blocks/{r.json()['id']}", headers=h).status_code == 204


def test_booking_channels(world):
    h = world.login("owner@banahaw.ph")
    codes = {c["code"] for c in world.client.get("/channels", headers=h).json()}
    assert codes == {"phone", "email", "walk_in"}
    r = world.client.post("/channels", headers=h, json={"code": "messenger", "name": "Messenger"})
    assert r.status_code == 201
    assert (
        world.client.post(
            "/channels", headers=h, json={"code": "messenger", "name": "FB"}
        ).status_code
        == 409
    )
    phone = next(c for c in world.client.get("/channels", headers=h).json() if c["code"] == "phone")
    r = world.client.put(f"/channels/{phone['id']}", headers=h, json={"code": "tel", "name": "Tel"})
    assert r.status_code == 422
    desk = world.login("desk@banahaw.ph")
    assert len(world.client.get("/channels", headers=desk).json()) == 4
    assert (
        world.client.post("/channels", headers=desk, json={"code": "x", "name": "X"}).status_code
        == 403
    )


# --- Bookings ------------------------------------------------------------------------


def test_phone_booking_needs_branch_access_and_conflict_is_refused(world):
    a = world.a
    body = {
        "branch_id": str(a.branch.id),
        "client_id": str(a.client.id),
        "channel_id": str(a.phone.id),
        "variant_id": str(a.swedish60.id),
        "starts_at": at(10).isoformat(),
        "guests": [{"therapist_id": str(a.ana.id), "therapist_requested": True}],
    }
    north = world.login("north.mgr@banahaw.ph")
    assert world.client.post("/bookings", json=body, headers=north).status_code == 403

    h = world.login("desk@banahaw.ph")
    r = world.client.post("/bookings", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["items"][0]["therapist_id"] == str(a.ana.id)
    assert world.client.post("/bookings", json=body, headers=h).status_code == 422

    avail = world.client.get(
        f"/branches/{a.branch.id}/availability",
        params={"variant_id": str(a.swedish60.id), "starts_at": at(10).isoformat()},
        headers=h,
    )
    assert {p["therapist_id"] for p in avail.json()} == {str(a.ben.id)}

    booking_id = r.json()["id"]
    assert (
        world.client.post(f"/bookings/{booking_id}/status/completed", headers=h).status_code == 409
    )
    assert (
        world.client.post(f"/bookings/{booking_id}/status/cancelled", headers=h).status_code == 200
    )
    other = world.login("owner@other.ph", spa="other-spa")
    assert (
        world.client.post(f"/bookings/{booking_id}/status/cancelled", headers=other).status_code
        == 404
    )


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _book(world, h, **over) -> dict:
    a = world.a
    body = {
        "branch_id": str(a.branch.id),
        "client_id": str(a.client.id),
        "channel_id": str(a.phone.id),
        "variant_id": str(a.swedish60.id),
        "starts_at": at(10).isoformat(),
        "guests": [{}],
        **over,
    }
    r = world.client.post("/bookings", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_booking_detail_edit_and_history(world):
    a = world.a
    h = world.login("desk@banahaw.ph")
    b = _book(
        world,
        h,
        guests=[{"therapist_id": str(a.ana.id), "therapist_requested": True}],
        notes="Likes firm pressure",
    )
    assert b["client"]["name"] == "Maria Santos"
    assert b["channel_name"] == "Phone call"
    assert b["service_name"] == "Swedish massage" and b["total"] == "800.00"
    assert b["items"][0]["therapist_name"] == "Ana" and b["items"][0]["therapist_requested"]

    r = world.client.patch(
        f"/bookings/{b['id']}", json={"notes": "Firm", "channel_id": str(a.walk_in.id)}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "Firm" and r.json()["channel_name"] == "Walk-in"

    detail = world.client.get(f"/bookings/{b['id']}", headers=h).json()
    assert [e["event_type"] for e in detail["events"]] == ["booking.created", "booking.edited"]
    assert detail["events"][0]["user_name"] == "Desk"

    other = world.login("owner@other.ph", spa="other-spa")
    assert world.client.get(f"/bookings/{b['id']}", headers=other).status_code == 404
    # Another spa's client or channel cannot be attached to this booking.
    r = world.client.patch(
        f"/bookings/{b['id']}", json={"client_id": str(world.b.client.id)}, headers=h
    )
    assert r.status_code == 422


def test_slots_and_walk_in_check_in(world):
    a = world.a
    h = world.login("desk@banahaw.ph")
    body = {"variant_id": str(a.swedish60.id), "date": "2026-11-02", "guests": [{}], "limit": 3}
    r = world.client.post(f"/branches/{a.branch.id}/slots", json=body, headers=h)
    assert r.status_code == 200, r.text
    # The shifts start at 9:00, which is when the branch opens.
    assert [ts(s["starts_at"]) for s in r.json()] == [at(9), at(9, 15), at(9, 30)]
    body = {
        "variant_id": str(a.couples60.id),
        "after": at(13, 5).isoformat(),
        "guests": [{"preferred_gender": "female"}, {"preferred_gender": "male"}],
        "limit": 1,
    }
    r = world.client.post(f"/branches/{a.branch.id}/slots", json=body, headers=h)
    (slot,) = r.json()
    assert ts(slot["starts_at"]) == at(13, 15)
    assert {p["therapist_id"] for p in slot["pairs"]} <= {
        str(a.ana.id),
        str(a.cora.id),
        str(a.ben.id),
    }

    walk = _book(
        world,
        h,
        channel_id=str(a.walk_in.id),
        starts_at=at(13, 15).isoformat(),
        check_in_now=True,
    )
    assert walk["status"] == "checked_in"


def test_reschedule_cancel_and_checkout_over_the_api(world):
    a = world.a
    h = world.login("desk@banahaw.ph")
    b = _book(world, h, guests=[{"therapist_id": str(a.ana.id)}])
    _book(world, h, starts_at=at(15).isoformat(), guests=[{"therapist_id": str(a.ben.id)}])

    # Ben is busy at 15:00, so moving there with Ben is refused...
    r = world.client.post(
        f"/bookings/{b['id']}/reschedule",
        json={"starts_at": at(15).isoformat(), "guests": [{"therapist_id": str(a.ben.id)}]},
        headers=h,
    )
    assert r.status_code == 422
    # ...and the slots search for this booking treats its own 10:00 slot as free.
    r = world.client.post(
        f"/branches/{a.branch.id}/slots",
        json={
            "variant_id": str(a.swedish60.id),
            "after": at(10).isoformat(),
            "guests": [{"therapist_id": str(a.ana.id)}],
            "exclude_booking_id": b["id"],
            "limit": 1,
        },
        headers=h,
    )
    assert ts(r.json()[0]["starts_at"]) == at(10)

    r = world.client.post(
        f"/bookings/{b['id']}/reschedule", json={"starts_at": at(11).isoformat()}, headers=h
    )
    assert r.status_code == 200, r.text
    moved = r.json()
    assert ts(moved["starts_at"]) == at(11)
    assert moved["items"][0]["therapist_id"] == str(a.ana.id)

    for to in ("confirmed", "checked_in", "in_service"):
        assert world.client.post(f"/bookings/{b['id']}/status/{to}", headers=h).status_code == 200
    r = world.client.post(
        f"/bookings/{b['id']}/status/completed", json={"amount_paid": "850"}, headers=h
    )
    assert r.status_code == 200 and r.json()["amount_paid"] == "850"
    assert r.json()["events"][-1]["payload"]["amount_paid"] == "850"

    c = _book(world, h, starts_at=at(16).isoformat())
    r = world.client.post(
        f"/bookings/{c['id']}/status/cancelled", json={"reason": "Sick"}, headers=h
    )
    assert r.json()["status"] == "cancelled" and r.json()["cancel_reason"] == "Sick"


def test_day_board(world):
    a = world.a
    h = world.login("desk@banahaw.ph")
    b = _book(world, h, guests=[{"therapist_id": str(a.ana.id)}])
    gone = _book(world, h, starts_at=at(12).isoformat())
    world.client.post(f"/bookings/{gone['id']}/status/cancelled", headers=h)

    r = world.client.get(f"/branches/{a.branch.id}/board", params={"date": "2026-11-02"}, headers=h)
    assert r.status_code == 200, r.text
    board = r.json()
    assert [t["name"] for t in board["therapists"]] == ["Ana", "Ben", "Cora"]
    assert ts(board["therapists"][0]["shifts"][0]["starts_at"]) == at(9)
    assert len(board["beds"]) == 4
    (item,) = board["items"]
    assert item["booking_id"] == b["id"] and item["client_name"] == "Maria Santos"
    assert ts(item["blocked_until"]) == at(11, 20)

    r = world.client.get(
        f"/branches/{a.branch.id}/board",
        params={"date": "2026-11-02", "include_cancelled": True},
        headers=h,
    )
    assert len(r.json()["items"]) == 2
    r = world.client.get(f"/branches/{a.branch.id}/board", params={"date": "2026-11-03"}, headers=h)
    assert r.json()["items"] == [] and r.json()["therapists"][0]["shifts"] == []
    north = world.login("north.mgr@banahaw.ph")
    assert (
        world.client.get(
            f"/branches/{a.branch.id}/board", params={"date": "2026-11-02"}, headers=north
        ).status_code
        == 403
    )


# --- Clients ---------------------------------------------------------------------------


def test_client_search_create_and_history(world):
    a = world.a
    h = world.login("desk@banahaw.ph")
    # Maria's mobile is +639170000000; people type it many ways.
    for q in ("maria", "0917 000", "+63917000", "9170000000"):
        names = [c["name"] for c in world.client.get("/clients", params={"q": q}, headers=h).json()]
        assert names == ["Maria Santos"], q

    r = world.client.post(
        "/clients", json={"name": "Duplicate", "mobile": "0917-000-0000"}, headers=h
    )
    assert r.status_code == 409 and "Maria Santos" in r.json()["detail"]
    r = world.client.post(
        "/clients",
        json={"name": " Jun Cruz ", "mobile": "0918 555 1234", "allergies": "Peanut oil"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    jun = r.json()
    assert jun["name"] == "Jun Cruz"
    r = world.client.put(
        f"/clients/{jun['id']}",
        json={"name": "Jun Cruz", "mobile": "09185551234", "notes": "Prefers male therapist"},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["notes"] == "Prefers male therapist"

    _book(world, h, client_id=jun["id"], guests=[{"therapist_id": str(a.ben.id)}])
    detail = world.client.get(f"/clients/{jun['id']}", headers=h).json()
    (visit,) = detail["visits"]
    assert visit["therapist_name"] == "Ben" and visit["branch_name"] == "Main"

    other = world.login("owner@other.ph", spa="other-spa")
    assert world.client.get(f"/clients/{jun['id']}", headers=other).status_code == 404
    assert world.client.get("/clients", params={"q": "jun"}, headers=other).json() == []
