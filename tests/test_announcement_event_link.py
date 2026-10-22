"""Announcement event-link embedding — the linked event must be visible."""

from datetime import datetime, timedelta

import pytest

from app.routes.announcements import _event_link_html, _html_to_markdown, _upcoming_events
from tests.factories import make_event

pytestmark = pytest.mark.integration


class TestEventLinkEmbedding:
    async def test_resolves_event_to_html_and_plain(self, db_session, patch_global_session):
        ev = await make_event(db_session, title="Monthly FTX")
        await db_session.flush()

        html, plain = await _event_link_html(str(ev.id))
        assert "Monthly FTX" in html
        assert "Monthly FTX" in plain
        assert f"/events/{ev.id}" in html
        assert f"/events/{ev.id}" in plain
        # The line must be a sanitizable link (no raw angle content leak).
        assert html.startswith("<p>📅")

    async def test_unknown_event_returns_empty(self, db_session):
        html, plain = await _event_link_html("999999")
        assert html == ""
        assert plain == ""

    async def test_non_numeric_returns_empty(self, db_session):
        html, plain = await _event_link_html("abc")
        assert html == ""
        assert plain == ""

    async def test_picker_reaches_past_the_old_25_event_cap(self, db_session, patch_global_session):
        """January events were cut off once 25 nearer rows filled the dropdown."""
        late_ids = []
        start = datetime(2027, 1, 2, 9, 0)
        for i in range(30):
            ev = await make_event(
                db_session,
                title=f"Picker {i}",
                date_start=start + timedelta(days=i),
            )
            late_ids.append(ev.id)
        far = await make_event(
            db_session,
            title="Way out",
            date_start=datetime(2030, 6, 1, 9, 0),
        )
        await db_session.flush()

        rows = await _upcoming_events()
        got = {row["id"] for row in rows}
        assert late_ids[25] in got
        assert far.id not in got

    async def test_event_line_survives_markdown_conversion(self, db_session, patch_global_session):
        """The event link must survive the Talk cross-post markdown conversion."""
        ev = await make_event(db_session, title="CQB Course")
        await db_session.flush()
        html, _plain = await _event_link_html(str(ev.id))
        body = f"<p>See you there.</p>{html}"
        md = _html_to_markdown(body)
        assert "CQB Course" in md
        assert f"/events/{ev.id}" in md
