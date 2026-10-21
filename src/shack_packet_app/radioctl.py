"""The radio's controls always to hand (0.3.0): the model behind the main
window's control strip (ui/controlstrip.py) and the pad's dials.

Brian, 20/10/2026: "easier to change RX and TX gain/power settings etc",
with no dialogs - changed where the operator is looking, applied at once,
mid-test included, and logged.

- **A CAT radio** (IcomLink): RF power, RF gain and squelch as the radio
  has them, read from it and written straight back. Each goes through the
  library's data set-up (shack_packet RigLink.set_control, DataSetup.adjust):
  saved to the journal before the first change and put back when the link
  closes, crash-safe, like everything else the app changes on a radio. The
  meters: signal while receiving; Po, SWR and ALC on transmit
  (shack_rig.icom.controls), with a warning when a transmission's SWR is
  markedly worse than the last one's.
- **A TM-V71** (KenwoodLink): RF power as H, M or L - a choice, not a
  level (`link.control_choices`), through the same journal - and its
  squelch, shown but read only (`link.control_writable`): the knob is the
  operator's and the app reads busy from it. No S-meter command exists, so
  the meter is "channel busy" or "clear".
- **A Pluto**: TX gain in the setup check's 2 dB steps, capped by the
  band's drive cap as everywhere else (PlutoLink.set_tx_gain refuses
  anything above it), and RX gain AGC or manual. These are the app's own
  settings, not the radio's, so a change is also kept in Settings.

**Never on the window's thread.** Every read and write waits on the radio
(an Icom's answer, a Pluto's iio write), so they run on one worker thread,
in order; the strip reads `values`/`meters`, which the worker fills.

**Logged.** Every change is a log line and, while a link test runs, a
note in its report (linkctl.LinkTest.note), so a result can be read
against what the radio was doing.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger(__name__)

CAT_CONTROLS = ("rf_power", "rf_gain", "squelch", "power_level")
#: Meters while the strip is shown, and the controls again in case the
#: operator turned a knob on the radio itself.
METER_EVERY_S = 0.5
CONTROLS_EVERY_S = 5.0
#: A pad dial's detent, in percent of the control's range.
DIAL_STEP_PCT = 2
NAMES = {"rf_power": "RF power", "rf_gain": "RF gain", "squelch": "squelch", "tx_gain": "TX gain",
         "rx_gain": "RX gain", "power_level": "RF power"}


def _pct(raw, top: int = 255) -> int | None:
    """A raw level as percent of its scale: an Icom's 0-255 (its own
    rounding, shack_rig.icom.controls), or another radio's (a TM-V71's
    squelch, 00-1F)."""
    if raw is None:
        return None
    if top == 255:
        from shack_rig.icom.controls import percent
        return percent(raw)
    return round(100 * int(raw) / top)


class RadioControls:
    def __init__(self, ctl, *, save=None):
        self.ctl = ctl
        #: Called with nothing after a Pluto gain change has been put into
        #: ctl.settings, to keep it (the window's save_settings).
        self.save = save
        #: The controls as last read: {"rf_power": 25 (percent), ...}, or
        #: {"tx_gain": -30.0, "rx_gain": None} for a Pluto.
        self.values: dict = {}
        #: Per control the link has: [(raw, name)] for one chosen by name
        #: (a TM-V71's power), and whether the app may change it.
        self.choices: dict = {}
        self.writable: dict = {}
        self.meters: dict = {}
        self.warning = ""
        self.changes: list[dict] = []
        self._link = None
        self._jobs: queue.Queue = queue.Queue()
        self._busy = False
        self._last_meter = 0.0
        self._last_controls = 0.0
        self._was_tx = False
        self._swr = None
        self._thread = threading.Thread(target=self._run, name="radioctl", daemon=True)
        self._thread.start()

    # -- what the station has ----------------------------------------------------------

    @property
    def link(self):
        return self.ctl.link

    def kind(self) -> str:
        """"pluto", "cat" (a link with live controls), or "" (none)."""
        link = self.link
        if link is None:
            return ""
        if hasattr(link, "set_tx_gain") and self.ctl.settings.radio.kind == "pluto":
            return "pluto"
        if hasattr(link, "control_keys"):
            return "cat"
        return ""

    def tx_cap(self) -> float | None:
        s = self.ctl.settings
        try:
            return s.fast.drive_cap(s.channel.frequency_hz)
        except Exception:                           # noqa: BLE001 - no cap known: none applied here
            return None

    # -- the strip's tick ----------------------------------------------------------------

    def tick(self, visible: bool = True) -> None:
        """Queue the reads that are due (never blocks)."""
        link = self.link
        if link is not self._link:
            self._link = link
            self.values, self.meters, self._swr = {}, {}, None
            self._last_controls = 0.0
        if not visible or link is None or self._busy:
            return
        now = time.monotonic()
        if now - self._last_controls >= CONTROLS_EVERY_S:
            self._last_controls = now
            self._jobs.put(("read", None, None))
        if self.kind() == "cat" and now - self._last_meter >= METER_EVERY_S:
            self._last_meter = now
            self._jobs.put(("meters", None, None))

    # -- changing ------------------------------------------------------------------------

    def set(self, key: str, value) -> None:
        """Set a control: percent for a CAT radio's, dB (or None for AGC)
        for a Pluto's gains. Applied on the worker, at once."""
        self._jobs.put(("set", key, value))

    def nudge(self, key: str, steps: int) -> None:
        """A pad dial: `steps` detents up or down from the value shown."""
        now = self.values.get(key)
        if now is None or not self.writable.get(key, True):
            return
        if key in self.choices:
            # Choices are listed most power first (H, M, L): up is towards
            # the front, one choice per detent.
            raws = [raw for raw, _name in self.choices[key]]
            if now in raws:
                i = max(0, min(len(raws) - 1, raws.index(now) - steps))
                if raws[i] != now:
                    self.set(key, raws[i])
            return
        if key == "tx_gain":
            from .ui.sdrpages import TX_GAIN_STEPS
            cap = self.tx_cap()
            allowed = [g for g in TX_GAIN_STEPS if cap is None or g <= cap + 1e-9]
            nearest = min(allowed, key=lambda g: abs(g - now))
            i = max(0, min(len(allowed) - 1, allowed.index(nearest) + (1 if steps > 0 else -1) * abs(steps)))
            self.set(key, allowed[i])
        else:
            self.set(key, max(0, min(100, now + steps * DIAL_STEP_PCT)))

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """For the tests: until the worker has done what was asked."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._jobs.empty() and not self._busy:
                return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        self._jobs.put(None)

    # -- the worker --------------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self._busy = True
            try:
                what, key, value = job
                link = self.link
                if link is None:
                    continue
                if what == "read":
                    self._read(link)
                elif what == "meters":
                    self._meters(link)
                elif what == "set":
                    self._set(link, key, value)
            except Exception:                       # noqa: BLE001 - a control that fails is said, never fatal
                log.exception("radio control %s", job[0])
            finally:
                self._busy = False

    def _read(self, link) -> None:
        kind = self.kind()
        if kind == "pluto":
            self.values = {"tx_gain": float(link.tx_gain_db), "rx_gain": link.rx_gain}
        elif kind == "cat":
            keys = [k for k in CAT_CONTROLS if k in link.control_keys()]
            values, choices, writable = {}, {}, {}
            for k in keys:
                named = getattr(link, "control_choices", lambda _k: None)(k)
                writable[k] = getattr(link, "control_writable", lambda _k: True)(k)
                raw = link.read_control(k)
                if named:
                    choices[k] = list(named)
                    values[k] = raw
                else:
                    values[k] = _pct(raw, getattr(link, "control_range", lambda _k: 255)(k))
            self.values, self.choices, self.writable = values, choices, writable

    def _meters(self, link) -> None:
        from shack_rig.icom.controls import SwrWatch
        m = link.read_meters()
        tx = "po_pct" in m
        if self._swr is None:
            self._swr = SwrWatch()
        if tx:
            self._swr.seen(m.get("swr"))
        elif self._was_tx:
            warn = self._swr.over_ended()
            if warn:
                self.warning = warn
                log.warning("%s", warn)
                self._note(warn)
                self.ctl._emit("radio", "swr-jump", {"text": warn})
        self._was_tx = tx
        self.meters = m

    def _set(self, link, key: str, value) -> None:
        before = self.values.get(key)
        if key in ("tx_gain", "rx_gain"):
            if key == "tx_gain":
                link.set_tx_gain(float(value))
                self.ctl.settings.sdr_radio.pluto_tx_gain_db = float(value)
            else:
                link.set_rx_gain(None if value is None else float(value))
                self.ctl.settings.sdr_radio.pluto_rx_gain_db = None if value is None else float(value)
            if self.save is not None:
                self.save()
            self.values[key] = value
            ok = True
        elif not self.writable.get(key, True):
            ok = False                              # read only: never sent
        elif key in self.choices:
            line = link.set_control(key, value)
            ok = line is not None and line.status == "set"
            got = link.read_control(key)
            self.values[key] = got if got is not None else before
        else:
            from shack_rig.icom.controls import level
            line = link.set_control(key, level(value))
            ok = line is not None and line.status == "set"
            got = link.read_control(key)
            self.values[key] = _pct(got) if got is not None else before
        named = dict(self.choices.get(key) or ())
        text = (f"{NAMES.get(key, key)} {named.get(before, _show(key, before))} -> "
                f"{named.get(value, _show(key, value))}" + ("" if ok else " (refused)"))
        self.changes.append({"wall": time.time(), "key": key, "before": before, "after": value, "ok": ok})
        log.warning("radio control: %s", text)
        self._note(text)

    def _note(self, text: str) -> None:
        lt = getattr(self.ctl, "linktest", None)
        if lt is not None:
            lt.note(text)


def _show(key: str, v) -> str:
    if v is None:
        return "AGC" if key == "rx_gain" else "?"
    if key in ("tx_gain", "rx_gain"):
        return f"{float(v):.0f} dB"
    return f"{v} %"
