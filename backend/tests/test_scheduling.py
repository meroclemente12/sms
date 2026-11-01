from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app import models as m
from app.lifecycle import InvalidTransition, transition
from app.scheduling import (
    GuestRequest,
    GuestWish,
    NotAvailable,
    Pair,
    SlotTaken,
    create_booking,
    find_couples,
    find_pairs,
    find_slots,
    pick_pairs,
    reschedule,
)
from tests.conftest import at, make_spa


def book(session, spa, pair, start, **kw):
    return create_booking(
        session,
        tenant_id=spa.tenant.id,
        branch_id=spa.branch.id,
        client_id=spa.client.id,
        channel_id=spa.phone.id,
        variant_id=kw.pop("variant_id", spa.swedish60.id),
        starts_at=start,
        guests=kw.pop("guests", None) or [GuestRequest(pair=pair)],
        **kw,
    )


def therapists(pairs):
    return {p.therapist_id for p in pairs}


def beds(pairs):
    return {p.bed_id for p in pairs}


def test_only_skilled_therapists_are_offered(session, spa):
    pairs = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10))
    assert therapists(pairs) == {spa.ana.id, spa.ben.id}  # Cora is not trained in Swedish


def test_single_guest_prefers_single_rooms_over_couples_room(session, spa):
    pairs = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10))
    first_beds = [p.bed_id for p in pairs if p.therapist_id == pairs[0].therapist_id]
    assert set(first_beds[:2]) == {spa.bed_a.id, spa.bed_b.id}


def test_gender_preference_filters_therapists(session, spa):
    pairs = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10), gender="male")
    assert therapists(pairs) == {spa.ben.id}


def test_requested_therapist_is_the_only_candidate(session, spa):
    pairs = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10), therapist_id=spa.ana.id)
    assert therapists(pairs) == {spa.ana.id}


def test_outside_shift_is_not_offered(session, spa):
    assert find_pairs(session, spa.branch.id, spa.swedish60.id, at(17, 30)) == []
    assert find_pairs(session, spa.branch.id, spa.swedish60.id, at(8)) == []


def test_booked_therapist_is_busy_but_free_right_after(session, spa):
    book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    assert spa.ana.id not in therapists(
        find_pairs(session, spa.branch.id, spa.swedish60.id, at(10, 30))
    )
    # Therapist buffer is not needed: Ana can start again at 11:00 on another bed.
    pairs = find_pairs(session, spa.branch.id, spa.swedish60.id, at(11), therapist_id=spa.ana.id)
    assert spa.bed_a.id not in beds(pairs)  # bed A still being cleaned until 11:20
    assert spa.bed_b.id in beds(pairs)


def test_bed_cleanup_buffer_is_protected(session, spa):
    book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    assert spa.bed_a.id not in beds(
        find_pairs(session, spa.branch.id, spa.swedish60.id, at(11, 10))
    )
    assert spa.bed_a.id in beds(find_pairs(session, spa.branch.id, spa.swedish60.id, at(11, 20)))


def test_time_blocks_remove_therapist_and_bed(session, spa):
    session.add_all(
        [
            m.TimeBlock(
                tenant_id=spa.tenant.id,
                therapist_id=spa.ana.id,
                starts_at=at(12),
                ends_at=at(13),
                reason="break",
            ),
            m.TimeBlock(
                tenant_id=spa.tenant.id,
                bed_id=spa.bed_b.id,
                starts_at=at(12),
                ends_at=at(13),
                reason="maintenance",
            ),
        ]
    )
    session.flush()
    pairs = find_pairs(session, spa.branch.id, spa.swedish60.id, at(12))
    assert spa.ana.id not in therapists(pairs)
    assert spa.bed_b.id not in beds(pairs)


def test_database_refuses_double_booked_therapist(session, spa):
    book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    # Bypass the search, as two desks clicking at the same moment would.
    with pytest.raises(SlotTaken):
        book(session, spa, Pair(spa.ana.id, spa.bed_b.id, spa.bed_b.room_id), at(10, 30))


def test_database_refuses_double_booked_bed_inside_cleanup(session, spa):
    book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    with pytest.raises(SlotTaken):
        book(session, spa, Pair(spa.ben.id, spa.bed_a.id, spa.bed_a.room_id), at(11, 10))
    book(session, spa, Pair(spa.ben.id, spa.bed_a.id, spa.bed_a.room_id), at(11, 20))


