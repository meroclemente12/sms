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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
    # Checks cashless payments the front desk logged; cannot book or change setup.
    accountant = "accountant"


class PaymentKind(enum.StrEnum):
    cash = "cash"
    cashless = "cashless"


class DiscountKind(enum.StrEnum):
    percent = "percent"
    fixed = "fixed"


class AccountKind(enum.StrEnum):
    """What kind of place money sits in."""

    cash = "cash"  # the cash drawer at a branch
    bank = "bank"
    ewallet = "ewallet"  # GCash, Maya
    petty_cash = "petty_cash"


class FundDirection(enum.StrEnum):
    money_in = "in"
    money_out = "out"


class PaymentStatus(enum.StrEnum):
    """Where a payment is in the accountant's check. Cash is not checked."""

    not_needed = "not_needed"
    pending = "pending"
    verified = "verified"
    flagged = "flagged"


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
    # When one business day ends and the next begins (during closed hours). Sales,
    # payments, pay and the day board from this time until the same time the next
    # morning belong to one date, so a 1 PM to 1 AM branch keeps its night together.
    day_starts_at: Mapped[time] = mapped_column(Time, default=time(6, 0))
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
    # Paid to the therapist for each guest served with this option (set by its length).
    commission: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(0))
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


class TherapistAttendance(TimestampMixin, Base):
    """A stretch of time a therapist was clocked in (available for booking).

    The front desk clocks therapists in and out from the day board. Each day with at
    least one clock-in earns the daily allowance. At most one open row per therapist."""

    __tablename__ = "therapist_attendance"
    __table_args__ = (
        CheckConstraint(
            "clock_out_at IS NULL OR clock_out_at >= clock_in_at", name="attendance_in_order"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    therapist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("therapists.id", ondelete="CASCADE"), index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    # The branch's calendar day of the clock-in.
    work_date: Mapped[date] = mapped_column(Date)
    clock_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clock_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clocked_in_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    clocked_out_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


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
    # The therapist's commission, fixed when the service is completed so a later change
    # in Services setup does not rewrite past pay.
    commission: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

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
    # clock_timestamp(), not now(): events written in one transaction keep their order.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.clock_timestamp()
    )


# --- Payments ------------------------------------------------------------------------


class PaymentMethod(TimestampMixin, Base):
    """How a guest can pay at one branch: Cash, GCash, BPI transfer, card..."""

    __tablename__ = "payment_methods"
    __table_args__ = (UniqueConstraint("branch_id", "name"), _enum_check("kind", PaymentKind))

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default=PaymentKind.cashless)
    # Ask the desk for the transaction / reference number when this method is used.
    needs_reference: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The fund account the money lands in. Methods are made and kept in step from the
    # account (Setup > Accounts), one per branch where guests can pay into it.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fund_accounts.id", ondelete="SET NULL"), index=True
    )


