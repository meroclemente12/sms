"""Setup screens' API: branches, rooms, beds, services, prices, therapists, skills,
shifts, time blocks and booking channels.

Who can change what:
  * spa-wide lists (branches, bed types, services, booking channels): admin;
  * one branch's rooms, beds, prices, therapists, shifts and time blocks: a manager of
    that branch, or an admin;
  * every signed-in staff member can read what belongs to their branches.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminSession, ManagerSession, Staff, StaffSession, ensure_branch
from app.models import (
    Bed,
    BedType,
    BookingChannel,
    Branch,
    BranchServicePrice,
    Room,
    RoomType,
    Service,
    ServiceVariant,
    Shift,
    Therapist,
    TherapistSkill,
    TimeBlock,
)

router = APIRouter(tags=["setup"])


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _get(session: Session, model, obj_id: uuid.UUID, what: str):
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{what} not found")
    return obj


def _flush(session: Session, conflict: str = "That name is already used") -> None:
    try:
        session.flush()
    except IntegrityError as e:
        raise HTTPException(409, conflict) from e


def _apply(obj, data: BaseModel, exclude: set[str] | None = None) -> None:
    for key, value in data.model_dump(exclude=exclude or set()).items():
        setattr(obj, key, value)


# --- Branches ------------------------------------------------------------------------


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    timezone: str = "Asia/Manila"
    opens_at: time = time(9, 0)
    closes_at: time = time(22, 0)
    active: bool = True

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError("Unknown timezone") from e
        return v


class BranchOut(Out):
    id: uuid.UUID
    name: str
    address: str | None
    timezone: str
    opens_at: time
    closes_at: time
    active: bool


@router.get("/branches", response_model=list[BranchOut])
def list_branches(ctx: StaffSession) -> list[Branch]:
    session, staff = ctx
    rows = session.scalars(select(Branch).order_by(Branch.name))
    return [b for b in rows if staff.can_access_branch(b.id)]


@router.post("/branches", response_model=BranchOut, status_code=201)
def create_branch(ctx: AdminSession, body: BranchIn) -> Branch:
    session, staff = ctx
    branch = Branch(tenant_id=staff.tenant_id)
    _apply(branch, body)
    session.add(branch)
    _flush(session)
    return branch


@router.put("/branches/{branch_id}", response_model=BranchOut)
def update_branch(ctx: ManagerSession, branch_id: uuid.UUID, body: BranchIn) -> Branch:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    branch = _get(session, Branch, branch_id, "Branch")
    _apply(branch, body)
    _flush(session)
    return branch


# --- Bed types -----------------------------------------------------------------------


class BedTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class BedTypeOut(Out):
    id: uuid.UUID
    name: str


@router.get("/bed-types", response_model=list[BedTypeOut])
def list_bed_types(ctx: StaffSession) -> list[BedType]:
    session, _ = ctx
    return list(session.scalars(select(BedType).order_by(BedType.name)))


@router.post("/bed-types", response_model=BedTypeOut, status_code=201)
def create_bed_type(ctx: AdminSession, body: BedTypeIn) -> BedType:
    session, staff = ctx
    bt = BedType(tenant_id=staff.tenant_id, name=body.name.strip())
    session.add(bt)
    _flush(session)
    return bt


@router.put("/bed-types/{bed_type_id}", response_model=BedTypeOut)
def update_bed_type(ctx: AdminSession, bed_type_id: uuid.UUID, body: BedTypeIn) -> BedType:
    session, _ = ctx
    bt = _get(session, BedType, bed_type_id, "Bed type")
    bt.name = body.name.strip()
    _flush(session)
    return bt


# --- Rooms and beds ------------------------------------------------------------------


class BedIn(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    bed_type_id: uuid.UUID
    active: bool = True


class BedOut(Out):
    id: uuid.UUID
    room_id: uuid.UUID
    label: str
    bed_type_id: uuid.UUID
    active: bool


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    room_type: RoomType = RoomType.single
    active: bool = True


class RoomOut(Out):
    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    room_type: str
    active: bool
    beds: list[BedOut]


@router.get("/branches/{branch_id}/rooms", response_model=list[RoomOut])
def list_rooms(ctx: StaffSession, branch_id: uuid.UUID) -> list[Room]:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    rooms = list(
        session.scalars(select(Room).where(Room.branch_id == branch_id).order_by(Room.name))
    )
    for r in rooms:
        r.beds.sort(key=lambda b: b.label)
    return rooms


@router.post("/branches/{branch_id}/rooms", response_model=RoomOut, status_code=201)
def create_room(ctx: ManagerSession, branch_id: uuid.UUID, body: RoomIn) -> Room:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    _get(session, Branch, branch_id, "Branch")
    room = Room(tenant_id=staff.tenant_id, branch_id=branch_id)
    _apply(room, body)
    session.add(room)
    _flush(session)
    session.refresh(room)
    return room


def _room_for_write(session: Session, staff: Staff, room_id: uuid.UUID) -> Room:
    room = _get(session, Room, room_id, "Room")
    ensure_branch(session, staff, room.branch_id)
    return room


@router.put("/rooms/{room_id}", response_model=RoomOut)
def update_room(ctx: ManagerSession, room_id: uuid.UUID, body: RoomIn) -> Room:
    session, staff = ctx
    room = _room_for_write(session, staff, room_id)
    _apply(room, body)
    _flush(session)
    return room


@router.post("/rooms/{room_id}/beds", response_model=BedOut, status_code=201)
def create_bed(ctx: ManagerSession, room_id: uuid.UUID, body: BedIn) -> Bed:
    session, staff = ctx
    _room_for_write(session, staff, room_id)
    _get(session, BedType, body.bed_type_id, "Bed type")
    bed = Bed(tenant_id=staff.tenant_id, room_id=room_id)
    _apply(bed, body)
    session.add(bed)
    _flush(session)
    return bed


@router.put("/beds/{bed_id}", response_model=BedOut)
def update_bed(ctx: ManagerSession, bed_id: uuid.UUID, body: BedIn) -> Bed:
    session, staff = ctx
    bed = _get(session, Bed, bed_id, "Bed")
    _room_for_write(session, staff, bed.room_id)
    _get(session, BedType, body.bed_type_id, "Bed type")
    _apply(bed, body)
    _flush(session)
    return bed


# --- Services ------------------------------------------------------------------------


class VariantIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    duration_min: int = Field(gt=0, le=600)
    default_price: Decimal = Field(ge=0)
    active: bool = True


class VariantOut(Out):
    id: uuid.UUID
    service_id: uuid.UUID
    label: str
    duration_min: int
    default_price: Decimal
    active: bool


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = None
    bed_type_id: uuid.UUID
    buffer_before_min: int = Field(default=0, ge=0, le=120)
    buffer_after_min: int = Field(default=20, ge=0, le=120)
    guest_count: Literal[1, 2] = 1
    active: bool = True


class ServiceOut(Out):
    id: uuid.UUID
    name: str
    category: str | None
    bed_type_id: uuid.UUID
    buffer_before_min: int
    buffer_after_min: int
    guest_count: int
    active: bool
    variants: list[VariantOut]


@router.get("/services", response_model=list[ServiceOut])
def list_services(ctx: StaffSession) -> list[Service]:
    session, _ = ctx
    services = list(session.scalars(select(Service).order_by(Service.category, Service.name)))
    for s in services:
        s.variants.sort(key=lambda v: v.duration_min)
    return services


@router.post("/services", response_model=ServiceOut, status_code=201)
def create_service(ctx: AdminSession, body: ServiceIn) -> Service:
    session, staff = ctx
    _get(session, BedType, body.bed_type_id, "Bed type")
    svc = Service(tenant_id=staff.tenant_id)
    _apply(svc, body)
    session.add(svc)
    _flush(session)
    session.refresh(svc)
    return svc


@router.put("/services/{service_id}", response_model=ServiceOut)
def update_service(ctx: AdminSession, service_id: uuid.UUID, body: ServiceIn) -> Service:
    session, _ = ctx
    svc = _get(session, Service, service_id, "Service")
    _get(session, BedType, body.bed_type_id, "Bed type")
    _apply(svc, body)
    _flush(session)
    return svc


@router.post("/services/{service_id}/variants", response_model=VariantOut, status_code=201)
def create_variant(ctx: AdminSession, service_id: uuid.UUID, body: VariantIn) -> ServiceVariant:
    session, staff = ctx
    _get(session, Service, service_id, "Service")
    v = ServiceVariant(tenant_id=staff.tenant_id, service_id=service_id)
    _apply(v, body)
    session.add(v)
    _flush(session)
    return v


@router.put("/variants/{variant_id}", response_model=VariantOut)
def update_variant(ctx: AdminSession, variant_id: uuid.UUID, body: VariantIn) -> ServiceVariant:
    session, _ = ctx
    v = _get(session, ServiceVariant, variant_id, "Service option")
    _apply(v, body)
    _flush(session)
    return v


# --- Branch prices -------------------------------------------------------------------


class PriceOut(BaseModel):
    service_id: uuid.UUID
    service_name: str
    variant_id: uuid.UUID
    variant_label: str
    default_price: Decimal
    branch_price: Decimal | None
    offered: bool


class PriceIn(BaseModel):
    price: Decimal | None = Field(default=None, ge=0, description="Empty = use the default")
    offered: bool = True


@router.get("/branches/{branch_id}/prices", response_model=list[PriceOut])
def list_prices(ctx: StaffSession, branch_id: uuid.UUID) -> list[PriceOut]:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    overrides = {
        p.service_variant_id: p
        for p in session.scalars(
            select(BranchServicePrice).where(BranchServicePrice.branch_id == branch_id)
        )
    }
    rows = session.execute(
        select(Service, ServiceVariant)
        .join(ServiceVariant, ServiceVariant.service_id == Service.id)
        .where(Service.active.is_(True), ServiceVariant.active.is_(True))
        .order_by(Service.name, ServiceVariant.duration_min)
    )
    out = []
    for svc, v in rows:
        o = overrides.get(v.id)
        out.append(
            PriceOut(
                service_id=svc.id,
                service_name=svc.name,
                variant_id=v.id,
                variant_label=v.label,
                default_price=v.default_price,
                branch_price=o.price if o else None,
                offered=o.offered if o else True,
            )
        )
    return out


@router.put("/branches/{branch_id}/prices/{variant_id}", status_code=204)
def set_price(ctx: ManagerSession, branch_id: uuid.UUID, variant_id: uuid.UUID, body: PriceIn):
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    _get(session, ServiceVariant, variant_id, "Service option")
    row = session.scalar(
        select(BranchServicePrice).where(
            BranchServicePrice.branch_id == branch_id,
            BranchServicePrice.service_variant_id == variant_id,
        )
    )
    if row is None:
        row = BranchServicePrice(
            tenant_id=staff.tenant_id, branch_id=branch_id, service_variant_id=variant_id
        )
        session.add(row)
    row.price = body.price
    row.offered = body.offered
    _flush(session)


# --- Therapists and skills -----------------------------------------------------------


class TherapistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    gender: Literal["female", "male", "other"]
    employment_status: str = "regular"
    display_colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    active: bool = True
    skill_service_ids: list[uuid.UUID] = []


class TherapistOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    gender: str
    employment_status: str
    display_colour: str | None
    active: bool
    skill_service_ids: list[uuid.UUID]


def _therapist_out(session: Session, t: Therapist) -> TherapistOut:
    skills = session.scalars(
        select(TherapistSkill.service_id).where(TherapistSkill.therapist_id == t.id)
    )
    return TherapistOut(
        id=t.id,
        branch_id=t.branch_id,
        name=t.name,
        gender=t.gender,
        employment_status=t.employment_status,
        display_colour=t.display_colour,
        active=t.active,
        skill_service_ids=list(skills),
    )


def _save_therapist(session: Session, staff: Staff, t: Therapist, body: TherapistIn):
    _apply(t, body, exclude={"skill_service_ids"})
    session.add(t)
    _flush(session)
    session.execute(delete(TherapistSkill).where(TherapistSkill.therapist_id == t.id))
    for sid in set(body.skill_service_ids):
        _get(session, Service, sid, "Service")
        session.add(TherapistSkill(tenant_id=staff.tenant_id, therapist_id=t.id, service_id=sid))
    _flush(session)
    return _therapist_out(session, t)


@router.get("/branches/{branch_id}/therapists", response_model=list[TherapistOut])
def list_therapists(ctx: StaffSession, branch_id: uuid.UUID) -> list[TherapistOut]:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    rows = session.scalars(
        select(Therapist).where(Therapist.branch_id == branch_id).order_by(Therapist.name)
    )
    return [_therapist_out(session, t) for t in rows]


@router.post("/branches/{branch_id}/therapists", response_model=TherapistOut, status_code=201)
def create_therapist(ctx: ManagerSession, branch_id: uuid.UUID, body: TherapistIn):
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    _get(session, Branch, branch_id, "Branch")
    return _save_therapist(
        session, staff, Therapist(tenant_id=staff.tenant_id, branch_id=branch_id), body
    )


@router.put("/therapists/{therapist_id}", response_model=TherapistOut)
def update_therapist(ctx: ManagerSession, therapist_id: uuid.UUID, body: TherapistIn):
    session, staff = ctx
    t = _get(session, Therapist, therapist_id, "Therapist")
    ensure_branch(session, staff, t.branch_id)
    return _save_therapist(session, staff, t, body)


# --- Shifts --------------------------------------------------------------------------


class ShiftIn(BaseModel):
    therapist_id: uuid.UUID
    work_date: date
    start: time
    end: time = Field(description="If not after start, the shift ends the next day")


class ShiftOut(Out):
    id: uuid.UUID
    therapist_id: uuid.UUID
    work_date: date
    starts_at: datetime
    ends_at: datetime


class CopyWeekIn(BaseModel):
    from_week_start: date
    to_week_start: date


def _branch_tz(branch: Branch) -> ZoneInfo:
    return ZoneInfo(branch.timezone)


def _therapist_in_branch(session, therapist_id, branch_id) -> Therapist:
    t = _get(session, Therapist, therapist_id, "Therapist")
    if t.branch_id != branch_id:
        raise HTTPException(422, "That therapist works at another branch")
    return t


@router.get("/branches/{branch_id}/shifts", response_model=list[ShiftOut])
def list_shifts(
    ctx: StaffSession,
    branch_id: uuid.UUID,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> list[Shift]:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    return list(
        session.scalars(
            select(Shift)
            .where(
                Shift.branch_id == branch_id,
                Shift.work_date >= date_from,
                Shift.work_date <= date_to,
            )
            .order_by(Shift.work_date, Shift.starts_at)
        )
    )


@router.post("/branches/{branch_id}/shifts", response_model=ShiftOut, status_code=201)
def create_shift(ctx: ManagerSession, branch_id: uuid.UUID, body: ShiftIn) -> Shift:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    branch = _get(session, Branch, branch_id, "Branch")
    _therapist_in_branch(session, body.therapist_id, branch_id)
    tz = _branch_tz(branch)
    starts = datetime.combine(body.work_date, body.start, tzinfo=tz)
    end_day = body.work_date if body.end > body.start else body.work_date + timedelta(days=1)
    ends = datetime.combine(end_day, body.end, tzinfo=tz)
    clash = session.scalar(
        select(Shift.id).where(
            Shift.therapist_id == body.therapist_id,
            Shift.starts_at < ends,
            Shift.ends_at > starts,
        )
    )
    if clash:
        raise HTTPException(409, "This therapist already has a shift at that time")
    shift = Shift(
        tenant_id=staff.tenant_id,
        therapist_id=body.therapist_id,
        branch_id=branch_id,
        work_date=body.work_date,
        starts_at=starts,
        ends_at=ends,
    )
    session.add(shift)
    _flush(session)
    return shift


@router.delete("/shifts/{shift_id}", status_code=204)
def delete_shift(ctx: ManagerSession, shift_id: uuid.UUID) -> None:
    session, staff = ctx
    shift = _get(session, Shift, shift_id, "Shift")
    ensure_branch(session, staff, shift.branch_id)
    session.delete(shift)


@router.post("/branches/{branch_id}/shifts/copy-week", response_model=list[ShiftOut])
def copy_week(ctx: ManagerSession, branch_id: uuid.UUID, body: CopyWeekIn) -> list[Shift]:
    """Copy one week's shifts to another week, skipping any that would overlap."""
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    branch = _get(session, Branch, branch_id, "Branch")
    tz = _branch_tz(branch)
    offset = body.to_week_start - body.from_week_start
    source = session.scalars(
        select(Shift).where(
            Shift.branch_id == branch_id,
            Shift.work_date >= body.from_week_start,
            Shift.work_date < body.from_week_start + timedelta(days=7),
        )
    ).all()
    created = []
    for s in source:
        # Rebuild from local wall-clock times so a daylight-saving change keeps 09:00 at 09:00.
        local_start = s.starts_at.astimezone(tz)
        local_end = s.ends_at.astimezone(tz)
        starts = datetime.combine(local_start.date() + offset, local_start.time(), tzinfo=tz)
        ends = datetime.combine(local_end.date() + offset, local_end.time(), tzinfo=tz)
        clash = session.scalar(
            select(Shift.id).where(
                Shift.therapist_id == s.therapist_id,
                Shift.starts_at < ends,
                Shift.ends_at > starts,
            )
        )
        if clash:
            continue
        new = Shift(
            tenant_id=staff.tenant_id,
            therapist_id=s.therapist_id,
            branch_id=branch_id,
            work_date=s.work_date + offset,
            starts_at=starts,
            ends_at=ends,
        )
        session.add(new)
        created.append(new)
    _flush(session)
    return created


