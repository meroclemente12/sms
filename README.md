# Banahaw Reservations

Front desk reservation system for Banahaw Heals Spa, built as a multi-tenant SaaS
(each spa business is a tenant with many branches).

Design doc: https://claude.ai/artifact/6pfMAsBWf1NBC9jntFHKb6 · Phase 1 backlog: [docs/phase1-backlog.md](docs/phase1-backlog.md)

## Layout

| Path | What |
| --- | --- |
| `backend/` | Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic |
| `backend/app/models.py` | Domain model (tenants, branches, rooms, beds, services, therapists, bookings) |
| `backend/app/scheduling.py` | Availability engine: valid therapist + bed pairs, couples bookings |
| `backend/app/lifecycle.py` | Booking status transitions with history |
| `backend/alembic/versions/0001_initial_schema.py` | Schema, double-booking exclusion constraints, row-level security |
| `frontend/` | React + TypeScript (Vite) front desk app |

## How double-booking is prevented

Each booking item stores the time it blocks (service plus cleanup buffer) as a
`tstzrange`. PostgreSQL exclusion constraints refuse any two active items that overlap
on the same therapist or the same bed, so two desks clicking at once cannot both win.

## Run locally

```bash
docker compose up -d db
cd backend
uv sync
export DATABASE_URL=postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw
uv run alembic upgrade head
uv run uvicorn app.api.main:app --reload     # http://localhost:8000/docs

cd ../frontend
npm install
npm run dev                                   # http://localhost:5173
```

## Tests

```bash
cd backend
export TEST_DATABASE_URL=postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw_test
uv run pytest
```

Tests need a PostgreSQL 16 database they can drop and recreate.
