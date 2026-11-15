"""Branch menus copied from the printed price lists, and a loader that puts one into a spa.

    uv run python -m app.cli import-menu --spa banahaw --branch "The Rise"

Each menu item becomes a service. Items with a member and a non-member price get two
options ("Member · 75 min" and "Non-member · 75 min") so the front desk picks the right
price at booking; items with one price get a single option. Packages are services in a
"... package" category with their own length and price.

Prices are set as this branch's prices. A service the loader creates is hidden at the
spa's other branches (Setup > Services can offer it there). Running it again updates
prices and lengths instead of adding duplicates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Bed,
    BedType,
    Branch,
    BranchServicePrice,
    Room,
    Service,
    ServiceVariant,
    Therapist,
    TherapistSkill,
)
from app.pay import default_commission


@dataclass(frozen=True)
class MenuItem:
    name: str
    category: str
    minutes: int
    member: int | None  # None: one price for everyone
    price: int  # non-member price, or the only price
    foot: bool = False  # done on a foot spa chair when the branch has one


# "Banahaw The Rise" menu (Assembly Grounds at The Rise), received 2026-11-15.
# Descriptions from the menu, for reference:
#   Hilot Pamaná: signature 5-in-1 deep tissue healing massage (Swedish, Shiatsu, Thai,
#     reflexology and hilot).  Hilot Kagalingan: Hilot Ginhawa with Netibo healing.
#   Body scrub: removal of dead skin cells, with a free hair spa.
#   Hilot Ginhawa: relaxing Swedish massage.  Thai dry massage: no oil.
#   Foot & back: Swedish and Shiatsu.
THE_RISE = [
    MenuItem("Hilot Pamaná", "Massage", 75, 880, 930),
    MenuItem("Hilot Kagalingan", "Massage", 75, 880, 930),
    MenuItem("Body Scrub + Free Hair Spa", "Body care", 60, 950, 1050),
    MenuItem("Hilot Ginhawa", "Massage", 60, None, 750),
    MenuItem("Thai Dry Massage", "Massage", 60, None, 750),
    MenuItem("Foot & Back Massage", "Massage", 60, None, 750),
    MenuItem("Foot Spa + Foot Reflex", "Foot care", 60, None, 750, foot=True),
    # Starter packages
    MenuItem("Package 1: Hilot Pamaná with Ventosa", "Starter package", 90, 1200, 1300),
    MenuItem(
        "Package 2: Hilot Pamaná with Netibo & Cranio Sacral Therapy",
        "Starter package",
        90,
        1200,
        1300,
    ),
    MenuItem(
        "Package 3: Hilot Ginhawa with Body Scrub + Free Hair Spa",
        "Starter package",
        110,
        1550,
        1700,
    ),
    MenuItem(
        "Package 4: Hilot Pamaná with Body Scrub + Free Hair Spa",
        "Starter package",
        120,
        1650,
        1800,
    ),
    # Exclusive package
    MenuItem(
        "Package 5: Hilot Pamaná with Ventosa + Body Scrub + Free Hair Spa",
        "Exclusive package",
        140,
        1850,
        2000,
    ),
    # Premium packages
    MenuItem(
        "Package 6: Foot Spa + Foot Reflex + Body Scrub + Free Hair Spa"
        " + Hilot Pamaná with Netibo & Cranio Sacral",
        "Premium package",
        180,
        2550,
        2700,
    ),
    MenuItem(
        "Package 7: Foot Spa + Foot Reflex + Body Scrub + Free Hair Spa + Ear Candling"
        " + Netibo & Ventosa Hilot Pamaná",
        "Premium package",
        210,
        2800,
        3000,
    ),
]

MENUS = {"the-rise": THE_RISE}


def _options(item: MenuItem) -> list[tuple[str, Decimal]]:
    length = f"{item.minutes} min"
    if item.member is None:
        return [(length, Decimal(item.price))]
    return [
        (f"Member · {length}", Decimal(item.member)),
        (f"Non-member · {length}", Decimal(item.price)),
    ]


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    no_commission: list[str] = field(default_factory=list)
    skills_added: int = 0


def _bed_types(session: Session, branch: Branch) -> tuple[uuid.UUID, uuid.UUID]:
    """The branch's massage bed type, and the foot spa chair type (or the bed type)."""
    counts = session.execute(
        select(BedType.id, BedType.name, func.count(Bed.id))
        .join(Bed, Bed.bed_type_id == BedType.id)
        .join(Room, Room.id == Bed.room_id)
        .where(Room.branch_id == branch.id, Bed.active, Room.active)
        .group_by(BedType.id, BedType.name)
        .order_by(func.count(Bed.id).desc())
    ).all()
    if not counts:
        raise ValueError(f"Branch '{branch.name}' has no beds yet; add rooms and beds first")
    foot = next((tid for tid, name, _ in counts if "foot" in name.lower()), None)
    massage = next((tid for tid, name, _ in counts if "foot" not in name.lower()), counts[0][0])
    return massage, foot or massage


