"""Ops console: immunes, guest guard, duty team.

PP-324 / PP-325 / PP-323 backend behaviour. The 10s HTMX roster refresh is
covered by asserting the roster partial re-renders the badge from DB state.
"""

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.events import EventGuardDuty, EventGuardSlot, EventGuest, EventRSVP
from tests.factories import make_event, make_member, make_rsvp

pytestmark = pytest.mark.integration


def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


async def _slot(db, event, number=1, label="0000-0200"):
    slot = EventGuardSlot(
        event_id=event.id, slot_number=number, slot_label=label,
        created_at=datetime.utcnow(),
    )
    db.add(slot)
    await db.flush()
    return slot


# ─── PP-324 Immunes ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_assign_skips_immunes(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session)
    eligible = await make_member(db_session, last_name="Eligible")
    exempt = await make_member(db_session, last_name="Exempt")
    await make_rsvp(db_session, event, eligible, attended=False, checked_in=True)
    await make_rsvp(
        db_session, event, exempt, attended=False, checked_in=True, immunes=True
    )
    await _slot(db_session, event)
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/events/{event.id}/ops/guard/auto-assign",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 200), resp.text

    async with db_sessionmaker() as s:
        duties = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assigned = {d.member_id for d in duties}
        assert eligible.id in assigned
        assert exempt.id not in assigned


