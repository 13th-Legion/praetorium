"""Phase 2 — timezone correctness for CT-stored event fields.

Guards the double-shift fix: _fmt_ct_stored must NOT clock-shift a naive-CT
datetime, while _to_cdt (UTC-stored audit fields) must convert.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.routes.events import _fmt_ct_stored, _to_cdt

pytestmark = pytest.mark.unit

_CDT = ZoneInfo("America/Chicago")


class TestFmtCtStored:
    def test_no_clock_shift_summer(self):
        # A 19:00 meeting stored naive-CT must still read 19:00 (CDT), not 14:00.
        dt = datetime(2026, 6, 1, 19, 0)
        out = _fmt_ct_stored(dt)
        assert (out.hour, out.minute) == (19, 0)
        assert out.tzinfo == _CDT

    def test_no_clock_shift_winter(self):
        dt = datetime(2026, 1, 15, 20, 0)
        out = _fmt_ct_stored(dt)
        assert (out.hour, out.minute) == (20, 0)

    def test_none_passthrough(self):
        assert _fmt_ct_stored(None) is None


class TestToCdtStillConverts:
    def test_utc_stored_converts_to_ct(self):
        # A naive-UTC audit timestamp at 14:00 UTC → 09:00 CDT (summer, -5).
        dt = datetime(2026, 6, 1, 14, 0)
        out = _to_cdt(dt)
        assert out.hour == 9

    def test_the_two_helpers_differ_by_offset(self):
        dt = datetime(2026, 6, 1, 19, 0)
        stored = _fmt_ct_stored(dt)
        as_utc = _to_cdt(dt)
        # stored keeps 19:00; utc-interpretation shifts it earlier (14:00).
        assert stored.hour == 19
        assert as_utc.hour == 14
