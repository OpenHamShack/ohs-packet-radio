"""The IC-9700's spectrum for the IC-9700 page (0.3.0): a waterfall's rows
from the radio's own scope, the packet channels marked on it, which of
them look busy, and where a click may tune.

**From the radio, not an SDR.** The 9700 sends its whole 475-point sweep
in one LAN message once its scope output is on (CI-V 27 00; shack_rig's
assembler, IcomRig's keeper). `ScopeFeed.attach` asks for it through
shack_packet IcomControl while the page is shown and `detach` stops
asking when it is hidden, so a radio nobody is watching sends no sweeps
(the keeper switches the output off SCOPE_RELEASE_S later). Over a USB
cable at 19200 baud the rig refuses the scope (a sweep would fill the
line); the page shows the rig's own sentence.

**Busy channels** are judged from the sweep alone: a packet channel's peak
within its bandwidth against the sweep's floor (the median point). The
scope's 0-160 is not calibrated in dB; Icom's display spans about 80 dB
for those 160 steps, so UNITS_PER_DB is 2 - an assumption until measured
on the radio, which is why the threshold is a setting.

**Click to tune** goes to the nearest packet channel within SNAP_HZ, or
else to the click rounded to RASTER_HZ, and only where the band plan
allows packet for this licence (presets.judge); the station is disarmed
first, because a retune is a new channel to check before transmitting.
"""
from __future__ import annotations

import collections
import threading
from dataclasses import dataclass

import numpy as np

ROWS = 300
UNITS_PER_DB = 2.0
CHANNEL_BW_HZ = 12_500
SNAP_HZ = 6_250
RASTER_HZ = 5_000


@dataclass(frozen=True)
class Row:
    start: int
    end: int
    data: np.ndarray            # uint8, 0-160


@dataclass(frozen=True)
class Channel:
    hz: int
    label: str


class ScopeFeed:
    """Sweeps from the rig's loop, kept for the window's thread."""

    def __init__(self, rows: int = ROWS):
        self._rows: collections.deque[Row] = collections.deque(maxlen=rows)
        self._lock = threading.Lock()
        self.rev = 0
        self._stop = None
        self.refused: str | None = None

    def add(self, spectrum: dict) -> None:
        """A whole sweep (IcomRig's listener; on the rig's loop, so short)."""
        data = np.asarray(spectrum.get("data") or [], dtype=np.uint8)
        if not data.size:
            return
        row = Row(int(spectrum["start"]), int(spectrum["end"]), data)
        with self._lock:
            self._rows.append(row)
            self.rev += 1

    def rows(self) -> list[Row]:
        with self._lock:
            return list(self._rows)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self.rev += 1

    @property
    def attached(self) -> bool:
        return self._stop is not None

    def attach(self, icom, settings=None) -> bool:
        """Start the sweeps (and send the page's span and reference)."""
        if self._stop is not None:
            return True
        self.refused = icom.scope_refusal()
        if self.refused:
            return False
        self._stop = icom.listen_scope(self.add)
        if settings is not None:
            icom.set_scope(span_hz=int(settings.span_hz), ref_db=float(settings.ref_db), speed=settings.speed)
        return True

    def detach(self) -> None:
        stop, self._stop = self._stop, None
        if stop is not None:
            stop()


def channels(profile: dict, current_hz: int, extra: tuple = ()) -> list[Channel]:
    """The packet channels to mark: the band plan's packet presets for this
    station, the station's own channel, and any extra (a DD channel)."""
    from . import presets
    out = {int(p.hz): Channel(int(p.hz), p.label) for p in presets.presets(profile)}
    out.setdefault(int(current_hz), Channel(int(current_hz), ""))
    for hz, label in extra:
        out.setdefault(int(hz), Channel(int(hz), label))
    return sorted(out.values(), key=lambda c: c.hz)


def in_view(row: Row, chans: list[Channel]) -> list[Channel]:
    return [c for c in chans if row.start <= c.hz <= row.end]


def busy(row: Row, chans: list[Channel], threshold_db: float, bw_hz: int = CHANNEL_BW_HZ) -> dict[int, bool]:
    """Channel Hz -> busy, for the channels inside this sweep."""
    n = row.data.size
    span = max(1, row.end - row.start)
    floor = float(np.median(row.data))
    out = {}
    for c in in_view(row, chans):
        lo = int((c.hz - bw_hz / 2 - row.start) / span * (n - 1))
        hi = int((c.hz + bw_hz / 2 - row.start) / span * (n - 1)) + 1
        seg = row.data[max(0, lo):min(n, max(hi, lo + 1))]
        out[c.hz] = bool(seg.size) and float(seg.max()) - floor >= threshold_db * UNITS_PER_DB
    return out


def tune_target(hz: float, chans: list[Channel], profile: dict) -> tuple[int | None, str]:
    """(Hz to tune, "") or (None, the band plan's sentence)."""
    from . import presets
    near = min(chans, key=lambda c: abs(c.hz - hz), default=None)
    if near is not None and abs(near.hz - hz) <= SNAP_HZ:
        target = near.hz
    else:
        target = int(round(hz / RASTER_HZ) * RASTER_HZ)
    verdict = presets.judge(profile, target)
    if not verdict["allowed"]:
        return None, verdict["refusal"]
    return target, ""