def test_cancelling_frees_the_slot(session, spa):
    b = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    transition(session, b, m.BookingStatus.cancelled)
    book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))


def test_lifecycle_rules_and_history(session, spa):
    b = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    with pytest.raises(InvalidTransition):
        transition(session, b, m.BookingStatus.completed)
    for step in ("confirmed", "checked_in", "in_service", "completed"):
        transition(session, b, m.BookingStatus(step))
    events = session.scalars(
        select(m.BookingEvent.event_type)
        .where(m.BookingEvent.booking_id == b.id)
        .order_by(m.BookingEvent.occurred_at, m.BookingEvent.event_type)
    ).all()
    assert len(events) == 5 and "booking.created" in events


def test_channel_and_preferences_are_recorded(session, spa):
    b = create_booking(
        session,
        tenant_id=spa.tenant.id,
        branch_id=spa.branch.id,
        client_id=spa.client.id,
        channel_id=spa.walk_in.id,
        variant_id=spa.swedish60.id,
        starts_at=at(10),
        guests=[
            GuestRequest(
                Pair(spa.ben.id, spa.bed_a.id, spa.bed_a.room_id),
                therapist_requested=True,
                preferred_gender="male",
            )
        ],
        status=m.BookingStatus.checked_in,
    )
    assert b.channel_id == spa.walk_in.id
    assert b.items[0].therapist_requested and b.items[0].preferred_gender == "male"


def test_branch_price_override_and_not_offered(session, spa):
    row = m.BranchServicePrice(
        tenant_id=spa.tenant.id,
        branch_id=spa.branch.id,
        service_variant_id=spa.swedish60.id,
        price=Decimal("950"),
    )
    session.add(row)
    session.flush()
    b = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    assert b.items[0].price == Decimal("950")
    row.offered = False
    session.flush()
    with pytest.raises(NotAvailable):
        book(session, spa, Pair(spa.ben.id, spa.bed_b.id, spa.bed_b.room_id), at(10))


def test_couples_uses_one_couples_room_and_two_therapists(session, spa):
    p1, p2 = find_couples(session, spa.branch.id, spa.couples60.id, at(14))
    assert p1.room_id == p2.room_id == spa.couples_beds[0].room_id
    assert p1.therapist_id != p2.therapist_id
    b = book(
        session,
        spa,
        None,
        at(14),
        variant_id=spa.couples60.id,
        guests=[GuestRequest(p1), GuestRequest(p2, guest_name="Partner")],
    )
    assert len(b.items) == 2
    with pytest.raises(NotAvailable):
        find_couples(session, spa.branch.id, spa.couples60.id, at(14, 30))
    transition(session, b, m.BookingStatus.cancelled)
    assert {i.status for i in b.items} == {"cancelled"}
    find_couples(session, spa.branch.id, spa.couples60.id, at(14, 30))


def test_couples_needs_two_guests(session, spa):
    p1, _ = find_couples(session, spa.branch.id, spa.couples60.id, at(14))
    with pytest.raises(ValueError):
        book(session, spa, None, at(14), variant_id=spa.couples60.id, guests=[GuestRequest(p1)])


def test_row_level_security_isolates_tenants(session, spa):
    other = make_spa(session, name="Other Spa")
    session.execute(text("SET LOCAL ROLE banahaw_app"))
    session.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(spa.tenant.id)})
    names = set(session.scalars(text("SELECT name FROM tenants")))
    therapist_tenants = set(session.scalars(text("SELECT DISTINCT tenant_id FROM therapists")))
    assert names == {"Banahaw Heals"}
    assert therapist_tenants == {spa.tenant.id}
    assert other.tenant.id not in therapist_tenants


# --- Picking, free times and moving ----------------------------------------------------


def test_find_slots_skips_taken_times_and_respects_closing(session, spa):
    # Ana and Ben are the only Swedish therapists; book both at 10:00.
    p = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10))
    book(session, spa, p[0], at(10))
    p = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10))
    book(session, spa, p[0], at(10))
    slots = find_slots(
        session, spa.branch.id, spa.swedish60.id, [GuestWish()], after=at(9, 50), until=at(12)
    )
    starts = [s.starts_at for s in slots]
    # 10:00–10:45 are taken (therapists busy until 11:00); 11:00 is the first free start,
    # and nothing that would run past 12:00 is offered.
    assert starts == [at(11)]


