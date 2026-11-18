"""Auto-award Volunteer Service ribbon to attended members on volunteering-event finalization (PP-424)."""

import pytest

from sqlalchemy import select

from app.models.ribbons import MemberRibbon, RibbonCatalog, VolunteerServiceGrant
from app.routes.events import _auto_award_volunteer_service, _reverse_volunteer_service
from tests.factories import make_member, make_event, make_rsvp

pytestmark = pytest.mark.integration


async def _seed_volunteer_catalog(db):
    """CI builds its schema without seed data; insert the catalog row."""
    exists = (await db.execute(
        select(RibbonCatalog).where(RibbonCatalog.code == "volunteer")
    )).scalar_one_or_none()
    if not exists:
        db.add(RibbonCatalog(
            code="volunteer",
            section="rack",
            name="Volunteer Service",
            precedence=19,
            base_points=5,
            device_increment=5,
            max_devices=0,
            is_auto=False,
            claimable=True,
            active=True,
        ))
        await db.flush()


async def test_awards_base_ribbon_to_attended(db_session):
    await _seed_volunteer_catalog(db_session)
    ev = await make_event(db_session, category="volunteering")
    m = await make_member(db_session, nc_username="vol1")
    await make_rsvp(db_session, ev, m, attended=True)
    await db_session.flush()

    summary = await _auto_award_volunteer_service(db_session, ev)
    await db_session.commit()

    assert "Auto-awarded Volunteer Service to 1 member" in summary
    ribbon = (await db_session.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == m.id, MemberRibbon.ribbon_code == "volunteer"
        )
    )).scalar_one()
    assert ribbon.device_count == 0
    assert ribbon.source == "auto"
    grant = (await db_session.execute(
        select(VolunteerServiceGrant).where(VolunteerServiceGrant.event_id == ev.id)
    )).scalar_one()
    assert grant.member_id == m.id


async def test_not_attended_gets_nothing(db_session):
    await _seed_volunteer_catalog(db_session)
    ev = await make_event(db_session, category="volunteering")
    m = await make_member(db_session, nc_username="vol2")
    await make_rsvp(db_session, ev, m, attended=False)
    await db_session.flush()

    summary = await _auto_award_volunteer_service(db_session, ev)
    await db_session.commit()

    assert "No confirmed attendees to award." in summary
    row = (await db_session.execute(
        select(MemberRibbon).where(MemberRibbon.ribbon_code == "volunteer")
    )).scalars().first()
    assert row is None


async def test_second_event_increments_device(db_session):
    await _seed_volunteer_catalog(db_session)
    m = await make_member(db_session, nc_username="vol3")
    ev1 = await make_event(db_session, category="volunteering")
    ev2 = await make_event(db_session, category="volunteering")
    await make_rsvp(db_session, ev1, m, attended=True)
    await make_rsvp(db_session, ev2, m, attended=True)
    await db_session.flush()

    await _auto_award_volunteer_service(db_session, ev1)
    await _auto_award_volunteer_service(db_session, ev2)
    await db_session.commit()

    ribbon = (await db_session.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == m.id, MemberRibbon.ribbon_code == "volunteer"
        )
    )).scalar_one()
    assert ribbon.device_count == 1


async def test_reverse_removes_base_ribbon(db_session):
    await _seed_volunteer_catalog(db_session)
    ev = await make_event(db_session, category="volunteering")
    m = await make_member(db_session, nc_username="vol4")
    await make_rsvp(db_session, ev, m, attended=True)
    await db_session.flush()

    await _auto_award_volunteer_service(db_session, ev)
    await db_session.commit()

    await _reverse_volunteer_service(db_session, ev)
    await db_session.commit()

    ribbon = (await db_session.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == m.id, MemberRibbon.ribbon_code == "volunteer"
        )
    )).scalar_one_or_none()
    assert ribbon is None
    grant = (await db_session.execute(
        select(VolunteerServiceGrant).where(VolunteerServiceGrant.event_id == ev.id)
    )).scalars().first()
    assert grant is None


async def test_reverse_only_decrements_when_more_stints(db_session):
    await _seed_volunteer_catalog(db_session)
    m = await make_member(db_session, nc_username="vol5")
    ev1 = await make_event(db_session, category="volunteering")
    ev2 = await make_event(db_session, category="volunteering")
    await make_rsvp(db_session, ev1, m, attended=True)
    await make_rsvp(db_session, ev2, m, attended=True)
    await db_session.flush()

    await _auto_award_volunteer_service(db_session, ev1)
    await _auto_award_volunteer_service(db_session, ev2)
    await db_session.commit()

    await _reverse_volunteer_service(db_session, ev1)
    await db_session.commit()

    ribbon = (await db_session.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == m.id, MemberRibbon.ribbon_code == "volunteer"
        )
    )).scalar_one()
    assert ribbon.device_count == 0


async def test_double_finalize_same_event_is_idempotent(db_session):
    await _seed_volunteer_catalog(db_session)
    ev = await make_event(db_session, category="volunteering")
    m = await make_member(db_session, nc_username="vol6")
    await make_rsvp(db_session, ev, m, attended=True)
    await db_session.flush()

    await _auto_award_volunteer_service(db_session, ev)
    await db_session.commit()
    summary2 = await _auto_award_volunteer_service(db_session, ev)
    await db_session.commit()

    assert "already awarded" in summary2
    ribbon = (await db_session.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == m.id, MemberRibbon.ribbon_code == "volunteer"
        )
    )).scalar_one()
    assert ribbon.device_count == 0
