# Phase 1 backlog: Front desk core

Each item below becomes one GitHub issue on the project board. Labels: `backend`, `frontend`, `infra`, `db`. Milestone: Phase 1.

## Foundation
1. **[infra] Monorepo scaffold** — `backend/` (Python 3.12, FastAPI, SQLAlchemy 2, Alembic), `frontend/` (React + TypeScript + Vite), docker-compose with PostgreSQL 16, README. Done when `docker compose up` serves the API and app locally.
2. **[infra] CI** — GitHub Actions: backend lint (ruff) + tests (pytest against Postgres), frontend lint + typecheck + build.
3. **[db] Core schema migration** — tenants, branches, rooms, beds, bed types, services, service variants, branch service prices, therapists, therapist skills, bed capabilities, shifts, time blocks, clients, booking channels, bookings, booking items, booking events, users.
4. **[db] Double-booking guards** — `tstzrange` on booking items + exclusion constraints per therapist and per bed (active statuses only, buffers included). Tests prove a concurrent double-book fails.
5. **[db] Tenant isolation** — tenant id on every table, PostgreSQL row-level security policies, API sets the tenant per request. Test: tenant A cannot read tenant B.
6. **[backend] Auth and roles** — email + password login, roles (front desk, manager, admin), per-branch access.

## Setup (manager)
7. **[backend+frontend] Branches, rooms, beds** — CRUD, active flag, room type, bed type.
8. **[backend+frontend] Services and branch pricing** — services, variants (60/90 min), 20-min default cleanup buffer, required bed type, per-branch price and offered flag.
9. **[backend+frontend] Therapists and skills** — CRUD, gender, home branch, skill checklist per service.
10. **[backend+frontend] Shifts and time blocks** — weekly shifts per therapist, breaks, leave, bed maintenance.
11. **[backend+frontend] Booking channels** — seeded Phone call, Email, Walk-in; managers can add more.

## Scheduling and booking
12. **[backend] Availability engine** — given branch, service variant, time window, optional therapist / gender: return valid (therapist, bed) pairs ranked (requested therapist, least-busy, best-fit bed). Unit tests for every hard rule.
13. **[backend] Couples bookings** — couples service needs one couples room with 2 free beds + 2 skilled therapists at the same time; one booking, two items, moved/cancelled together.
14. **[backend] Booking lifecycle** — Booked → Confirmed → Checked in → In service → Completed; Cancelled, No-show; reschedule; every change written to booking events.
15. **[backend] Live updates** — server-sent events per branch so every desk's board refreshes.

## Front desk screens
16. **[frontend] Day board** — lane per therapist (toggle per bed), today's bookings coloured by status, drag to reschedule, click empty slot to book.
17. **[frontend] New booking flow** — channel (phone / email), client search-or-create, service, time, optional therapist or gender, pick from suggested pairs. Target under 60 s.
18. **[frontend] Walk-in flow** — channel auto Walk-in, earliest free pairs from now, book + check in in one step. Target under 20 s.
19. **[frontend] Client lookup** — search by mobile or name, history, notes, allergies.
20. **[frontend] Check-in / check-out** — status buttons, amount paid note.

## Release
21. **[infra] Deploy** — Cloudflare Pages (frontend), Render Singapore (API), Neon Singapore (Postgres), env config, nightly backups check.
22. **[all] Pilot at one branch** — gate: desk runs a full week without paper.
