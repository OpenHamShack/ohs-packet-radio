"""The path test, as the station runs it: which fast modes a path carries,
one direction at a time (shack_packet.pathtest has the design; this is the
app's half - who may send, what is listened to, and the counts the Path
test page draws).

Asked for 18/10/2026 for Brian (GM4BRI) and Ray (GM0OTB), 23.5 km apart,
each with a Pluto, one antenna and no T/R relay: the sender's antenna goes
on its Pluto's TX port and the receiver's on its RX port, by hand, and the
two swap roles for the other direction. The page says so; nothing here can
check where a cable is.

**The link is the station's own adaptive link, never one made for the
test.** A test needs an adaptive Pluto link (several modes on one link:
`modes`, `listen_modes`). Two ways were weighed: reopen the radio with an
override ladder for the test and put it back after, or require the
operator's own adaptation settings to be on and test the ladder they give.
The second is taken. Reopening a Pluto restarts its guard and its
quiet-transmitter checks for the sake of a test, and a "put it back" that
fails half way (the radio gone, the window closed mid-test) leaves the
station on settings nobody chose. And the ladder in use is the one that
matters: `adapt_start`, the one thing a test feeds back, must be on the
receiving station's ladder anyway. So the page says what to set when
adaptation is off or the radio is not a Pluto, and changes nothing itself.

**Sending is a transmission like any other.** The frames go through
Engine.send_frame(..., mode=) on the running engine, so the arming, its
expiry, the Pluto's guard and drive cap and every refusal the channel and
the link make apply exactly as they do to a chat line; nothing here keys.
On top: a mode wider than the band plan allows at this frequency
(fastsettings.wide_data_refusal) or not on the link's ladder is refused
before anything is queued. One frame is queued at a time, the next
`gap_s` after the last left the air, so Stop has at most one frame to
withdraw, and a disarm (Disarm, the pad's HALT, an expiry, the radio
going) ends the run at the engine's next poll - the channel has already
dropped what was queued by then.

**Receiving** listens on every mode of the ladder at once
(Engine.listen_also) and counts every path-test frame heard into a
pathtest.Tally, with the C/N0 the engine recorded for it (Engine.last_heard,
set just before the monitors are called). The frequency offset per frame
is not recorded by the engine or the Pluto's receivers in this ohs-libs, so
that column stays empty until it is. Bad FCS per mode is read from the
receivers' framers, counted from when receiving started.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import threading

from shack_packet import adapt, ax25, modem, pathtest
from shack_packet.fastsettings import wide_data_refusal

log = logging.getLogger(__name__)

#: Where path-test frames are addressed: an experimental tocall, as the
#: library's own path test uses, so APRS software leaves them alone.
DESTINATION = "APZOHS"
#: How long a queued frame may wait to leave before the run gives up on it
#: (the channel busy for that long, or a link that failed without saying).
STUCK_S = 30.0
COUNT_RANGE = (1, 500)
GAP_RANGE_S = (0.0, 30.0)

#: Every fast mode, fastest first: the modes a Pluto link can be asked for.
FAST_MODES = tuple(sorted(modem.CODES, key=lambda m: -adapt.threshold_dbhz(m)))


def snr_db(mode: str, cn0_dbhz: float | None) -> float | None:
    """C/N0 as a signal-to-noise ratio in the mode's occupied bandwidth:
    the figure the receiver's thresholds and sim.frame_success are written
    in, and the one an operator used to S-meters and SNR can compare."""
    if cn0_dbhz is None:
        return None
    return round(cn0_dbhz - 10.0 * math.log10(modem.mode(mode).occupied_hz), 1)


class _Sender:
    """The sending role, as one of the engine's services (polled with its
    lock held, on the engine's clock: the simulator runs it as it runs
    every other service)."""

    def __init__(self, run: PathTest, engine, frames: list, gap_s: float):
        self.run = run
        self.engine = engine
        self.frames = frames                        # [(mode, seq, info)]
        self.gap_s = gap_s
        self.index = 0
        self.sent = 0
        self.pending = None                         # the Outgoing in the channel's queue
        self.queued_at = 0.0
        self.next_at = engine.clock()
        #: Ended: polled no more. Left in engine.services until the next
        #: start or stop takes it out, because it ends inside the engine's
        #: loop over that list, where removing it would skip the next one.
        self.done = False

    def next_deadline(self) -> float | None:
        if self.done:
            return None
        if self.pending is not None:
            return self.queued_at + STUCK_S
        return self.next_at

    def poll(self, now: float) -> None:
        if self.done:
            return
        why = self.engine.station.refusal()
        if why is not None:
            # Disarmed, expired, halted: the channel drops what is queued at
            # its next decision; the run ends here, said as the station's
            # reason.
            self.run._sender_ended(self, why)
            return
        if self.pending is not None:
            if now - self.queued_at > STUCK_S:
                self.run._sender_ended(self, "path-stuck")
            return
        if self.index >= len(self.frames):
            self.run._sender_ended(self, "finished")
            return
        if now + 1e-9 < self.next_at:
            return
        mode, _seq, info = self.frames[self.index]
        station = self.engine.station
        frame = ax25.Frame.ui(station.address(), ax25.Address.from_text(DESTINATION, flag=False), info)
        channel = self.engine.channel
        before = len(channel._normal)
        self.engine.send_frame(frame, on_sent=self._sent, mode=mode)
        # The Outgoing just appended, kept so Stop can take it back out.
        self.pending = channel._normal[-1] if len(channel._normal) > before else None
        self.queued_at = now
        self.index += 1

    def _sent(self, end: float) -> None:
        self.pending = None
        self.sent += 1
        self.next_at = end + self.gap_s

    def withdraw(self) -> None:
        """Take a frame not yet sent back out of the channel's queue
        (engine lock held). ChannelAccess has no call for it, so its queue
        is reached directly: a frame left there would key the radio after
        Stop was pressed."""
        if self.pending is not None:
            try:
                self.engine.channel._normal.remove(self.pending)
            except ValueError:
                pass                                # already taken for a transmission
            self.pending = None


class PathTest:
    """One controller's path test: idle, sending or receiving.

    `role` is "" | "send" | "receive"; `ended` the reason key the last run
    ended with ("finished", "stopped", or the station's refusal key). The
    tally of the last receive stays after it stops, for its report."""

    def __init__(self, ctl):
        self.ctl = ctl
        self._lock = threading.Lock()
        self.role = ""
        self.ended = ""
        self.sender: _Sender | None = None
        self.total = 0
        self.tally: pathtest.Tally | None = None
        self.modes: tuple[str, ...] = ()
        self._engine = None
        self._fcs_base: dict[str, int] = {}

    # -- what may be done ---------------------------------------------------------

    def readiness(self) -> str:
        """"" when a path test can run on this station now, else why not:
        "not-started", "not-pluto", "not-adaptive", "no-radio" (the link
        is not open), "link-not-adaptive" (adaptation is on in the settings
        but the radio was opened without it: they have not been applied)."""
        ctl = self.ctl
        if ctl.engine is None:
            return "not-started"
        if ctl.settings.radio.kind != "pluto":
            return "not-pluto"
        if not ctl.settings.fast.adapt:
            return "not-adaptive"
        if ctl.link is None or ctl.link_state != "open":
            return "no-radio"
        if not self.ladder():
            return "link-not-adaptive"
        return ""

    def ladder(self) -> tuple[str, ...]:
        """The running link's modes, fastest first (empty if it has none)."""
        modes = getattr(self.ctl.link, "modes", None) or ()
        return tuple(modem.mode(m).name for m in modes)

    def choices(self) -> list[tuple[str, str, str]]:
        """(mode, refusal key, detail) for every fast mode at this
        frequency: "" may be sent, "not-on-ladder", or "out-of-plan" with
        the band plan's reason."""
        f = float(self.ctl.settings.channel.frequency_hz)
        ladder = self.ladder()
        out = []
        for m in FAST_MODES:
            why = wide_data_refusal(f, m)
            if why:
                out.append((m, "out-of-plan", why))
            elif m not in ladder:
                out.append((m, "not-on-ladder", ""))
            else:
                out.append((m, "", ""))
        return out

    def send_refusal(self, modes) -> tuple[str, str]:
        """("", "") if these modes may be sent now, else (key, detail)."""
        why = self.readiness()
        if why:
            return why, ""
        if self.role:
            return "path-busy", ""
        if not modes:
            return "no-modes", ""
        allowed = {m: (k, d) for m, k, d in self.choices()}
        for m in modes:
            key, detail = allowed.get(modem.mode(m).name, ("not-on-ladder", ""))
            if key:
                return key, f"{m}: {detail}" if detail else m
        st = self.ctl.station
        if st is None or not st.armed:
            return "not-armed", ""
        return "", ""

    # -- sending ------------------------------------------------------------------

    def start_sending(self, modes, count: int = pathtest.COUNT, gap_s: float = pathtest.GAP_S) -> tuple[str, str]:
        """Send `count` numbered frames at each mode, fastest first;
        ("", "") once started, else the refusal (send_refusal)."""
        why, detail = self.send_refusal(modes)
        if why:
            return why, detail
        count = max(COUNT_RANGE[0], min(COUNT_RANGE[1], int(count)))
        gap_s = max(GAP_RANGE_S[0], min(GAP_RANGE_S[1], float(gap_s)))
        order = [m for m in FAST_MODES if m in {modem.mode(x).name for x in modes}]
        engine = self.ctl.engine
        frames = list(pathtest.plan(order, count=count))
        with engine.lock:
            engine.services[:] = [x for x in engine.services if not (isinstance(x, _Sender) and x.done)]
            self.sender = _Sender(self, engine, frames, gap_s)
            engine.services.append(self.sender)
        with self._lock:
            self.role, self.ended, self.total, self.modes = "send", "", len(frames), tuple(order)
            self._engine = engine
        engine._wake()
        log.warning("path test: sending %d frames at %s, %.1f s apart, as %s", len(frames), ", ".join(order),
                    gap_s, engine.station.callsign)
        return "", ""

    def _sender_ended(self, sender: _Sender, why: str) -> None:
        """From the sender's poll (engine lock held)."""
        sender.done = True
        with self._lock:
            if self.sender is sender:
                self.role, self.ended = "", why
        log.warning("path test: sending ended (%s), %d of %d frames sent", why, sender.sent, len(sender.frames))

    def progress(self) -> dict:
        s = self.sender
        if s is None:
            return {"sent": 0, "total": 0, "mode": ""}
        mode = s.frames[min(s.index, len(s.frames)) - 1][0] if s.index else ""
        return {"sent": s.sent, "total": len(s.frames), "mode": mode}

    # -- receiving ----------------------------------------------------------------

    def receive_refusal(self) -> str:
        why = self.readiness()
        if why:
            return why
        if self.role:
            return "path-busy"
        return ""

    def start_receiving(self, modes=None, count: int = pathtest.COUNT) -> str:
        """Listen on the whole ladder and count; `modes` and `count` are what
        the sender said it would send (a mode heard not at all still gets
        its row). "" once started, else why not."""
        why = self.receive_refusal()
        if why:
            return why
        engine = self.ctl.engine
        ladder = self.ladder()
        expect = [m for m in (modes or ladder) if modem.mode(m).name in ladder]
        tally = pathtest.Tally()
        tally.expect(expect, max(COUNT_RANGE[0], min(COUNT_RANGE[1], int(count))))
        with engine.lock:
            self._fcs_base = self._bad_fcs_now(engine)
            engine.monitors.append(self._heard)
            engine.listen_also(ladder)
        with self._lock:
            self.role, self.ended, self.tally, self.modes = "receive", "", tally, tuple(expect)
            self._engine = engine
        log.info("path test: receiving on %s", ", ".join(ladder))
        return ""

    def _heard(self, frame) -> None:
        """An engine monitor: every frame heard, the engine's lock held."""
        engine = self._engine
        mode, cn0 = engine.last_heard if engine is not None else (None, None)
        with self._lock:
            if self.tally is not None and self.role == "receive":
                self.tally.heard(frame, cn0=cn0)

    @staticmethod
    def _bad_fcs_now(engine) -> dict[str, int]:
        """Bad FCS per mode so far, from each mode's receiver: the largest
        of its slicers' counts, as every slicer of a receiver sees the same
        signal and summing them would count one bad frame several times."""
        out = {}
        for mode, rx in list(getattr(engine, "receivers", {}).items()):
            counts = [s.framer.bad_fcs for s in getattr(rx, "slicers", []) if hasattr(s, "framer")]
            if counts:
                out[mode] = max(counts)
        return out

    def rows(self) -> list[dict]:
        """The live table: Tally.rows plus each mode's SNR equivalent, and
        bad FCS since receiving started."""
        engine = self._engine
        with self._lock:
            tally = self.tally
            if tally is None:
                return []
            if engine is not None and self.role == "receive":
                now = self._bad_fcs_now(engine)
                tally.set_bad_fcs({m: n - self._fcs_base.get(m, 0) for m, n in now.items()})
            rows = tally.rows()
            if not rows and tally.expected:
                # Nothing heard yet: the modes expected, all missing, so the
                # table shows what is being waited for.
                rows = [{"run": "", "mode": m, "bitrate": modem.mode(m).bitrate, "decoded": 0, "missing": c,
                         "count": c, "pct": 0.0, "cn0_dbhz": None, "offset_hz": None,
                         "bad_fcs": tally.bad_fcs.get(m), "from": None}
                        for m, c in sorted(tally.expected.items(), key=lambda x: -adapt.threshold_dbhz(x[0]))]
        for r in rows:
            r["snr_db"] = snr_db(r["mode"], r["cn0_dbhz"])
        return rows

    def best_mode(self) -> str | None:
        with self._lock:
            return self.tally.best_mode() if self.tally is not None else None

    def report(self) -> str:
        """The latest run's text report ("" if nothing was ever received)."""
        with self._lock:
            if self.tally is None:
                return ""
            station = str(self.ctl.station.callsign) if self.ctl.station and self.ctl.station.callsign else ""
            return self.tally.report(frequency_hz=float(self.ctl.settings.channel.frequency_hz), station=station)

    def use_for_adaptation(self) -> tuple[str, str]:
        """Store the best mode as settings.fast.adapt_start, and on the
        running engine for connections from now: this station's first
        request for the direction it received starts there. Refused
        ("no-best", or "not-on-ladder") for a mode the settings' ladder
        does not have. Returns (key, mode)."""
        best = self.best_mode()
        if best is None:
            return "no-best", ""
        fast = self.ctl.settings.fast
        try:
            ladder = fast.for_frequency(float(self.ctl.settings.channel.frequency_hz)).ladder() or ()
        except Exception:                           # noqa: BLE001 - a ladder the settings cannot make
            ladder = ()
        if best not in ladder:
            return "not-on-ladder", best
        fast.adapt_start = best
        engine = self.ctl.engine
        if engine is not None and engine.link_config.adapt and best in engine.link_config.adapt:
            with engine.lock:
                engine.link_config = dataclasses.replace(engine.link_config, adapt_start=best)
        log.info("path test: adaptation starts at %s for receiving", best)
        return "", best

    # -- stopping -----------------------------------------------------------------

    def stop(self, why: str = "stopped") -> None:
        """End either role now. A frame still queued is taken back, and a
        transmission in progress is cut (as Disarm cuts one): Stop means
        the carrier goes."""
        with self._lock:
            role, sender, engine = self.role, self.sender, self._engine
            self.role = ""
            if role:
                self.ended = why
        if engine is None or not role:
            return
        with engine.lock:
            if role == "send" and sender is not None:
                sender.withdraw()
                sender.done = True
                engine.services[:] = [x for x in engine.services if x is not sender]
            if role == "receive":
                if self._heard in engine.monitors:
                    engine.monitors.remove(self._heard)
                engine.listen_also(())
        if role == "send":
            link = self.ctl.link
            abort = getattr(link, "abort", None)
            if abort is not None and getattr(link, "keyed", False):
                abort()
            log.warning("path test: sending stopped (%s)", why)
        else:
            # The counts so far, final: bad FCS as it stood at the stop.
            with self._lock:
                if self.tally is not None:
                    now = self._bad_fcs_now(engine)
                    self.tally.set_bad_fcs({m: n - self._fcs_base.get(m, 0) for m, n in now.items()})
            log.info("path test: receiving stopped (%s)", why)
