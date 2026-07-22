"""app_settings service — typed scalar config with defaults.

Sync, cached, fallback-safe. Every getter takes the caller's default so a
missing key (pre-seed or transient DB miss) never breaks anything. Named
settings_store to avoid colliding with config.get_settings / app.settings.
"""

from __future__ import annotations

import json
import time
import logging
from typing import Any, Optional

from sqlalchemy import select

from app.models.app_setting import AppSetting

log = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_cache: Optional[dict[str, tuple[str, str]]] = None  # key -> (value, value_type)
_cache_ts: float = 0.0


def _load_sync() -> dict[str, tuple[str, str]]:
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        eng = create_engine(get_settings().database_url_sync)
        try:
            with eng.connect() as conn:
                rows = conn.execute(select(AppSetting.key, AppSetting.value, AppSetting.value_type)).all()
        finally:
            eng.dispose()
        return {r[0]: (r[1], r[2]) for r in rows}
    except Exception as e:
        log.warning(f"settings_store falling back to defaults: {e}")
        return {}


def _all() -> dict[str, tuple[str, str]]:
    global _cache, _cache_ts
    now = time.time()
    if _cache is None or (now - _cache_ts) > _CACHE_TTL:
        _cache = _load_sync()
        _cache_ts = now
    return _cache


def warm() -> None:
    global _cache, _cache_ts
    _cache = _load_sync()
    _cache_ts = time.time()


def invalidate() -> None:
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0


def _coerce(raw: str, vtype: str) -> Any:
    if vtype == "int":
        return int(raw)
    if vtype == "float":
        return float(raw)
    if vtype == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if vtype == "json":
        return json.loads(raw)
    return raw


def get(key: str, default: Any = None) -> Any:
    entry = _all().get(key)
    if entry is None:
        return default
    try:
        return _coerce(entry[0], entry[1])
    except Exception:
        return default


def get_int(key: str, default: int) -> int:
    v = get(key, default)
    try:
        return int(v)
    except Exception:
        return default


def get_float(key: str, default: float) -> float:
    v = get(key, default)
    try:
        return float(v)
    except Exception:
        return default


def get_str(key: str, default: str) -> str:
    v = get(key, default)
    return str(v) if v is not None else default


def get_json(key: str, default: Any) -> Any:
    v = get(key, default)
    return v if v is not None else default


# ─── Convenience typed accessors for known settings ─────────────────────────

def geo_center() -> tuple[float, float]:
    return (get_float("geo_center_lat", 32.7512), get_float("geo_center_lng", -97.0457))


def geo_zone_start() -> int:
    return get_int("geo_zone_start", 330)


def geo_zone_size() -> int:
    return get_int("geo_zone_size", 60)


def warno_talk_room() -> str:
    return get_str("warno_talk_room", "atnd3vgf")


def max_shops() -> int:
    return get_int("max_shops", 2)


def tenure_points() -> dict[str, int]:
    return {
        "gold": get_int("tenure_points_gold", 15),
        "silver": get_int("tenure_points_silver", 9),
        "bronze": get_int("tenure_points_bronze", 3),
    }


def ftx_device_thresholds() -> list[int]:
    return get_json("ftx_device_thresholds", [5, 10, 25, 50])
