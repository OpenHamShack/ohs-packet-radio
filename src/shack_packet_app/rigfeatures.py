"""The station's radio beyond packet (Packet Radio 0.3.0, workstream F):
the IC-9700's scope, adaptive power, satellite tracking with its transmit
gate and Doppler, and 23 cm DD - held by the Controller as `ctl.rigx`.

**One thread, once a second**, of its own rather than the window's timer:
the satellite gate disarms the station when a pass ends whether or not
anybody is looking at the Satellites tab, and adaptive power keeps
judging with the window minimised. Each tick asks the radio only through
shack_packet IcomControl, on the link's own loop, and none of it keys.

**The order at stop matters**: a pass's VFOs and adaptive power's RF
POWER are put back here, before the Controller closes the link, whose own
restore then puts the frequency and the packet set-up back.

**The arming gate** (`arm_refusal`): while a satellite is being tracked and
the channel is its uplink, arming needs the satellite above the minimum
elevation with fresh elements, and the station is disarmed the moment that
stops being true (`sat-los`). The band plan keeps terrestrial stations out
of 145.806-146.000 MHz ("plan_reserved"), which is exactly where the ISS
listens; for a tracked satellite's uplink that one refusal is excused, and
the pass gate stands in for it. Every other band-plan refusal (the licence,
the power) stands.
"""
from __future__ import annotations

import logging
import threading
import time

from .adaptpower import PowerControl

log = logging.getLogger(__name__)

#: How close the channel must be to a satellite's uplink to count as
#: working it.
ON_CHANNEL_HZ = 10_000
DD_KIND = "ic-9700-dd"


class SatTracker:
    """The followed satellite: its elements, its passes, and the session
    that gates transmit and steers Doppler during a pass."""

    def __init__(self, ctl, store=None, clock=time.time):
        self.ctl = ctl
        self.clock = clock
        self._store = store
        self.session = None
        self.error = ""

    @property
    def store(self):
        if self._store is None:
            from . import __version__, host, update
            from .sat.tle import MAX_BYTES, TleStore
            ua = update.user_agent("packet", __version__)
            self._store = TleStore(host.data_dir() / "satellites.tle",
                                   fetch=lambda url: update.fetch(url, ua, MAX_BYTES), clock=self.clock)
        return self._store

    def birds(self) -> dict:
        from .sat.tle import birds
        return birds(self.ctl.settings.sat.extra_birds)

    def bird(self):
        return self.birds().get(int(self.ctl.settings.sat.tracking or 0))

    def satellite(self):
        b = self.bird()
        return self.store.find(b.norad) if b is not None else None

    def on_uplink(self, hz: float | None = None) -> bool:
        b = self.bird()
        hz = self.ctl.settings.channel.frequency_hz if hz is None else hz
        return b is not None and abs(float(hz) - b.uplink_hz) <= ON_CHANNEL_HZ

    def position(self):
        return self.ctl.position()

    def passes(self, hours: float = 24.0):
        sat, pos = self.satellite(), self.position()
        if sat is None or pos is None:
            return []
        s = self.ctl.settings.sat
        return sat.passes(self.clock(), hours, pos[0], pos[1], s.altitude_m)

    def _new_session(self, icom):
        from .sat.session import PassSession
        s, pos = self.ctl.settings.sat, self.position()
        sat, bird = self.satellite(), self.bird()
        engine = self.ctl.engine
        doppler = bool(s.doppler and icom is not None and self.on_uplink())
        return PassSession(sat, bird, pos[0], pos[1], alt_m=s.altitude_m, control=icom if doppler else None,
                           doppler=doppler, min_el_deg=s.min_elevation_deg, step_hz=s.step_hz,
                           transmitting=lambda: bool(engine is not None and engine.channel.transmitting),
                           clock=self.clock, age_days=lambda: self.store.age_days(bird.norad))

    def tx_refusal(self) -> str | None:
        if self.bird() is None or not self.on_uplink():
            return None
        if self.position() is None:
            return "sat-no-locator"
        sess = self.session or self._new_session(None)
        return sess.tx_refusal()

    def tick(self, icom) -> None:
        bird = self.bird()
        if bird is None or self.position() is None:
            self.stop()
            return
        if self.session is None or self.session.bird != bird:
            self.stop()
            self.session = self._new_session(icom)
        sess = self.session
        if sess.doppler and sess.control is not icom:
            # The link was rebuilt under us: start again on the new one.
            self.stop()
            self.session = sess = self._new_session(icom)
        look = sess.tick()
        if look is None:
            return
        rising = look.el_deg >= 0.0
        if rising and not sess.active:
            try:
                sess.start()
                self.error = ""
            except Exception as e:                  # noqa: BLE001 - IcomControlError and the radio's own
                self.error = getattr(e, "reason", "") or str(e)
                log.warning("satellite pass: the radio could not be set up for Doppler: %s", e)
                sess.doppler = False
                sess.active = True
        elif not rising and sess.active:
            sess.stop()
        st = self.ctl.station
        if st is not None and st.armed and self.on_uplink() and sess.tx_refusal() is not None:
            log.warning("satellite %s below %.0f degrees: transmit disarmed", bird.name, sess.min_el_deg)
            self.ctl.disarm()
            self.ctl._emit("tx", "sat-los", {"satellite": bird.name})

    def stop(self) -> None:
        sess, self.session = self.session, None
        if sess is not None and sess.active:
            sess.stop()

    # -- the ISS set-up -------------------------------------------------------------

    def station_for(self, settings, bird):
        """A copy of `settings` working `bird`: the channel on its uplink
        (custom), the APRS path its digipeater answers, and what they were,
        saved to put back. Tracking that bird."""
        new = settings.copy()
        if not new.sat.saved:
            new.sat.saved = {"preset": new.channel.preset, "frequency_hz": new.channel.frequency_hz,
                             "path": new.aprs.path}
        new.channel.preset = "custom"
        new.channel.frequency_hz = int(bird.uplink_hz)
        if bird.path:
            new.aprs.path = bird.path
        new.sat.tracking = bird.norad
        return new

    @staticmethod
    def station_back(settings):
        new = settings.copy()
        saved = new.sat.saved or {}
        if saved:
            new.channel.preset = saved.get("preset", new.channel.preset)
            new.channel.frequency_hz = int(saved.get("frequency_hz", new.channel.frequency_hz))
            new.aprs.path = saved.get("path", new.aprs.path)
        new.sat.saved = {}
        return new


