"""Naive America/Chicago clock.

Event.date_start and the other CalDAV-synced event times are stored as naive
wall-clock Central. Comparing them to datetime.utcnow() drops an event off
"upcoming" about five hours early.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")


def now_ct() -> datetime:
    """Current Central time, naive, matching the event-column convention."""
    return datetime.now(_CT).replace(tzinfo=None)
