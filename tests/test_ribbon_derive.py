"""Phase 2 — ribbon auto-derivation.

Focus on the reseed-proof fix: triggers resolve by stable NAME, not row id,
and FTX attendance thresholds award the right device counts.
"""

import pytest

from app.services import ribbon_derive as rd
from tests.factories import make_member, make_event, make_rsvp

pytestmark = pytest.mark.integration


class TestFtxDeviceThresholds:
    def test_thresholds_are_5_10_25_50(self):
        # Thresholds now come from app_settings (fallback default when unseeded).
        from app.services import settings_store
        assert settings_store.ftx_device_thresholds() == [5, 10, 25, 50]

    def test_device_count_steps(self):
        assert rd._ftx_devices(0) == 0
        assert rd._ftx_devices(4) == 0
        assert rd._ftx_devices(5) == 1
        assert rd._ftx_devices(10) == 2
        assert rd._ftx_devices(25) == 3
        assert rd._ftx_devices(50) == 4


class TestNameResolvers:
    async def test_cert_resolver_warns_and_skips_missing(self, db_session):
        # Empty cert table → every name is missing → empty map, no crash.
        result = await rd._resolve_cert_ids(db_session, ["Sabre", "Marksman"])
        assert result == {}

    async def test_item_resolver_missing_returns_empty(self, db_session):
        result = await rd._resolve_item_ids(db_session, {"Basic Land Navigation"})
        assert result == set()

    async def test_cert_resolver_matches_by_name_case_insensitive(self, db_session):
        from app.models.training import Certification
        c = Certification(name="Sabre", category="tab")
        db_session.add(c)
        await db_session.flush()
        result = await rd._resolve_cert_ids(db_session, ["sabre"])
        assert result == {"sabre": c.id}


class TestDeriveFtxAttendance:
    async def test_five_ftx_awards_ftx_ribbon_with_device(self, db_session):
        m = await make_member(db_session)
        for i in range(5):
            e = await make_event(db_session, title=f"FTX {i}", category="ftx")
            await make_rsvp(db_session, e, m, attended=True)
        await db_session.flush()
        ribbons = await rd.derive_ribbons(db_session, m)
        codes = {r["code"] for r in ribbons}
        assert "ftx" in codes
        ftx = next(r for r in ribbons if r["code"] == "ftx")
        assert ftx["device_count"] == 1   # 5 attended = first device tier


class TestInstructorRibbonSplit:
    """Instructor ribbons are split by event category (Cav, 2026-09-21).

    The rule used to count EVERY class block regardless of category:

        select(count()).select_from(EventScheduleBlock)
            .where(instructor_id == mid, activity_type == "class")

    so teaching an online_training session silently counted toward the FTX
    instructor ribbon, and instructor_online had no derivation at all -- it was
    manual-only, which is why "online training should reward the instructor"
    never happened. instructor_ftx is now ftx/mcftx only; instructor_online
    covers online_training.
    """

    @staticmethod
    async def _block(db, event, member, activity_type="class", start="0800", title="CLASS: Test"):
        from app.models.schedule import EventScheduleBlock
        b = EventScheduleBlock(
            event_id=event.id,
            instructor_id=member.id,
            activity_type=activity_type,
            day_number=1,
            start_time=start,
            title=title,
            created_by="test",
        )
        db.add(b)
        await db.flush()
        return b

    async def test_online_training_awards_instructor_online_not_ftx(self, db_session):
        """The exact regression: an online class must NOT bump instructor_ftx."""
        m = await make_member(db_session)
        e = await make_event(db_session, title="Comms Class", category="online_training")
        await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_online" in codes
        assert "instructor_ftx" not in codes, (
            "an online_training class must not count toward the FTX instructor ribbon"
        )

    async def test_ftx_class_awards_instructor_ftx_not_online(self, db_session):
        m = await make_member(db_session)
        e = await make_event(db_session, title="FTX", category="ftx")
        await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" in codes
        assert "instructor_online" not in codes

    async def test_mcftx_class_also_counts_toward_instructor_ftx(self, db_session):
        m = await make_member(db_session)
        e = await make_event(db_session, title="MCFTX", category="mcftx")
        await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" in codes

    async def test_device_count_is_one_less_than_classes_taught(self, db_session):
        m = await make_member(db_session)
        for i in range(3):
            e = await make_event(db_session, title=f"Online {i}", category="online_training")
            await self._block(db_session, e, m, start=f"0{8 + i}00")

        ribbons = await rd.derive_ribbons(db_session, m)
        online = next(r for r in ribbons if r["code"] == "instructor_online")
        assert online["device_count"] == 2  # 3 taught, base + 2 devices

    async def test_non_class_activity_earns_no_instructor_ribbon(self, db_session):
        """Running the admin block at an FTX is not instructing."""
        m = await make_member(db_session)
        e = await make_event(db_session, title="FTX", category="ftx")
        await self._block(db_session, e, m, activity_type="admin", title="Admin")

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" not in codes
        assert "instructor_online" not in codes

    async def test_both_ribbons_when_teaching_both_kinds(self, db_session):
        m = await make_member(db_session)
        f = await make_event(db_session, title="FTX", category="ftx")
        o = await make_event(db_session, title="Online", category="online_training")
        await self._block(db_session, f, m)
        await self._block(db_session, o, m, start="0900")

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert {"instructor_ftx", "instructor_online"} <= codes

    async def test_other_categories_earn_nothing(self, db_session):
        """meeting / external_training / social must not grant instructor ribbons.

        external_training is deliberately excluded -- Cav scoped this to online
        training only.
        """
        m = await make_member(db_session)
        for cat in ("meeting", "external_training", "social", "volunteering"):
            e = await make_event(db_session, title=cat, category=cat)
            await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" not in codes
        assert "instructor_online" not in codes
