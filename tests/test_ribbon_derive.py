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
        assert rd.FTX_DEVICE_THRESHOLDS == [5, 10, 25, 50]

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
