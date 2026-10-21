"""OpenStreetMap tiles under the APRS map: off unless the operator turns them
on (Settings › Map), and then fetched only for the area in view, kept on
disk, and credited on the map.

This is the one part of the app that uses the internet for something other
than what the operator asked to send (Brian, 18/10/2026: the built-in
basemap always works offline; tiles are an option, off by default, with
the internet use stated and credited). Nothing here runs while tiles are
off: the map makes no TileStore.

The OSM Foundation's tile usage policy (operations.osmfoundation.org/
policies/tiles, read 18/10/2026), point by point:
- **A real User-Agent** naming the app and how to reach its makers
  (`user_agent()`); never a library's default, which is blocked.
- **Attribution on the map**, "© OpenStreetMap contributors", drawn by the
  map whenever a tile is shown (mapview.py).
- **Cache at least 7 days** and never send no-cache: a tile on disk is
  used for KEEP_S before it is asked for again, and stays in use, stale,
  for as long as the server cannot be reached.
- **No bulk download, no prefetch, no offline packs**: `want()` replaces
  the queue with the tiles of the current view, so panning away drops
  what was not yet fetched, and nothing beyond the view is ever asked for.
- At most two requests at a time, and a tile that failed is not asked for
  again for RETRY_S.

The tile server is the operator's to change (a club's own, or a paid
provider); the same rules are kept for any of them. The disk cache is
capped (Settings, 200 MB by default): the oldest tiles go first.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
KEEP_S = 7 * 86400.0
RETRY_S = 60.0
WORKERS = 2
MAX_ZOOM = 19


def user_agent() -> str:
    from . import __version__
    return f"OpenHamShack-Packet-Radio/{__version__} (+https://openhamshack.com)"


def valid_template(url: str) -> bool:
    return url.startswith(("https://", "http://")) and all(k in url for k in ("{z}", "{x}", "{y}"))


def http_fetch(url: str, timeout: float = 15.0) -> bytes:
    """One GET with the app's User-Agent; OSError on anything but 200."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise OSError(f"HTTP {r.status}")
        return r.read()


class TileStore:
    """Tiles for one URL template, cached under `cache_dir`. `on_ready` is
    told (z, x, y) from a worker thread when a tile arrives."""

    def __init__(self, cache_dir: Path, url: str = OSM_URL, *, cap_bytes: int = 200 * 1024 * 1024,
                 fetch: Callable[[str], bytes] = http_fetch, on_ready: Callable[[int, int, int], None] | None = None,
                 clock: Callable[[], float] = time.time):
        if not valid_template(url):
            raise ValueError("a tile URL needs http(s):// and {z}, {x} and {y}")
        self.url = url
        self.dir = Path(cache_dir) / hashlib.sha1(url.encode()).hexdigest()[:12]
        self.cap = int(cap_bytes)
        self.fetch = fetch
        self.on_ready = on_ready
        self.clock = clock
        self.fetched = 0
        self.failed: dict[tuple, float] = {}
        self._queue: list[tuple] = []
        self._busy: set[tuple] = set()
        self._cond = threading.Condition()
        self._stop = False
        self._workers = [threading.Thread(target=self._work, daemon=True, name=f"map tiles {i}")
                         for i in range(WORKERS)]
        for w in self._workers:
            w.start()

    def path(self, z: int, x: int, y: int) -> Path:
        return self.dir / str(z) / str(x) / f"{y}.png"

    def get(self, z: int, x: int, y: int) -> bytes | None:
        """A tile from disk, stale or not (stale ones are asked for again
        by want()); None when there is none yet."""
        try:
            return self.path(z, x, y).read_bytes()
        except OSError:
            return None

    def fresh(self, z: int, x: int, y: int) -> bool:
        try:
            return self.clock() - self.path(z, x, y).stat().st_mtime < KEEP_S
        except OSError:
            return False

    def want(self, tiles: list[tuple[int, int, int]]) -> None:
        """The tiles the map shows now: those missing or older than KEEP_S
        are queued and everything else queued before is dropped."""
        now = self.clock()
        need = [t for t in tiles if not self.fresh(*t) and now >= self.failed.get(t, 0.0)]
        with self._cond:
            self._queue = [t for t in need if t not in self._busy]
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._queue = []
            self._cond.notify_all()

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                t = self._queue.pop(0)
                self._busy.add(t)
            z, x, y = t
            try:
                data = self.fetch(self.url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)))
                p = self.path(z, x, y)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(p)
                self.fetched += 1
                if self.fetched % 50 == 0:
                    self.prune()
            except Exception as e:                  # noqa: BLE001 - offline, refused, a bad tile: the basemap shows
                log.info("map tile %s/%s/%s not fetched: %s", z, x, y, e)
                self.failed[t] = self.clock() + RETRY_S
                data = None
            finally:
                with self._cond:
                    self._busy.discard(t)
            if data is not None and self.on_ready is not None:
                try:
                    self.on_ready(z, x, y)
                except Exception:                   # noqa: BLE001
                    log.exception("tile listener")

    def prune(self) -> int:
        """Oldest tiles removed until the cache is under its cap; returns
        how many went."""
        files = []
        for p in self.dir.rglob("*.png"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
        total = sum(f[1] for f in files)
        gone = 0
        for _m, size, p in sorted(files):
            if total <= self.cap:
                break
            try:
                p.unlink()
            except OSError:
                continue
            total -= size
            gone += 1
        return gone


def tiles_for(west: float, south: float, east: float, north: float, z: int) -> list[tuple[int, int, int]]:
    """The Web Mercator tiles (z, x, y) covering a lat/lon box."""
    import math
    n = 2 ** z

    def xy(lat, lon):
        lat = max(-85.05, min(85.05, lat))
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return max(0, min(n - 1, x)), max(0, min(n - 1, y))
    x0, y0 = xy(north, west)
    x1, y1 = xy(south, east)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a tile, in degrees."""
    import math
    n = 2 ** z

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y)
