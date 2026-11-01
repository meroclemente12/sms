"""HTTP API for the front desk."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, bookings, clients, payments, setup
from app.config import settings

app = FastAPI(title="Banahaw Reservations API", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(setup.router)
app.include_router(bookings.router)
app.include_router(clients.router)
app.include_router(payments.router)
