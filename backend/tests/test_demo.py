"""The demo spa that `docker compose up` loads must be usable straight away."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.main import app
from app.demo import DEMO_PASSWORD, DEMO_SLUG, seed_demo


@pytest.fixture()
def demo(engine):
    assert seed_demo() is True
    yield TestClient(app)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenants WHERE slug = :s"), {"s": DEMO_SLUG})


def test_demo_spa_is_ready_to_book(demo):
    assert seed_demo() is False  # running it again changes nothing

    token = demo.post(
        "/auth/login",
        json={"spa": DEMO_SLUG, "email": "desk@banahaw.ph", "password": DEMO_PASSWORD},
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    me = demo.get("/auth/me", headers=h).json()
    assert me["role"] == "front_desk"
    branch_id = me["branches"][0]["id"]

    therapists = demo.get(f"/branches/{branch_id}/therapists", headers=h).json()
    assert len(therapists) == 6
    rooms = demo.get(f"/branches/{branch_id}/rooms", headers=h).json()
    assert sum(len(r["beds"]) for r in rooms) == 9

    services = {s["name"]: s for s in demo.get("/services", headers=h).json()}
    swedish = services["Swedish massage"]["variants"][0]
    tomorrow = datetime.now(ZoneInfo("Asia/Manila")).date() + timedelta(days=1)
    start = datetime.combine(tomorrow, time(14, 0), tzinfo=ZoneInfo("Asia/Manila"))
    pairs = demo.get(
        f"/branches/{branch_id}/availability",
        params={"variant_id": swedish["id"], "starts_at": start.isoformat()},
        headers=h,
    ).json()
    assert pairs, "tomorrow afternoon should have a free therapist and bed"
