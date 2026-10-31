import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import models as m
from app.api.main import app
from tests.conftest import at, make_spa


@pytest.fixture()
def seeded(engine):
    with Session(engine) as s:
        spa = make_spa(s)
        s.commit()
        yield spa
        s.execute(delete(m.Tenant).where(m.Tenant.id == spa.tenant.id))
        s.commit()


def test_phone_booking_auto_picks_pair_and_conflict_is_409(seeded):
    spa = seeded
    client = TestClient(app)
    h = {"X-Tenant-ID": str(spa.tenant.id)}
    body = {
        "branch_id": str(spa.branch.id),
        "client_id": str(spa.client.id),
        "channel_id": str(spa.phone.id),
        "variant_id": str(spa.swedish60.id),
        "starts_at": at(10).isoformat(),
        "guests": [{"therapist_id": str(spa.ana.id), "therapist_requested": True}],
    }
    r = client.post("/bookings", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["items"][0]["therapist_id"] == str(spa.ana.id)

    r2 = client.post("/bookings", json=body, headers=h)
    assert r2.status_code == 422  # Ana is no longer free at 10:00

    avail = client.get(
        f"/branches/{spa.branch.id}/availability",
        params={"variant_id": str(spa.swedish60.id), "starts_at": at(10).isoformat()},
        headers=h,
    )
    assert {p["therapist_id"] for p in avail.json()} == {str(spa.ben.id)}

    booking_id = r.json()["id"]
    assert client.post(f"/bookings/{booking_id}/status/completed", headers=h).status_code == 409
    assert client.post(f"/bookings/{booking_id}/status/cancelled", headers=h).status_code == 200
