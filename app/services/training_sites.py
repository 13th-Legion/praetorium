"""Training-site service — DB-backed single source of truth.

Reads the `s2_training_sites` + `s2_training_site_maps` tables and exposes the
same shapes the old hardcoded `app/training_sites.py` dict did (site dicts,
`get_site`, `get_site_maps`). Falls back to the constants seed if the tables
are empty (e.g. mid-deploy before the migration/seed), so the FTX builder never
breaks.

This is the S2-owned map library: S2 adds/removes sites and uploads maps here,
and the FTX builder's training-site dropdown + auto-maps read from this same
source.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

from sqlalchemy import select

from app import database
from app.models.s2_intel import S2TrainingSite, S2TrainingSiteMap
from app.training_sites import TRAINING_SITES as _SEED

log = logging.getLogger(__name__)

_CACHE_TTL = 30.0  # seconds
_cache: Optional[list[dict]] = None
_cache_ts: float = 0.0


def _seed() -> list[dict]:
    """Fallback site list from the legacy constants dict."""
    out = []
    for key, site in _SEED.items():
        maps = []
        for scale_key, url in site["maps"].items():
            maps.append({"label": _map_label(site["name"], scale_key), "url": url})
        out.append({
            "key": key, "name": site["name"], "nickname": site.get("nickname"),
            "address": site.get("address"), "is_active": True, "maps": maps,
        })
    return out


def _map_label(name: str, scale_key: str) -> str:
    if scale_key == "10k":
        return "1:10,000"
    if scale_key == "10k_marked":
        return "1:10,000 (Marked)"
    if scale_key == "25k":
        return "1:25,000"
    if scale_key == "25k_marked":
        return "1:25,000 (Marked)"
    return scale_key


def invalidate() -> None:
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0


async def _load() -> list[dict]:
    try:
        async with database.async_session() as db:
            sites = (await db.execute(
                select(S2TrainingSite)
                .where(S2TrainingSite.is_active.is_(True))
                .order_by(S2TrainingSite.id)
            )).scalars().all()
            if not sites:
                return _seed()
            site_ids = [s.id for s in sites]
            maps_rows = (await db.execute(
                select(S2TrainingSiteMap).where(S2TrainingSiteMap.site_id.in_(site_ids))
            )).scalars().all()
        maps_by_site: dict[int, list[dict]] = {}
        for m in maps_rows:
            maps_by_site.setdefault(m.site_id, []).append({"label": m.label, "url": m.url})
        out = []
        for s in sites:
            out.append({
                "key": s.key, "name": s.name, "nickname": s.nickname,
                "address": s.address, "is_active": s.is_active,
                "maps": maps_by_site.get(s.id, []),
            })
        return out
    except Exception:
        log.exception("training_sites service: falling back to constants seed")
        return _seed()


async def _cached() -> list[dict]:
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is None or (now - _cache_ts) > _CACHE_TTL:
        _cache = await _load()
        _cache_ts = now
    return _cache


async def all_sites() -> list[dict]:
    return await _cached()


async def site_map() -> dict[str, dict]:
    return {s["key"]: s for s in await _cached()}


async def get_site(key: str) -> dict | None:
    return (await site_map()).get(key)


async def get_site_maps(key: str) -> list[dict]:
    site = await get_site(key)
    return site["maps"] if site else []


async def site_options() -> list[dict]:
    """[(key, display name)] for dropdowns."""
    return [(s["key"], s["name"]) for s in await _cached()]