class RigFeatures:
    TICK_S = 1.0

    def __init__(self, ctl, *, tle_store=None, clock=time.monotonic, wall=time.time):
        self.ctl = ctl
        self.power = PowerControl(ctl, clock=clock, wall=wall)
        self.sat = SatTracker(ctl, store=tle_store, clock=wall)
        self._icom = None
        self._icom_link = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- the radio ----------------------------------------------------------------------

    def icom(self):
        """IcomControl on the station's open IcomLink, or None."""
        link = self.ctl.link
        if self.ctl.link_state != "open" or link is None:
            return None
        from shack_packet.radios.icom import IcomLink
        if not isinstance(link, IcomLink):
            return None
        if link is not self._icom_link:
            from shack_packet.radios.icomctl import IcomControl
            self._icom, self._icom_link = IcomControl(link), link
        return self._icom if self._icom.available() else None

    # -- life ------------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rig features", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.TICK_S):
            self.tick()

    def tick(self) -> None:
        with self._lock:
            icom = None
            try:
                icom = self.icom()
                self.power.tick(icom)
            except Exception:                       # noqa: BLE001 - one feature's failure is not the others'
                log.exception("adaptive power")
            try:
                self.sat.tick(icom)
            except Exception:                       # noqa: BLE001
                log.exception("satellite tracking")

    def stop(self) -> None:
        """Before the link closes: the pass and the power put back."""
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(5)
        with self._lock:
            for name, fn in (("satellite pass", self.sat.stop), ("adaptive power", self.power.stop)):
                try:
                    fn()
                except Exception:                   # noqa: BLE001 - closing regardless
                    log.exception("stopping %s", name)
        self._icom = self._icom_link = None

    # -- click to tune ----------------------------------------------------------------------

    def retune(self, hz: int) -> bool:
        """The scope's click: disarmed first (a new channel is checked again
        at the next Arm), the radio tuned live, and the station's channel
        moved with it so the band-plan check, the status bar and the next
        start all agree. Tuned through the link's data set-up where it has
        one (RigLink.tune_channel), so it counts as the app's change: the
        link's restore still puts the radio back on the frequency it was
        found on when the station stops - unlike a channel the operator
        turned the radio to (tuning.py)."""
        icom = self.icom()
        if icom is None:
            return False
        self.ctl.disarm()
        link = self.ctl.link
        tune = getattr(link, "tune_channel", None)
        if not (tune(int(hz)) if tune is not None else icom.tune(int(hz))):
            return False
        from . import presets
        ch = self.ctl.settings.channel
        old = int(ch.frequency_hz)
        match = next((p.id for p in presets.presets(self.ctl.settings.station.profile()) if p.hz == int(hz)), None)
        ch.preset, ch.frequency_hz = match or "custom", int(hz)
        if tune is None and link is not None and hasattr(link, "frequency"):
            link.frequency = int(hz)
        self.ctl._emit("radio", "retuned", {"hz": int(hz), "from": old, "by": "app", "preset": match or ""})
        return True

    # -- the controller's arming ----------------------------------------------------------

    def arm_refusal(self) -> tuple[str, str]:
        why = self.sat.tx_refusal()
        if why:
            b = self.sat.bird()
            return why, b.name if b is not None else ""
        return "", ""

    def band_plan_excused(self, verdict: dict, hz: float) -> bool:
        """Whether this band-plan refusal is the satellite segment's, on a
        tracked satellite's uplink (module docstring)."""
        refusal = (verdict.get("verdict") or {}).get("refusal") or {}
        return refusal.get("code") == "plan_reserved" and self.sat.on_uplink(hz) and self.sat.tx_refusal() is None
