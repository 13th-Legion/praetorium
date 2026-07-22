"""Wave A — ranks service (DB-backed rank SSoT).

Verifies the sync accessors resolve from the seeded ranks table, the constants
fallback works when the table is empty, and the drift fixes hold (E-8M present,
nc_group complete, election eligibility correct).
"""

import pytest

from app.services import ranks
from app.models.rank import Rank

pytestmark = pytest.mark.integration


async def _seed(db):
    rows = [
        ("E-1", "RCT", "Recruit", 1, None, "Rank - Recruit", "enlisted", False),
        ("E-4", "CPL", "Corporal", 4, "enl_corporal.svg", "Rank - Enlisted", "enlisted", True),
        ("E-8M", "MSG", "Master Sergeant", 8, "enl_master_sergeant.svg", "Rank - NCO", "nco", True),
        ("O-3", "CPT", "Captain", 18, "off_captain.svg", "Rank - Officer", "officer", True),
    ]
    for g, a, t, o, ins, ncg, pc, el in rows:
        db.add(Rank(grade=g, abbr=a, title=t, sort_order=o, insignia=ins,
                    nc_group=ncg, pay_category=pc, election_eligible=el, archived=False))
    await db.flush()
    await db.commit()


class TestFallback:
    def test_fallback_never_blank(self):
        # With no seeded snapshot bound, the sync accessors fall back to
        # constants — must be non-empty (guards the blank-rank regression).
        ranks.invalidate()
        am = ranks.abbr_map()
        assert am.get("E-4") == "CPL"
        assert am.get("E-1") == "RCT"
        assert am.get("E-8M") == "MSG"       # E-8M present in fallback
        assert len(am) >= 19

    def test_fallback_eligibility(self):
        ranks.invalidate()
        elig = ranks.eligible_grades()
        assert "E-1" not in elig            # recruit not eligible
        assert "E-8M" in elig
        assert "O-5" not in elig            # nonexistent grade never present

    def test_choices_shape(self):
        ranks.invalidate()
        ch = dict(ranks.choices())
        assert ch["E-4"] == "CPL — Corporal"


class TestAsyncFromDB:
    async def test_all_ranks_reads_seeded(self, patch_global_session, db_session):
        await _seed(db_session)
        rows = await ranks.all_ranks()
        grades = {r.grade for r in rows}
        assert {"E-1", "E-4", "E-8M", "O-3"}.issubset(grades)
        e8m = next(r for r in rows if r.grade == "E-8M")
        assert e8m.nc_group == "Rank - NCO"       # was missing pre-refactor
        assert e8m.election_eligible is True
