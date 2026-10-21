"""Orbital elements: kept on disk, updated from CelesTrak when the operator
asks, and the packet satellites the app knows how to work.

**Only on demand.** Predictions run offline from the last file fetched;
the app never goes to the internet for elements by itself (a desktop app
that phones home on a timer is one more thing a tester's firewall asks
about). CelesTrak's amateur group needs no key and is served over HTTPS;
the reply is checked to parse before it replaces the cache, so a captive
portal's login page or a truncated download never loses the elements the
station already had.

**How old is too old.** SGP4 from a week-old TLE puts the ISS some
kilometres out - nothing to a pass time or a Doppler step at 2 m. At a
month the ISS has re-boosted or decayed far enough that passes can be
minutes out, so passes from elements older than TOO_OLD_DAYS are not
predicted, and STALE_DAYS says "update" first.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .track import Satellite, parse_tles

CELESTRAK_AMATEUR = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle"
STALE_DAYS = 7
TOO_OLD_DAYS = 30
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Bird:
    """A satellite this app can work packet through. `how` is the IC-9700
    set-up for its Doppler (shack_rig.icom.satdd): split for one band both
    ways, satellite for cross-band."""
    norad: int
    name: str
    uplink_hz: int
    downlink_hz: int
    path: str
    how: str
    note: str = ""


#: The ISS's APRS digipeater (ARISS): 145.825 MHz FM AFSK 1200 both ways,
#: digipeating via the alias ARISS. Other packet satellites come and go
#: (and their transponders with them); one is added here once it is
#: confirmed in service, or by the operator in settings (sat.extra_birds).
#: PACSAT-style file downloads (broadcast protocol, the FTL0 uplink) are
#: deferred: no satellite in service carries one for 1200 baud FM today.
BIRDS = {
    25544: Bird(25544, "ISS", 145_825_000, 145_825_000, "ARISS", "split",
                "APRS digipeater; FM, 1200 baud"),
}


def birds(extra: list | None = None) -> dict[int, Bird]:
    """BIRDS with the operator's own additions (dicts with the same keys)."""
    out = dict(BIRDS)
    for d in extra or []:
        try:
            b = Bird(int(d["norad"]), str(d["name"]), int(d["uplink_hz"]), int(d["downlink_hz"]),
                     str(d.get("path") or ""), str(d.get("how") or "split"), str(d.get("note") or ""))
        except (KeyError, TypeError, ValueError):
            continue
        out[b.norad] = b
    return out


class TleError(Exception):
    pass


class TleStore:
    """The elements file. `fetch(url) -> bytes` is how the update gets it
    (the app's update.fetch with its user agent; a test's stand-in)."""

    def __init__(self, path: Path, fetch=None, clock=time.time):
        self.path = Path(path)
        self.fetch = fetch
        self.clock = clock
        self._sats: list[Satellite] | None = None

    def satellites(self) -> list[Satellite]:
        if self._sats is None:
            try:
                self._sats = parse_tles(self.path.read_text(encoding="ascii", errors="replace"))
            except OSError:
                self._sats = []
        return self._sats

    def find(self, key) -> Satellite | None:
        """By NORAD number, or by name (any case)."""
        sats = self.satellites()
        if isinstance(key, int) or str(key).strip().isdigit():
            n = int(key)
            return next((s for s in sats if s.number == n), None)
        k = str(key).strip().upper()
        return next((s for s in sats if s.name.upper() == k), None)

    def age_days(self, number: int | None = None) -> float | None:
        """How old the elements are: one satellite's epoch, or the newest
        epoch in the file; None with nothing loaded."""
        sats = [s for s in self.satellites() if number is None or s.number == number]
        if not sats:
            return None
        return (self.clock() - max(s.epoch_unix for s in sats)) / 86400.0

    def update(self, url: str = CELESTRAK_AMATEUR) -> int:
        """Fetch, check, and replace the cache. Returns how many
        satellites came; TleError if nothing usable did (the cache kept)."""
        if self.fetch is None:
            raise TleError("no way to fetch")
        try:
            data = self.fetch(url)
        except Exception as e:                      # noqa: BLE001 - the network's many ways to fail
            raise TleError(str(e) or type(e).__name__) from None
        if len(data) > MAX_BYTES:
            raise TleError("the reply is too big to be elements")
        text = data.decode("ascii", errors="replace")
        sats = parse_tles(text)
        if not sats:
            raise TleError("the reply had no orbital elements in it")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="ascii")
        os.replace(tmp, self.path)
        self._sats = sats
        return len(sats)
