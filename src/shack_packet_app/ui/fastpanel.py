"""The fast modes and the amplifier, as the operator sets them (0.2.1):
Settings › Fast modes, and the same panel from the setup check's Pluto
page. One widget for both, so the two never say different things.

What it holds is settings.FastRadioSettings; what each setting means and
which values are refused is shack_packet.fastsettings', and fastmodes.py
has the app's choices. The panel adds what the operator has to see while
choosing:

- **The band plan, live** for the mode (or adaptive ladder) at the channel's
  frequency: whether a mode wider than 25 kHz is inside a high-speed data
  segment, and where the Pluto's LO leakage and IQ image land (ifo and
  2 x ifo below the channel). A mode out of plan cannot be armed
  (controller.arm_refusal); the panel says so before OK, and offers
  1299.525 MHz, the 23 cm DD channel every mode up to 150 kHz fits with its
  LO and image inside the segment.
- **PTT outputs by firmware** (fastmodes.ptt_outputs): "firmware" only for a
  Pluto checked as PlutoDVB2 or Tezuka, gpo0-3 only for ADI's firmware, and
  nothing until the Pluto has been checked.
- **The amplifier**, with its two notices where it is set up: the band-pass
  filter goes AFTER the amplifier (its harmonics, and the Pluto's, are what
  the filter is for), and the amplifier is powered only for transmit runs
  (a Pluto that reboots radiates its TX LO at 2450 MHz, about -10 dB, until
  something quiets it, and an amplifier left on would amplify that). The
  drive cap is per band; "Find the drive level…" runs fastmodes.DriveSession
  through the window's station, so every burst it sends is armed, guarded
  and band-plan checked like any other transmission.
"""
from __future__ import annotations

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import fastmodes
from ..settings import FastRadioSettings
from . import fmt, texts


def _note(text: str, name: str = "dim") -> QLabel:
    w = QLabel(text)
    w.setWordWrap(True)
    w.setObjectName(name)
    return w


def _tr(text: str) -> str:
    return QCoreApplication.translate("FastPanel", text)


def plan_verdict(fs, hz: float):
    """(sentence, label object name, fastmodes.PlanCheck or None) for
    FastSettings `fs` at `hz`: what the band plan says about the mode or
    ladder there, the LO leakage and IQ image (module docstring). Shared
    by this panel and the Link test page, where the mode is chosen too."""
    problems = [p for p in fs.problems() if p != "fast-out-of-plan"]
    check = None if problems else fastmodes.plan(fs, hz)
    lines, name = [], "good"
    if problems:
        lines.append(texts.reason(problems[0]))
        name = "bad"
    elif check is not None:
        lines.append(_tr("%1 occupies %2.").replace("%1", texts.fast_mode(check.mode))
                     .replace("%2", fmt.khz(check.occupied_hz)))
        if not check.fits:
            name = "bad"
            lines.append(texts.reason("fast-out-of-plan"))
        elif check.segment is not None:
            lines.append(_tr("%1 is inside the band plan's high speed data, %2-%3.")
                         .replace("%1", fmt.mhz(hz)).replace("%2", fmt.mhz(check.segment[0]))
                         .replace("%3", fmt.mhz(check.segment[1])))
        if check.wide or check.segment is not None:
            lines.append(_tr("The Pluto's LO leakage is at %1 and its IQ image at %2 (%3 and twice that "
                             "below the channel).").replace("%1", fmt.mhz(check.lo_hz))
                         .replace("%2", fmt.mhz(check.image_hz)).replace("%3", fmt.khz(check.ifo_hz)))
        for out, what in ((check.lo_out, _tr("The LO leakage")), (check.image_out, _tr("The IQ image"))):
            if out:
                name = "bad" if name == "bad" else "warn"
                lines.append(_tr("%1 falls outside the segment: choose a channel higher in it.")
                             .replace("%1", what))
    return " ".join(lines), name, check


