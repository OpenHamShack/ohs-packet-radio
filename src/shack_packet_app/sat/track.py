"""Where a satellite is from the station: look angles, passes, Doppler.

SGP4 is the vendored sgp4 2.27 (`_sgp4`, MIT); everything after it is
here, in plain math, so each step can be checked against a textbook:

1. SGP4 gives the satellite's position and velocity in TEME (km, km/s).
2. TEME to Earth-fixed by Greenwich mean sidereal time (IAU 1982, the
   rotation SGP4's own frame is defined against; polar motion and the
   equation of the equinoxes are left out - tens of metres, nothing to a
   pass prediction or a Doppler step).
3. The station's own Earth-fixed position from its locator on WGS84.
4. The difference, turned into south-east-zenith at the station: azimuth,
   elevation, range; the range rate from the relative velocity (the
   satellite's TEME velocity rotated, less the Earth's spin at its place).

**Passes** are found by sampling the elevation every SCAN_S and bisecting
each sign change to RESOLVE_S; a pass already in progress at the start is
reported from the start. SCAN_S 30 s finds every LEO pass above a few
degrees (the shortest a station on the ISS's inclination sees are about two
minutes); one grazing the horizon for less than that can be missed, which
for a packet station is no loss.

**Doppler**: the station receives f(1 - v/c) and must transmit f/(1 - v/c)
so the satellite hears f, with v the range rate (positive moving away).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ._sgp4 import WGS72, Satrec

C_KM_S = 299_792.458
#: WGS84.
EARTH_A_KM = 6378.137
EARTH_F = 1 / 298.257223563
#: Earth's rotation, rad/s.
OMEGA_E = 7.292115146706979e-5
#: Unix time of the Julian date 2440587.5.
JD_UNIX0 = 2440587.5

SCAN_S = 30.0
RESOLVE_S = 1.0


class TrackError(Exception):
    pass


@dataclass(frozen=True)
class Look:
    az_deg: float
    el_deg: float
    range_km: float
    #: Positive moving away, km/s.
    range_rate_km_s: float

    def doppler(self, hz: float) -> tuple[float, float]:
        """(receive, transmit) Hz for a satellite on `hz` (module docstring)."""
        k = 1 - self.range_rate_km_s / C_KM_S
        return hz * k, hz / k


@dataclass(frozen=True)
class Pass:
    aos: float              # unix seconds
    tca: float              # highest point
    los: float
    max_el_deg: float
    aos_az_deg: float
    los_az_deg: float

    @property
    def duration_s(self) -> float:
        return self.los - self.aos


def jd(unix: float) -> tuple[float, float]:
    """Unix seconds as SGP4's split Julian date (whole, fraction)."""
    d = unix / 86400.0 + JD_UNIX0
    whole = math.floor(d - 0.5) + 0.5
    return whole, d - whole


