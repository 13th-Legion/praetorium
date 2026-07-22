"""Team metadata service — DB-backed single source of truth.

Reads the `teams` table and exposes the same shapes the old hardcoded
constants did (order map, designation map, talk-token map, ordered geo-zone
list, options list, plus color/emoji lookups). Cached in-process with a short
TTL and an explicit invalidate() the rename flow calls after a change.

Falls back to the constants seeds if the table is empty/missing (e.g. before
the migration runs), so nothing breaks mid-deploy.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.database import async_session
from app.models.team import Team
from app import constants as _const

log = logging.getLogger(__name__)

_CACHE_TTL = 30.0  # seconds
_cache: Optional[list["TeamMeta"]] = None
_cache_ts: float = 0.0


@dataclass(frozen=True)
class TeamMeta:
    name: str
    designation: Optional[str]
    sort_order: int
    geo_zone_index: Optional[int]
    talk_token: Optional[str]
    color: Optional[str]
    emoji: Optional[str]
    is_hq: bool
    archived: bool


def _seed_from_constants() -> list[TeamMeta]:
    """Fallback if the teams table isn't populated yet."""
    out: list[TeamMeta] = []
    for name, order in _const.TEAM_ORDER.items():
        is_hq = name == "Headquarters"
        try:
            gz = _const.GEO_ZONE_TEAMS.index(name)
        except ValueError:
            gz = None
        out.append(TeamMeta(
            name=name,
            designation=_const.TEAM_DESIGNATION.get(name),
            sort_order=order,
            geo_zone_index=gz,
            talk_token=_const.TEAM_TALK_TOKENS.get(name),
            color=None, emoji=None, is_hq=is_hq, archived=False,
        ))
    return out


async def _load() -> list[TeamMeta]:
    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(Team).order_by(Team.sort_order, Team.name)
            )).scalars().all()
        if not rows:
            return _seed_from_constants()
        return [
            TeamMeta(
                name=r.name, designation=r.designation, sort_order=r.sort_order,
                geo_zone_index=r.geo_zone_index, talk_token=r.talk_token,
                color=r.color, emoji=r.emoji, is_hq=r.is_hq, archived=r.archived,
            )
            for r in rows
        ]
    except Exception as e:  # table missing mid-migration, etc.
        log.warning(f"teams service falling back to constants: {e}")
        return _seed_from_constants()


async def all_teams(include_archived: bool = False) -> list[TeamMeta]:
    """All teams, cached, ordered by sort_order."""
    global _cache, _cache_ts
    now = time.time()
    if _cache is None or (now - _cache_ts) > _CACHE_TTL:
        _cache = await _load()
        _cache_ts = now
    if include_archived:
        return list(_cache)
    return [t for t in _cache if not t.archived]


def invalidate() -> None:
    """Force a reload on next access (call after any team mutation)."""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0


# ─── Convenience shapes matching the old constants ──────────────────────────

async def team_order() -> dict[str, int]:
    return {t.name: t.sort_order for t in await all_teams()}


async def team_options() -> list[str]:
    return [t.name for t in await all_teams()]


async def geo_zone_teams() -> list[str]:
    """Team names ordered by geo_zone_index (only geo teams)."""
    geo = [t for t in await all_teams() if t.geo_zone_index is not None]
    geo.sort(key=lambda t: t.geo_zone_index)
    return [t.name for t in geo]


async def talk_tokens() -> dict[str, str]:
    return {t.name: t.talk_token for t in await all_teams() if t.talk_token}


async def designations() -> dict[str, str]:
    return {t.name: t.designation for t in await all_teams() if t.designation}


async def colors() -> dict[str, str]:
    return {t.name: t.color for t in await all_teams() if t.color}


async def emojis() -> dict[str, str]:
    return {t.name: t.emoji for t in await all_teams() if t.emoji}


async def get_by_name(name: str) -> Optional[TeamMeta]:
    for t in await all_teams(include_archived=True):
        if t.name == name:
            return t
    return None
