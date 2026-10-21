"""Tracking one satellite for the station: where it is now, whether the
station may transmit to it, and Doppler on an IC-9700.

**Transmit only during a pass, above the horizon** (Brian's rule for
satellite work). `tx_refusal()` is the station's arming test while
tracking: no satellite elements, elements too old, or the satellite below
`min_el_deg` all refuse, and the controller disarms the station the moment
a pass in progress drops below it (rigfeatures.py). The margin defaults to
5 degrees rather than 0: a satellite on the horizon is behind the
neighbours' roofs anyway, and the time a margin costs is the part of the
pass nobody hears.

**Doppler** (`tick`), with the radio's VFOs set up and put back by
shack_packet IcomControl (shack_rig.icom.satdd). Receive is moved as soon
as the correction has drifted `step_hz` from what the radio has;
transmit the same, but never while transmitting: a frequency step in the
middle of an over is a frame lost. The step is about 2 ppm of the
frequency, 300 Hz at 2 m: an FM packet receiver's filter does not notice
that much, and a 2 m pass's whole Doppler (about +-3.5 kHz) takes some
two dozen steps rather than one a second.
"""
from __future__ import annotations

import logging
import time

from .tle import Bird, TOO_OLD_DAYS
from .track import Look, Satellite, TrackError

log = logging.getLogger(__name__)

DEFAULT_MIN_EL_DEG = 5.0


def auto_step_hz(hz: float) -> int:
    """About 2 ppm, in whole 100 Hz, never under 100."""
    return max(100, int(round(hz * 2e-6 / 100.0)) * 100)


class PassSession:
    def __init__(self, sat: Satellite | None, bird: Bird, lat: float, lon: float, *, alt_m: float = 0.0,
                 control=None, doppler: bool = True, min_el_deg: float = DEFAULT_MIN_EL_DEG, step_hz: int = 0,
                 transmitting=lambda: False, clock=time.time, age_days=lambda: None):
        self.sat = sat
        self.bird = bird
        self.lat, self.lon, self.alt_m = lat, lon, alt_m
        self.control = control
        self.doppler = bool(doppler and control is not None)
        self.min_el_deg = max(0.0, float(min_el_deg))
        self.rx_step = step_hz or auto_step_hz(bird.downlink_hz)
        self.tx_step = step_hz or auto_step_hz(bird.uplink_hz)
        self.transmitting = transmitting
        self.clock = clock
        self.age_days = age_days
        self.look: Look | None = None
        self.rx_hz: int | None = None
        self.tx_hz: int | None = None
        self.steps = {"rx": 0, "tx": 0, "tx_held": 0}
        self.active = False
        self.error = ""

    # -- the transmit gate ---------------------------------------------------------

    def tx_refusal(self) -> str | None:
        if self.sat is None:
            return "sat-no-elements"
        age = self.age_days()
        if age is not None and age > TOO_OLD_DAYS:
            return "sat-old-elements"
        look = self._look(self.clock())
        if look is None:
            return "sat-no-elements"
        if look.el_deg < self.min_el_deg:
            return "sat-below-horizon"
        return None

    def _look(self, now: float) -> Look | None:
        if self.sat is None:
            return None
        try:
            self.look = self.sat.look(now, self.lat, self.lon, self.alt_m)
        except TrackError as e:
            self.error = str(e)
            self.look = None
        return self.look

    def corrected(self, look: Look) -> tuple[int, int]:
        rx, _ = look.doppler(self.bird.downlink_hz)
        _, tx = look.doppler(self.bird.uplink_hz)
        return int(round(rx / 10.0)) * 10, int(round(tx / 10.0)) * 10

    # -- Doppler ---------------------------------------------------------------------

    def start(self) -> list:
        """Set the radio up for the pass (if Doppler is on). Returns the
        set-up's report; raises what IcomControl raises."""
        report = []
        look = self._look(self.clock())
        if self.doppler and look is not None:
            rx, tx = self.corrected(look)
            report = self.control.begin_pass(self.bird.how, rx_hz=rx, tx_hz=tx)
            self.rx_hz, self.tx_hz = rx, tx
            log.info("satellite %s: Doppler on (%s), receive %d Hz, transmit %d Hz", self.bird.name,
                     self.bird.how, rx, tx)
        self.active = True
        return report

    def tick(self) -> Look | None:
        look = self._look(self.clock())
        if not (self.active and self.doppler) or look is None:
            return look
        rx, tx = self.corrected(look)
        new_rx = rx if self.rx_hz is None or abs(rx - self.rx_hz) >= self.rx_step else None
        new_tx = tx if self.tx_hz is None or abs(tx - self.tx_hz) >= self.tx_step else None
        if new_tx is not None and self.transmitting():
            self.steps["tx_held"] += 1
            new_tx = None
        if new_rx is None and new_tx is None:
            return look
        try:
            ok = self.control.retune(rx_hz=new_rx, tx_hz=new_tx)
        except Exception as e:                      # noqa: BLE001 - the radio went; the pass stays tracked
            self.error = str(e)
            return look
        if ok:
            if new_rx is not None:
                self.rx_hz = new_rx
                self.steps["rx"] += 1
            if new_tx is not None:
                self.tx_hz = new_tx
                self.steps["tx"] += 1
        return look

    def stop(self) -> list:
        self.active = False
        if not self.doppler:
            return []
        try:
            lines = self.control.end_pass()
        except Exception:                           # noqa: BLE001 - journalled; put back at the next pass
            log.exception("putting the radio back after the pass")
            return []
        self.rx_hz = self.tx_hz = None
        return lines