class FastPanel(QWidget):
    """`fast` is the FastRadioSettings to show (a copy is edited; `result()`
    gives it back); `family` the Pluto's checked firmware family ("" when
    unchecked); `frequency` a callable giving the channel's Hz now;
    `set_frequency(hz)` moves the channel (None: the suggestion is only
    said); `drive_helper(band)` runs the drive-level helper and returns the
    cap to save or None (None: the button is not offered)."""

    def __init__(self, fast: FastRadioSettings, *, family: str = "", frequency=lambda: 144_800_000,
                 set_frequency=None, drive_helper=None, is_pluto: bool = True, parent=None):
        super().__init__(parent)
        import dataclasses
        self.fast = dataclasses.replace(fast, drive_caps=dict(fast.drive_caps))
        self.family = family or ""
        self.frequency = frequency
        self.set_frequency = set_frequency
        self.drive_helper = drive_helper
        lay = QVBoxLayout(self)
        if not is_pluto:
            lay.addWidget(_note(_tr("These settings are for a Pluto, the one radio here that carries the fast "
                                    "modes. Other radios send 1200 baud AFSK."), "warn"))
        f = QFormLayout()
        self.mode = QComboBox()
        for m in fastmodes.MODES:
            self.mode.addItem(texts.fast_mode(m), m)
        self.adapt = QCheckBox(_tr("Link adaptation: each direction runs at the fastest mode its receiver can "
                                   "decode"))
        self.top = QComboBox()
        self.floor = QComboBox()
        for m in fastmodes.MODES[1:]:
            self.top.addItem(texts.fast_mode(m), m)
            self.floor.addItem(texts.fast_mode(m), m)
        self.window = QSpinBox()
        self.window.setRange(1, 63)
        # 0.3.2: the window is chosen per mode unless the operator sets one
        # (fastmodes.default_window); shown either way.
        self.window_auto = QCheckBox(_tr("Best for the mode"))
        self.window_auto.setToolTip(_tr("As many frames as one transmission carries at this mode: 31 at 9600, "
                                        "62 at 19 200, 63 above."))
        self.window_auto.toggled.connect(self._window_shown)
        self.paclen = QSpinBox()
        self.paclen.setRange(1, 2048)
        self.paclen.setSuffix(_tr(" bytes"))
        self.srej = QCheckBox(_tr("Selective reject (when the other station offers it too)"))
        self.txdelay = QSpinBox()
        self.txdelay.setRange(0, 1000)
        self.txdelay.setSuffix(_tr(" ms"))
        self.turn_auto = QCheckBox(_tr("The Pluto's own"))
        self.turnaround = QSpinBox()
        self.turnaround.setRange(0, 500)
        self.turnaround.setSuffix(_tr(" ms"))
        self.turn_auto.toggled.connect(lambda on: self.turnaround.setEnabled(not on))
        turn = QHBoxLayout()
        turn.addWidget(self.turn_auto)
        turn.addWidget(self.turnaround, 1)
        f.addRow(_tr("Mode"), self.mode)
        f.addRow(self.adapt)
        f.addRow(_tr("Fastest mode"), self.top)
        f.addRow(_tr("Slowest mode"), self.floor)
        f.addRow(_note(_tr("Both stations must have link adaptation on. The slowest mode carries every "
                           "acknowledgement and connect.")))
        win = QHBoxLayout()
        win.addWidget(self.window_auto)
        win.addWidget(self.window, 1)
        f.addRow(_tr("Window"), win)
        f.addRow(_tr("Packet length (PACLEN)"), self.paclen)
        f.addRow(self.srej)
        f.addRow(_tr("TXDELAY"), self.txdelay)
        f.addRow(_tr("Turnaround"), turn)
        lay.addLayout(f)
        self.plan_text = _note("", "")
        lay.addWidget(self.plan_text)
        self.use_suggested = QPushButton(_tr("Use %1").replace("%1", fmt.mhz(fastmodes.SUGGESTED_HZ)))
        self.use_suggested.clicked.connect(self._use_suggested)
        self.use_suggested.setVisible(False)
        lay.addWidget(self.use_suggested)

        amp = QGroupBox(_tr("PTT output and amplifier"))
        af = QFormLayout(amp)
        af.addRow(_note(_tr("Put the band-pass filter AFTER the amplifier."), "bad"))
        af.addRow(_note(_tr("Power the amplifier only for transmit runs. A Pluto that restarts transmits on "
                            "2450 MHz by itself until this program switches it off, and an amplifier left on "
                            "would amplify that."), "bad"))
        self.ptt = QComboBox()
        for p in fastmodes.ptt_outputs(self.family):
            self.ptt.addItem(texts.ptt_output(p), p)
        self.ptt_note = _note("")
        self.lead = QSpinBox()
        self.tail = QSpinBox()
        for w in (self.lead, self.tail):
            w.setRange(0, 500)
            w.setSuffix(_tr(" ms"))
        af.addRow(_tr("PTT output"), self.ptt)
        af.addRow(self.ptt_note)
        af.addRow(_tr("Relay lead"), self.lead)
        af.addRow(_tr("Relay tail"), self.tail)
        self.cap_on = QCheckBox()
        self.cap = QDoubleSpinBox()
        self.cap.setRange(-89.75, 0.0)
        self.cap.setSingleStep(1.0)
        self.cap.setDecimals(2)
        self.cap.setSuffix(_tr(" dB"))
        self.cap_on.toggled.connect(lambda on: self.cap.setEnabled(on))
        cap = QHBoxLayout()
        cap.addWidget(self.cap_on)
        cap.addWidget(self.cap, 1)
        self.find_drive = QPushButton(_tr("Find the drive level…"))
        self.find_drive.clicked.connect(self._find_drive)
        self.find_drive.setVisible(drive_helper is not None)
        cap.addWidget(self.find_drive)
        af.addRow(_tr("Drive cap"), cap)
        af.addRow(_note(_tr("The most transmit gain the Pluto may use on this band: an amplifier's drive "
                            "limit. Each band keeps its own, and the guard on the Pluto holds it.")))
        lay.addWidget(amp)
        lay.addStretch(1)
        self._load()
        for sig in (self.mode.currentIndexChanged, self.adapt.toggled, self.top.currentIndexChanged,
                    self.floor.currentIndexChanged):
            sig.connect(lambda *_: self.refresh())
            sig.connect(self._window_shown)
        self.paclen.valueChanged.connect(self._window_shown)
        self.refresh()

    # -- in and out --------------------------------------------------------------------------

    def band(self) -> str:
        from ..sdrplan import band_of
        return band_of(self.frequency())

    def _load(self):
        fs = self.fast
        self.mode.setCurrentIndex(max(0, self.mode.findData(fs.mode)))
        self.adapt.setChecked(bool(fs.adapt))
        self.top.setCurrentIndex(max(0, self.top.findData(fs.top_mode)))
        self.floor.setCurrentIndex(max(0, self.floor.findData(fs.floor_mode)))
        self.window.setValue(int(fs.window))
        self.window_auto.setChecked(bool(fs.window_auto))
        self.paclen.setValue(int(fs.paclen))
        self.srej.setChecked(bool(fs.srej))
        self.txdelay.setValue(int(fs.txdelay_ms))
        self.turn_auto.setChecked(fs.turnaround_ms is None)
        self.turnaround.setValue(int(fs.turnaround_ms or 100))
        self.turnaround.setEnabled(fs.turnaround_ms is not None)
        idx = self.ptt.findData(fs.ptt_output)
        if idx < 0 and fs.ptt_output != "none":
            # A saved output this Pluto's firmware is not offered (a Pluto
            # changed, or never checked): shown, so OK does not quietly turn
            # it off, and said.
            self.ptt.addItem(texts.ptt_output(fs.ptt_output), fs.ptt_output)
            idx = self.ptt.count() - 1
        self.ptt.setCurrentIndex(max(0, idx))
        self.lead.setValue(int(fs.relay_lead_ms))
        self.tail.setValue(int(fs.relay_tail_ms))
        self._load_cap()
        self._window_shown()                        # once PACLEN and the modes are in

    def _window_shown(self, *_):
        """Automatic: the spin box shows the window the mode gets, greyed."""
        auto = self.window_auto.isChecked()
        self.window.setEnabled(not auto)
        if auto:
            from ..fastmodes import default_window
            adapt = self.adapt.isChecked()
            mode = (self.floor.currentData() if adapt else self.mode.currentData()) or "g3ruh9600"
            if mode == "afsk1200":
                self.window.setValue(7)
            else:
                self.window.setValue(default_window(mode, self.paclen.value()))

    def _load_cap(self):
        #: The band the cap controls show: the channel's when they were loaded.
        self._cap_band = self.band()
        cap = self.fast.drive_caps.get(self._cap_band)
        self.cap_on.setText(_tr("An amplifier on %1").replace("%1", self._cap_band or "?"))
        self.cap_on.setChecked(cap is not None)
        self.cap.setValue(float(cap) if cap is not None else -30.0)
        self.cap.setEnabled(cap is not None)

    def result(self) -> FastRadioSettings:
        fs = self.fast
        fs.mode = self.mode.currentData()
        fs.adapt = self.adapt.isChecked()
        fs.top_mode, fs.floor_mode = self.top.currentData(), self.floor.currentData()
        fs.paclen, fs.srej = self.paclen.value(), self.srej.isChecked()
        fs.window_auto = self.window_auto.isChecked()
        if not fs.window_auto:
            fs.window = self.window.value()
        fs.txdelay_ms = float(self.txdelay.value())
        fs.turnaround_ms = None if self.turn_auto.isChecked() else float(self.turnaround.value())
        fs.ptt_output = self.ptt.currentData()
        fs.relay_lead_ms, fs.relay_tail_ms = float(self.lead.value()), float(self.tail.value())
        band = self._cap_band
        if band:
            if self.cap_on.isChecked():
                fs.drive_caps[band] = float(self.cap.value())
            else:
                fs.drive_caps.pop(band, None)
        return fs

    # -- what the operator sees while choosing ----------------------------------------------------

    def refresh(self):
        """The band plan for the mode at the channel's frequency now, and the
        PTT output's note. Called on every change here, and by the dialog
        when the channel's frequency changes."""
        adapt = self.adapt.isChecked()
        self.mode.setEnabled(not adapt)
        self.top.setEnabled(adapt)
        self.floor.setEnabled(adapt)
        if self.band() != self._cap_band:
            # The channel moved to another band: keep what was set for the
            # old one, and show the new one's.
            self.result()
            self._load_cap()
        hz = self.frequency()
        text, name, check = plan_verdict(self.result().for_frequency(hz), hz)
        self.plan_text.setText(text)
        self.plan_text.setObjectName(name)
        self.plan_text.setStyleSheet("")
        self.use_suggested.setVisible(self.set_frequency is not None and check is not None
                                      and (not check.fits or check.lo_out or check.image_out)
                                      and int(hz) != fastmodes.SUGGESTED_HZ)
        ptt = self.ptt.currentData()
        if not self.family:
            self.ptt_note.setText(_tr("Check this Pluto in the setup check (Your radio) first: which PTT outputs "
                                      "it has depends on its firmware."))
        elif ptt == "firmware":
            self.ptt_note.setText(_tr("The firmware's own GPO0 follows the transmitter; the relay lead is added "
                                      "to the turnaround."))
        elif ptt not in fastmodes.ptt_outputs(self.family):
            self.ptt_note.setText(_tr("This Pluto's firmware drives its GPOs itself: choose another output."))
        else:
            self.ptt_note.setText(_tr("Which pins reach a connector depends on the board: check it with a meter "
                                      "before connecting a relay."))

    def _use_suggested(self):
        if self.set_frequency is not None:
            self.set_frequency(fastmodes.SUGGESTED_HZ)
            self.refresh()

    def _find_drive(self):
        if self.drive_helper is None:
            return
        cap = self.drive_helper(self.band())
        if cap is not None:
            self.cap_on.setChecked(True)
            self.cap.setValue(float(cap))


