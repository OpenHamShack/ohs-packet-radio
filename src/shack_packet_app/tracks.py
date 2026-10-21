"""Where each station has been: the APRS map's tracks, and the course and
speed its last position report gave. No Qt.

Fed by the controller with every position heard (on the radio or by the SDR
monitor), whichever page is on screen, so a track has no gaps for the
minutes the operator spent on the Chat page. The engine's own heard list
keeps only the last position.

Kept for `keep_s` (Settings › Map, an hour by default) and then dropped
point by point; a point is added only when the station has moved at least
MIN_MOVE_KM, so a fixed station beaconing every ten minutes has a one-point
"track" and draws no line.
"""
from __future__ import annotations

import collections
import math
import threading
from dataclasses import dataclass, field

MIN_MOVE_KM = 0.05
#: A track holds at most this many points (a station beaconing every few
#: seconds for an hour would otherwise be thousands).
MAX_POINTS = 500


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat = (b[0] - a[0]) * 111.32
    dlon = (b[1] - a[1]) * 111.32 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


@dataclass
class Track:
    points: collections.deque = field(default_factory=lambda: collections.deque(maxlen=MAX_POINTS))
    course_deg: float | None = None
    speed_kmh: float | None = None
    last: float = 0.0


class Tracks:
    def __init__(self, keep_s: float = 3600.0):
        self.keep_s = float(keep_s)
        self._by_call: dict[str, Track] = {}
        self._lock = threading.Lock()

    def update(self, call: str, t: float, lat: float, lon: float, course_deg: float | None = None,
               speed_kmh: float | None = None) -> None:
        """A position heard at wall time `t`."""
        with self._lock:
            tr = self._by_call.setdefault(call, Track())
            if not tr.points or _km(tr.points[-1][1:], (lat, lon)) >= MIN_MOVE_KM:
                tr.points.append((t, lat, lon))
            tr.course_deg, tr.speed_kmh, tr.last = course_deg, speed_kmh, t
            self._prune(tr, t)

    def track(self, call: str, now: float | None = None) -> list[tuple[float, float, float]]:
        """(t, lat, lon) oldest first, within keep_s of `now` (default: the
        last time anything was heard from it)."""
        with self._lock:
            tr = self._by_call.get(call)
            if tr is None:
                return []
            self._prune(tr, tr.last if now is None else now)
            return list(tr.points)

    def motion(self, call: str) -> tuple[float | None, float | None]:
        """(course in degrees, speed in km/h) from its last report, either
        None when that report did not say."""
        with self._lock:
            tr = self._by_call.get(call)
            return (tr.course_deg, tr.speed_kmh) if tr is not None else (None, None)

    def _prune(self, tr: Track, now: float) -> None:
        while len(tr.points) > 1 and now - tr.points[0][0] > self.keep_s:
            tr.points.popleft()

    def clear(self) -> None:
        with self._lock:
            self._by_call.clear()
