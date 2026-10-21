"""The control strip under the window's pages (0.3.0; radioctl.py says
what it drives and why): the channel and mode, the radio's power, gain
and squelch or a Pluto's gains, and live meters - one line, no dialogs.

Sliders write when let go (or after a pause while dragged by keyboard),
not on every pixel: each write is a CI-V round trip and a read-back.
Changing the channel or the mode rebuilds the station as Settings does
(disarmed), so those two are a choice from a list, never a slider.

The channel shown is the station's, which follows the radio's own VFO
(tuning.py, 0.3.2): the matching preset's name, or "Other frequency" with
the frequency when the radio is somewhere no preset is. The window calls
`reset` when the channel moves.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QSlider, QSpinBox

from .. import fastmodes, presets
from ..radioctl import RadioControls
from . import texts
from .sdrpages import fill_tx_gain
from .widgets import label

ADAPT = "adaptive"


class _Slider(QSlider):
    """0-100 %, written on release or a short pause."""

    def __init__(self, key, strip):
        super().__init__(Qt.Orientation.Horizontal)
        self.key, self.strip = key, strip
        self.setRange(0, 100)
        self.setFixedWidth(90)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.commit)
        self.valueChanged.connect(lambda _v: self.timer.start() if not self.isSliderDown() else None)
        self.sliderReleased.connect(self.commit)
        self.pending = None

    def commit(self):
        self.timer.stop()
        self.pending = self.value()
        self.strip.controls.set(self.key, self.value())

    def show_value(self, v):
        if v is None or self.isSliderDown() or self.timer.isActive():
            return
        if self.pending is not None and v != self.pending:
            return                                  # the write is still on its way
        self.pending = None
        self.blockSignals(True)
        self.setValue(int(v))
        self.blockSignals(False)


class ControlStrip(QFrame):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("controlstrip")
        self.controls = RadioControls(window.ctl, save=window.save_settings)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        self.preset = QComboBox()
        self.preset.setToolTip(self.tr("The channel (rebuilds the station, disarmed)"))
        self.preset.activated.connect(self._preset_chosen)
        self.mode = QComboBox()
        self.mode.setToolTip(self.tr("The packet mode (rebuilds the station, disarmed)"))
        self.mode.activated.connect(self._mode_chosen)
        lay.addWidget(self.preset)
        lay.addWidget(self.mode)
        # A CAT radio's controls.
        self.cat = {}
        self.cat_labels = {}
        for key, name in (("rf_power", self.tr("Power")), ("rf_gain", self.tr("RF gain")),
                          ("squelch", self.tr("Squelch"))):
            lab = QLabel(name)
            sl = _Slider(key, self)
            val = QLabel("")
            val.setMinimumWidth(34)
            self.cat[key], self.cat_labels[key] = sl, (lab, val)
            for w in (lab, sl, val):
                lay.addWidget(w)
        # A radio whose power is a choice of steps (a TM-V71's H, M, L).
        self.power_label = QLabel(self.tr("Power"))
        self.power_choice = QComboBox()
        self.power_choice.setToolTip(self.tr("The radio's power step; put back when the radio is closed"))
        self.power_choice.activated.connect(
            lambda _i: self.controls.set("power_level", self.power_choice.currentData()))
        for w in (self.power_label, self.power_choice):
            lay.addWidget(w)
        # A Pluto's.
        self.tx_gain_label = QLabel(self.tr("TX gain"))
        self.tx_gain = QComboBox()
        self.tx_gain.setToolTip(self.tr("From the next transmission; steps above the band's drive cap are greyed"))
        self.tx_gain.activated.connect(lambda _i: self.controls.set("tx_gain", float(self.tx_gain.currentData())))
        self.rx_agc = QCheckBox(self.tr("RX AGC"))
        self.rx_agc.toggled.connect(self._rx_agc)
        self.rx_gain = QSpinBox()
        self.rx_gain.setRange(0, 71)
        self.rx_gain.setSuffix(self.tr(" dB"))
        self.rx_gain.editingFinished.connect(lambda: self.controls.set("rx_gain", float(self.rx_gain.value())))
        for w in (self.tx_gain_label, self.tx_gain, self.rx_agc, self.rx_gain):
            lay.addWidget(w)
        lay.addStretch(1)
        self.meter = label("", "dim")
        self.warn = label("", "bad")
        lay.addWidget(self.meter)
        lay.addWidget(self.warn)
        self._kind = None
        self.reset()

    # -- filling ---------------------------------------------------------------------------

    def reset(self):
        """The channel and mode lists from the settings (after a change)."""
        s = self.window.settings
        self.preset.blockSignals(True)
        self.preset.clear()
        for p in presets.presets(s.station.profile()):
            if p.enabled:
                self.preset.addItem(p.label, (p.id, p.hz))
        i = next((i for i in range(self.preset.count()) if self.preset.itemData(i)[1] == s.channel.frequency_hz), -1)
        if i < 0:
            mhz = f"{s.channel.frequency_hz / 1e6:.4f}"
            self.preset.addItem(self.tr("Other frequency (%1 MHz)").replace("%1", mhz),
                                ("custom", s.channel.frequency_hz))
            i = self.preset.count() - 1
        self.preset.setCurrentIndex(i)
        self.preset.blockSignals(False)
        self.mode.blockSignals(True)
        self.mode.clear()
        if s.radio.kind == "pluto":
            for m in fastmodes.MODES:
                self.mode.addItem(texts.fast_mode(m), m)
            self.mode.addItem(self.tr("Adaptive"), ADAPT)
            self.mode.setCurrentIndex(max(0, self.mode.findData(ADAPT if s.fast.adapt else s.fast.mode)))
            self.mode.setEnabled(True)
        elif s.radio.kind == "tm-v71":
            # Its DATA jack's two speeds (airtime.TMV71_MODES); the link sets
            # the radio's menu 518 to match when the station is rebuilt.
            self.mode.addItem(texts.fast_mode("afsk1200"), 1200)
            self.mode.addItem(texts.fast_mode("g3ruh9600"), 9600)
            self.mode.setCurrentIndex(max(0, self.mode.findData(int(s.radio.data_speed or 1200))))
            self.mode.setEnabled(True)
            self.mode.setToolTip(self.tr("The DATA jack's speed: 9600 needs the PR9 pin and a 9600 station at the "
                                         "far end (rebuilds the station, disarmed)"))
        else:
            self.mode.addItem(self.tr("AFSK 1200"), "afsk1200")
            self.mode.setEnabled(False)
            self.mode.setToolTip(self.tr("This radio sends AFSK 1200; the fast modes need a Pluto"))
        self.mode.blockSignals(False)
        self._kind = None

    def _preset_chosen(self, _i):
        pid, hz = self.preset.currentData()
        s = self.window.settings
        if hz == s.channel.frequency_hz:
            return
        new = s.copy()
        new.channel.preset, new.channel.frequency_hz = pid, int(hz)
        self.controls._note(f"channel {s.channel.frequency_hz / 1e6:.4f} -> {hz / 1e6:.4f} MHz")
        self.window.apply_settings(new)

    def _mode_chosen(self, _i):
        choice = self.mode.currentData()
        s = self.window.settings
        new = s.copy()
        if s.radio.kind == "tm-v71":
            if int(choice) == int(s.radio.data_speed or 1200):
                return
            new.radio.data_speed = int(choice)
            self.controls._note(f"DATA speed -> {choice}")
            self.window.apply_settings(new)
            return
        new.fast.adapt = choice == ADAPT
        if choice != ADAPT:
            new.fast.mode = choice
        if new.fast == s.fast:
            return
        self.controls._note(f"mode -> {choice}")
        self.window.apply_settings(new)

    def _rx_agc(self, on: bool):
        self.rx_gain.setEnabled(not on)
        if self.controls.values.get("rx_gain", 0) is None and on:
            return
        self.controls.set("rx_gain", None if on else float(self.rx_gain.value()))

    # -- live --------------------------------------------------------------------------------

    def tick(self):
        c = self.controls
        c.tick(self.isVisible())
        kind = c.kind()
        if kind != self._kind:
            self._kind = kind
            for key, sl in self.cat.items():
                for w in (sl, *self.cat_labels[key]):
                    w.setVisible(kind == "cat")
            for w in (self.tx_gain_label, self.tx_gain, self.rx_agc, self.rx_gain):
                w.setVisible(kind == "pluto")
            for w in (self.power_label, self.power_choice):
                w.setVisible(False)                 # shown below, for a radio that has it
            self.meter.setVisible(kind == "cat")
        v = c.values
        if kind == "cat":
            for key, sl in self.cat.items():
                # A radio shows only the controls it has (a TM-V71: no RF
                # gain, its power a choice below, its squelch read only).
                shown = key in v
                for w in (sl, *self.cat_labels[key]):
                    w.setVisible(shown)
                have = shown and v[key] is not None
                sl.setEnabled(have and c.writable.get(key, True))
                sl.show_value(v.get(key))
                self.cat_labels[key][1].setText(f"{v[key]} %" if have else "-")
                sl.setToolTip(self.tr("Read from the radio; set it on the radio's own knob")
                              if shown and not c.writable.get(key, True) else "")
            choices = c.choices.get("power_level")
            for w in (self.power_label, self.power_choice):
                w.setVisible(bool(choices))
            if choices:
                if self.power_choice.count() != len(choices):
                    self.power_choice.clear()
                    for raw, name in choices:
                        self.power_choice.addItem(name, raw)
                if not self.power_choice.view().isVisible():
                    self.power_choice.setCurrentIndex(max(0, self.power_choice.findData(v.get("power_level"))))
            self.meter.setText(self._meter_text(c.meters))
        elif kind == "pluto" and v:
            cap = c.tx_cap()
            if self.tx_gain.count() == 0 or self.tx_gain.currentData() != v.get("tx_gain"):
                fill_tx_gain(self.tx_gain, float(v["tx_gain"]), cap)
            self.rx_agc.blockSignals(True)
            self.rx_agc.setChecked(v.get("rx_gain") is None)
            self.rx_agc.blockSignals(False)
            self.rx_gain.setEnabled(v.get("rx_gain") is not None)
            if v.get("rx_gain") is not None and not self.rx_gain.hasFocus():
                self.rx_gain.setValue(int(v["rx_gain"]))
        self.warn.setText(c.warning)

    def _meter_text(self, m: dict) -> str:
        if "po_pct" in m:
            def n(x, fmt):
                return "-" if x is None else format(x, fmt)
            return self.tr("TX  Po %1 %  SWR %2  ALC %3 %").replace("%1", n(m.get("po_pct"), ".0f")).replace(
                "%2", n(m.get("swr"), ".1f")).replace("%3", n(m.get("alc_pct"), ".0f"))
        if m.get("keyed"):
            return self.tr("Transmitting")
        if "busy" in m:
            # A radio with no S-meter command (a TM-V71): its squelch.
            return {True: self.tr("Channel busy"), False: self.tr("Channel clear")}.get(m["busy"], "")
        s = m.get("s_db")
        if s is None:
            return ""
        return self.tr("Signal %1").replace("%1", f"S9{s:+.0f} dB" if s > 0 else f"S{max(0, round((s + 54) / 6))}")

    def dial(self, which: str, steps: int):
        """A pad dial (padactions): power or squelch on a CAT radio, TX gain
        on a Pluto for "power"."""
        kind = self.controls.kind()
        if which == "power":
            key = "tx_gain" if kind == "pluto" else ("power_level" if "power_level" in self.controls.values
                                                     else "rf_power")
            self.controls.nudge(key, steps)
        elif which == "squelch" and kind == "cat":
            self.controls.nudge("squelch", steps)

    def close(self):
        self.controls.close()