# --- Time blocks ---------------------------------------------------------------------


class TimeBlockIn(BaseModel):
    therapist_id: uuid.UUID | None = None
    bed_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: Literal["break", "leave", "maintenance", "other"]


class TimeBlockOut(Out):
    id: uuid.UUID
    therapist_id: uuid.UUID | None
    bed_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime
    reason: str


def _block_branch(session: Session, tb_therapist, tb_bed) -> uuid.UUID:
    if tb_therapist:
        return _get(session, Therapist, tb_therapist, "Therapist").branch_id
    bed = _get(session, Bed, tb_bed, "Bed")
    return bed.room.branch_id


@router.get("/branches/{branch_id}/time-blocks", response_model=list[TimeBlockOut])
def list_time_blocks(
    ctx: StaffSession,
    branch_id: uuid.UUID,
    date_from: Annotated[datetime, Query(alias="from")],
    date_to: Annotated[datetime, Query(alias="to")],
) -> list[TimeBlock]:
    session, staff = ctx
    ensure_branch(session, staff, branch_id)
    therapist_ids = select(Therapist.id).where(Therapist.branch_id == branch_id)
    bed_ids = select(Bed.id).join(Room).where(Room.branch_id == branch_id)
    return list(
        session.scalars(
            select(TimeBlock)
            .where(
                (TimeBlock.therapist_id.in_(therapist_ids)) | (TimeBlock.bed_id.in_(bed_ids)),
                TimeBlock.starts_at < date_to,
                TimeBlock.ends_at > date_from,
            )
            .order_by(TimeBlock.starts_at)
        )
    )


