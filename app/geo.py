"""Geographic zone assignment for fireteam geofencing.

6 equal 60° bearing slices from center point (I-30 & N Great Southwest Pkwy).
Zone boundaries:
  Alpha:   330° – 30°  (N)
  Bravo:    30° – 90°  (NE)
  Charlie:  90° – 150° (E/SE)
  Delta:   150° – 210° (S)
  Echo:    210° – 270° (SW/W)
  Foxtrot: 270° – 330° (NW)
"""

import math
import re
from app.constants import GEO_CENTER, GEO_ZONE_START, GEO_ZONE_SIZE, GEO_ZONE_TEAMS

# US state abbreviations + a few common full-name -> abbr for parsing.
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


def parse_oneline_address(raw: str) -> dict:
    """Parse a one-line US address into components.

    Handles the common case where a member typed the entire address
    (street, city, state, zip) into a single field. Returns a dict with
    keys street / city / state / zip — any value may be None if it could
    not be confidently extracted. Conservative: if it can't find a state
    or zip anchor, it leaves city/state/zip None and returns the input as
    street unchanged.

    Examples handled:
      '3637 East Trinity Mills Road, Dallas, TX 75287'
      '1400 Brimwood Dr. McKinney, TX 75072'
      '520 Samuels Avenue, APT 3211, Fort Worth, TX 76102'
      '700 Ipswich Avenue, APT 54108, Fort Worth, TX, 76131'
    """
    out = {"street": None, "city": None, "state": None, "zip": None}
    if not raw:
        return out
    s = raw.strip().rstrip(",").strip()
    if not s:
        return out

    # Pull a trailing zip if present.
    zip_val = None
    m = _ZIP_RE.search(s)
    if m:
        zip_val = m.group(1)
        s = s[: m.start()].strip().rstrip(",").strip()

    # Pull a trailing state (2-letter abbr or known full name), now at the end.
    state_val = None
    tokens = s.split()
    if tokens:
        last = tokens[-1].strip(",.").upper()
        if last in _STATE_ABBRS:
            state_val = last
            s = " ".join(tokens[:-1]).strip().rstrip(",").strip()
        else:
            # try a two-word full state name, e.g. 'New Mexico'
            low = s.lower().rstrip(",").strip()
            for name, abbr in _STATE_NAMES.items():
                if low.endswith(name):
                    state_val = abbr
                    s = s[: len(s) - len(name)].strip().rstrip(",").strip()
                    break

    # If we found no state and no zip, we can't safely split — leave as-is.
    if not state_val and not zip_val:
        out["street"] = raw.strip()
        return out

    # City is the segment after the last comma; if no comma, the last token.
    city_val = None
    if "," in s:
        head, _, tail = s.rpartition(",")
        city_val = tail.strip() or None
        s = head.strip().rstrip(",").strip()
    else:
        # No comma between street and city (e.g. 'Dr. McKinney'): take the
        # trailing capitalized word(s) as city only if there's a clear break.
        parts = s.split()
        if len(parts) >= 2:
            city_val = parts[-1].strip(",.")
            s = " ".join(parts[:-1]).strip().rstrip(",").strip()

    out["street"] = s or None
    out["city"] = city_val
    out["state"] = state_val
    out["zip"] = zip_val
    return out


def calc_bearing(lat: float, lon: float) -> float:
    """Calculate bearing from center point to given coordinates."""
    lat1, lon1 = math.radians(GEO_CENTER[0]), math.radians(GEO_CENTER[1])
    lat2, lon2 = math.radians(lat), math.radians(lon)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = (math.cos(lat1) * math.sin(lat2) -
         math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    b = math.degrees(math.atan2(x, y))
    return (b + 360) % 360


def bearing_to_zone(bearing: float) -> str:
    """Convert a bearing to a zone team name."""
    idx = int(((bearing - GEO_ZONE_START + 360) % 360) / GEO_ZONE_SIZE)
    return GEO_ZONE_TEAMS[idx]


def assign_zone(lat: float, lon: float) -> tuple[str, float]:
    """Assign a geographic zone based on coordinates.
    Returns (team_name, bearing).
    """
    b = calc_bearing(lat, lon)
    return bearing_to_zone(b), b


import logging

import httpx

log = logging.getLogger(__name__)

_UA = "13thLegion-Praetorium/1.0 (portal.13thlegion.org)"


def geocode_zip(zip_code: str) -> tuple[float | None, float | None]:
    """Geocode a US zip code via Nominatim. Returns (lat, lon) or (None, None)."""
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"postalcode": zip_code, "country": "US", "format": "json", "limit": 1},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        log.warning("geocode_zip(%r) failed: %s", zip_code, e)
    return None, None


def _census_geocode(address: str) -> tuple[float | None, float | None]:
    """Geocode via US Census Bureau (most accurate for US addresses)."""
    try:
        r = httpx.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=10,
        )
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            return float(c["y"]), float(c["x"])
    except Exception as e:
        log.warning("_census_geocode(%r) failed: %s", address, e)
    return None, None


def _nominatim_geocode(address: str) -> tuple[float | None, float | None]:
    """Geocode via Nominatim/OpenStreetMap (fallback)."""
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        log.warning("_nominatim_geocode(%r) failed: %s", address, e)
    return None, None


def geocode_address(address: str) -> tuple[float | None, float | None]:
    """Geocode a US address. Census Bureau primary, Nominatim fallback."""
    lat, lon = _census_geocode(address)
    if lat is not None:
        return lat, lon
    return _nominatim_geocode(address)


def split_oneline_into_fields(
    address: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
) -> dict | None:
    """If a full address was typed into the street field and city/zip are
    empty, parse and return the split fields. Returns None when there's
    nothing to do (fields already populated, or nothing parseable).

    Only fills fields that are currently empty — never overwrites values the
    user explicitly entered. When it moves city/state/zip out of the street
    field, it also trims the street down to just the street portion.
    """
    addr = (address or "").strip()
    if not addr:
        return None
    city = (city or "").strip()
    zip_code = (zip_code or "").strip()
    # Only act when city or zip is missing (the symptom of a crammed field).
    if city and zip_code:
        return None

    parsed = parse_oneline_address(addr)
    if not (parsed["state"] or parsed["zip"]) or not parsed["city"]:
        # Couldn't confidently pull city + a state/zip anchor — leave alone.
        return None

    result = {
        "address": parsed["street"] or addr,
        "city": city or parsed["city"],
        "state": (state or "").strip() or parsed["state"] or "TX",
        "zip_code": zip_code or parsed["zip"],
    }
    return result


def geocode_member_fields(
    address: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
) -> tuple[float | None, float | None]:
    """Best-effort geocode from whatever address parts a member has.

    Handles the common case where the entire address (incl. city/zip) was
    entered into the `address` field and city/zip are NULL. Tries, in order:
      1. Full composed 'address, city, state zip' (whatever parts exist)
      2. The raw address field on its own (often already a one-line address)
      3. Zip code alone
    Returns (lat, lon) or (None, None).
    """
    parts = [p.strip() for p in (address, city, f"{(state or '').strip()} {(zip_code or '').strip()}".strip()) if p and p.strip()]
    composed = ", ".join(parts).strip(" ,")

    for candidate in (composed, (address or "").strip()):
        if candidate:
            lat, lon = geocode_address(candidate)
            if lat is not None:
                return lat, lon
    if zip_code and zip_code.strip():
        return geocode_zip(zip_code.strip())
    return None, None
