"""Admin commands.

Create a spa (tenant) with its first admin and the default booking channels:

    uv run python -m app.cli create-tenant --name "Banahaw Heals" --slug banahaw \\
        --admin-email owner@example.com --admin-name "Owner"

The password is asked for interactively (or read from ADMIN_PASSWORD).
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import uuid

from sqlalchemy import select

from app.api.setup import DEFAULT_CHANNELS
from app.db import SessionLocal, set_tenant
from app.models import BookingChannel, Tenant, User, UserRole
from app.security import hash_password

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")


def create_tenant(name: str, slug: str, admin_email: str, admin_name: str, password: str) -> Tenant:
    slug = slug.lower()
    if not SLUG.match(slug):
        raise ValueError("Slug must be 3-63 lowercase letters, digits or hyphens")
    with SessionLocal() as session, session.begin():
        if session.scalar(select(Tenant.id).where(Tenant.slug == slug)):
            raise ValueError(f"A spa with the code '{slug}' already exists")
        tenant = Tenant(id=uuid.uuid4(), name=name, slug=slug)
        set_tenant(session, tenant.id)
        session.add(tenant)
        session.flush()
        for code, label in DEFAULT_CHANNELS:
            session.add(BookingChannel(tenant_id=tenant.id, code=code, name=label))
        session.add(
            User(
                tenant_id=tenant.id,
                email=admin_email.strip().lower(),
                name=admin_name,
                role=UserRole.admin,
                password_hash=hash_password(password),
            )
        )
    return tenant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ct = sub.add_parser("create-tenant", help="Create a spa with its first admin")
    ct.add_argument("--name", required=True)
    ct.add_argument("--slug", required=True, help="Sign-in code, e.g. banahaw")
    ct.add_argument("--admin-email", required=True)
    ct.add_argument("--admin-name", default="Admin")
    args = parser.parse_args(argv)

    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    try:
        tenant = create_tenant(args.name, args.slug, args.admin_email, args.admin_name, password)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Created '{tenant.name}'. Staff sign in with spa code '{tenant.slug}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
