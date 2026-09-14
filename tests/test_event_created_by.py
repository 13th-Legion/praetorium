"""`events.created_by` — resolution for display.

We already tracked this. The column is VARCHAR(64) NOT NULL, all 375 production
rows are populated back to 2019, and it already drives the owning-leader edit
permission in `event_detail`. It was simply never rendered in any template, so
from the UI it looked like the field didn't exist.

These pin the resolver's two jobs: turn a username into a readable name, and
degrade gracefully for everything that isn't a current member — because the
real data contains separated members and six flavours of automation
('backfill', 'historical_backfill', 'spooky (series extend)',
'spooky (holiday shift)', 'spooky (silent import)', 'sync').
"""

import pytest

from app.routes.events import _resolve_creator
from tests.factories import make_member

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_current_member_resolves_to_rank_and_callsign(db_session):
    m = await make_member(db_session, nc_username="levi.kavadas",
                          last_name="Kavadas", callsign="Cav",
                          rank_grade="O-3", status="active")
    await db_session.flush()

    got = await _resolve_creator(db_session, "levi.kavadas")

    assert got["display"] == m.display_name
    assert "Kavadas" in got["display"]
    assert got["is_system"] is False
    assert got["member_id"] == m.id, "must link to the profile"


@pytest.mark.asyncio
async def test_separated_member_still_resolves(db_session):
    """Creators leave the unit. An event they made must still show their name,
    not a bare username — so the lookup deliberately has no status filter."""
    m = await make_member(db_session, nc_username="gone.away",
                          last_name="Away", callsign="Ghost",
                          rank_grade="E-4", status="separated")
    await db_session.flush()

    got = await _resolve_creator(db_session, "gone.away")

    assert got["member_id"] == m.id
    assert got["is_system"] is False
    assert "Away" in got["display"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "backfill",
    "historical_backfill",
    "sync",
    "unknown",
    "spooky (series extend)",
    "spooky (holiday shift)",
    "spooky (silent import)",
])
async def test_automation_creators_pass_through_unchanged(db_session, raw):
    """These strings are already descriptive; don't mangle them, and don't
    pretend they link to a profile."""
    got = await _resolve_creator(db_session, raw)
    assert got["display"] == raw
    assert got["is_system"] is True
    assert got["member_id"] is None


@pytest.mark.asyncio
async def test_unknown_username_falls_back_to_the_raw_value(db_session):
    """Better to show the stored username than to hide who made it."""
    got = await _resolve_creator(db_session, "someone.deleted")
    assert got["display"] == "someone.deleted"
    assert got["is_system"] is True
    assert got["member_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "   ", None])
async def test_blank_is_reported_as_unknown_not_crashed(db_session, raw):
    """The column is NOT NULL in production, but a resolver that explodes on an
    empty string would take the whole event page down with it."""
    got = await _resolve_creator(db_session, raw)
    assert got["display"] == "Unknown"
    assert got["is_system"] is True


@pytest.mark.asyncio
async def test_a_member_is_preferred_over_the_system_heuristic(db_session):
    """The 'contains a space' heuristic must not beat a real match."""
    m = await make_member(db_session, nc_username="sync.operator",
                          last_name="Operator", rank_grade="E-5",
                          status="active")
    await db_session.flush()
    got = await _resolve_creator(db_session, "sync.operator")
    assert got["member_id"] == m.id, "exact username match wins"
    assert got["is_system"] is False
