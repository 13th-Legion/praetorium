"""Editing a recurring series' range must reconcile occurrences (PP-326).

The series is materialized (one Event row per occurrence, linked by series_id).
Editing the range means: add occurrences missing from the requested window
(cloned from the anchor), cancel FUTURE occurrences that now fall outside it,
and never touch past occurrences (attendance history).

Also covers the cadence helpers: `_series_cadence` inference (weekly / monthly
day-of-month / nth-weekday / last-weekday, and the stored RRULE path) and the
occurrence-stepping helpers that materialize the window.
"""

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.models.events import Event
from app.routes.events import (
    _series_cadence,
    _nth_weekday_of_month,
    _occurrences_between,
)

from tests.factories import make_event


def _occ(dt, *, master=False, rule=None, series="s1"):
    return Event(
        title="T",
        category="meeting",
        date_start=dt,
        created_by="test",
        series_id=series,
        is_series_master=master,
        recurrence_rule=rule,
    )


class TestSeriesCadence:
    def test_weekly_inferred(self):
        base = datetime.datetime(2026, 10, 7, 20, 0)
        occs = [_occ(base + datetime.timedelta(days=7 * i)) for i in range(4)]
        assert _series_cadence(occs) == ("weekly",)

    def test_monthly_day_of_month(self):
        occs = [_occ(datetime.datetime(2026, m, 15, 19, 0)) for m in (9, 10, 11, 12)]
        assert _series_cadence(occs) == ("monthly_day", 15)

    def test_monthly_nth_weekday(self):
        occs = [
            _occ(datetime.datetime(2026, 10, 14, 20, 0)),
            _occ(datetime.datetime(2026, 11, 11, 20, 0)),
            _occ(datetime.datetime(2026, 12, 9, 20, 0)),
        ]
        assert _series_cadence(occs) == ("monthly_nth", 2, "WE")

    def test_monthly_last_weekday(self):
        occs = [
            _occ(datetime.datetime(2026, 10, 29, 20, 0)),
            _occ(datetime.datetime(2026, 11, 26, 20, 0)),
            _occ(datetime.datetime(2026, 12, 31, 20, 0)),
        ]
        assert _series_cadence(occs) == ("monthly_nth", -1, "TH")

    def test_insufficient_occurrences(self):
        assert _series_cadence([_occ(datetime.datetime(2026, 10, 7, 20, 0))]) is None
        assert _series_cadence([]) is None

    def test_irregular_gaps(self):
        occs = [
            _occ(datetime.datetime(2026, 10, 7, 20, 0)),
            _occ(datetime.datetime(2026, 10, 9, 20, 0)),
            _occ(datetime.datetime(2026, 10, 28, 20, 0)),
        ]
        assert _series_cadence(occs) is None

    def test_from_stored_rrule_weekly(self):
        occs = [_occ(datetime.datetime(2026, 10, 7, 20, 0), master=True,
                     rule="FREQ=WEEKLY;COUNT=52")]
        assert _series_cadence(occs) == ("weekly",)

    def test_from_stored_rrule_monthly_nth(self):
        occs = [_occ(datetime.datetime(2026, 10, 7, 20, 0), master=True,
                     rule="FREQ=MONTHLY;BYDAY=2WE;COUNT=12")]
        assert _series_cadence(occs) == ("monthly_nth", 2, "WE")

    def test_from_stored_rrule_monthly_day(self):
        occs = [_occ(datetime.datetime(2026, 10, 15, 20, 0), master=True,
                     rule="FREQ=MONTHLY;BYMONTHDAY=15")]
        assert _series_cadence(occs) == ("monthly_day", 15)


class TestNthWeekdayOfMonth:
    def test_second_wednesday(self):
        assert _nth_weekday_of_month(2026, 10, 2, "WE") == 14

    def test_last_wednesday(self):
        assert _nth_weekday_of_month(2026, 10, -1, "WE") == 28

    def test_fifth_absent(self):
        assert _nth_weekday_of_month(2026, 11, 5, "WE") is None

    def test_first_monday(self):
        assert _nth_weekday_of_month(2026, 10, 1, "MO") == 5