def gmst_rad(unix: float) -> float:
    """Greenwich mean sidereal time (IAU 1982), radians."""
    w, f = jd(unix)
    t = ((w - 2451545.0) + f) / 36525.0
    sec = (67310.54841 + (876600.0 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3)
    return math.radians((sec % 86400.0) / 240.0) % (2 * math.pi)


def station_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> tuple[float, float, float]:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    e2 = EARTH_F * (2 - EARTH_F)
    n = EARTH_A_KM / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = alt_m / 1000.0
    return ((n + h) * math.cos(lat) * math.cos(lon), (n + h) * math.cos(lat) * math.sin(lon),
            (n * (1 - e2) + h) * math.sin(lat))


class Satellite:
    """One satellite from its TLE."""

    def __init__(self, name: str, line1: str, line2: str):
        self.name = name.strip()
        self.line1, self.line2 = line1.strip(), line2.strip()
        try:
            self.satrec = Satrec.twoline2rv(self.line1, self.line2, WGS72)
        except Exception as e:                      # noqa: BLE001 - the library's ValueError and its friends
            raise TrackError(f"{self.name}: not a TLE ({e})") from None
        if self.satrec.error:
            raise TrackError(f"{self.name}: not a TLE (sgp4 error {self.satrec.error})")
        self.number = int(self.satrec.satnum)

    @property
    def epoch_unix(self) -> float:
        return (self.satrec.jdsatepoch + self.satrec.jdsatepochF - JD_UNIX0) * 86400.0

    def teme(self, unix: float):
        w, f = jd(unix)
        err, r, v = self.satrec.sgp4(w, f)
        if err:
            raise TrackError(f"{self.name}: sgp4 error {err} (the TLE is too old, or the satellite has decayed)")
        return r, v

    def look(self, unix: float, lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> Look:
        r, v = self.teme(unix)
        th = gmst_rad(unix)
        c, s = math.cos(th), math.sin(th)
        # TEME -> Earth-fixed: rotate by -GMST; the velocity also loses the
        # Earth's spin (omega x r).
        x, y, z = c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]
        vx = c * v[0] + s * v[1] + OMEGA_E * y
        vy = -s * v[0] + c * v[1] - OMEGA_E * x
        vz = v[2]
        ox, oy, oz = station_ecef(lat_deg, lon_deg, alt_m)
        dx, dy, dz = x - ox, y - oy, z - oz
        lat, lon = math.radians(lat_deg), math.radians(lon_deg)
        sl, cl, so, co = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
        south = sl * co * dx + sl * so * dy - cl * dz
        east = -so * dx + co * dy
        up = cl * co * dx + cl * so * dy + sl * dz
        rng = math.sqrt(dx * dx + dy * dy + dz * dz)
        el = math.degrees(math.asin(up / rng))
        az = math.degrees(math.atan2(east, -south)) % 360.0
        rate = (dx * vx + dy * vy + dz * vz) / rng
        return Look(az, el, rng, rate)

    def passes(self, start: float, hours: float, lat_deg: float, lon_deg: float, alt_m: float = 0.0,
               min_el_deg: float = 0.0) -> list[Pass]:
        """Every pass rising above the horizon between `start` and `hours`
        later whose highest point clears `min_el_deg`."""
        def el(t):
            return self.look(t, lat_deg, lon_deg, alt_m).el_deg

        def edge(a, b, rising):
            # el(a) and el(b) straddle 0; bisect to RESOLVE_S.
            while b - a > RESOLVE_S:
                m = (a + b) / 2
                if (el(m) > 0) == rising:
                    b = m
                else:
                    a = m
            return b if rising else a

        out: list[Pass] = []
        end = start + hours * 3600.0
        t, prev = start, el(start)
        aos = start if prev > 0 else None
        while t < end:
            t2 = min(t + SCAN_S, end)
            now = el(t2)
            if prev <= 0 < now:
                aos = edge(t, t2, True)
            elif prev > 0 >= now and aos is not None:
                out.append(self._pass(aos, edge(t, t2, False), lat_deg, lon_deg, alt_m))
                aos = None
            t, prev = t2, now
        if aos is not None:
            out.append(self._pass(aos, end, lat_deg, lon_deg, alt_m))
        return [p for p in out if p.max_el_deg >= min_el_deg]

    def _pass(self, aos, los, lat, lon, alt) -> Pass:
        # The top by golden-section search: elevation is unimodal over a pass.
        a, b = aos, los
        g = (math.sqrt(5) - 1) / 2
        while b - a > RESOLVE_S:
            m1, m2 = b - g * (b - a), a + g * (b - a)
            if self.look(m1, lat, lon, alt).el_deg < self.look(m2, lat, lon, alt).el_deg:
                a = m1
            else:
                b = m2
        tca = (a + b) / 2
        return Pass(aos, tca, los, self.look(tca, lat, lon, alt).el_deg, self.look(aos, lat, lon, alt).az_deg,
                    self.look(los, lat, lon, alt).az_deg)


def parse_tles(text: str) -> list[Satellite]:
    """Every satellite in a three-line (name, 1, 2) TLE file; a set that
    does not parse is skipped, so one bad entry cannot hide the rest."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 ") and not name.startswith(("1 ", "2 ")):
            try:
                out.append(Satellite(name, l1, l2))
            except TrackError:
                pass
            i += 3
        else:
            i += 1
    return out
