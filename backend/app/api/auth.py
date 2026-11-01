"""Sign in, who am I, and staff accounts (admin only)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminSession, StaffSession
from app.db import SessionLocal, set_tenant, use_app_role
from app.models import Branch, Tenant, User, UserBranch, UserRole
from app.security import burn_verify_time, hash_password, issue_token, verify_password

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    spa: str = Field(description="The spa's sign-in code (tenant slug)")
    email: str
    password: str


class BranchRef(BaseModel):
    id: uuid.UUID
    name: str


class MeOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str
    tenant_id: uuid.UUID
    tenant_name: str
    branches: list[BranchRef]


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: MeOut


def _me(session, user: User) -> MeOut:
    tenant = session.get(Tenant, user.tenant_id)
    q = select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)
    if user.role != UserRole.admin:
        q = q.join(UserBranch, UserBranch.branch_id == Branch.id).where(
            UserBranch.user_id == user.id
        )
    return MeOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=tenant.name,
        branches=[BranchRef(id=b.id, name=b.name) for b in session.scalars(q)],
    )


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn) -> LoginOut:
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Spa code, email or password is wrong")
    with SessionLocal() as session, session.begin():
        use_app_role(session)
        tenant_id = session.scalar(
            text("SELECT tenant_id_for_slug(:slug)"), {"slug": body.spa.strip()}
        )
        if tenant_id is None:
            burn_verify_time(body.password)
            raise bad
        set_tenant(session, tenant_id)
        user = session.scalar(
            select(User).where(
                User.tenant_id == tenant_id,
                func.lower(User.email) == body.email.strip().lower(),
            )
        )
        if user is None:
            burn_verify_time(body.password)
            raise bad
        if not verify_password(user.password_hash, body.password) or not user.active:
            raise bad
        token, expires = issue_token(user.id, tenant_id)
        return LoginOut(access_token=token, expires_at=expires, user=_me(session, user))


@router.get("/auth/me", response_model=MeOut)
def me(ctx: StaffSession) -> MeOut:
    session, staff = ctx
    return _me(session, staff.user)


class UserIn(BaseModel):
    email: str
    name: str
    role: UserRole = UserRole.front_desk
    password: str | None = None
    branch_ids: list[uuid.UUID] = []
    active: bool = True


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    active: bool
    branch_ids: list[uuid.UUID]


def _user_out(session, user: User) -> UserOut:
    ids = session.scalars(select(UserBranch.branch_id).where(UserBranch.user_id == user.id))
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        active=user.active,
        branch_ids=list(ids),
    )


@router.get("/users", response_model=list[UserOut])
def list_users(ctx: AdminSession) -> list[UserOut]:
    session, _ = ctx
    return [_user_out(session, u) for u in session.scalars(select(User).order_by(User.name))]


def _save_user(session, staff, user: User, body: UserIn) -> UserOut:
    user.email = body.email.strip().lower()
    user.name = body.name.strip()
    user.role = body.role
    user.active = body.active
    if body.password:
        try:
            user.password_hash = hash_password(body.password)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    if user.id == staff.user.id and (body.role != UserRole.admin or not body.active):
        raise HTTPException(422, "You cannot remove your own admin access")
    session.add(user)
    try:
        session.flush()
    except IntegrityError as e:
        raise HTTPException(409, "A user with that email already exists") from e
    session.query(UserBranch).filter(UserBranch.user_id == user.id).delete()
    for bid in set(body.branch_ids):
        if session.get(Branch, bid) is None:
            raise HTTPException(422, "Unknown branch")
        session.add(UserBranch(tenant_id=staff.tenant_id, user_id=user.id, branch_id=bid))
    session.flush()
    return _user_out(session, user)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(ctx: AdminSession, body: UserIn) -> UserOut:
    session, staff = ctx
    if not body.password:
        raise HTTPException(422, "A password is required for a new user")
    return _save_user(session, staff, User(tenant_id=staff.tenant_id), body)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(ctx: AdminSession, user_id: uuid.UUID, body: UserIn) -> UserOut:
    session, staff = ctx
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return _save_user(session, staff, user, body)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/password", status_code=204)
def change_password(ctx: StaffSession, body: PasswordIn) -> None:
    session, staff = ctx
    if not verify_password(staff.user.password_hash, body.current_password):
        raise HTTPException(422, "Current password is wrong")
    try:
        staff.user.password_hash = hash_password(body.new_password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
