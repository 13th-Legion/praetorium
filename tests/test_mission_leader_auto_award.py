"""Auto-award Mission Leader ribbon to vexillation commanders on FTX finalization."""

import pytest

from app.models.events import EventVexillation
from app.models.ribbons import MemberRibbon, RibbonCatalog
from app.routes.events import _auto_award_mission_leader, _reverse_mission_leader
from tests.factories import make_member, make_event

pytestmark = pytest.mark.integration


async def _seed_mission_leader_catalog(db):
    """Ensure the mission_leader code exists in ribbon_catalog (FK target).

    CI builds its test schema via Base.metadata.create_all (no seed data), so
    the migration-0011 seed never reaches it. Prod carries the row; tests must
    insert their own.
    """
    from sqlalchemy import select
    exists = (await db.execute(
        select(RibbonCatalog).where(RibbonCatalog.code == "mission_leader")
    )).scalar_one_or_none()
    if not exists:
        db.add(RibbonCatalog(
            code="mission_leader", section="rack", name="Mission Leader",
            precedence=8, claimable=True,
        ))
        await db.flush()


async def _make_vex(db, event, commander_id=None, name="Alpha"):
    v = EventVexillation(
        event_id=event.id,
        name=name,
        commander_id=commander_id,
        field_status="in_field",
        created_by="test",
    )
    db.add(v)
    await db.flush()
    return v


class TestMissionLeaderAutoAward:
    @pytest.fixture(autouse=True)
    async def _seed_catalog(self, db_session):
        await _seed_mission_leader_catalog(db_session)

    async def test_awards_mission_leader_to_commander(self, db_session):
        ev = await make_event(db_session, category="ftx", title="Monthly FTX")
        m = await make_member(db_session)
        await _make_vex(db_session, ev, commander_id=m.id)
        await db_session.flush()

        summary = await _auto_award_mission_leader(db_session, ev)
        await db_session.commit()

        from sqlalchemy import select
        row = (await db_session.execute(
            select(MemberRibbon).where(
                MemberRibbon.member_id == m.id,
                MemberRibbon.ribbon_code == "mission_leader",
            )
        )).scalar_one_or_none()

        assert row is not None
        assert row.device_count == 0
        assert row.source == "auto"
        assert "commander" in summary.lower()

    async def test_increments_device_on_second_stint(self, db_session):
        ev = await make_event(db_session, category="ftx", title="Monthly FTX")
        m = await make_member(db_session)
        await _make_vex(db_session, ev, commander_id=m.id)
        db_session.add(MemberRibbon(
            member_id=m.id,
            ribbon_code="mission_leader",
            device_count=0,
            source="manual",
        ))
        await db_session.flush()

        await _auto_award_mission_leader(db_session, ev)
        await db_session.commit()

        from sqlalchemy import select
        row = (await db_session.execute(
            select(MemberRibbon).where(
                MemberRibbon.member_id == m.id,
                MemberRibbon.ribbon_code == "mission_leader",
            )
        )).scalar_one_or_none()

        assert row.device_count == 1

    async def test_no_vexillations_returns_no_award(self, db_session):
        ev = await make_event(db_session, category="ftx")
        await db_session.flush()
        summary = await _auto_award_mission_leader(db_session, ev)
        assert "No vexillation commanders" in summary

    async def test_dedupe_multiple_vexillations_same_commander(self, db_session):
        from sqlalchemy import select
        ev = await make_event(db_session, category="ftx", title="Monthly FTX")
        m = await make_member(db_session)
        await _make_vex(db_session, ev, commander_id=m.id, name="Alpha")
        await _make_vex(db_session, ev, commander_id=m.id, name="Bravo")
        await db_session.flush()

        summary = await _auto_award_mission_leader(db_session, ev)
        await db_session.commit()

        rows = (await db_session.execute(
            select(MemberRibbon).where(
                MemberRibbon.member_id == m.id,
                MemberRibbon.ribbon_code == "mission_leader",
            )
        )).scalars().all()

        assert len(rows) == 1
        assert "commander" in summary.lower()

    async def test_second_pass_same_event_does_not_add_a_device(self, db_session):
        from sqlalchemy import select
        ev = await make_event(db_session, category="ftx", title="Monthly FTX")
        m = await make_member(db_session)
        await _make_vex(db_session, ev, commander_id=m.id)
        await db_session.flush()

        await _auto_award_mission_leader(db_session, ev)
        summary = await _auto_award_mission_leader(db_session, ev)
        await db_session.commit()

        row = (await db_session.execute(
            select(MemberRibbon).where(MemberRibbon.member_id == m.id)
        )).scalar_one()
        assert row.device_count == 0
        assert "already recorded" in summary.lower()

    async def test_reverse_deletes_the_only_auto_award(self, db_session):
        from sqlalchemy import select
        ev = await make_event(db_session, category="ftx")
        m = await make_member(db_session)
        await _make_vex(db_session, ev, commander_id=m.id)
        await _auto_award_mission_leader(db_session, ev)
        await _reverse_mission_leader(db_session, ev)
        await db_session.commit()

        row = (await db_session.execute(
            select(MemberRibbon).where(MemberRibbon.member_id == m.id)
        )).scalar_one_or_none()
        assert row is None

    async def test_reverse_one_stint_of_two_decrements(self, db_session):
        from sqlalchemy import select
        ev1 = await make_event(db_session, category="ftx", title="One")
        ev2 = await make_event(db_session, category="ftx", title="Two")
        m = await make_member(db_session)
        await _make_vex(db_session, ev1, commander_id=m.id, name="Alpha")
        await _make_vex(db_session, ev2, commander_id=m.id, name="Alpha")
        await _auto_award_mission_leader(db_session, ev1)
        await _auto_award_mission_leader(db_session, ev2)
        await _reverse_mission_leader(db_session, ev2)
        await db_session.commit()

        row = (await db_session.execute(
            select(MemberRibbon).where(MemberRibbon.member_id == m.id)
        )).scalar_one()
        assert row.device_count == 0
        assert row.source == "auto"

    async def test_skips_commanderless_vexillations(self, db_session):
        ev = await make_event(db_session, category="ftx")
        await _make_vex(db_session, ev, commander_id=None)
        await db_session.flush()
        summary = await _auto_award_mission_leader(db_session, ev)
        assert "No vexillation commanders" in summary