class TestOccurrencesBetween:
    def test_weekly_window(self):
        anchor = datetime.datetime(2026, 10, 7, 20, 0)
        start = datetime.datetime(2026, 10, 1, 0, 0)
        end = datetime.datetime(2026, 10, 31, 23, 59, 59)
        got = _occurrences_between(anchor, ("weekly",), start, end)
        assert got == [datetime.datetime(2026, 10, d, 20, 0) for d in (7, 14, 21, 28)]

    def test_weekly_preserves_time(self):
        anchor = datetime.datetime(2026, 10, 7, 19, 30)
        got = _occurrences_between(anchor, ("weekly",),
                                   datetime.datetime(2026, 10, 7, 0, 0),
                                   datetime.datetime(2026, 10, 21, 23, 59, 59))
        assert all(t.hour == 19 and t.minute == 30 for t in got)

    def test_monthly_nth_window(self):
        anchor = datetime.datetime(2026, 10, 14, 20, 0)
        got = _occurrences_between(anchor, ("monthly_nth", 2, "WE"),
                                   datetime.datetime(2026, 10, 1, 0, 0),
                                   datetime.datetime(2027, 1, 31, 23, 59, 59))
        assert got == [
            datetime.datetime(2026, 10, 14, 20, 0),
            datetime.datetime(2026, 11, 11, 20, 0),
            datetime.datetime(2026, 12, 9, 20, 0),
            datetime.datetime(2027, 1, 13, 20, 0),
        ]


def _csrf(c):
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


def _post_recurrence(c, event_id, start=None, end=None):
    tok = _csrf(c)
    data = {"csrf_token": tok}
    if start:
        data["series_start"] = start
    if end:
        data["series_end"] = end
    return c.post(
        f"/api/events/{event_id}/series-recurrence",
        data=data,
        headers={"X-CSRF-Token": tok},
    )


@pytest.fixture
async def weekly_series(db_sessionmaker, patch_global_session):
    """Three weekly Command Meeting occurrences: Oct 7, 14, 21 2026."""
    sid = uuid.uuid4().hex
    async with db_sessionmaker() as db:
        base = datetime.datetime(2026, 10, 7, 20, 0)
        ids = []
        for i in range(3):
            e = await make_event(
                db,
                title="Command Meeting",
                category="meeting",
                date_start=base + datetime.timedelta(days=7 * i),
                date_end=base + datetime.timedelta(days=7 * i, hours=1),
                series_id=sid,
                is_series_master=(i == 0),
                meeting_mode="online",
                talk_token="7jrpakx2",
                status="active",
            )
            ids.append(int(e.id))
        await db.commit()
    return {"series_id": sid, "anchor_id": ids[0]}


@pytest.mark.asyncio
async def test_extend_end_adds_occurrences(auth_client, weekly_series, db_sessionmaker):
    r = _post_recurrence(auth_client, weekly_series["anchor_id"], end="2026-11-04")
    assert r.status_code == 200, r.text

    async with db_sessionmaker() as db:
        rows = (await db.execute(
            select(Event).where(Event.series_id == weekly_series["series_id"])
        )).scalars().all()
        dts = sorted(e.date_start for e in rows)
        assert [d.date() for d in dts] == [
            datetime.date(2026, 10, 7),
            datetime.date(2026, 10, 14),
            datetime.date(2026, 10, 21),
            datetime.date(2026, 10, 28),
            datetime.date(2026, 11, 4),
        ]


@pytest.mark.asyncio
async def test_trim_end_cancels_future_only(auth_client, weekly_series, db_sessionmaker):
    _post_recurrence(auth_client, weekly_series["anchor_id"], end="2026-11-04")
    r = _post_recurrence(auth_client, weekly_series["anchor_id"], end="2026-10-21")
    assert r.status_code == 200, r.text

    async with db_sessionmaker() as db:
        rows = (await db.execute(
            select(Event).where(Event.series_id == weekly_series["series_id"])
        )).scalars().all()
        by_dt = {e.date_start.date(): e for e in rows}
        assert by_dt[datetime.date(2026, 10, 28)].status == "cancelled"
        assert by_dt[datetime.date(2026, 11, 4)].status == "cancelled"
        assert by_dt[datetime.date(2026, 10, 21)].status == "active"


@pytest.mark.asyncio
async def test_requires_series(auth_client, db_sessionmaker, patch_global_session):
    async with db_sessionmaker() as db:
        e = await make_event(db, title="Standalone", category="meeting",
                             date_start=datetime.datetime(2026, 10, 7, 20, 0))
        await db.commit()
        eid = int(e.id)
    r = _post_recurrence(auth_client, eid, end="2026-11-04")
    assert r.status_code == 200
    assert "not part of a recurring series" in r.text


@pytest.mark.asyncio
async def test_non_admin_forbidden(client, weekly_series):
    r = client.post(f"/api/events/{weekly_series['anchor_id']}/series-recurrence",
                    data={"series_end": "2026-11-04"})
    assert r.status_code in (302, 403)
