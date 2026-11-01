# Banahaw Reservations

Front desk reservation system for Banahaw Heals Spa, built as a multi-tenant SaaS
(each spa business is a tenant with many branches).

Design doc: https://claude.ai/artifact/6pfMAsBWf1NBC9jntFHKb6 · Phase 1 backlog: [docs/phase1-backlog.md](docs/phase1-backlog.md)

## Layout

| Path | What |
| --- | --- |
| `backend/` | Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic |
| `backend/app/models.py` | Domain model (tenants, branches, rooms, beds, services, therapists, bookings) |
| `backend/app/scheduling.py` | Availability engine: valid therapist + bed pairs, couples bookings, free times, moving bookings |
| `backend/app/lifecycle.py` | Booking status transitions with history |
| `backend/app/api/` | HTTP API: `auth.py` (sign-in, staff), `setup.py` (setup screens), `bookings.py` (day board, bookings), `clients.py` |
| `backend/app/cli.py` | `create-tenant` command |
| `backend/alembic/versions/0001_initial_schema.py` | Schema, double-booking exclusion constraints, row-level security |
| `frontend/` | React + TypeScript (Vite) front desk app: day board, bookings, walk-ins, clients, setup |

## Front desk screens

- **Day board** (home page): one column per therapist (or per bed), today's bookings
  coloured by status, hatched where a therapist is off shift. Click an empty time to book
  it, drag a booking to move it, click a booking to open it. Refreshes every 20 seconds.
- **New booking**: channel (phone, email, ...), client search by mobile or name (or add
  a new one), service, optional therapist or gender per guest, then pick from the free
  times the system finds. Couples services book two therapists in one couples room.
- **Walk-in**: the earliest free times from now; booking checks the guest in at once.
- **Booking panel**: confirm, check in, start, complete (with the amount paid), cancel
  (with a reason), no-show, change time / therapist / length, edit notes or channel,
  and the full history of who changed what.
- **Clients**: search, notes, allergies (shown in red wherever the client appears) and
  visit history across branches.

Prices on the menu are per booking: a couples massage price covers both guests.

## How double-booking is prevented

Each booking item stores the time it blocks (service plus cleanup buffer) as a
`tstzrange`. PostgreSQL exclusion constraints refuse any two active items that overlap
on the same therapist or the same bed, so two desks clicking at once cannot both win.

## Try it with Docker (easiest)

You only need [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
docker compose up --build
```

The first run takes a few minutes. When the log says `Uvicorn running`, open
http://localhost:8080 and sign in:

| Spa code | Email | Password | Role |
| --- | --- | --- | --- |
| `banahaw` | `owner@banahaw.ph` | `banahaw-demo` | Admin |
| `banahaw` | `manager@banahaw.ph` | `banahaw-demo` | Manager |
| `banahaw` | `desk@banahaw.ph` | `banahaw-demo` | Front desk |

The demo spa has one branch (Main) with four single rooms, a couples suite and a foot
spa lounge, a sample service menu, six therapists with shifts for the next two weeks,
and a few sample bookings today and tomorrow.
The API and its docs are at http://localhost:8000/docs.

- Stop: `Ctrl+C`, or `docker compose down` if you started it with `-d`.
- Start over with an empty database: `docker compose down -v`, then `up --build` again.
- After pulling new code, run `docker compose up --build` again. Your data is kept.
- If port 5432, 8000 or 8080 is already in use on your computer, change the left-hand
  number under `ports:` in `docker-compose.yml` (for 8000, also update `VITE_API_URL`
  and `CORS_ORIGINS`).

The Docker setup is for trying the app on your own computer only. Its passwords and
signing secret are public, so never use it for real client data.

## Run locally for development

```bash
docker compose up -d db
cd backend
uv sync
export DATABASE_URL=postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw
uv run alembic upgrade head
# Create your spa and its first admin (asks for a password):
uv run python -m app.cli create-tenant --name "Banahaw Heals" --slug banahaw \
    --admin-email you@example.com --admin-name "Your Name"
uv run uvicorn app.api.main:app --reload     # http://localhost:8000/docs

cd ../frontend
npm install
npm run dev                                   # http://localhost:5173
```

Sign in with spa code `banahaw`, then use the Setup pages: branches and rooms,
services and prices, therapists, shifts and time off, booking channels, staff accounts.

### Settings (environment variables)

| Name | What | Default |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL connection | local docker-compose database |
| `JWT_SECRET` | Signs sign-in tokens; set a long random value in production (`openssl rand -hex 32`) | insecure dev value |
| `ACCESS_TOKEN_HOURS` | How long a sign-in lasts | 12 |
| `CORS_ORIGINS` | Comma-separated web app origins allowed to call the API | `http://localhost:5173` |
| `VITE_API_URL` (frontend) | Where the web app finds the API | `http://localhost:8000` |

## Who can do what

| Role | Can |
| --- | --- |
| Front desk | Book guests and view setup at their branches |
| Manager | Also change rooms, beds, prices, therapists, shifts and time off at their branches |
| Admin | Everything at every branch, plus branches, the service menu, bed types, booking channels and staff accounts |

Every API request runs as the `banahaw_app` database role (created by migration 0003),
so PostgreSQL row-level security keeps each spa's data separate even if the app
connects as the table owner.

## Tests

```bash
cd backend
export TEST_DATABASE_URL=postgresql+psycopg://banahaw:banahaw@localhost:5432/banahaw_test
uv run pytest
```

Tests need a PostgreSQL 16 database they can drop and recreate.
