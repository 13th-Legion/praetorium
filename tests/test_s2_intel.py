"""S2 Intelligence & Security — models, RBAC, and helpers."""

import pytest
from datetime import datetime, timezone

from sqlalchemy import select

from app.routes.s2_admin import S2_ROLES, _can_manage, _clean_html, _dtg
from app.models.s2_intel import (
    IIR,
    S2ChallengePassword,
    S2TrainingSite,
    S2TrainingSiteMap,
)
from tests.factories import make_member, make_event

pytestmark = pytest.mark.integration


# ─── RBAC + helpers (pure) ──────────────────────────────────────────────────

class TestS2RBAC:
    def test_roles(self):
        assert S2_ROLES == {"s2", "command", "admin"}

    def test_s2_can_manage(self):
        assert _can_manage({"roles": ["s2"]}) is True

    def test_command_can_manage(self):
        assert _can_manage({"roles": ["command"]}) is True

    def test_outsider_cannot_manage(self):
        assert _can_manage({"roles": ["s1"]}) is False

    def test_no_user_cannot_manage(self):
        assert _can_manage(None) is False

    def test_guest_bundle_cannot_manage(self):
        # Guests are handed s2/command/admin so pages render. That is not S2.
        assert _can_manage({"roles": ["guest", "s2", "command", "admin"]}) is False


class TestHelpers:
    def test_dtg_format(self):
        d = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
        assert _dtg(d) == "241800Z SEP 26"

    def test_clean_html_strips_script(self):
        out = _clean_html("<p>hello</p><script>alert(1)</script>")
        assert "<script" not in out
        assert "hello" in out

    def test_clean_html_allows_links(self):
        out = _clean_html('<a href="https://x.com">x</a>')
        assert 'href="https://x.com"' in out

    def test_clean_html_strips_img_style_attr(self):
        # img allows src/alt/width/height but NOT style (data-URI XSS guard)
        out = _clean_html('<img src="https://x/y.png" style="width:999px">')
        assert "style=" not in out
        assert "src=" in out


# ─── Models ──────────────────────────────────────────────────────────────────

class TestIIRModel:
    async def test_defaults(self, db_session):
        m = await make_member(db_session)
        i = IIR(report_number="IIR-0001", dtg="241800Z SEP 26",
                country_area="DFW", subject="Test", details="<p>body</p>",
                author_id=m.id)
        db_session.add(i)
        await db_session.flush()
        assert i.dissemination_tier == "command_only"
        assert i.status == "active"

    async def test_tier_can_be_unit_wide(self, db_session):
        i = IIR(report_number="IIR-0002", dtg="241800Z SEP 26",
                country_area="DFW", subject="T", details="d",
                dissemination_tier="unit_wide")
        db_session.add(i)
        await db_session.flush()
        assert i.dissemination_tier == "unit_wide"


class TestChallengePassword:
    async def test_defaults(self, db_session):
        s = S2ChallengePassword(challenge="Texas", password="Star")
        db_session.add(s)
        await db_session.flush()
        assert s.active is True
        assert s.running_password is None

    async def test_event_scoped(self, db_session):
        ev = await make_event(db_session, category="ftx")
        s = S2ChallengePassword(challenge="Texas", password="Star", event_id=ev.id)
        db_session.add(s)
        await db_session.flush()
        assert s.event_id == ev.id


class TestTrainingSites:
    async def test_site_and_maps(self, db_session):
        s = S2TrainingSite(key="able", name="Able", address="1925 E FM 4")
        db_session.add(s)
        await db_session.flush()
        db_session.add(S2TrainingSiteMap(site_id=s.id, label="1:25,000", url="/x.pdf"))
        await db_session.flush()

        from sqlalchemy.orm import selectinload
        loaded = (await db_session.execute(
            select(S2TrainingSite).where(S2TrainingSite.id == s.id).options(selectinload(S2TrainingSite.maps))
        )).scalar_one()
        assert len(loaded.maps) == 1
        assert loaded.maps[0].label == "1:25,000"

    async def test_site_key_unique(self, db_session):
        db_session.add(S2TrainingSite(key="able", name="Able"))
        await db_session.flush()
        db_session.add(S2TrainingSite(key="able", name="Dup"))
        with pytest.raises(Exception):
            await db_session.flush()