def test_find_slots_for_a_requested_therapist(session, spa):
    p = find_pairs(session, spa.branch.id, spa.swedish60.id, at(10), therapist_id=spa.ana.id)
    book(session, spa, p[0], at(10))
    slots = find_slots(
        session,
        spa.branch.id,
        spa.swedish60.id,
        [GuestWish(therapist_id=spa.ana.id)],
        after=at(10),
        until=at(13),
        limit=2,
    )
    assert [s.starts_at for s in slots] == [at(11), at(11, 15)]
    assert all(s.pairs[0].therapist_id == spa.ana.id for s in slots)


def test_pick_pairs_couples_needs_two_wishes(session, spa):
    pairs = pick_pairs(session, spa.branch.id, spa.couples60.id, at(10), [GuestWish(), GuestWish()])
    assert len(pairs) == 2 and pairs[0].room_id == pairs[1].room_id
    with pytest.raises(ValueError):
        pick_pairs(session, spa.branch.id, spa.couples60.id, at(10), [GuestWish()])


def test_reschedule_keeps_therapist_and_bed_when_free(session, spa):
    pair = Pair(spa.ana.id, spa.bed_b.id, spa.bed_b.room_id)
    b = book(session, spa, pair, at(10))
    reschedule(session, b, starts_at=at(10, 30))
    item = b.items[0]
    assert (item.therapist_id, item.bed_id, item.starts_at) == (
        spa.ana.id,
        spa.bed_b.id,
        at(10, 30),
    )
    assert item.status == "booked"
    events = session.scalars(
        select(m.BookingEvent.event_type).where(m.BookingEvent.booking_id == b.id)
    ).all()
    assert "booking.rescheduled" in events


def test_reschedule_to_a_taken_therapist_is_refused_and_changes_nothing(session, spa):
    mine = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    book(session, spa, Pair(spa.ben.id, spa.bed_b.id, spa.bed_b.room_id), at(12))
    with pytest.raises(NotAvailable):
        reschedule(session, mine, starts_at=at(12), wishes=[GuestWish(therapist_id=spa.ben.id)])
    item = mine.items[0]
    assert (item.therapist_id, item.starts_at, item.status) == (spa.ana.id, at(10), "booked")


def test_reschedule_can_change_length_and_reprices(session, spa):
    sw90 = m.ServiceVariant(
        tenant_id=spa.tenant.id,
        service_id=spa.swedish60.service_id,
        label="90 min",
        duration_min=90,
        default_price=Decimal("1100"),
    )
    session.add(sw90)
    session.flush()
    b = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    reschedule(session, b, starts_at=at(10), variant_id=sw90.id)
    assert b.items[0].ends_at == at(11, 30) and b.items[0].price == Decimal("1100")


def test_reschedule_moves_both_couples_guests_together(session, spa):
    pairs = find_couples(session, spa.branch.id, spa.couples60.id, at(10))
    b = book(
        session,
        spa,
        None,
        at(10),
        variant_id=spa.couples60.id,
        guests=[GuestRequest(pair=p) for p in pairs],
    )
    reschedule(session, b, starts_at=at(14))
    assert {i.starts_at for i in b.items} == {at(14)}
    assert len({i.therapist_id for i in b.items}) == 2


def test_completed_booking_cannot_be_moved(session, spa):
    b = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    for s in ("checked_in", "in_service", "completed"):
        transition(session, b, m.BookingStatus(s))
    with pytest.raises(ValueError):
        reschedule(session, b, starts_at=at(15))


def test_couples_price_is_for_the_whole_booking(session, spa):
    pairs = find_couples(session, spa.branch.id, spa.couples60.id, at(10))
    b = book(
        session,
        spa,
        None,
        at(10),
        variant_id=spa.couples60.id,
        guests=[GuestRequest(pair=p) for p in pairs],
    )
    assert sorted(i.price for i in b.items) == [Decimal("750"), Decimal("750")]


def test_history_keeps_the_order_things_happened(session, spa):
    b = book(session, spa, Pair(spa.ana.id, spa.bed_a.id, spa.bed_a.room_id), at(10))
    for s in ("confirmed", "checked_in", "in_service"):
        transition(session, b, m.BookingStatus(s))
    events = session.scalars(
        select(m.BookingEvent.event_type)
        .where(m.BookingEvent.booking_id == b.id)
        .order_by(m.BookingEvent.occurred_at)
    ).all()
    assert events == [
        "booking.created",
        "booking.confirmed",
        "booking.checked_in",
        "booking.in_service",
    ]
