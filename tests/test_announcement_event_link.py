"""Announcement event-link embedding — the linked event must be visible."""

import pytest

from app.routes.announcements import _event_link_html, _html_to_markdown
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

    async def test_event_line_survives_markdown_conversion(self, db_session, patch_global_session):
        """The event link must survive the Talk cross-post markdown conversion."""
        ev = await make_event(db_session, title="CQB Course")
        await db_session.flush()
        html, _plain = await _event_link_html(str(ev.id))
        body = f"<p>See you there.</p>{html}"
        md = _html_to_markdown(body)
        assert "CQB Course" in md
        assert f"/events/{ev.id}" in md
