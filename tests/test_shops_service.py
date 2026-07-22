"""Wave B — shops service (DB-backed S1-S6 SSoT)."""

import pytest

from app.services import shops
from app.models.shop import Shop

pytestmark = pytest.mark.integration


async def _seed(db):
    rows = [
        ("S1", "S1 — Personnel & Administration", "S1 — Personnel", "📋", "s1", "s1", True, 1),
        ("S3", "S3 — Operations & Training", "S3 — Ops & Training", "⚔️", "s3", "s3,leader", True, 3),
        ("S5", "S5 — Medical", "S5 — Medical", "🩹", "s5", "s5", False, 5),
    ]
    for k, n, sn, ic, r, ar, hd, o in rows:
        db.add(Shop(key=k, name=n, short_name=sn, icon=ic, role=r, access_roles=ar,
                    has_dashboard=hd, sort_order=o, description="x", archived=False))
    await db.flush()
    await db.commit()


class TestFallback:
    def test_fallback_never_empty(self):
        shops.invalidate()
        alls = shops.all_shops()
        assert len(alls) == 6
        assert shops.name_map()["S3"] == "S3 — Operations & Training"

    def test_s3_access_includes_leader(self):
        shops.invalidate()
        assert "leader" in shops.access_roles("s3")
        assert {"command", "admin"}.issubset(shops.access_roles("s3"))

    def test_s1_access_no_leader(self):
        shops.invalidate()
        assert "leader" not in shops.access_roles("s1")

    def test_role_map(self):
        shops.invalidate()
        assert shops.role_map()["S1"] == "s1"

    def test_meta_has_dashboard(self):
        shops.invalidate()
        assert shops.meta_map()["s1"]["has_dashboard"] is True
        assert shops.meta_map()["s5"]["has_dashboard"] is False


class TestAsyncFromDB:
    async def test_reads_seeded(self, patch_global_session, db_session):
        await _seed(db_session)
        rows = await shops.all_shops_async()
        keys = {s.key for s in rows}
        assert {"S1", "S3", "S5"}.issubset(keys)
        s3 = next(s for s in rows if s.key == "S3")
        assert "leader" in s3.access_roles
