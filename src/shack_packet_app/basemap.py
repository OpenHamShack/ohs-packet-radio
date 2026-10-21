"""The APRS map's built-in basemap: coastlines, land borders and towns, read
from the file tools/basemap.py builds (data/basemap.json names it). No Qt,
no network: the map draws from this whatever else is on or off.

The file is Natural Earth (public domain) and GeoNames cities5000 (CC BY
4.0, which asks for credit: `ATTRIBUTION` is drawn on the map and
basemap.json carries each source's URL, licence, date and checksum).

Loaded once, on first use, and kept: about 4 MB of arrays in memory. A
missing or unreadable file is logged and gives None, and the map falls back
to its locator grid alone, which is what release 1 drew.
"""
from __future__ import annotations

import io
import json
import logging
import threading
from importlib import resources

import numpy as np

log = logging.getLogger(__name__)

_lock = threading.Lock()
_loaded: Basemap | None | bool = False


class Lines:
    """One polyline layer, cut into runs of at most a few hundred points,
    each with its box (min lon, min lat, max lon, max lat)."""

    def __init__(self, d: np.ndarray, first: np.ndarray, start: np.ndarray, box: np.ndarray, scale: float):
        steps = d.astype(np.int32)
        steps[start] = first
        total = np.cumsum(steps, axis=0)
        # cumsum ran across runs: take off what the runs before each one added.
        before = np.zeros_like(first)
        before[1:] = total[start[1:] - 1]
        lengths = np.diff(np.append(start, len(d)))
        self.points = ((total - np.repeat(before, lengths, axis=0)) / scale).astype(np.float64)
        self.start = start
        self.end = np.append(start[1:], len(d))
        self.box = box

    def __len__(self) -> int:
        return len(self.start)

    def visible(self, west: float, south: float, east: float, north: float) -> np.ndarray:
        """Indices of the runs whose box meets this one."""
        b = self.box
        return np.nonzero((b[:, 0] <= east) & (b[:, 2] >= west) & (b[:, 1] <= north) & (b[:, 3] >= south))[0]

    def run(self, i: int) -> np.ndarray:
        """(k, 2) lon, lat of run i."""
        return self.points[self.start[i]:self.end[i]]


class Towns:
    """Largest first, so the first towns in view are the ones to label."""

    def __init__(self, names: np.ndarray, name_end: np.ndarray, lat: np.ndarray, lon: np.ndarray, pop: np.ndarray,
                 scale: float):
        self._names = names.tobytes()
        self._end = name_end
        self.lat = lat / scale
        self.lon = lon / scale
        self.pop = pop

    def __len__(self) -> int:
        return len(self.pop)

    def name(self, i: int) -> str:
        a = int(self._end[i - 1]) if i else 0
        return self._names[a:int(self._end[i])].decode("utf-8")

    def visible(self, west: float, south: float, east: float, north: float, limit: int = 400) -> np.ndarray:
        """Indices of the towns in this box, largest first, at most `limit`."""
        m = (self.lon >= west) & (self.lon <= east) & (self.lat >= south) & (self.lat <= north)
        return np.nonzero(m)[0][:limit]


class Basemap:
    def __init__(self, arrays, manifest: dict):
        scale = float(manifest["scale"])
        self.manifest = manifest
        self.attribution = manifest.get("attribution", "")
        self.layers = {key: Lines(arrays[f"{key}_d"], arrays[f"{key}_first"], arrays[f"{key}_start"],
                                  arrays[f"{key}_box"], scale)
                       for key in ("coast10", "border10", "coast50", "border50")}
        self.towns = Towns(arrays["towns_names"], arrays["towns_name_end"], arrays["towns_lat"],
                           arrays["towns_lon"], arrays["towns_pop"], scale)


def load() -> Basemap | None:
    """The bundled basemap, read on the first call; None if it cannot be."""
    global _loaded
    with _lock:
        if _loaded is False:
            try:
                pkg = resources.files(__package__).joinpath("data")
                manifest = json.loads(pkg.joinpath("basemap.json").read_text(encoding="utf-8"))
                with np.load(io.BytesIO(pkg.joinpath(manifest["file"]).read_bytes()), allow_pickle=False) as z:
                    _loaded = Basemap({k: z[k] for k in z.files}, manifest)
            except Exception:                       # noqa: BLE001 - the grid still draws without it
                log.exception("the built-in basemap could not be read; the map shows the locator grid only")
                _loaded = None
        return _loaded
