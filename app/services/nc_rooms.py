"""NC Talk room directory service — DB-backed (SSoT).

Powers the online-meeting channel dropdown and builds join links. Sync, cached,
fallback-safe (mirrors the P1 config pattern).
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.models.nc_room import NCRoom

log = logging.getLogger(__name__)
_TTL = 60.0
_cache: Optional[list["RoomMeta"]] = None
_cache_ts: float = 0.0


@dataclass(frozen=True)
class RoomMeta:
    token: str
    name: str
    meeting_selectable: bool
    sort_order: int
    archived: bool


def _load() -> list[RoomMeta]:
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        eng = create_engine(get_settings().database_url_sync)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    select(NCRoom.token, NCRoom.name, NCRoom.meeting_selectable,
                           NCRoom.sort_order, NCRoom.archived)
                    .order_by(NCRoom.sort_order)
                ).all()
        finally:
            eng.dispose()
        return [RoomMeta(*r) for r in rows]
    except Exception as e:
        log.warning(f"nc_rooms service load failed (empty list): {e}")
        return []


def _all() -> list[RoomMeta]:
    global _cache, _cache_ts
    now = time.time()
    if _cache is None or (now - _cache_ts) > _TTL:
        _cache = _load()
        _cache_ts = now
    return _cache


def warm() -> None:
    global _cache, _cache_ts
    _cache = _load()
    _cache_ts = time.time()


def invalidate() -> None:
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0


def selectable_rooms() -> list[RoomMeta]:
    """Rooms offered in the online-meeting dropdown (selectable, not archived)."""
    return [r for r in _all() if r.meeting_selectable and not r.archived]


def name_for(token: str) -> Optional[str]:
    for r in _all():
        if r.token == token:
            return r.name
    return None


def join_url(token: str) -> str:
    """Nextcloud Talk join/call link for a room token."""
    from config import get_settings
    return f"{get_settings().nc_url}/call/{token}"


async def all_rooms_async(selectable_only: bool = False) -> list[RoomMeta]:
    """Async read (uses app.database.async_session). Parity with other services;
    also what tests exercise so they hit the patched test session."""
    from app.database import async_session
    async with async_session() as db:
        rows = (await db.execute(
            select(NCRoom).order_by(NCRoom.sort_order)
        )).scalars().all()
    metas = [RoomMeta(token=r.token, name=r.name, meeting_selectable=r.meeting_selectable,
                      sort_order=r.sort_order, archived=r.archived) for r in rows]
    if selectable_only:
        return [r for r in metas if r.meeting_selectable and not r.archived]
    return metas
