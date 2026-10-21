"""Maidenhead locators, both ways, for the station profile and the map.

The map is drawn on the locator grid itself (ui/mapview.py), so the same
arithmetic decides where a square's lines go and where a station lands on
it. Six characters (a subsquare, about 5 x 2.5 km) is the most a profile
needs and the most an APRS position is worth placing; eight is accepted
and used.
"""
from __future__ import annotations

import re

_LOCATOR = re.compile(r"^[A-R]{2}([0-9]{2}([A-X]{2}([0-9]{2})?)?)?$")


def valid(text: str) -> bool:
    return bool(_LOCATOR.match(text.strip().upper()))


def to_latlon(text: str) -> tuple[float, float]:
    """The centre of the square `text` names, (lat, lon) in degrees.
    Raises ValueError for anything that is not a locator."""
    loc = text.strip().upper()
    if not valid(loc):
        raise ValueError(loc)
    lon, lat = -180.0, -90.0
    w, h = 20.0, 10.0
    lon += (ord(loc[0]) - 65) * w
    lat += (ord(loc[1]) - 65) * h
    if len(loc) >= 4:
        w, h = w / 10, h / 10
        lon += int(loc[2]) * w
        lat += int(loc[3]) * h
    if len(loc) >= 6:
        w, h = w / 24, h / 24
        lon += (ord(loc[4]) - 65) * w
        lat += (ord(loc[5]) - 65) * h
    if len(loc) >= 8:
        w, h = w / 10, h / 10
        lon += int(loc[6]) * w
        lat += int(loc[7]) * h
    return lat + h / 2, lon + w / 2


def from_latlon(lat: float, lon: float, chars: int = 6) -> str:
    """The locator of the square holding (lat, lon), 2 to 8 characters."""
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError((lat, lon))
    lon = min(lon + 180.0, 359.999999)
    lat = min(lat + 90.0, 179.999999)
    out = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    lon, lat = lon % 20, lat % 10
    if chars >= 4:
        out += str(int(lon // 2)) + str(int(lat // 1))
        lon, lat = lon % 2, lat % 1
    if chars >= 6:
        out += chr(65 + int(lon // (2 / 24))) + chr(65 + int(lat // (1 / 24)))
        lon, lat = lon % (2 / 24), lat % (1 / 24)
    if chars >= 8:
        out += str(int(lon // (2 / 240))) + str(int(lat // (1 / 240)))
    return out


#: The mean Earth radius, km (IUGG): what every locator-distance tool uses,
#: so our "523 km" is the number the other station's logger shows.
EARTH_KM = 6371.0088


def distance_bearing(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Great-circle distance in km and initial bearing in degrees true
    from `a` to `b`, each (lat, lon)."""
    import math
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    km = 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return km, (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
