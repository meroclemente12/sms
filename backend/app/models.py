"""Domain model for the front desk reservation system.

Every table carries ``tenant_id`` so PostgreSQL row-level security can keep each
spa business's data apart (see the initial migration).
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)


class Gender(enum.StrEnum):
    female = "female"
    male = "male"
    other = "other"


class RoomType(enum.StrEnum):
    single = "single"
    couples = "couples"
    wet = "wet"


class BookingStatus(enum.StrEnum):
    booked = "booked"
    confirmed = "confirmed"
    checked_in = "checked_in"
    in_service = "in_service"
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"


# Statuses that occupy a therapist and a bed. Cancelled and no-show free the slot.
ACTIVE_STATUSES: tuple[str, ...] = (
    BookingStatus.booked,
    BookingStatus.confirmed,
    BookingStatus.checked_in,
    BookingStatus.in_service,
    BookingStatus.completed,
)


class UserRole(enum.StrEnum):
    front_desk = "front_desk"
    manager = "manager"
    admin = "admin"


def _enum_check(column: str, values: type[enum.StrEnum]) -> CheckConstraint:
    allowed = ", ".join(f"'{v.value}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_allowed")


# --- Tenancy -------------------------------------------------------------------------


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    # Short unique handle staff type at login (later also the SaaS subdomain).
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    plan: Mapped[str] = mapped_column(String(50), default="standard")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    default_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Manila")
    currency: Mapped[str] = mapped_column(String(3), default="PHP")


class Branch(TimestampMixin, Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Manila")
    opens_at: Mapped[time] = mapped_column(Time, default=time(9, 0))
    closes_at: Mapped[time] = mapped_column(Time, default=time(22, 0))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- Places --------------------------------------------------------------------------


class BedType(TimestampMixin, Base):
    """A kind of station: massage bed, foot spa chair, tub."""

    __tablename__ = "bed_types"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(100))


class Room(TimestampMixin, Base):
    __tablename__ = "rooms"
    __table_args__ = (_enum_check("room_type", RoomType),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    room_type: Mapped[str] = mapped_column(String(20), default=RoomType.single)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    beds: Mapped[list[Bed]] = relationship(back_populates="room")


class Bed(TimestampMixin, Base):
    __tablename__ = "beds"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    bed_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bed_types.id"))
    label: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    room: Mapped[Room] = relationship(back_populates="beds")


# --- Menu ----------------------------------------------------------------------------


class Service(TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (CheckConstraint("guest_count IN (1, 2)", name="guest_count_allowed"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100))
    bed_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bed_types.id"))
    buffer_before_min: Mapped[int] = mapped_column(Integer, default=0)
    buffer_after_min: Mapped[int] = mapped_column(Integer, default=20)
    # 2 = couples service: two guests side by side in one couples room.
    guest_count: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    variants: Mapped[list[ServiceVariant]] = relationship(back_populates="service")


class ServiceVariant(TimestampMixin, Base):
    __tablename__ = "service_variants"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    duration_min: Mapped[int] = mapped_column(Integer)
    default_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    service: Mapped[Service] = relationship(back_populates="variants")


class BranchServicePrice(TimestampMixin, Base):
    """Per-branch price override; ``offered = false`` hides the variant at that branch."""

    __tablename__ = "branch_service_prices"
    __table_args__ = (UniqueConstraint("branch_id", "service_variant_id"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    service_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("service_variants.id", ondelete="CASCADE")
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    offered: Mapped[bool] = mapped_column(Boolean, default=True)


# --- People --------------------------------------------------------------------------


class Therapist(TimestampMixin, Base):
    __tablename__ = "therapists"
    __table_args__ = (_enum_check("gender", Gender),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    gender: Mapped[str] = mapped_column(String(10))
    employment_status: Mapped[str] = mapped_column(String(30), default="regular")
    display_colour: Mapped[str | None] = mapped_column(String(7))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TherapistSkill(TimestampMixin, Base):
    __tablename__ = "therapist_skills"
    __table_args__ = (UniqueConstraint("therapist_id", "service_id"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    therapist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("therapists.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    level: Mapped[str | None] = mapped_column(String(30))


class Shift(TimestampMixin, Base):
    __tablename__ = "shifts"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="shift_positive"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    therapist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("therapists.id", ondelete="CASCADE"), index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"))
    work_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TimeBlock(TimestampMixin, Base):
    """Unavailable time for a therapist or a bed (break, leave, maintenance)."""

    __tablename__ = "time_blocks"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="block_positive"),
        CheckConstraint("(therapist_id IS NULL) <> (bed_id IS NULL)", name="block_one_resource"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    therapist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("therapists.id", ondelete="CASCADE"), index=True
    )
    bed_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("beds.id", ondelete="CASCADE"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(50))


class Client(TimestampMixin, Base):
    """A guest, shared across all branches of a tenant."""

    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    mobile: Mapped[str | None] = mapped_column(String(30), index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    gender: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)
    allergies: Mapped[str | None] = mapped_column(Text)


# --- Bookings ------------------------------------------------------------------------


class BookingChannel(TimestampMixin, Base):
    """How the reservation was made: Phone call, Email, Walk-in, or more added later."""

    __tablename__ = "booking_channels"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (_enum_check("status", BookingStatus),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("booking_channels.id"))
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.booked)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    items: Mapped[list[BookingItem]] = relationship(back_populates="booking")


class BookingItem(TimestampMixin, Base):
    """One service for one guest: therapist + bed + time.

    ``blocked_from``/``blocked_until`` include the service buffers. The ``during`` range is
    generated from them and guarded by exclusion constraints in the migration, so the
    database itself refuses a double-booked therapist or bed.
    """

    __tablename__ = "booking_items"
    __table_args__ = (
        _enum_check("status", BookingStatus),
        CheckConstraint("ends_at > starts_at", name="item_positive"),
        CheckConstraint(
            "blocked_from <= starts_at AND blocked_until >= ends_at", name="item_buffers"
        ),
        CheckConstraint(
            "preferred_gender IS NULL OR preferred_gender IN ('female', 'male')",
            name="preferred_gender_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    guest_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"))
    guest_name: Mapped[str | None] = mapped_column(String(200))
    service_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_variants.id"))
    therapist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("therapists.id"), index=True)
    bed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("beds.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.booked)
    therapist_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_gender: Mapped[str | None] = mapped_column(String(10))

    booking: Mapped[Booking] = relationship(back_populates="items")
    bed: Mapped[Bed] = relationship()


class BookingEvent(Base):
    """Append-only history of every change to a booking."""

    __tablename__ = "booking_events"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Access --------------------------------------------------------------------------


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"), _enum_check("role", UserRole))

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    email: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.front_desk)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserBranch(Base):
    __tablename__ = "user_branches"

    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True
    )


TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(
    t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c
)
