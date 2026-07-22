"""Rank structure service — DB-backed single source of truth.

Mirrors app/services/teams.py, but with an important difference: rank data is
consumed **synchronously** all over the app (routes do `RANK_ABBR.get(grade)`
and pass the dict straight to Jinja). So this service exposes BOTH:

  * async loaders (`all_ranks()`, `invalidate()`), like teams; and
  * **sync accessors** (`abbr_map()`, `title_map()`, `order_map()`, `index_map()`,
    `insignia_map()`, `nc_group_map()`, `eligible_grades()`, `choices()`), backed
    by a sync in-process snapshot.

Safety: the sync snapshot is primed at startup (`warm()` in the app lifespan)
and refreshed after any mutation via `invalidate()`. It ALWAYS falls back to the
constants seed if the DB snapshot is empty — so ranks can never render blank
(the exact regression we just fixed in attendance_analytics). The constants
remain as the authoritative fallback seed only; the DB row is the live source.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.database import async_session
from app.models.rank import Rank
from app import constants as _const

log = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_snapshot: Optional[list["RankMeta"]] = None
_snapshot_ts: float = 0.0


@dataclass(frozen=True)
class RankMeta:
    grade: str
    abbr: str
    title: str
    sort_order: int
    insignia: Optional[str]
    nc_group: Optional[str]
    pay_category: str
    election_eligible: bool
    archived: bool


def _seed_from_constants() -> list[RankMeta]:
    """Fallback seed if the ranks table is empty/missing (pre-migration, or a
    transient DB miss). Uses the canonical constants + the promotions ordering.
    Guarantees the sync accessors are never empty."""
    # Order from constants.RANK_ABBR insertion order (matches promotions order).
    out: list[RankMeta] = []
    nc_group_default = {
        "E-1": "Rank - Recruit", "E-2": "Rank - Enlisted", "E-3": "Rank - Enlisted",
        "E-4": "Rank - Enlisted", "E-5": "Rank - NCO", "E-6": "Rank - NCO",
        "E-7": "Rank - NCO", "E-8M": "Rank - NCO", "E-8": "Rank - NCO",
        "E-9": "Rank - NCO", "W-1": "Rank - Officer", "W-2": "Rank - Officer",
        "W-3": "Rank - Officer", "W-4": "Rank - Officer", "W-5": "Rank - Officer",
        "O-1": "Rank - Officer", "O-2": "Rank - Officer", "O-3": "Rank - Officer",
        "O-4": "Rank - Officer",
    }
    def _cat(g: str) -> str:
        if g.startswith("O-"):
            return "officer"
        if g.startswith("W-"):
            return "warrant"
        if g in ("E-5", "E-6", "E-7", "E-8", "E-8M", "E-9"):
            return "nco"
        return "enlisted"
    for i, (grade, abbr) in enumerate(_const.RANK_ABBR.items(), start=1):
        out.append(RankMeta(
            grade=grade, abbr=abbr, title=_const.RANK_TITLE.get(grade, abbr),
            sort_order=i, insignia=None, nc_group=nc_group_default.get(grade),
            pay_category=_cat(grade), election_eligible=(grade != "E-1"),
            archived=False,
        ))
    return out


def _load_sync() -> list[RankMeta]:
    """Synchronous DB load via a short-lived sync engine. Falls back to the
    constants seed on any error or empty table."""
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        url = get_settings().database_url_sync
        eng = create_engine(url)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    select(
                        Rank.grade, Rank.abbr, Rank.title, Rank.sort_order,
                        Rank.insignia, Rank.nc_group, Rank.pay_category,
                        Rank.election_eligible, Rank.archived,
                    ).order_by(Rank.sort_order)
                ).all()
        finally:
            eng.dispose()
        if not rows:
            return _seed_from_constants()
        return [RankMeta(*r) for r in rows]
    except Exception as e:
        log.warning(f"ranks service falling back to constants (sync load): {e}")
        return _seed_from_constants()


def _snapshot_now() -> list[RankMeta]:
    global _snapshot, _snapshot_ts
    now = time.time()
    if _snapshot is None or (now - _snapshot_ts) > _CACHE_TTL:
        _snapshot = _load_sync()
        _snapshot_ts = now
    return _snapshot


def warm() -> None:
    """Prime the snapshot at startup (called from the app lifespan)."""
    global _snapshot, _snapshot_ts
    _snapshot = _load_sync()
    _snapshot_ts = time.time()


def invalidate() -> None:
    """Force a reload on next access (call after any rank mutation)."""
    global _snapshot, _snapshot_ts
    _snapshot = None
    _snapshot_ts = 0.0


# ─── Sync accessors (drop-in replacements for the old constants dicts) ───────

def _active(include_archived: bool = False) -> list[RankMeta]:
    snap = _snapshot_now()
    return snap if include_archived else [r for r in snap if not r.archived]


def abbr_map() -> dict[str, str]:
    return {r.grade: r.abbr for r in _active()}


def title_map() -> dict[str, str]:
    return {r.grade: r.title for r in _active()}


def order_map() -> dict[str, int]:
    return {r.grade: r.sort_order for r in _active()}


def ordered_grades() -> list[str]:
    return [r.grade for r in sorted(_active(), key=lambda r: r.sort_order)]


def index_map() -> dict[str, int]:
    """Zero-based index by ascending order (matches promotions.RANK_INDEX)."""
    return {g: i for i, g in enumerate(ordered_grades())}


def insignia_map() -> dict[str, str]:
    return {r.grade: r.insignia for r in _active() if r.insignia}


def nc_group_map() -> dict[str, str]:
    return {r.grade: r.nc_group for r in _active() if r.nc_group}


def eligible_grades() -> set[str]:
    return {r.grade for r in _active() if r.election_eligible}


def choices() -> list[tuple[str, str]]:
    """(grade, 'ABBR — Title') for form dropdowns, ordered."""
    return [(r.grade, f"{r.abbr} — {r.title}") for r in sorted(_active(), key=lambda r: r.sort_order)]


# ─── Async accessors (parity with teams service) ────────────────────────────

async def all_ranks(include_archived: bool = False) -> list[RankMeta]:
    try:
        async with async_session() as db:
            rows = (await db.execute(select(Rank).order_by(Rank.sort_order))).scalars().all()
        if not rows:
            return _seed_from_constants()
        metas = [
            RankMeta(
                grade=r.grade, abbr=r.abbr, title=r.title, sort_order=r.sort_order,
                insignia=r.insignia, nc_group=r.nc_group, pay_category=r.pay_category,
                election_eligible=r.election_eligible, archived=r.archived,
            ) for r in rows
        ]
    except Exception as e:
        log.warning(f"ranks service falling back to constants (async load): {e}")
        metas = _seed_from_constants()
    return metas if include_archived else [r for r in metas if not r.archived]