class Payment(TimestampMixin, Base):
    """Money received for a booking, logged by the desk and (if cashless) checked later."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="payment_positive"),
        _enum_check("status", PaymentStatus),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    method_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_methods.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    reference: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.pending)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.clock_timestamp()
    )
    checked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_note: Mapped[str | None] = mapped_column(Text)

    method: Mapped[PaymentMethod] = relationship()


# --- Discounts -----------------------------------------------------------------------


class DiscountCode(TimestampMixin, Base):
    """A code the desk types at check-out, e.g. BDAY20 for 20% off.

    Empty ``branch_ids`` / ``service_ids`` mean every branch / every service."""

    __tablename__ = "discount_codes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        _enum_check("kind", DiscountKind),
        CheckConstraint("value > 0", name="discount_positive"),
        CheckConstraint("kind <> 'percent' OR value <= 100", name="percent_at_most_100"),
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="max_uses_positive"),
        CheckConstraint(
            "starts_on IS NULL OR ends_on IS NULL OR ends_on >= starts_on", name="dates_in_order"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    # Stored upper case; the desk can type it in any case.
    code: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default=DiscountKind.percent)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    once_per_client: Mapped[bool] = mapped_column(Boolean, default=False)
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}", default=list
    )
    service_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}", default=list
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DiscountRedemption(Base):
    """A code used on a booking. One code per booking. A cancelled or no-show booking
    keeps its row for the record but no longer counts as a use."""

    __tablename__ = "discount_redemptions"
    __table_args__ = (
        UniqueConstraint("booking_id"),
        CheckConstraint("amount > 0", name="redemption_positive"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    code_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discount_codes.id", ondelete="RESTRICT"), index=True
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    redeemed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    code: Mapped[DiscountCode] = relationship()


# --- Therapist pay -------------------------------------------------------------------


class QuotaBasis(enum.StrEnum):
    sales = "sales"  # peso amount of completed services
    services = "services"  # number of completed services


class PaySettings(TimestampMixin, Base):
    """Spa-wide pay rules: daily allowance and the weekly sales quota bonus.

    ``quota_tiers`` is a list of ``{"threshold": n, "bonus": amount}``; a therapist earns
    the bonus of the highest threshold reached in the week (not the sum)."""

    __tablename__ = "pay_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        _enum_check("quota_basis", QuotaBasis),
        CheckConstraint("daily_allowance >= 0", name="allowance_not_negative"),
        CheckConstraint("week_starts_on BETWEEN 0 AND 6", name="week_day_allowed"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    daily_allowance: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal(250))
    quota_basis: Mapped[str] = mapped_column(String(20), default=QuotaBasis.sales)
    quota_tiers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
    # 0 = Monday ... 6 = Sunday
    week_starts_on: Mapped[int] = mapped_column(Integer, default=0)


# --- Accounting ----------------------------------------------------------------------


class FundAccount(TimestampMixin, Base):
    """Where money is kept: a branch's cash drawer or petty cash, a bank account, GCash.

    Every account belongs to one branch (the same bank account used by two branches is
    set up once per branch). The balance is the opening balance plus the ledger
    (fund_transactions) plus guest payments made with a payment method linked to it."""

    __tablename__ = "fund_accounts"
    __table_args__ = (
        UniqueConstraint("branch_id", "name"),
        _enum_check("kind", AccountKind),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default=AccountKind.bank)
    account_number: Mapped[str | None] = mapped_column(String(100))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal(0))
    opening_date: Mapped[date] = mapped_column(Date)
    # Guests can pay into it at check-out (a payment method is kept for it).
    accepts_payments: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_reference: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FundCategory(TimestampMixin, Base):
    """What a transaction was for: Laundry, Electricity, Salaries, Other income..."""

    __tablename__ = "fund_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        _enum_check("direction", FundDirection),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(3), default=FundDirection.money_out)
    # The front desk may log petty cash expenses in this category.
    desk_can_use: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FundTransaction(TimestampMixin, Base):
    """One movement of money into or out of one account.

    A transfer (petty cash refill, cash deposited to the bank) is two rows sharing a
    ``transfer_id``: out of one account and into the other. Guest payments are not
    copied here; they are read from ``payments``. Nothing is deleted: a mistake is
    voided, with a reason, and stops counting."""

    __tablename__ = "fund_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="fund_amount_positive"),
        _enum_check("direction", FundDirection),
        Index(
            "uq_fund_transactions_occurrence",
            "schedule_id",
            "due_date",
            "account_id",
            unique=True,
            postgresql_where=text("schedule_id IS NOT NULL AND voided_at IS NULL"),
        ),
        Index(
            "uq_fund_transactions_payout",
            "therapist_id",
            "pay_week_start",
            unique=True,
            postgresql_where=text("therapist_id IS NOT NULL AND voided_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fund_accounts.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # The business date it belongs to (see app.business_day).
    business_date: Mapped[date] = mapped_column(Date, index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fund_categories.id"))
    description: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(100))
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.clock_timestamp()
    )
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    # Posted from a recurring schedule, for the occurrence due on due_date.
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fund_schedules.id"), index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    # Paid out of money set aside (an allocation), which it then reduces.
    allocation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fund_allocations.id"), index=True
    )
    # A therapist's weekly pay (see app.pay), paid for the week starting pay_week_start.
    therapist_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("therapists.id"))
    pay_week_start: Mapped[date | None] = mapped_column(Date)


class ScheduleKind(enum.StrEnum):
    expense = "expense"
    income = "income"
    transfer = "transfer"


class Frequency(enum.StrEnum):
    weekly = "weekly"
    monthly = "monthly"


class FundSchedule(TimestampMixin, Base):
    """A transaction that comes back every week or month: rent, electricity, the petty
    cash refill. On each due date it shows as Due; an admin posts it (with the real
    amount) or skips it. Nothing is posted by itself."""

    __tablename__ = "fund_schedules"
    __table_args__ = (
        _enum_check("kind", ScheduleKind),
        _enum_check("frequency", Frequency),
        CheckConstraint("amount > 0", name="schedule_amount_positive"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_allowed"),
        CheckConstraint("day_of_month BETWEEN 1 AND 31", name="day_of_month_allowed"),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10), default=ScheduleKind.expense)
    # expense: paid from; income: received in; transfer: from
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fund_accounts.id"))
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fund_accounts.id"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fund_categories.id"))
    # The usual amount; it can be changed when posting.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    frequency: Mapped[str] = mapped_column(String(10), default=Frequency.monthly)
    # weekly: 0 = Monday ... 6 = Sunday
    weekday: Mapped[int] = mapped_column(Integer, default=0)
    # monthly: 29-31 fall on the last day in shorter months
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    skipped: Mapped[list[date]] = mapped_column(ARRAY(Date), server_default="{}", default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FundAllocation(TimestampMixin, Base):
    """Money set aside inside one account for a future cost (bed maintenance, the
    Christmas party). The money stays in the account; the account's available balance
    is its balance minus what is set aside. The amount set aside is the sum of its
    moves minus what was paid out of it (fund_transactions.allocation_id)."""

    __tablename__ = "fund_allocations"
    __table_args__ = (UniqueConstraint("account_id", "name"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fund_accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    target_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    target_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class FundAllocationMove(Base):
    """Money put aside into an allocation (positive) or released back (negative)."""

    __tablename__ = "fund_allocation_moves"
    __table_args__ = (CheckConstraint("amount <> 0", name="move_not_zero"),)

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    allocation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fund_allocations.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    business_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.clock_timestamp()
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
