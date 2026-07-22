"""Shop (S1-S6) service — DB-backed single source of truth.

Mirrors app/services/ranks.py: async loaders + sync accessors (shops are read
synchronously in request handlers and passed to templates), TTL-cached, warm()
at startup, invalidate() after mutations, with a constants/hardcoded fallback
so the shop catalog can never render empty pre-migration.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from app.database import async_session
from app.models.shop import Shop

log = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_snapshot: Optional[list["ShopMeta"]] = None
_snapshot_ts: float = 0.0

# Command/admin always have access to every shop, regardless of access_roles.
_ALWAYS = {"command", "admin"}


@dataclass(frozen=True)
class ShopMeta:
    key: str
    name: str
    short_name: str
    icon: str
    description: str
    role: str
    access_roles: tuple[str, ...]
    has_dashboard: bool
    sort_order: int
    archived: bool


# Fallback seed (matches the migration seed) — used if the shops table is empty
# or unreachable, so nothing renders blank mid-deploy.
_SEED = [
    ("S1", "S1 — Personnel & Administration", "S1 — Personnel", "📋", "s1", ("s1",), True, 1,
     "Recruiting pipeline, onboarding, roster and records, promotions, awards and ribbons, "
     "documents, and unit communications. The admin backbone of the company."),
    ("S2", "S2 — Intelligence & Security", "S2 — Intel & Security", "🔍", "s2", ("s2",), False, 2,
     "Area studies, threat analysis, OPSEC, and security. Produces the intel products that "
     "inform planning and keep the unit sharp."),
    ("S3", "S3 — Operations & Training", "S3 — Ops & Training", "⚔️", "s3", ("s3", "leader"), True, 3,
     "Plans and runs FTXs and training: event building, TRADOC blocks, weapons qualification, "
     "land nav, and attendance tracking. Where the training schedule comes to life."),
    ("S4", "S4 — Logistics", "S4 — Logistics", "📦", "s4", ("s4",), False, 4,
     "Supply, equipment, transport, and sustainment. Makes sure the unit has the gear and "
     "support it needs in the field."),
    ("S5", "S5 — Medical", "S5 — Medical", "🩹", "s5", ("s5",), False, 5,
     "Combat lifesaver and medical training, aid planning, and health/safety oversight for "
     "training events."),
    ("S6", "S6 — Communications", "S6 — Comms", "📡", "s6", ("s6",), False, 6,
     "Radio (HAM/GMRS) and digital comms, nets and frequency planning, and the IT/portal "
     "infrastructure that ties everything together."),
]


def _seed_fallback() -> list[ShopMeta]:
    return [
        ShopMeta(key=k, name=n, short_name=sn, icon=ic, description=d, role=r,
                 access_roles=ar, has_dashboard=hd, sort_order=o, archived=False)
        for (k, n, sn, ic, r, ar, hd, o, d) in _SEED
    ]


def _parse_roles(s: Optional[str]) -> tuple[str, ...]:
    return tuple(x.strip() for x in (s or "").split(",") if x.strip())


def _load_sync() -> list[ShopMeta]:
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        eng = create_engine(get_settings().database_url_sync)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    select(
                        Shop.key, Shop.name, Shop.short_name, Shop.icon,
                        Shop.description, Shop.role, Shop.access_roles,
                        Shop.has_dashboard, Shop.sort_order, Shop.archived,
                    ).order_by(Shop.sort_order)
                ).all()
        finally:
            eng.dispose()
        if not rows:
            return _seed_fallback()
        return [
            ShopMeta(key=r[0], name=r[1], short_name=r[2], icon=r[3], description=r[4],
                     role=r[5], access_roles=_parse_roles(r[6]), has_dashboard=r[7],
                     sort_order=r[8], archived=r[9])
            for r in rows
        ]
    except Exception as e:
        log.warning(f"shops service falling back to seed (sync load): {e}")
        return _seed_fallback()


def _snapshot_now() -> list[ShopMeta]:
    global _snapshot, _snapshot_ts
    now = time.time()
    if _snapshot is None or (now - _snapshot_ts) > _CACHE_TTL:
        _snapshot = _load_sync()
        _snapshot_ts = now
    return _snapshot


def warm() -> None:
    global _snapshot, _snapshot_ts
    _snapshot = _load_sync()
    _snapshot_ts = time.time()


def invalidate() -> None:
    global _snapshot, _snapshot_ts
    _snapshot = None
    _snapshot_ts = 0.0


def _active(include_archived: bool = False) -> list[ShopMeta]:
    snap = _snapshot_now()
    return snap if include_archived else [s for s in snap if not s.archived]


# ─── Sync accessors (replace the old shops.py dicts) ─────────────────────────

def all_shops(include_archived: bool = False) -> list[ShopMeta]:
    return sorted(_active(include_archived), key=lambda s: s.sort_order)


def by_key(key: str) -> Optional[ShopMeta]:
    for s in _active(include_archived=True):
        if s.key == key:
            return s
    return None


def by_role(role: str) -> Optional[ShopMeta]:
    for s in _active(include_archived=True):
        if s.role == role:
            return s
    return None


def signup_catalog() -> list[dict]:
    """Shape matching the old SHOP_SIGNUP_CATALOG (key/name/icon/desc)."""
    return [{"key": s.key, "name": s.name, "icon": s.icon, "desc": s.description}
            for s in all_shops()]


def meta_map() -> dict[str, dict]:
    """Shape matching the old SHOP_META, keyed by lowercase role."""
    return {s.role: {"name": s.short_name, "icon": s.icon, "has_dashboard": s.has_dashboard}
            for s in all_shops()}


def role_map() -> dict[str, str]:
    """Shape matching the old SHOP_ROLE: {'S1': 's1', ...}."""
    return {s.key: s.role for s in all_shops()}


def name_map() -> dict[str, str]:
    """{'S1': 'S1 — Personnel & Administration', ...} (canonical full names)."""
    return {s.key: s.name for s in all_shops()}


def access_map() -> dict[str, set[str]]:
    """Shape matching the old SHOP_ACCESS (keyed by role): role -> allowed roles
    incl. command/admin."""
    return {s.role: (set(s.access_roles) | _ALWAYS) for s in all_shops()}


def access_roles(role_key: str) -> set[str]:
    s = by_role(role_key)
    return (set(s.access_roles) | _ALWAYS) if s else set(_ALWAYS)


# ─── Async parity ────────────────────────────────────────────────────────────

async def all_shops_async(include_archived: bool = False) -> list[ShopMeta]:
    try:
        async with async_session() as db:
            rows = (await db.execute(select(Shop).order_by(Shop.sort_order))).scalars().all()
        if not rows:
            metas = _seed_fallback()
        else:
            metas = [
                ShopMeta(key=r.key, name=r.name, short_name=r.short_name, icon=r.icon,
                         description=r.description, role=r.role,
                         access_roles=_parse_roles(r.access_roles),
                         has_dashboard=r.has_dashboard, sort_order=r.sort_order,
                         archived=r.archived)
                for r in rows
            ]
    except Exception as e:
        log.warning(f"shops service falling back to seed (async load): {e}")
        metas = _seed_fallback()
    return metas if include_archived else [s for s in metas if not s.archived]
