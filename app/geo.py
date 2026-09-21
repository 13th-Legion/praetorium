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
from app.address_parse import parse_oneline_address  # noqa: F401  (re-export)
from app.constants import GEO_CENTER, GEO_ZONE_START, GEO_ZONE_SIZE, GEO_ZONE_TEAMS  # noqa: F401 (fallback seeds)
from app.services import settings_store as _ss

def calc_bearing(lat: float, lon: float) -> float:
    """Calculate bearing from center point to given coordinates."""
    _c = _ss.geo_center()
    lat1, lon1 = math.radians(_c[0]), math.radians(_c[1])
    lat2, lon2 = math.radians(lat), math.radians(lon)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = (math.cos(lat1) * math.sin(lat2) -
         math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    b = math.degrees(math.atan2(x, y))
    return (b + 360) % 360


def bearing_to_zone(bearing: float, geo_teams: list[str] | None = None) -> str:
    """Convert a bearing to a zone team name.

    geo_teams: ordered list of geo-zone team names (index 0 = North slice at
    330°..30°). Pass the DB-derived list from teams.geo_zone_teams() so renames
    are honored; falls back to the constants seed when omitted.
    """
    teams = geo_teams or GEO_ZONE_TEAMS
    idx = int(((bearing - _ss.geo_zone_start() + 360) % 360) / _ss.geo_zone_size())
    return teams[idx]


def assign_zone(lat: float, lon: float, geo_teams: list[str] | None = None) -> tuple[str, float]:
    """Assign a geographic zone based on coordinates.
    Returns (team_name, bearing). Pass geo_teams (DB-derived) to honor renames.
    """
    b = calc_bearing(lat, lon)
    return bearing_to_zone(b, geo_teams), b


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
      2. The raw address field on its own — ONLY if it carries its own
         locality (a state abbr or a 5-digit zip). A bare street with no
         city/state (e.g. rural 'County Road 499') is skipped here because
         geocoding it alone matches same-named roads in other states
         (produced an Alabama hit for a Hico, TX member — 2026-07-15).
      3. Zip code alone (reliable region anchor).
    Returns (lat, lon) or (None, None).
    """
    parts = [p.strip() for p in (address, city, f"{(state or '').strip()} {(zip_code or '').strip()}".strip()) if p and p.strip()]
    composed = ", ".join(parts).strip(" ,")

    raw = (address or "").strip()
    # Only trust the raw address alone if it self-anchors to a locality:
    # contains a 5-digit zip or a state token. Otherwise it's a bare street
    # and must not override the zip-centroid fallback.
    raw_self_anchored = bool(
        raw and (re.search(r"\b\d{5}\b", raw) or re.search(r",\s*[A-Za-z]{2}\b", raw))
    )

    candidates = [composed]
    if raw_self_anchored:
        candidates.append(raw)
    for candidate in candidates:
        if candidate:
            lat, lon = geocode_address(candidate)
            if lat is not None:
                return lat, lon
    if zip_code and zip_code.strip():
        return geocode_zip(zip_code.strip())
    return None, None
