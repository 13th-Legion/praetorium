"""Ops-console check-in vs official attendance, and Finalize's copy step.

The confirmation list used to show every RSVP with boxes driven only by
`attended`, so a fully checked-in FTX looked empty. Finalize already copied
`checked_in -> attended` (PP-074d); unchecking did not clear check-in, so
Finalize would put the person right back. These pin both sides.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.events import EventRSVP
from app.models.member import Member
from app.models.training import MemberTradoc, TradocItem
from app.services import attendance as attendance_svc
from tests.factories import make_event, make_member, make_rsvp

pytestmark = pytest.mark.integration


def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


@pytest.mark.asyncio
async def test_present_means_checked_in_or_attended(db_session):
    event = await make_event(db_session)
    m = await make_member(db_session)
    only_in = await make_rsvp(db_session, event, m, attended=False, checked_in=True)
    assert attendance_svc.is_present(only_in) is True
    only_att = await make_rsvp(
        db_session, event, await make_member(db_session),
        attended=True, checked_in=False,
    )
    assert attendance_svc.is_present(only_att) is True
    neither = await make_rsvp(
        db_session, event, await make_member(db_session),
        attended=False, checked_in=False,
    )
    assert attendance_svc.is_present(neither) is False


@pytest.mark.asyncio
async def test_revoke_clears_both_flags_and_recomputes_ftx(db_session):
    event = await make_event(db_session, title="Field Training Exercise",
                             date_start=datetime(2026, 9, 11, 19, 0))
    older = await make_event(db_session, title="Older FTX",
                             date_start=datetime(2026, 7, 10, 19, 0))
    m = await make_member(db_session, ftx_count=2, last_ftx=date(2026, 9, 11))
    rsvp = await make_rsvp(db_session, event, m, attended=True, checked_in=True,
                           checked_in_by="levi.kavadas")
    await make_rsvp(db_session, older, m, attended=True, checked_in=True)
    radio = TradocItem(block=3, block_name="Supplemental", name="Radio")
    patrol = TradocItem(block=4, block_name="Combat", name="Patrolling")
    db_session.add_all([radio, patrol])
    await db_session.flush()
    db_session.add(MemberTradoc(
        member_id=m.id, item_id=radio.id, signed_off_by="auto",
        ftx_date=date(2026, 9, 11),
        notes="Auto-credited: Field Training Exercise",
    ))
    # A different FTX's auto-credit must survive.
    db_session.add(MemberTradoc(
        member_id=m.id, item_id=patrol.id, signed_off_by="auto",
        ftx_date=date(2026, 7, 10),
        notes="Auto-credited: Older FTX",
    ))
    await db_session.flush()
    patrol_id = patrol.id

    await attendance_svc.revoke_rsvp_attendance(db_session, event, rsvp)
    await db_session.commit()

    await db_session.refresh(rsvp)
    await db_session.refresh(m)
    assert rsvp.checked_in is False
    assert rsvp.attended is False
    assert rsvp.checked_in_by is None
    assert m.ftx_count == 1
    assert m.last_ftx == date(2026, 7, 10)

    left = (await db_session.execute(
        select(MemberTradoc).where(MemberTradoc.member_id == m.id)
    )).scalars().all()
    assert [row.item_id for row in left] == [patrol_id]


@pytest.mark.asyncio
async def test_finalize_copies_checkins_that_are_not_yet_attended(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session, title="FTX Copy Test")
    showed = await make_member(db_session)
    no_show = await make_member(db_session)
    await make_rsvp(db_session, event, showed, attended=False, checked_in=True,
                    status="attending")
    await make_rsvp(db_session, event, no_show, attended=False, checked_in=False,
                    status="attending")
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/api/events/{event.id}/finalize",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert resp.status_code == 200, resp.text
    assert "finalized" in resp.text.lower()

    async with db_sessionmaker() as s:
        rows = (await s.execute(
            select(EventRSVP).where(EventRSVP.event_id == event.id)
        )).scalars().all()
        by_member = {r.member_id: r for r in rows}
        assert by_member[showed.id].attended is True
        assert by_member[no_show.id].attended is False


@pytest.mark.asyncio
async def test_unchecking_clears_checkin_so_finalize_cannot_restore(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    """The bug: toggle only flipped `attended`, Finalize copied check-in back."""
    event = await make_event(db_session, title="FTX Toggle Test")
    m = await make_member(db_session)
    rsvp = await make_rsvp(db_session, event, m, attended=False, checked_in=True,
                           status="attending")
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/api/events/{event.id}/attendance/{rsvp.id}",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert resp.status_code == 200, resp.text

    async with db_sessionmaker() as s:
        got = (await s.execute(
            select(EventRSVP).where(EventRSVP.id == rsvp.id)
        )).scalar_one()
        assert got.attended is False
        assert got.checked_in is False, "must clear check-in or Finalize restores them"

    tok = _csrf(auth_client)
    auth_client.post(
        f"/api/events/{event.id}/finalize",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    async with db_sessionmaker() as s:
        got = (await s.execute(
            select(EventRSVP).where(EventRSVP.id == rsvp.id)
        )).scalar_one()
        assert got.attended is False


@pytest.mark.asyncio
async def test_roster_seeds_from_checkin_and_hides_declined(
    auth_client, db_session, patch_global_session
):
    event = await make_event(db_session)
    in_ops = await make_member(db_session, last_name="Inops")
    declined = await make_member(db_session, last_name="Declined")
    leftover = await make_member(db_session, last_name="Leftover")
    await make_rsvp(db_session, event, in_ops, attended=False, checked_in=True,
                    status="attending")
    await make_rsvp(db_session, event, declined, attended=False, checked_in=False,
                    status="declined")
    await make_rsvp(db_session, event, leftover, attended=False, checked_in=False,
                    status="attending")
    await db_session.commit()

    resp = auth_client.get(f"/api/events/{event.id}/attendance-roster")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert 'data-att-group="present"' in body
    assert "Inops" in body
    assert 'id="att-other"' in body
    assert "<details" in body
    assert "Declined / pending" in body
    # Check All must not target the declined expander.
    assert 'id="att-expected"' in body


@pytest.mark.asyncio
async def test_uncheckin_endpoint_clears_ops_and_official(
    auth_client, db_session, patch_global_session, db_sessionmaker
):
    event = await make_event(db_session)
    m = await make_member(db_session)
    await make_rsvp(db_session, event, m, attended=True, checked_in=True,
                    status="attending")
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/events/{event.id}/ops/uncheckin",
        data={"member_id": m.id, "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 200), resp.text

    async with db_sessionmaker() as s:
        got = (await s.execute(
            select(EventRSVP).where(
                EventRSVP.event_id == event.id, EventRSVP.member_id == m.id
            )
        )).scalar_one()
        assert got.checked_in is False
        assert got.attended is False
