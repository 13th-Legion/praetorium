"""A second award keeps its own comment. The first comment stays."""

import pytest
from sqlalchemy import select

from app.models.ribbons import MemberRibbon, RibbonCatalog, RibbonCitation
from app.services.ribbon_citations import citations_for, record_citation
from tests.factories import make_member

pytestmark = pytest.mark.integration


async def _catalog(db):
    db.add(RibbonCatalog(
        code="real_world_deploy", section="rack", name="Real World Deployment",
        precedence=3, base_points=15, device_increment=5, max_devices=0, claimable=True,
    ))
    await db.flush()


class TestRibbonCitations:
    async def test_second_award_keeps_the_first_comment(self, db_session):
        await _catalog(db_session)
        member = await make_member(db_session)
        ribbon = MemberRibbon(
            member_id=member.id, ribbon_code="real_world_deploy",
            device_count=0, reason="Kerrville supply run", awarded_by="Cav", source="manual",
        )
        db_session.add(ribbon)
        await record_citation(db_session, ribbon, 0, "Kerrville supply run", "Cav")

        ribbon.device_count = 1
        await record_citation(db_session, ribbon, 1, "Waco wildfire drop", "Cav")
        await db_session.commit()

        notes = await citations_for(db_session, [ribbon.id])
        assert notes[ribbon.id] == ["Kerrville supply run", "Waco wildfire drop"]
        assert ribbon.reason == "Kerrville supply run"

    async def test_blank_followup_does_not_erase_the_comment(self, db_session):
        await _catalog(db_session)
        member = await make_member(db_session)
        ribbon = MemberRibbon(
            member_id=member.id, ribbon_code="real_world_deploy",
            device_count=0, reason="Kerrville supply run", source="manual",
        )
        db_session.add(ribbon)
        await record_citation(db_session, ribbon, 0, "Kerrville supply run", "Cav")
        await record_citation(db_session, ribbon, 0, "   ", "Cav")
        await db_session.commit()

        row = (await db_session.execute(
            select(RibbonCitation).where(RibbonCitation.member_ribbon_id == ribbon.id)
        )).scalar_one()
        assert row.reason == "Kerrville supply run"