def import_menu(
    session: Session,
    branch: Branch,
    items: list[MenuItem],
    *,
    all_therapists_can_do: bool = False,
) -> ImportResult:
    """Load ``items`` into the spa and set them as ``branch``'s prices. Call inside a
    transaction with the tenant set."""
    tid = branch.tenant_id
    result = ImportResult()
    massage_bed, foot_bed = _bed_types(session, branch)
    other_branches = session.scalars(
        select(Branch.id).where(Branch.tenant_id == tid, Branch.id != branch.id)
    ).all()

    for item in items:
        svc = session.scalar(
            select(Service).where(
                Service.tenant_id == tid, func.lower(Service.name) == item.name.lower()
            )
        )
        is_new = svc is None
        if svc is None:
            svc = Service(
                tenant_id=tid,
                name=item.name,
                category=item.category,
                bed_type_id=foot_bed if item.foot else massage_bed,
                guest_count=1,
                buffer_after_min=20,
            )
            session.add(svc)
            session.flush()
            result.created.append(item.name)
        else:
            svc.category = item.category
            svc.active = True
            result.updated.append(item.name)

        existing = {
            v.label: v
            for v in session.scalars(
                select(ServiceVariant).where(ServiceVariant.service_id == svc.id)
            )
        }
        for label, price in _options(item):
            variant = existing.get(label)
            if variant is None:
                variant = ServiceVariant(
                    tenant_id=tid,
                    service_id=svc.id,
                    label=label,
                    duration_min=item.minutes,
                    default_price=price,
                    commission=default_commission(item.minutes),
                )
                session.add(variant)
                session.flush()
            else:
                variant.duration_min = item.minutes
                variant.active = True
                if is_new:
                    variant.default_price = price
            if variant.commission == 0 and item.name not in result.no_commission:
                result.no_commission.append(item.name)

            row = session.scalar(
                select(BranchServicePrice).where(
                    BranchServicePrice.branch_id == branch.id,
                    BranchServicePrice.service_variant_id == variant.id,
                )
            )
            if row is None:
                row = BranchServicePrice(
                    tenant_id=tid, branch_id=branch.id, service_variant_id=variant.id
                )
                session.add(row)
            row.price = price
            row.offered = True

            if is_new:
                for other in other_branches:
                    session.add(
                        BranchServicePrice(
                            tenant_id=tid,
                            branch_id=other,
                            service_variant_id=variant.id,
                            price=None,
                            offered=False,
                        )
                    )

        if all_therapists_can_do:
            therapists = session.scalars(
                select(Therapist.id).where(Therapist.branch_id == branch.id, Therapist.active)
            ).all()
            has = set(
                session.scalars(
                    select(TherapistSkill.therapist_id).where(TherapistSkill.service_id == svc.id)
                )
            )
            for therapist_id in therapists:
                if therapist_id not in has:
                    session.add(
                        TherapistSkill(tenant_id=tid, therapist_id=therapist_id, service_id=svc.id)
                    )
                    result.skills_added += 1

    session.flush()
    return result
