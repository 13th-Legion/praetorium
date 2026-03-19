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
from app.constants import GEO_CENTER, GEO_ZONE_START, GEO_ZONE_SIZE, GEO_ZONE_TEAMS


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


def geocode_zip(zip_code: str) -> tuple[float | None, float | None]:
    """Geocode a US zip code via Nominatim. Returns (lat, lon) or (None, None)."""
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"postalcode": zip_code, "country": "US", "format": "json", "limit": 1},
            headers={"User-Agent": "13thLegion-Praetorium/1.0"},
            timeout=10,
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return None, None


def _census_geocode(address: str) -> tuple[float | None, float | None]:
    """Geocode via US Census Bureau (most accurate for US addresses)."""
    import requests
    try:
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=10,
        )
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            return float(c["y"]), float(c["x"])
    except Exception:
        pass
    return None, None


def _nominatim_geocode(address: str) -> tuple[float | None, float | None]:
    """Geocode via Nominatim/OpenStreetMap (fallback)."""
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": "13thLegion-Praetorium/1.0"},
            timeout=10,
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return None, None


def geocode_address(address: str) -> tuple[float | None, float | None]:
    """Geocode a US address. Census Bureau primary, Nominatim fallback."""
    lat, lon = _census_geocode(address)
    if lat is not None:
        return lat, lon
    return _nominatim_geocode(address)
