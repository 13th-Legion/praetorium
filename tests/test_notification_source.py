"""Notification source tracking — a deleted record purges the rows it fanned out.

When an announcement is created, each member's in-portal Notification records the
NC announcement id in `source_id` (``announcement:<id>``). Deleting that
announcement must remove those rows, so no member is left with a bell entry
pointing at a gone announcement. Regression: deleting an announcement only called
the NC Announcement Center delete, leaving the in-portal rows orphaned.
"""

import pytest

from app.models.notifications import Notification
from app.routes.notifications import (
    create_notification_for_all,
    delete_notifications_by_source,
)
from sqlalchemy import select
from tests.factories import make_member

pytestmark = pytest.mark.integration


class TestNotificationSourcePurge:
    async def test_create_for_all_stamps_source_id(self, db_session):
        m1 = await make_member(db_session)
        m2 = await make_member(db_session)
        await db_session.flush()

        await create_notification_for_all(
            db_session, "announcement", "📢 Test",
            body="hi", link="/dashboard", icon="📢",
            source_id="announcement:42",
        )

        result = await db_session.execute(select(Notification))
        rows = result.scalars().all()
        assert len(rows) == 2
        assert all(r.source_id == "announcement:42" for r in rows)
        assert {r.member_id for r in rows} == {m1.id, m2.id}

    async def test_delete_by_source_removes_only_matching(self, db_session):
        m1 = await make_member(db_session)
        m2 = await make_member(db_session)
        await db_session.flush()

        await create_notification_for_all(
            db_session, "announcement", "📢 A", source_id="announcement:1"
        )
        await create_notification_for_all(
            db_session, "announcement", "📢 B", source_id="announcement:2"
        )

        removed = await delete_notifications_by_source(db_session, "announcement:1")
        assert removed == 2

        result = await db_session.execute(
            select(Notification).order_by(Notification.id)
        )
        remaining = result.scalars().all()
        assert len(remaining) == 2
        assert all(r.source_id == "announcement:2" for r in remaining)

    async def test_legacy_rows_without_source_survive(self, db_session):
        # Rows created before source_id existed (null) are never swept by a purge.
        m = await make_member(db_session)
        await db_session.flush()
        db_session.add(Notification(
            member_id=m.id, category="announcement", title="📢 Legacy",
        ))
        await db_session.flush()

        removed = await delete_notifications_by_source(db_session, "announcement:9")
        assert removed == 0

        result = await db_session.execute(select(Notification))
        assert len(result.scalars().all()) == 1
