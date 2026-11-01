"""Request dependencies: database session scoped to the signed-in user's tenant, and
role / branch checks."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, set_tenant, use_app_role
from app.models import Branch, User, UserBranch, UserRole
from app.security import read_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Staff:
    """The signed-in user, with the branches they may work in."""

    user: User
    branch_ids: frozenset[uuid.UUID]

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.user.tenant_id

    @property
    def role(self) -> str:
        return self.user.role

    def can_access_branch(self, branch_id: uuid.UUID) -> bool:
        return self.role == UserRole.admin or branch_id in self.branch_ids


def _unauthorized(detail: str = "Please sign in") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def staff_session(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Iterator[tuple[Session, Staff]]:
    if creds is None:
        raise _unauthorized()
    try:
        claims = read_token(creds.credentials)
    except jwt.InvalidTokenError as e:
        raise _unauthorized("Your session has expired, please sign in again") from e
    with SessionLocal() as session, session.begin():
        use_app_role(session)
        set_tenant(session, claims.tenant_id)
        user = session.get(User, claims.user_id)
        if user is None or not user.active or user.tenant_id != claims.tenant_id:
            raise _unauthorized()
        branch_ids = frozenset(
            session.scalars(select(UserBranch.branch_id).where(UserBranch.user_id == user.id))
        )
        session.info["tenant_id"] = user.tenant_id
        yield session, Staff(user=user, branch_ids=branch_ids)


StaffSession = Annotated[tuple[Session, Staff], Depends(staff_session)]


def require_role(*roles: UserRole):
    allowed = set(roles) | {UserRole.admin}

    def check(ctx: StaffSession) -> tuple[Session, Staff]:
        if ctx[1].role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this")
        return ctx

    return check


ManagerSession = Annotated[tuple[Session, Staff], Depends(require_role(UserRole.manager))]
AdminSession = Annotated[tuple[Session, Staff], Depends(require_role(UserRole.admin))]


def ensure_branch(session: Session, staff: Staff, branch_id: uuid.UUID) -> None:
    """The branch exists in this spa and the signed-in user may work in it."""
    if session.get(Branch, branch_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found")
    if not staff.can_access_branch(branch_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this branch")