class FastDialog(QDialog):
    """The panel on its own, for the setup check's Pluto page."""

    def __init__(self, parent, fast: FastRadioSettings, **kw):
        super().__init__(parent)
        self.setWindowTitle(_tr("Fast modes and amplifier"))
        lay = QVBoxLayout(self)
        self.panel = FastPanel(fast, **kw)
        lay.addWidget(self.panel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class DriveDialog(QDialog):
    """The drive-level helper's steps (fastmodes.DriveSession). `session`
    is the DriveSession; the dialog only presses its buttons and says what
    each step did. Closing it any way puts the station back."""

    def __init__(self, parent, session):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(_tr("Find the drive level"))
        lay = QVBoxLayout(self)
        lay.addWidget(_note(_tr("This transmits."), "bad"))
        lay.addWidget(_note(_tr(
            "With the amplifier on a dummy load and a power meter, or a receiver you can read, each step sets "
            "the Pluto's transmit gain a little higher, from %1. Press Send to arm for a minute and send a short "
            "burst; if the power rose as it should, press Good for the next step. Stop at the power you want, or "
            "as soon as the power stops rising with the gain (compression): the last good step becomes this "
            "band's drive cap.").replace("%1", fmt.number(session.steps[0], 0) + " dB"), ""))
        lay.addWidget(_note(_tr("Put the band-pass filter AFTER the amplifier. Power the amplifier only for "
                                "transmit runs."), "bad"))
        self.state = _note("", "heading")
        lay.addWidget(self.state)
        self.said = _note("", "")
        lay.addWidget(self.said)
        row = QHBoxLayout()
        self.send = QPushButton(_tr("Send a burst"))
        self.send.clicked.connect(self._send)
        self.good = QPushButton(_tr("Good: next step"))
        self.good.clicked.connect(self._good)
        self.stop = QPushButton(_tr("Stop and save the last good step"))
        self.stop.clicked.connect(self.accept)
        cancel = QPushButton(_tr("Cancel"))
        cancel.clicked.connect(self.reject)
        for b in (self.send, self.good, self.stop, cancel):
            row.addWidget(b)
        lay.addLayout(row)
        self.cap: float | None = None
        self.session.next_step()
        self._show()

    def _show(self):
        s = self.session
        self.state.setText(_tr("Step %1 of %2: %3 dB. Last good: %4.")
                           .replace("%1", str(s.idx + 1)).replace("%2", str(len(s.steps)))
                           .replace("%3", fmt.number(s.gain if s.gain is not None else 0.0, 1))
                           .replace("%4", _tr("none") if s.last_good is None else
                                    fmt.number(s.last_good, 1) + " dB"))
        self.stop.setEnabled(s.last_good is not None)

    def _send(self):
        why, detail = self.session.burst()
        if why:
            self.said.setText(texts.reason(why) + (f" ({detail})" if detail else ""))
        else:
            self.said.setText(_tr("Burst sent at %1 dB. Read the power, then press Good or Stop.")
                              .replace("%1", fmt.number(self.session.gain, 1)))

    def _good(self):
        if self.session.good() is None:
            self.said.setText(_tr("That was the last step (0 dB)."))
            self.good.setEnabled(False)
        else:
            self.said.setText(_tr("Next step set up. The radio reconnects and is disarmed: press Send when it "
                                  "is connected."))
        self._show()

    def done(self, r):
        cap = self.session.finish()
        self.cap = cap if r == QDialog.DialogCode.Accepted else None
        super().done(r)
