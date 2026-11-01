"""Password hashing and access tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings

_hasher = PasswordHasher()
_ALGO = "HS256"
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Verifying against a fixed hash when the user does not exist keeps login timing the same,
# so response time does not reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def burn_verify_time(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID


def issue_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[str, datetime]:
    expires = datetime.now(UTC) + timedelta(hours=settings.access_token_hours)
    token = jwt.encode(
        {"sub": str(user_id), "tid": str(tenant_id), "exp": expires},
        settings.jwt_secret,
        algorithm=_ALGO,
    )
    return token, expires


def read_token(token: str) -> TokenClaims:
    """Raises ``jwt.InvalidTokenError`` for a bad or expired token."""
    data = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO], options={"require": ["exp"]})
    return TokenClaims(user_id=uuid.UUID(data["sub"]), tenant_id=uuid.UUID(data["tid"]))