@router.post("/time-blocks", response_model=TimeBlockOut, status_code=201)
def create_time_block(ctx: ManagerSession, body: TimeBlockIn) -> TimeBlock:
    session, staff = ctx
    if (body.therapist_id is None) == (body.bed_id is None):
        raise HTTPException(422, "Choose either a therapist or a bed")
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "The end must be after the start")
    ensure_branch(session, staff, _block_branch(session, body.therapist_id, body.bed_id))
    tb = TimeBlock(tenant_id=staff.tenant_id)
    _apply(tb, body)
    session.add(tb)
    _flush(session)
    return tb


@router.delete("/time-blocks/{block_id}", status_code=204)
def delete_time_block(ctx: ManagerSession, block_id: uuid.UUID) -> None:
    session, staff = ctx
    tb = _get(session, TimeBlock, block_id, "Time block")
    ensure_branch(session, staff, _block_branch(session, tb.therapist_id, tb.bed_id))
    session.delete(tb)


# --- Booking channels ----------------------------------------------------------------

DEFAULT_CHANNELS: tuple[tuple[str, str], ...] = (
    ("phone", "Phone call"),
    ("email", "Email"),
    ("walk_in", "Walk-in"),
)


class ChannelIn(BaseModel):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=100)
    active: bool = True


class ChannelOut(Out):
    id: uuid.UUID
    code: str
    name: str
    active: bool


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(ctx: StaffSession) -> list[BookingChannel]:
    session, _ = ctx
    return list(session.scalars(select(BookingChannel).order_by(BookingChannel.name)))


@router.post("/channels", response_model=ChannelOut, status_code=201)
def create_channel(ctx: AdminSession, body: ChannelIn) -> BookingChannel:
    session, staff = ctx
    ch = BookingChannel(tenant_id=staff.tenant_id)
    _apply(ch, body)
    session.add(ch)
    _flush(session, "That channel code is already used")
    return ch


@router.put("/channels/{channel_id}", response_model=ChannelOut)
def update_channel(ctx: AdminSession, channel_id: uuid.UUID, body: ChannelIn) -> BookingChannel:
    session, _ = ctx
    ch = _get(session, BookingChannel, channel_id, "Channel")
    if ch.code in {c for c, _ in DEFAULT_CHANNELS} and body.code != ch.code:
        raise HTTPException(422, "The built-in channel codes cannot be changed")
    _apply(ch, body)
    _flush(session, "That channel code is already used")
    return ch
