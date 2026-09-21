"""Parse a one-line US address into street / city / state / zip.

Stdlib only. The recruit daemon runs on the host (not in the portal
container) and cannot import app.geo — that module pulls SQLAlchemy and
httpx. Both callers import this file.

Conservative: if it cannot find a state or zip anchor, city/state/zip stay
None and the whole input is returned as street.
"""

from __future__ import annotations

import re

_STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_STATE_NAMES = {
    "texas": "TX", "oklahoma": "OK", "louisiana": "LA", "arkansas": "AR",
    "new mexico": "NM", "california": "CA", "colorado": "CO", "florida": "FL",
    "georgia": "GA", "kansas": "KS", "missouri": "MO", "tennessee": "TN",
    "mississippi": "MS", "alabama": "AL", "arizona": "AZ",
}

_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b\s*$")

# Last-token-as-city is wrong whenever the remainder is just a street
# ("4315 Woodmeadow TX" became city=Woodmeadow, street=4315). Only split
# when a street-suffix token gives a clear break, so everything after it
# is the city — including two-word ones (Fort Worth, Grand Prairie).
_STREET_SUFFIXES = {
    "st", "street", "rd", "road", "dr", "drive", "ln", "lane",
    "ave", "avenue", "blvd", "boulevard", "ct", "court",
    "cir", "circle", "way", "pl", "place", "pkwy", "parkway",
    "hwy", "highway", "trl", "trail", "ter", "terrace",
    "loop", "run", "pass", "xing", "crossing", "fwy", "freeway",
    "expy", "expressway", "aly", "alley", "sq", "square",
    "pt", "point", "cv", "cove", "bnd", "bend", "rdg", "ridge",
    "hl", "hill", "vw", "view", "cres", "crescent", "row",
    "path", "pike", "tpke", "turnpike",
}
_UNIT_LEADERS = {"unit", "apt", "apartment", "ste", "suite", "spc", "space", "lot"}


def parse_oneline_address(raw: str) -> dict:
    """Parse a one-line US address into components.

    Returns a dict with keys street / city / state / zip — any value may
    be None if it could not be confidently extracted.

    Examples handled:
      '3637 East Trinity Mills Road, Dallas, TX 75287'
      '1400 Brimwood Dr. McKinney, TX 75072'
      '7821 Lovers Ln Dallas Tx 75225'
      '520 Samuels Avenue, APT 3211, Fort Worth, TX 76102'
      '700 Ipswich Avenue, APT 54108, Fort Worth, TX, 76131'
    """
    out = {"street": None, "city": None, "state": None, "zip": None}
    if not raw:
        return out
    s = raw.strip().rstrip(",").strip()
    if not s:
        return out

    zip_val = None
    m = _ZIP_RE.search(s)
    if m:
        zip_val = m.group(1)
        s = s[: m.start()].strip().rstrip(",").strip()

    state_val = None
    tokens = s.split()
    if tokens:
        last = tokens[-1].strip(",.").upper()
        if last in _STATE_ABBRS:
            state_val = last
            s = " ".join(tokens[:-1]).strip().rstrip(",").strip()
        else:
            low = s.lower().rstrip(",").strip()
            for name, abbr in _STATE_NAMES.items():
                if low.endswith(name):
                    state_val = abbr
                    s = s[: len(s) - len(name)].strip().rstrip(",").strip()
                    break

    if not state_val and not zip_val:
        out["street"] = raw.strip()
        return out

    city_val = None
    if "," in s:
        head, _, tail = s.rpartition(",")
        city_val = tail.strip() or None
        s = head.strip().rstrip(",").strip()
    else:
        city_val, s = _city_after_street_suffix(s)

    out["street"] = s or None
    out["city"] = city_val
    out["state"] = state_val
    out["zip"] = zip_val
    return out


def _city_after_street_suffix(s: str) -> tuple[str | None, str]:
    """Split '719 Cougar Dr Allen' into city='Allen', street='719 Cougar Dr'.

    Returns (city_or_None, remainder_used_as_street). Does not split when
    there is no street suffix — a last-token guess would steal the street
    name (Bergener: '4315 Woodmeadow TX').
    """
    parts = s.split()
    last_suffix = None
    for i, tok in enumerate(parts):
        if tok.strip(",.").lower() in _STREET_SUFFIXES:
            last_suffix = i
    if last_suffix is None or last_suffix >= len(parts) - 1:
        return None, s

    rest = parts[last_suffix + 1:]
    unit_take = 0
    if rest and rest[0].strip(",.#").lower() in _UNIT_LEADERS:
        unit_take = 1
        if len(rest) > 1:
            unit_take = 2
    city_parts = rest[unit_take:]
    if not city_parts:
        return None, s
    city = " ".join(city_parts).strip(",.") or None
    street = " ".join(parts[: last_suffix + 1] + rest[:unit_take]).strip()
    return city, street
