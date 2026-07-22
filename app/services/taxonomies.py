"""event_categories + member_statuses services — DB-backed lookup taxonomies.

Sync, cached, fallback-safe (seed matches the migration). Exposes the shapes
the old hardcoded dicts provided so call sites swap 1:1.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.models.event_category import EventCategory
from app.models.member_status import MemberStatus
from app.models.library_category import LibraryCategory

log = logging.getLogger(__name__)
_TTL = 60.0

# ─── Event categories ────────────────────────────────────────────────────────

_ec_cache: Optional[list["EventCatMeta"]] = None
_ec_ts: float = 0.0


@dataclass(frozen=True)
class EventCatMeta:
    code: str
    label: str
    icon: str
    rsvp_default: bool
    warno_lead_days: Optional[int]
    sort_order: int


_EC_SEED = [
    ("ftx", "FTX", "🏕️", True, 14, 1),
    ("mcftx", "MCFTX", "⚔️", True, 28, 2),
    ("online_training", "Online Training", "💻", False, None, 3),
    ("meeting", "Meeting", "🎖️", False, None, 4),
    ("external_training", "External Training", "🎓", True, None, 5),
    ("family_day", "Family Day", "👨‍👩‍👧‍👦", True, None, 6),
    ("social", "Social", "🤝", True, None, 7),
    ("volunteering", "Volunteering", "🫡", True, None, 8),
    ("other", "Other", "📅", False, None, 9),
]


def _ec_seed() -> list[EventCatMeta]:
    return [EventCatMeta(*r) for r in _EC_SEED]


def _ec_load() -> list[EventCatMeta]:
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        eng = create_engine(get_settings().database_url_sync)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    select(EventCategory.code, EventCategory.label, EventCategory.icon,
                           EventCategory.rsvp_default, EventCategory.warno_lead_days,
                           EventCategory.sort_order)
                    .where(EventCategory.archived == False)  # noqa: E712
                    .order_by(EventCategory.sort_order)
                ).all()
        finally:
            eng.dispose()
        return [EventCatMeta(*r) for r in rows] if rows else _ec_seed()
    except Exception as e:
        log.warning(f"event_categories falling back to seed: {e}")
        return _ec_seed()


def _ec_all() -> list[EventCatMeta]:
    global _ec_cache, _ec_ts
    now = time.time()
    if _ec_cache is None or (now - _ec_ts) > _TTL:
        _ec_cache = _ec_load()
        _ec_ts = now
    return _ec_cache


def event_categories() -> list[EventCatMeta]:
    return list(_ec_all())


def valid_categories() -> list[str]:
    return [c.code for c in _ec_all()]


def category_labels() -> dict[str, str]:
    return {c.code: c.label for c in _ec_all()}


def category_icons() -> dict[str, str]:
    return {c.code: c.icon for c in _ec_all()}


def rsvp_categories() -> set[str]:
    return {c.code for c in _ec_all() if c.rsvp_default}


def warno_lead_days() -> dict[str, int]:
    return {c.code: c.warno_lead_days for c in _ec_all() if c.warno_lead_days is not None}


# ─── Member statuses ─────────────────────────────────────────────────────────

_ms_cache: Optional[list["StatusMeta"]] = None
_ms_ts: float = 0.0


@dataclass(frozen=True)
class StatusMeta:
    code: str
    label: str
    color: str
    lifecycle: str
    sort_order: int


_MS_SEED = [
    ("recruit", "Recruit", "#42a5f5", "in_progress", 1),
    ("active", "Active", "#4caf50", "active", 2),
    ("inactive", "Inactive", "#888888", "left", 3),
    ("separated", "Separated", "#f39c12", "left", 4),
    ("blacklisted", "Blacklisted", "#ef5350", "left", 5),
]


def _ms_seed() -> list[StatusMeta]:
    return [StatusMeta(*r) for r in _MS_SEED]


def _ms_load() -> list[StatusMeta]:
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        eng = create_engine(get_settings().database_url_sync)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    select(MemberStatus.code, MemberStatus.label, MemberStatus.color,
                           MemberStatus.lifecycle, MemberStatus.sort_order)
                    .order_by(MemberStatus.sort_order)
                ).all()
        finally:
            eng.dispose()
        return [StatusMeta(*r) for r in rows] if rows else _ms_seed()
    except Exception as e:
        log.warning(f"member_statuses falling back to seed: {e}")
        return _ms_seed()


def _ms_all() -> list[StatusMeta]:
    global _ms_cache, _ms_ts
    now = time.time()
    if _ms_cache is None or (now - _ms_ts) > _TTL:
        _ms_cache = _ms_load()
        _ms_ts = now
    return _ms_cache


def member_statuses() -> list[StatusMeta]:
    return list(_ms_all())


def status_options() -> list[str]:
    return [s.code for s in _ms_all()]


def status_meta() -> dict[str, dict]:
    return {s.code: {"label": s.label, "color": s.color} for s in _ms_all()}


def statuses_by_lifecycle(bucket: str) -> set[str]:
    return {s.code for s in _ms_all() if s.lifecycle == bucket}


def left_statuses() -> set[str]:
    return statuses_by_lifecycle("left")


def stayed_statuses() -> set[str]:
    return statuses_by_lifecycle("active")


def in_progress_statuses() -> set[str]:
    return statuses_by_lifecycle("in_progress")


# ─── Library (publication) categories ───────────────────────────────────────

_lc_cache: Optional[list["LibCatMeta"]] = None
_lc_ts: float = 0.0


@dataclass(frozen=True)
class LibCatMeta:
    code: str
    category: str
    icon: str
    sort_order: int


_LC_SEED = [
    ("13LG", "13LG Publications", "⚔️", 1),
    ("TSM", "TSM Publications", "🛡️", 2),
    ("FM", "Field Manuals (FM)", "📗", 3),
    ("TC", "Training Circulars (TC)", "📘", 4),
    ("ATP", "Army Techniques Publications (ATP)", "📙", 5),
    ("TM", "Technical Manuals (TM)", "📕", 6),
    ("Other", "Other Publications", "📚", 7),
]


def _lc_seed() -> list[LibCatMeta]:
    return [LibCatMeta(*r) for r in _LC_SEED]


def _lc_load() -> list[LibCatMeta]:
    try:
        from sqlalchemy import create_engine
        from config import get_settings
        eng = create_engine(get_settings().database_url_sync)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    select(LibraryCategory.code, LibraryCategory.category,
                           LibraryCategory.icon, LibraryCategory.sort_order)
                    .order_by(LibraryCategory.sort_order)
                ).all()
        finally:
            eng.dispose()
        return [LibCatMeta(*r) for r in rows] if rows else _lc_seed()
    except Exception as e:
        log.warning(f"library_categories falling back to seed: {e}")
        return _lc_seed()


def _lc_all() -> list[LibCatMeta]:
    global _lc_cache, _lc_ts
    now = time.time()
    if _lc_cache is None or (now - _lc_ts) > _TTL:
        _lc_cache = _lc_load()
        _lc_ts = now
    return _lc_cache


def library_categories() -> list[dict]:
    """Shape matching the old LIBRARY_CATEGORIES list."""
    return [{"code": c.code, "category": c.category, "icon": c.icon} for c in _lc_all()]


def valid_library_codes() -> set[str]:
    return {c.code for c in _lc_all()}


def warm() -> None:
    global _ec_cache, _ec_ts, _ms_cache, _ms_ts, _lc_cache, _lc_ts
    _ec_cache = _ec_load(); _ec_ts = time.time()
    _ms_cache = _ms_load(); _ms_ts = time.time()
    _lc_cache = _lc_load(); _lc_ts = time.time()


def invalidate() -> None:
    global _ec_cache, _ec_ts, _ms_cache, _ms_ts, _lc_cache, _lc_ts
    _ec_cache = None; _ec_ts = 0.0
    _ms_cache = None; _ms_ts = 0.0
    _lc_cache = None; _lc_ts = 0.0
