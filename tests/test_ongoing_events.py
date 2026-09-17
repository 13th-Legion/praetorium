"""Events page: an event stays featured while it runs, and only then goes Past.

Reported 2026-09-16: "when an event starts it instantly gets put into the past
events category". The events list computed `is_past = event.date_start < now`,
so an event moved into the collapsed Past Events section the moment it BEGAN —
hiding the thing you most need to reach exactly while it is underway.

The tempting fix ("no date_end means it is still running") is wrong, and these
tests pin that down: production has 6 events with no `date_end` at all,
including Field Training Exercises from 2021, 2022 and 2023. Treating a missing
end as open-ended would feature a 2021 FTX as Happening Now.
"""
import datetime

import pytest

from app.routes.events import DEFAULT_EVENT_HOURS, _effective_end


class _Evt:
    """Minimal stand-in — _effective_end only reads date_start/date_end."""

    def __init__(self, start, end=None):
        self.date_start = start
        self.date_end = end


def _at(y, m, d, hh=0, mm=0):
    return datetime.datetime(y, m, d, hh, mm)


def _classify(event, now):
    """Mirror of the route's ongoing/past split, kept in one place."""
    end = _effective_end(event)
    return {
        "ongoing": event.date_start <= now <= end,
        "past": end < now,
        "upcoming": event.date_start > now,
    }


# ─── the reported bug ────────────────────────────────────────────────────────

def test_event_in_progress_is_ongoing_not_past():
    """19:00-22:00 drill, checked at 20:00: in progress, and NOT past."""
    evt = _Evt(_at(2026, 9, 16, 19, 0), _at(2026, 9, 16, 22, 0))
    state = _classify(evt, _at(2026, 9, 16, 20, 0))
    assert state["ongoing"] is True
    assert state["past"] is False


def test_event_becomes_past_only_after_it_ends():
    evt = _Evt(_at(2026, 9, 16, 19, 0), _at(2026, 9, 16, 22, 0))
    just_after = _classify(evt, _at(2026, 9, 16, 22, 1))
    assert just_after["past"] is True
    assert just_after["ongoing"] is False


def test_future_event_is_neither_ongoing_nor_past():
    evt = _Evt(_at(2026, 10, 1, 19, 0), _at(2026, 10, 1, 22, 0))
    state = _classify(evt, _at(2026, 9, 16, 20, 0))
    assert state["upcoming"] is True
    assert state["ongoing"] is False
    assert state["past"] is False


# ─── the trap: events with no recorded end ───────────────────────────────────

@pytest.mark.parametrize("year", [2021, 2022, 2023])
def test_ancient_event_without_end_is_past_not_ongoing(year):
    """Real production rows: FTXs with date_end NULL.

    If a missing end were treated as open-ended, these would show as
    Happening Now forever.
    """
    evt = _Evt(_at(year, 12, 10, 8, 0), None)
    state = _classify(evt, _at(2026, 9, 16, 20, 0))
    assert state["past"] is True, f"{year} FTX must be past"
    assert state["ongoing"] is False, f"{year} FTX must not be featured"


def test_timed_event_without_end_gets_bounded_window():
    """No end recorded: assume a bounded block, not forever."""
    start = _at(2026, 9, 16, 19, 0)
    evt = _Evt(start, None)
    assert _effective_end(evt) == start + datetime.timedelta(hours=DEFAULT_EVENT_HOURS)
    assert _classify(evt, start + datetime.timedelta(hours=1))["ongoing"] is True
    assert _classify(evt, start + datetime.timedelta(hours=DEFAULT_EVENT_HOURS, minutes=1))["past"] is True


def test_all_day_event_without_end_runs_to_end_of_day():
    evt = _Evt(_at(2026, 9, 16), None)
    assert _classify(evt, _at(2026, 9, 16, 23, 0))["ongoing"] is True
    assert _classify(evt, _at(2026, 9, 17, 0, 1))["past"] is True


# ─── all-day rows (24 such rows in production) ───────────────────────────────

def test_single_all_day_event_is_ongoing_on_its_own_day():
    """Stored midnight-to-midnight with end == start.

    Without widening this it could never satisfy start <= now <= end, so a
    single all-day event would never appear as ongoing at all.
    """
    evt = _Evt(_at(2026, 9, 16), _at(2026, 9, 16))
    assert _classify(evt, _at(2026, 9, 16, 12, 0))["ongoing"] is True
    assert _classify(evt, _at(2026, 9, 17, 0, 1))["past"] is True


def test_multi_day_all_day_event_stays_ongoing_across_days():
    """Event 440 'Waco Hydra Fire relief': 16 Sep -> 30 Sep, all-day."""
    evt = _Evt(_at(2026, 9, 16), _at(2026, 9, 30))
    assert _classify(evt, _at(2026, 9, 17, 12, 0))["ongoing"] is True
    assert _classify(evt, _at(2026, 9, 22, 3, 0))["ongoing"] is True
    assert _classify(evt, _at(2026, 10, 1, 0, 1))["past"] is True


def test_multi_day_event_is_not_past_midway_through():
    """The original bug at multi-day scale: a 3-day FTX must not be filed
    under Past on its second morning."""
    evt = _Evt(_at(2026, 9, 11, 19, 0), _at(2026, 9, 13, 16, 0))
    state = _classify(evt, _at(2026, 9, 12, 9, 0))
    assert state["ongoing"] is True
    assert state["past"] is False


def test_boundary_exactly_at_start_and_exactly_at_end():
    evt = _Evt(_at(2026, 9, 16, 19, 0), _at(2026, 9, 16, 22, 0))
    assert _classify(evt, _at(2026, 9, 16, 19, 0))["ongoing"] is True
    assert _classify(evt, _at(2026, 9, 16, 22, 0))["ongoing"] is True
    assert _classify(evt, _at(2026, 9, 16, 22, 0))["past"] is False


def test_template_renders_happening_now_section():
    """The route can classify correctly and still show nothing if the template
    was never wired up."""
    src = open("app/templates/pages/events.html", encoding="utf-8").read()
    assert "{% if ongoing %}" in src
    assert "Happening Now" in src
    # Featured ABOVE upcoming, otherwise it is not featured.
    assert src.index("Happening Now") < src.index("Upcoming Events")
