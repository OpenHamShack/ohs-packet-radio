"""Adaptive transmit power for a CAT radio: the lowest RF POWER that still
works, found from the link's own quality (Packet Radio 0.3.0; Brian's
"lowest power that works", 20/10/2026).

**Off by default, with a clear switch** (Settings in the IC-9700 page's
Power tab). On, it starts each band at the operator's cap the first time
and, after that, one step above the level that last worked there (per-band
memory, kept in settings: the 9700 keeps RF POWER per band too), and:

- steps DOWN one step after GOOD_WINDOWS clean windows in a row;
- steps UP one step on a bad window, two on a very bad one;
- never goes above the cap or below the floor, and after a step down
  that went straight back up, needs twice as many clean windows before
  the next one (the anti-flap rule adapt.py's mode ladder also has);
- does nothing on a window with too little traffic to judge.

**The evidence** is what this station can see for itself, every WINDOW_S:
the connected sessions' I frames sent and retransmitted and T1 expiries
(shack_packet datalink stats, summed over every connection, counted from
each one's own start), and the other station's SNR when something reports
it (`report_snr`: the link test's telemetry is the source today, and A's
link-test page calls it; a reading older than a window is not used).
Retransmissions are the honest signal: a frame the other end did not hear
is sent again whatever the reason, so a window is bad when a fifth or more
of its I frames were repeats or T1 ran out twice. Rejected: the S-meter of
the other station's replies - it says how well we hear them, not how well
they hear us.

**It never fights the operator.** Before every change it reads RF POWER
back; a level other than the one it last set means somebody turned the
knob (or A's control strip did), and adaptation stops for the session and
says so. It also stays off while the real-radio test's minimum-power step
is on (RadioSettings.minimum_power_for_test), which holds the radio at 0.
Every change is logged with its reason and kept for the page, and the
level found at the start is put back when the station stops - unless the
operator took over.

Icom (IC-9700, IC-705) only for now: shack_rig's Yaesu backend has no RF
POWER write yet (its CAT `PC` command), so the FTX-1 is a follow-up.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

WINDOW_S = 30.0
MIN_FRAMES = 4
GOOD_RATIO = 0.05
BAD_RATIO = 0.20
VERY_BAD_RATIO = 0.5
GOOD_WINDOWS = 3
GOOD_WINDOWS_MAX = 12
#: SNR (dB) from the other end, when one is reported: below SNR_LOW is a
#: bad window whatever the retries say (the next fade loses frames); a good
#: window needs SNR_HIGH when SNR is known.
SNR_LOW_DB = 6.0
SNR_HIGH_DB = 15.0

ICOM_KINDS = ("ic-9700", "ic-705")


def band_of(hz: float) -> str:
    for name, lo, hi in (("2m", 144e6, 148e6), ("70cm", 430e6, 450e6), ("23cm", 1240e6, 1300e6),
                         ("6m", 50e6, 54e6), ("4m", 70e6, 71e6), ("10m", 28e6, 29.7e6)):
        if lo <= hz <= hi:
            return name
    return f"{hz / 1e6:.0f}MHz"


def level_of(percent: float) -> int:
    return max(0, min(255, round(float(percent) * 255 / 100)))


def percent_of(level: int) -> int:
    return round(int(level) * 100 / 255)


@dataclass
class Evidence:
    sent: int = 0
    retried: int = 0
    t1: int = 0
    snr_db: float | None = None

    def verdict(self) -> str:
        """good, bad, very-bad, mixed or quiet."""
        snr = self.snr_db
        if self.sent < MIN_FRAMES and snr is None:
            return "quiet"
        ratio = self.retried / self.sent if self.sent else 0.0
        if self.sent >= MIN_FRAMES and (ratio >= VERY_BAD_RATIO or self.t1 >= 3):
            return "very-bad"
        if (self.sent >= MIN_FRAMES and (ratio >= BAD_RATIO or self.t1 >= 2)) or (snr is not None and snr < SNR_LOW_DB):
            return "bad"
        good_traffic = self.sent >= MIN_FRAMES and ratio <= GOOD_RATIO and self.t1 == 0
        if (good_traffic or self.sent < MIN_FRAMES) and (snr is None or snr >= SNR_HIGH_DB):
            return "good" if good_traffic or snr is not None else "quiet"
        return "mixed"


class PowerAdapter:
    """The decision alone, in percent. `window(evidence)` returns the new
    percent and why, or None for no change."""

    def __init__(self, cap: int, floor: int = 5, step: int = 10, start: int | None = None):
        self.cap = max(0, min(100, int(cap)))
        self.floor = max(0, min(self.cap, int(floor)))
        self.step = max(1, int(step))
        self.level = self.cap if start is None else max(self.floor, min(self.cap, int(start)))
        self.good_needed = GOOD_WINDOWS
        self.good_run = 0
        self.failed_at: int | None = None
        self.last_down = False

    def window(self, ev: Evidence) -> tuple[int, str] | None:
        v = ev.verdict()
        if v in ("quiet", "mixed"):
            if v == "mixed":
                self.good_run = 0
            return None
        if v in ("bad", "very-bad"):
            self.good_run = 0
            if self.last_down:
                # Down, then straight away worse: that level fails here, and
                # the next try at it waits twice as long.
                self.good_needed = min(GOOD_WINDOWS_MAX, self.good_needed * 2)
            self.failed_at = self.level
            self.last_down = False
            new = min(self.cap, self.level + self.step * (2 if v == "very-bad" else 1))
            if new == self.level:
                return None
            self.level = new
            return new, v
        self.good_run += 1
        self.last_down = False
        if self.good_run < self.good_needed:
            return None
        self.good_run = 0
        # The wait has been served (doubled if the last try failed), so the
        # level that failed may be tried again: conditions change.
        self.failed_at = None
        new = max(self.floor, self.level - self.step)
        if new >= self.level:
            return None
        self.level = new
        self.last_down = True
        return new, "good"


class PowerControl:
    """The adapter on the station's radio: reads, writes and restores RF
    POWER through shack_packet IcomControl, gathers the evidence, logs and
    keeps the history. `tick()` from rigfeatures' thread."""

    HISTORY = 200

    def __init__(self, ctl, clock=time.monotonic, wall=time.time, window_s: float = WINDOW_S):
        self.ctl = ctl
        self.clock = clock
        self.wall = wall
        self.window_s = window_s
        self.adapter: PowerAdapter | None = None
        self.icom = None
        self.found_level: int | None = None
        self.set_level: int | None = None
        self.band = ""
        self.state = "off"          # off | waiting | running | manual | unavailable | test-step
        self.history: list[dict] = []
        self._seen: dict[int, tuple[int, int, int]] = {}
        self._window_start: float | None = None
        self._snr: tuple[float, float] | None = None

    # -- what A's link test page (and anything else measuring) can call --------

    def report_snr(self, db: float) -> None:
        """The other station's SNR of our signal, from its telemetry."""
        self._snr = (float(db), self.clock())

    # -- life --------------------------------------------------------------------------

    def wanted(self) -> str | None:
        """Why this would not run now, or None."""
        s = self.ctl.settings
        if not s.power.enabled:
            return "off"
        if s.radio.kind not in ICOM_KINDS:
            return "unavailable"
        if s.radio.minimum_power_for_test:
            return "test-step"
        return None

    def tick(self, icom) -> None:
        why = self.wanted()
        if why is not None:
            if self.adapter is not None:
                self.stop()
            self.state = why
            return
        if icom is None or not icom.available():
            if self.adapter is not None:
                self._forget()
            self.state = "waiting"
            return
        if self.state == "manual":
            return
        if self.adapter is None or icom is not self.icom:
            self._begin(icom)
            return
        now = self.clock()
        if now - self._window_start < self.window_s:
            return
        self._window_start = now
        if self._taken_over():
            return
        ev = self.evidence()
        got = self.adapter.window(ev)
        if ev.verdict() == "good":
            self.ctl.settings.power.per_band[self.band] = self.adapter.level
        if got is not None:
            self._apply(got[0], got[1], ev)

    def _begin(self, icom) -> None:
        p = self.ctl.settings.power
        self.icom = icom
        self.band = band_of(self.ctl.settings.channel.frequency_hz)
        try:
            found = icom.rf_power()
        except Exception:                           # noqa: BLE001 - asked again next tick
            found = None
        if found is None:
            self.state = "waiting"
            self.icom = None
            return
        self.found_level = found
        remembered = p.per_band.get(self.band)
        start = None if remembered is None else int(remembered) + int(p.step_percent)
        self.adapter = PowerAdapter(p.cap_percent, p.floor_percent, p.step_percent, start)
        self._seen = {id(c): self._counts(c) for c in self._connections()}
        self._window_start = self.clock()
        self.state = "running"
        self._apply(self.adapter.level, "start", Evidence())

    def _taken_over(self) -> bool:
        """Read RF POWER back (once a window): a level other than the one
        last set means the operator changed it, and adaptation stops."""
        if self.set_level is None:
            return False
        try:
            now = self.icom.rf_power()
        except Exception:                           # noqa: BLE001 - asked again next window
            return False
        if now is None or abs(now - self.set_level) <= 1:
            return False
        self.state = "manual"
        self._log("manual", percent_of(now))
        return True

    def _apply(self, percent: int, why: str, ev: Evidence) -> None:
        icom, level = self.icom, level_of(percent)
        try:
            ok = icom.set_rf_power(level)
        except Exception as e:                      # noqa: BLE001 - the radio went; next tick says waiting
            log.warning("adaptive power: could not set RF power: %s", e)
            return
        if not ok:
            log.warning("adaptive power: the radio did not take RF power %d%%", percent)
            return
        self.set_level = level
        self._log(why, percent, ev=ev)

    def _log(self, why: str, percent: int, ev: Evidence | None = None, **extra) -> None:
        row = {"wall": self.wall(), "band": self.band, "percent": percent, "why": why,
               "sent": ev.sent if ev else None, "retried": ev.retried if ev else None,
               "t1": ev.t1 if ev else None, "snr": ev.snr_db if ev else None, **extra}
        self.history.append(row)
        del self.history[:-self.HISTORY]
        log.warning("adaptive power: %s %d%% on %s (%s; I frames %s, repeats %s, T1 %s, SNR %s)",
                    "set" if why != "manual" else "stopped at", percent, self.band, why, row["sent"],
                    row["retried"], row["t1"], row["snr"])
        emit = getattr(self.ctl, "_emit", None)
        if emit is not None:
            emit("power", why, {"percent": percent, "band": self.band})

    def stop(self) -> None:
        """Put the level found back, unless the operator took over."""
        icom, found = self.icom, self.found_level
        if (icom is not None and found is not None and self.state != "manual" and self.set_level is not None
                and self.set_level != found):
            try:
                if icom.available() and icom.set_rf_power(found):
                    self._log("restored", percent_of(found))
            except Exception:                       # noqa: BLE001 - closing regardless
                log.exception("adaptive power: putting RF power back")
        self._forget()
        self.state = "off"

    def _forget(self) -> None:
        self.adapter = self.icom = None
        self.found_level = self.set_level = None

    # -- evidence -----------------------------------------------------------------------

    def _connections(self) -> list:
        eng = getattr(self.ctl, "engine", None)
        return list(getattr(eng, "connections", {}).values()) if eng is not None else []

    @staticmethod
    def _counts(conn) -> tuple[int, int, int]:
        st = getattr(conn, "stats", {}) or {}
        return int(st.get("sent_I", 0)), int(st.get("retransmitted", 0)), int(st.get("t1_expired", 0))

    def evidence(self) -> Evidence:
        ev = Evidence()
        seen = {}
        for c in self._connections():
            now = self._counts(c)
            before = self._seen.get(id(c), (0, 0, 0))
            ev.sent += max(0, now[0] - before[0])
            ev.retried += max(0, now[1] - before[1])
            ev.t1 += max(0, now[2] - before[2])
            seen[id(c)] = now
        self._seen = seen
        if self._snr is not None and self.clock() - self._snr[1] <= self.window_s:
            ev.snr_db = self._snr[0]
        return ev
