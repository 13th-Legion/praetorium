"""Phase 2 — ribbon auto-derivation.

Focus on the reseed-proof fix: triggers resolve by stable NAME, not row id,
and FTX attendance thresholds award the right device counts.
"""

import pytest
from datetime import datetime, timedelta

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

    The two categories record their instructor in COMPLETELY DIFFERENT PLACES:

        ftx / mcftx     -> event_schedule_blocks.instructor_id
                           (events.instructor_id is NULL for every ftx row)
        online_training -> events.instructor_id
                           (these events carry no schedule blocks at all)

    The first version of this split counted only schedule blocks, so
    instructor_online could never fire for anyone -- no online_training event
    has a single schedule block. These tests pin BOTH mechanisms so that can
    not regress.

    Cav's rule: the instructor is rewarded AFTER the session finishes, so
    future occurrences of a recurring series must not count. Most
    online_training rows in production are future recurrences.
    """

    PAST = datetime(2026, 6, 1, 19, 0)
    FUTURE = datetime(2099, 1, 1, 19, 0)

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

    # ── online_training: events.instructor_id ────────────────────────────

    async def test_completed_online_session_awards_instructor_online(self, db_session):
        m = await make_member(db_session)
        await make_event(db_session, title="Online Training", category="online_training",
                         instructor_id=m.id, date_start=self.PAST)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_online" in codes
        assert "instructor_ftx" not in codes

    async def test_online_instructor_is_read_from_the_event_not_blocks(self, db_session):
        """Regression: counting only schedule blocks made this ribbon dead."""
        m = await make_member(db_session)
        e = await make_event(db_session, category="online_training",
                             instructor_id=m.id, date_start=self.PAST)
        # deliberately NO schedule block -- production online events have none
        from app.models.schedule import EventScheduleBlock
        from sqlalchemy import select as _sel, func as _f
        n = (await db_session.execute(
            _sel(_f.count()).select_from(EventScheduleBlock)
            .where(EventScheduleBlock.event_id == e.id))).scalar()
        assert n == 0

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_online" in codes

    async def test_future_online_session_does_not_count(self, db_session):
        """Reward comes AFTER it finishes; scheduled recurrences must not."""
        m = await make_member(db_session)
        await make_event(db_session, category="online_training",
                         instructor_id=m.id, date_start=self.FUTURE)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_online" not in codes

    async def test_cancelled_online_session_does_not_count(self, db_session):
        m = await make_member(db_session)
        await make_event(db_session, category="online_training", instructor_id=m.id,
                         date_start=self.PAST, status="cancelled")

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_online" not in codes

    async def test_online_device_count_is_one_less_than_sessions(self, db_session):
        m = await make_member(db_session)
        for i in range(3):
            await make_event(db_session, category="online_training", instructor_id=m.id,
                             date_start=self.PAST - timedelta(days=i))

        ribbons = await rd.derive_ribbons(db_session, m)
        online = next(r for r in ribbons if r["code"] == "instructor_online")
        assert online["device_count"] == 2

    async def test_finished_series_counts_but_future_siblings_do_not(self, db_session):
        """A recurring series: only the occurrences that already ran count."""
        m = await make_member(db_session)
        for i in range(2):
            await make_event(db_session, category="online_training", instructor_id=m.id,
                             date_start=self.PAST - timedelta(days=i))
        for i in range(5):
            await make_event(db_session, category="online_training", instructor_id=m.id,
                             date_start=self.FUTURE + timedelta(days=i))

        ribbons = await rd.derive_ribbons(db_session, m)
        online = next(r for r in ribbons if r["code"] == "instructor_online")
        assert online["device_count"] == 1, "only the 2 completed sessions count"

    # ── ftx / mcftx: event_schedule_blocks.instructor_id ─────────────────

    async def test_ftx_class_block_awards_instructor_ftx_not_online(self, db_session):
        m = await make_member(db_session)
        e = await make_event(db_session, title="FTX", category="ftx")
        await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" in codes
        assert "instructor_online" not in codes

    async def test_mcftx_class_block_also_counts_toward_instructor_ftx(self, db_session):
        m = await make_member(db_session)
        e = await make_event(db_session, title="MCFTX", category="mcftx")
        await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" in codes

    async def test_non_class_activity_earns_no_instructor_ribbon(self, db_session):
        """Running the admin block at an FTX is not instructing."""
        m = await make_member(db_session)
        e = await make_event(db_session, title="FTX", category="ftx")
        await self._block(db_session, e, m, activity_type="admin", title="Admin")

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" not in codes
        assert "instructor_online" not in codes

    async def test_event_level_instructor_on_ftx_does_not_grant_ftx_ribbon(self, db_session):
        """FTX instructors live on blocks; events.instructor_id is NULL for
        every production ftx row, so it must not be a second source."""
        m = await make_member(db_session)
        await make_event(db_session, category="ftx", instructor_id=m.id,
                         date_start=self.PAST)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" not in codes

    # ── both, and neither ────────────────────────────────────────────────

    async def test_both_ribbons_when_teaching_both_kinds(self, db_session):
        m = await make_member(db_session)
        f = await make_event(db_session, title="FTX", category="ftx")
        await self._block(db_session, f, m)
        await make_event(db_session, category="online_training",
                         instructor_id=m.id, date_start=self.PAST)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert {"instructor_ftx", "instructor_online"} <= codes

    async def test_other_categories_earn_nothing(self, db_session):
        """meeting / external_training / social must grant no instructor
        ribbon by EITHER mechanism. external_training is deliberately
        excluded -- Cav scoped this to online training only, and 74 meetings
        carry an events.instructor_id."""
        m = await make_member(db_session)
        for cat in ("meeting", "external_training", "social", "volunteering"):
            e = await make_event(db_session, title=cat, category=cat,
                                 instructor_id=m.id, date_start=self.PAST)
            await self._block(db_session, e, m)

        codes = {r["code"] for r in await rd.derive_ribbons(db_session, m)}
        assert "instructor_ftx" not in codes
        assert "instructor_online" not in codes
