"""Ops console: immunes, guest guard, duty team.

PP-324 / PP-325 / PP-323 backend behaviour. The 10s HTMX roster refresh is
covered by asserting the roster partial re-renders the badge from DB state.
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.events import EventGuardDuty, EventGuardSlot, EventRSVP
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