@pytest.mark.asyncio
async def test_clearing_immunes_returns_member_to_auto_assign_pool(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session)
    m = await make_member(db_session)
    await make_rsvp(db_session, event, m, attended=False, checked_in=True, immunes=True)
    await _slot(db_session, event)
    await db_session.commit()

    tok = _csrf(auth_client)
    auth_client.post(
        f"/events/{event.id}/ops/guard/auto-assign",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    async with db_sessionmaker() as s:
        n = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assert n == []

    resp = auth_client.post(
        f"/events/{event.id}/ops/immunes",
        data={"member_id": m.id, "immunes": "0", "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert resp.status_code == 200, resp.text
    assert "badge-immunes" not in resp.text

    auth_client.post(
        f"/events/{event.id}/ops/guard/auto-assign",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    async with db_sessionmaker() as s:
        duties = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assert {d.member_id for d in duties} == {m.id}


@pytest.mark.asyncio
async def test_marking_immunes_does_not_remove_existing_guard_assignment(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session)
    m = await make_member(db_session)
    await make_rsvp(db_session, event, m, attended=False, checked_in=True)
    slot = await _slot(db_session, event)
    db_session.add(EventGuardDuty(
        event_id=event.id, slot_id=slot.id, slot_number=1, slot_label=slot.slot_label,
        member_id=m.id, assigned_by="test", created_at=datetime.utcnow(),
    ))
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/events/{event.id}/ops/immunes",
        data={"member_id": m.id, "immunes": "1", "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert resp.status_code == 200, resp.text
    assert "badge-immunes" in resp.text
    assert "Immunes" in resp.text

    async with db_sessionmaker() as s:
        duties = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assert len(duties) == 1
        assert duties[0].member_id == m.id
        rsvp = (await s.execute(
            select(EventRSVP).where(
                EventRSVP.event_id == event.id, EventRSVP.member_id == m.id
            )
        )).scalar_one()
        assert rsvp.immunes is True


@pytest.mark.asyncio
async def test_manual_guard_assign_immunes_requires_override(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session)
    m = await make_member(db_session)
    await make_rsvp(db_session, event, m, attended=False, checked_in=True, immunes=True)
    await _slot(db_session, event)
    await db_session.commit()

    tok = _csrf(auth_client)
    blocked = auth_client.post(
        f"/events/{event.id}/ops/guard/assign",
        data={"slot_number": 1, "member_id": m.id, "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    assert blocked.status_code == 409, blocked.text

    async with db_sessionmaker() as s:
        n = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assert n == []

    ok = auth_client.post(
        f"/events/{event.id}/ops/guard/assign",
        data={
            "slot_number": 1, "member_id": m.id,
            "override_immunes": "1", "csrf_token": tok,
        },
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    assert ok.status_code in (303, 200), ok.text

    async with db_sessionmaker() as s:
        duties = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assert {d.member_id for d in duties} == {m.id}


@pytest.mark.asyncio
async def test_immunes_badge_survives_roster_partial_refresh(
    auth_client, db_session, patch_global_session
):
    event = await make_event(db_session)
    m = await make_member(db_session, last_name="Medic")
    await make_rsvp(db_session, event, m, attended=False, checked_in=True, immunes=True)
    await db_session.commit()

    resp = auth_client.get(f"/events/{event.id}/ops/roster")
    assert resp.status_code == 200, resp.text
    assert "badge-immunes" in resp.text
    assert "Medic" in resp.text
    assert "Mark Immunes" not in resp.text or "Clear Immunes" in resp.text


async def _guest(db, event, sponsor, first="Pat", last="Visitor", checked_in=True):
    g = EventGuest(
        event_id=event.id,
        sponsor_id=sponsor.id,
        first_name=first,
        last_name=last,
        relation="friend",
        is_walkin=True,
        checked_in_at=datetime.utcnow() if checked_in else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(g)
    await db.flush()
    return g


# ─── PP-325 Guests on guard ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_assign_appends_checked_in_guests_after_members(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    """Guests are appended after members (not interleaved by name)."""
    event = await make_event(db_session)
    member = await make_member(db_session, last_name="Zebra")
    sponsor = await make_member(db_session, last_name="Sponsor")
    await make_rsvp(db_session, event, member, attended=False, checked_in=True)
    await make_rsvp(db_session, event, sponsor, attended=False, checked_in=False)
    guest = await _guest(db_session, event, sponsor, first="Ann", last="Able")
    prereg = await _guest(
        db_session, event, sponsor, first="Skip", last="Prereg", checked_in=False
    )
    await _slot(db_session, event, number=1, label="0000-0200")
    await _slot(db_session, event, number=2, label="0200-0400")
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/events/{event.id}/ops/guard/auto-assign",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 200), resp.text

    async with db_sessionmaker() as s:
        duties = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
            .order_by(EventGuardDuty.id)
        )).scalars().all()
        assert [d.member_id for d in duties] == [member.id, None]
        assert [d.guest_id for d in duties] == [None, guest.id]
        assert duties[0].slot_number == 1
        assert duties[1].slot_number == 2
        assert prereg.id not in {d.guest_id for d in duties}


@pytest.mark.asyncio
async def test_guest_guard_unique_constraint(db_session):
    event = await make_event(db_session)
    sponsor = await make_member(db_session)
    guest = await _guest(db_session, event, sponsor)
    slot = await _slot(db_session, event)
    db_session.add(EventGuardDuty(
        event_id=event.id, slot_id=slot.id, slot_number=1, slot_label=slot.slot_label,
        guest_id=guest.id, assigned_by="test", created_at=datetime.utcnow(),
    ))
    await db_session.flush()
    db_session.add(EventGuardDuty(
        event_id=event.id, slot_id=slot.id, slot_number=1, slot_label=slot.slot_label,
        guest_id=guest.id, assigned_by="test", created_at=datetime.utcnow(),
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_guest_guard_controls_and_config_uses_name_not_id(
    auth_client, db_session, patch_global_session
):
    event = await make_event(db_session)
    sponsor = await make_member(db_session, last_name="Host", first_name="Sam")
    await make_rsvp(db_session, event, sponsor, attended=False, checked_in=True)
    guest = await _guest(db_session, event, sponsor, first="Riley", last="Guestson")
    slot = await _slot(db_session, event)
    db_session.add(EventGuardDuty(
        event_id=event.id, slot_id=slot.id, slot_number=1, slot_label=slot.slot_label,
        guest_id=guest.id, assigned_by="test", created_at=datetime.utcnow(),
    ))
    await db_session.commit()

    roster = auth_client.get(f"/events/{event.id}/ops/roster")
    assert roster.status_code == 200, roster.text
    assert "Guestson" in roster.text
    assert "badge-guest" in roster.text
    assert "Slot 1" in roster.text

    cfg = auth_client.get(f"/events/{event.id}/ops/guard/config")
    assert cfg.status_code == 200, cfg.text
    assert f"Guest #{guest.id}" not in cfg.text
    assert "Guestson, Riley" in cfg.text
    assert "Host" in cfg.text


@pytest.mark.asyncio
async def test_checked_in_guest_has_guard_assign_control(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session)
    sponsor = await make_member(db_session, last_name="Host")
    await make_rsvp(db_session, event, sponsor, attended=False, checked_in=True)
    guest = await _guest(db_session, event, sponsor, first="Riley", last="Guestson")
    await _slot(db_session, event)
    await db_session.commit()

    roster = auth_client.get(f"/events/{event.id}/ops/roster")
    assert roster.status_code == 200, roster.text
    assert 'name="guest_id"' in roster.text
    assert f'value="{guest.id}"' in roster.text
    assert "badge-guest" in roster.text

    tok = _csrf(auth_client)
    assigned = auth_client.post(
        f"/events/{event.id}/ops/guard/assign",
        data={"slot_number": 1, "guest_id": guest.id, "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    assert assigned.status_code in (303, 200), assigned.text

    async with db_sessionmaker() as s:
        duties = (await s.execute(
            select(EventGuardDuty).where(EventGuardDuty.event_id == event.id)
        )).scalars().all()
        assert len(duties) == 1
        assert duties[0].guest_id == guest.id
        assert duties[0].member_id is None

