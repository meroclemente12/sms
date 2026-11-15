"""Loading a branch's printed menu (The Rise) into a spa."""

from decimal import Decimal

from sqlalchemy import select

from app import models as m
from app.menus import THE_RISE, import_menu
from app.scheduling import GuestRequest, GuestWish, branch_price, create_booking, pick_pairs

from .conftest import at


def _variant(session, service_name: str, label: str) -> m.ServiceVariant:
    return session.scalar(
        select(m.ServiceVariant)
        .join(m.Service)
        .where(m.Service.name == service_name, m.ServiceVariant.label == label)
    )


def test_the_rise_menu_loads_with_member_prices_and_packages(session, spa):
    other = m.Branch(tenant_id=spa.tenant.id, name="Lucena")
    session.add(other)
    session.flush()

    result = import_menu(session, spa.branch, THE_RISE, all_therapists_can_do=True)
    assert len(result.created) == 14 and not result.updated
    assert result.skills_added == 14 * 3

    pamana = _variant(session, "Hilot Pamaná", "Member · 75 min")
    assert pamana.duration_min == 75
    assert branch_price(session, spa.branch.id, pamana) == Decimal(880)
    non = _variant(session, "Hilot Pamaná", "Non-member · 75 min")
    assert branch_price(session, spa.branch.id, non) == Decimal(930)

    ginhawa = session.scalars(
        select(m.ServiceVariant).join(m.Service).where(m.Service.name == "Hilot Ginhawa")
    ).all()
    assert [(v.label, v.default_price) for v in ginhawa] == [("60 min", Decimal(750))]

    p7 = session.scalar(select(m.Service).where(m.Service.name.like("Package 7:%")))
    assert p7.category == "Premium package"
    assert {(v.label, v.duration_min, v.default_price) for v in p7.variants} == {
        ("Member · 210 min", 210, Decimal(2800)),
        ("Non-member · 210 min", 210, Decimal(3000)),
    }
    # Package lengths with no default commission are reported for Setup.
    assert p7.name in result.no_commission
    assert "Hilot Pamaná" not in result.no_commission

    # New services are hidden at the other branch until it sets them up.
    hidden = session.scalar(
        select(m.BranchServicePrice).where(
            m.BranchServicePrice.branch_id == other.id,
            m.BranchServicePrice.service_variant_id == pamana.id,
        )
    )
    assert hidden.offered is False

    # A package books like any other service, at the member price.
    p4 = _variant(
        session, "Package 4: Hilot Pamaná with Body Scrub + Free Hair Spa", "Member · 120 min"
    )
    (pair,) = pick_pairs(session, spa.branch.id, p4.id, at(10), [GuestWish()])
    booking = create_booking(
        session,
        tenant_id=spa.tenant.id,
        branch_id=spa.branch.id,
        client_id=spa.client.id,
        channel_id=spa.phone.id,
        variant_id=p4.id,
        starts_at=at(10),
        guests=[GuestRequest(pair=pair)],
    )
    session.flush()
    session.refresh(booking)
    item = booking.items[0]
    assert item.price == Decimal(1650)
    assert item.ends_at - item.starts_at == at(12) - at(10)


def test_importing_again_updates_instead_of_duplicating(session, spa):
    import_menu(session, spa.branch, THE_RISE)
    variant = _variant(session, "Thai Dry Massage", "60 min")
    row = session.scalar(
        select(m.BranchServicePrice).where(m.BranchServicePrice.service_variant_id == variant.id)
    )
    row.price = Decimal(700)
    session.flush()

    again = import_menu(session, spa.branch, THE_RISE)
    assert not again.created and len(again.updated) == 14
    thai = session.scalars(select(m.Service).where(m.Service.name == "Thai Dry Massage")).all()
    assert len(thai) == 1
    assert branch_price(session, spa.branch.id, variant) == Decimal(750)
