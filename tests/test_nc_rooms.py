"""NC Talk room directory service + online-meeting join link."""

import pytest

from app.services import nc_rooms
from app.models.nc_room import NCRoom

pytestmark = pytest.mark.integration


async def _seed(db):
    rows = [
        ("7jrpakx2", "T1 · Command", True, 1),
        ("qxzmz85j", "T1 · S3 - Training & Ops", True, 2),
        ("hidden01", "Archived Room", False, 3),
    ]
    for t, n, sel, o in rows:
        db.add(NCRoom(token=t, name=n, meeting_selectable=sel, sort_order=o, archived=False))
    await db.flush()
    await db.commit()


class TestNCRooms:
    def test_join_url_format(self):
        assert nc_rooms.join_url("7jrpakx2") == "https://cloud.13thlegion.org/call/7jrpakx2"

    async def test_selectable_excludes_non_selectable(self, patch_global_session, db_session):
        # Use the async loader so we read the patched test session (the sync
        # accessor opens its own engine, which in tests is a separate in-memory DB).
        await _seed(db_session)
        sel = await nc_rooms.all_rooms_async(selectable_only=True)
        tokens = {r.token for r in sel}
        assert "7jrpakx2" in tokens
        assert "hidden01" not in tokens        # meeting_selectable=False excluded

    async def test_all_rooms_reads_seeded(self, patch_global_session, db_session):
        await _seed(db_session)
        rows = await nc_rooms.all_rooms_async()
        names = {r.name for r in rows}
        assert "T1 · Command" in names and "T1 · S3 - Training & Ops" in names
