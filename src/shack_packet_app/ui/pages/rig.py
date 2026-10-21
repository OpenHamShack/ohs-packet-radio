"""IC-9700: the radio beyond packet (0.3.0, workstream F), in four tabs -
its spectrum and waterfall, adaptive transmit power, satellites (passes,
live position, Doppler and the transmit gate) and 23 cm DD.

Nothing here talks to the radio itself: the scope comes through
scope.ScopeFeed and everything else through the controller's `rigx`
(rigfeatures.py), whose thread does the work whether or not this page is
open. The Spectrum tab asks for sweeps only while it is on screen (shown
and hidden with the page and the tab), so a radio nobody watches sends
none. Settings changed here are the window's own settings object, used at
once and saved with the rest when the window closes.
"""
from __future__ import annotations

import threading
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ... import scope as scope_mod
from ...adaptpower import ICOM_KINDS, percent_of
from .. import fmt, tables, texts, theme, widgets
from .sdr import _palette

#: Full spans the 9700 offers (27 15 takes half of each).
SPANS_HZ = (5_000, 10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000)
SPEEDS = ("fast", "mid", "slow")
DD_KIND = "ic-9700-dd"


class ScopeView(QWidget):
    """The latest sweep as a line over a waterfall of the ones before it,
    the packet channels marked, busy ones shaded. A click asks to tune."""

    clicked = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []
        self.chans: list = []
        self.busy: dict = {}
        self.message = ""
        self._lut = _palette()
        self._buf = None
        self.image: QImage | None = None
        self.setMinimumHeight(260)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def show_rows(self, rows, chans, busy, message: str = "") -> None:
        self.rows, self.chans, self.busy, self.message = rows, chans, busy, message
        self.image = None
        if rows:
            n = rows[-1].data.size
            same = [r.data for r in rows if r.data.size == n][::-1]          # newest at the top
            idx = np.clip(np.stack(same).astype(np.int32) * 255 // 160, 0, 255).astype(np.uint8)
            self._buf = np.ascontiguousarray(self._lut[idx])
            h, w = self._buf.shape
            self.image = QImage(self._buf.data, w, h, 4 * w, QImage.Format.Format_RGB32)
        self.update()

    def hz_at(self, x: float) -> float | None:
        if not self.rows:
            return None
        r = self.rows[-1]
        return r.start + max(0.0, min(1.0, x / max(1, self.width()))) * (r.end - r.start)

    def x_of(self, hz: float) -> float:
        r = self.rows[-1]
        return (hz - r.start) / max(1, r.end - r.start) * self.width()

    def mousePressEvent(self, e):
        hz = self.hz_at(e.position().x())
        if hz is not None and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(hz)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.colour("surface"))
        if not self.rows:
            p.setPen(theme.colour("ink-dim"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.message or self.tr("No spectrum from the radio"))
            return
        w, h = self.width(), self.height()
        top_h = int(h * 0.35)
        last = self.rows[-1]
        # Busy channels first, so the line and the markers draw over them.
        for c in self.chans:
            if self.busy.get(c.hz):
                bw = scope_mod.CHANNEL_BW_HZ / max(1, last.end - last.start) * w
                p.fillRect(QRectF(self.x_of(c.hz) - bw / 2, 0, max(3.0, bw), h), QColor(220, 60, 60, 70))
        pts = QPolygonF([QPointF(i / max(1, last.data.size - 1) * w, top_h - v / 160.0 * (top_h - 4))
                         for i, v in enumerate(last.data.tolist())])
        p.setPen(QPen(theme.colour("accent"), 1))
        p.drawPolyline(pts)
        if self.image is not None:
            p.drawImage(QRectF(0, top_h, w, h - top_h), self.image)
        fm = p.fontMetrics()
        for c in self.chans:
            x = self.x_of(c.hz)
            p.setPen(QPen(QColor(255, 80, 80) if self.busy.get(c.hz) else theme.colour("ink"), 1,
                          Qt.PenStyle.DashLine))
            p.drawLine(int(x), fm.height() + 2, int(x), h)
            p.drawText(QRectF(x - 70, 0, 140, fm.height() + 2), Qt.AlignmentFlag.AlignCenter,
                       c.label or fmt.mhz(c.hz))
        p.setPen(theme.colour("ink-dim"))
        p.drawText(QRectF(4, h - fm.height() - 2, w / 2, fm.height()), Qt.AlignmentFlag.AlignLeft,
                   fmt.mhz(last.start))
        p.drawText(QRectF(w / 2, h - fm.height() - 2, w / 2 - 4, fm.height()), Qt.AlignmentFlag.AlignRight,
                   fmt.mhz(last.end))


class SpectrumTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.feed = scope_mod.ScopeFeed()
        self._icom = None
        self._rev = -1
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.state = widgets.label("", "dim", wrap=True)
        top.addWidget(self.state, 1)
        self.span = QComboBox()
        for hz in SPANS_HZ:
            self.span.addItem(self.tr("±%1 kHz").replace("%1", fmt.number(hz / 2000, 1 if hz < 20_000 else 0)), hz)
        self.ref = QDoubleSpinBox()
        self.ref.setRange(-20.0, 20.0)
        self.ref.setSingleStep(0.5)
        self.ref.setSuffix(self.tr(" dB"))
        self.speed = QComboBox()
        for key, text in (("fast", self.tr("Fast")), ("mid", self.tr("Mid")), ("slow", self.tr("Slow"))):
            self.speed.addItem(text, key)
        for text, w in ((self.tr("Span"), self.span), (self.tr("Reference"), self.ref), (self.tr("Speed"), self.speed)):
            top.addWidget(QLabel(text))
            top.addWidget(w)
        lay.addLayout(top)
        self.view = ScopeView()
        self.view.clicked.connect(self._clicked)
        lay.addWidget(self.view, 1)
        self.hint = widgets.label(self.tr("Click a packet channel to tune to it. Shaded channels look busy. The "
                                          "station is disarmed when it retunes."), "dim", wrap=True)
        lay.addWidget(self.hint)
        self._load()
        self.span.currentIndexChanged.connect(lambda _i: self._send())
        self.ref.valueChanged.connect(lambda _v: self._send())
        self.speed.currentIndexChanged.connect(lambda _i: self._send())

    def _load(self):
        s = self.page.settings().scope
        for w in (self.span, self.ref, self.speed):
            w.blockSignals(True)
        i = self.span.findData(int(s.span_hz))
        self.span.setCurrentIndex(i if i >= 0 else SPANS_HZ.index(100_000))
        self.ref.setValue(float(s.ref_db))
        self.speed.setCurrentIndex(max(0, self.speed.findData(s.speed)))
        for w in (self.span, self.ref, self.speed):
            w.blockSignals(False)

    def _send(self):
        s = self.page.settings().scope
        s.span_hz, s.ref_db, s.speed = int(self.span.currentData()), float(self.ref.value()), self.speed.currentData()
        icom = self.page.ctl().rigx.icom()
        if icom is not None:
            try:
                icom.set_scope(span_hz=s.span_hz, ref_db=s.ref_db, speed=s.speed)
            except Exception:                       # noqa: BLE001 - the radio went; the next refresh says so
                pass

    def _clicked(self, hz: float):
        ctl = self.page.ctl()
        target, why = scope_mod.tune_target(hz, self.view.chans, ctl.settings.station.profile())
        if target is None:
            self.page.status(why)
            return
        if ctl.rigx.retune(target):
            self.page.status(self.tr("Tuned to %1; transmit disarmed.").replace("%1", fmt.mhz(target)))

    # -- on screen only --------------------------------------------------------------

    def showEvent(self, e):
        super().showEvent(e)
        self._attach()

    def hideEvent(self, e):
        self.feed.detach()
        self._icom = None
        super().hideEvent(e)

    def _attach(self) -> None:
        icom = self.page.ctl().rigx.icom()
        if icom is self._icom and self.feed.attached:
            return
        self.feed.detach()
        self.feed.clear()
        self._icom = icom
        if icom is None:
            return
        try:
            self.feed.attach(icom, self.page.settings().scope)
        except Exception:                           # noqa: BLE001 - the link went between the two calls
            self._icom = None

    def refresh(self):
        if not self.isVisible():
            return
        ctl = self.page.ctl()
        self._attach()
        if self._icom is None:
            msg = (self.tr("The spectrum needs an IC-9700 as the station's radio, connected.")
                   if ctl.settings.radio.kind not in ICOM_KINDS else self.tr("Waiting for the radio…"))
            self.state.setText(msg)
            self.view.show_rows([], [], {}, msg)
            return
        if self.feed.refused:
            msg = self.tr("This connection cannot carry the radio's spectrum: %1").replace("%1", self.feed.refused)
            self.state.setText(msg)
            self.view.show_rows([], [], {}, msg)
            return
        if self.feed.rev == self._rev:
            return
        self._rev = self.feed.rev
        rows = self.feed.rows()
        s = ctl.settings
        extra = ((s.dd.channel_hz, self.tr("DD")),)
        chans = scope_mod.channels(s.station.profile(), s.channel.frequency_hz, extra)
        busy = scope_mod.busy(rows[-1], chans, s.scope.busy_db) if rows else {}
        shown = scope_mod.in_view(rows[-1], chans) if rows else []
        self.view.show_rows(rows, shown, busy)
        n_busy = sum(1 for v in busy.values() if v)
        self.state.setText(self.tr("%1 sweeps; %2 of %3 packet channels in view look busy.")
                           .replace("%1", str(self.feed.rev)).replace("%2", str(n_busy)).replace("%3", str(len(shown))))


class PowerTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self._n = -1
        lay = QVBoxLayout(self)
        lay.addWidget(widgets.label(self.tr(
            "Adaptive power finds the lowest RF power that still works: it starts at your cap, steps down while "
            "the other station keeps acknowledging, and steps back up the moment frames need repeating. It never "
            "goes above the cap, logs every change, and stops if you change the power yourself. IC-9700 and "
            "IC-705."), wrap=True))
        self.enabled = QCheckBox(self.tr("Adapt transmit power (off until you switch it on)"))
        lay.addWidget(self.enabled)
        form = QFormLayout()
        self.cap, self.floor, self.step = QSpinBox(), QSpinBox(), QSpinBox()
        for w, lo, hi in ((self.cap, 1, 100), (self.floor, 0, 100), (self.step, 1, 50)):
            w.setRange(lo, hi)
            w.setSuffix(" %")
        form.addRow(self.tr("Never above (cap)"), self.cap)
        form.addRow(self.tr("Never below"), self.floor)
        form.addRow(self.tr("Step"), self.step)
        lay.addLayout(form)
        self.state = widgets.label("", "dim", wrap=True)
        lay.addWidget(self.state)
        self.history = tables.make([self.tr("Time (UTC)"), self.tr("Band"), self.tr("Power"), self.tr("Why"),
                                    self.tr("I frames"), self.tr("Repeats"), self.tr("T1"), self.tr("SNR")])
        lay.addWidget(self.history, 1)
        p = page.settings().power
        self.enabled.setChecked(bool(p.enabled))
        self.cap.setValue(int(p.cap_percent))
        self.floor.setValue(int(p.floor_percent))
        self.step.setValue(int(p.step_percent))
        self.enabled.toggled.connect(self._save)
        for w in (self.cap, self.floor, self.step):
            w.valueChanged.connect(lambda _v: self._save())

    def _save(self):
        p = self.page.settings().power
        p.enabled = self.enabled.isChecked()
        p.cap_percent, p.step_percent = self.cap.value(), self.step.value()
        p.floor_percent = min(self.floor.value(), self.cap.value())

    def _why(self, why: str) -> str:
        return {"start": self.tr("start (cap or the band's last good level)"), "good": self.tr("working: down"),
                "bad": self.tr("frames repeated: up"), "very-bad": self.tr("frames lost: up two steps"),
                "manual": self.tr("you changed the power: stopped"), "restored": self.tr("put back")}.get(why, why)

    def refresh(self):
        pc = self.page.ctl().rigx.power
        state = {"off": self.tr("Off."), "waiting": self.tr("On; waiting for the radio."),
                 "running": self.tr("On."), "unavailable": self.tr("Only an IC-9700 or IC-705 can do this."),
                 "manual": self.tr("Stopped for this session: the power was changed by hand."),
                 "test-step": self.tr("Not used while the real-radio test's minimum-power step is on (it holds "
                                      "the radio at its lowest power).")}.get(pc.state, pc.state)
        if pc.state == "running" and pc.adapter is not None:
            state += " " + self.tr("Now %1% on %2.").replace("%1", str(pc.adapter.level)).replace("%2", pc.band)
        self.state.setText(state)
        if len(pc.history) == self._n:
            return
        self._n = len(pc.history)
        tables.fill(self.history, [(fmt.utc_time(h["wall"]), h["band"], f"{h['percent']} %", self._why(h["why"]),
                                    "" if h["sent"] is None else h["sent"],
                                    "" if h["retried"] is None else h["retried"],
                                    "" if h["t1"] is None else h["t1"],
                                    "" if h["snr"] is None else fmt.number(h["snr"], 1))
                                   for h in reversed(pc.history)])


class SatTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self._updating: threading.Thread | None = None
        self._update_msg = ""
        self._passes_at = 0.0
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.bird = QComboBox()
        self.track = QCheckBox(self.tr("Track"))
        self.update_btn = QPushButton(self.tr("Update orbital elements"))
        self.update_btn.clicked.connect(self._update)
        row.addWidget(QLabel(self.tr("Satellite")))
        row.addWidget(self.bird, 1)
        row.addWidget(self.track)
        row.addWidget(self.update_btn)
        lay.addLayout(row)
        self.elements = widgets.label("", "dim", wrap=True)
        lay.addWidget(self.elements)
        form = QFormLayout()
        self.doppler = QCheckBox(self.tr("Correct Doppler on the IC-9700 during passes"))
        self.min_el = QDoubleSpinBox()
        self.min_el.setRange(0.0, 45.0)
        self.min_el.setSuffix(" °")
        form.addRow(self.doppler)
        form.addRow(self.tr("Transmit only above"), self.min_el)
        lay.addLayout(form)
        self.live = widgets.label("", None, wrap=True)
        lay.addWidget(self.live)
        buttons = QHBoxLayout()
        self.setup = QPushButton(self.tr("Set the station up for this satellite"))
        self.setup.clicked.connect(self._station_for)
        self.back = QPushButton(self.tr("Back to my channel"))
        self.back.clicked.connect(self._station_back)
        buttons.addWidget(self.setup)
        buttons.addWidget(self.back)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.passes = tables.make([self.tr("Rises (UTC)"), self.tr("Highest"), self.tr("Minutes"),
                                   self.tr("From"), self.tr("To")])
        lay.addWidget(self.passes, 1)
        lay.addWidget(widgets.label(self.tr(
            "The ISS digipeats APRS on 145.825 MHz via ARISS. Transmit is allowed only while the satellite is above "
            "the elevation set here, and is disarmed when it goes below. Orbital elements come from CelesTrak only "
            "when you press Update."), "dim", wrap=True))
        self._fill_birds()
        s = page.settings().sat
        self.doppler.setChecked(bool(s.doppler))
        self.min_el.setValue(float(s.min_elevation_deg))
        self.track.toggled.connect(lambda _on: self._save())
        self.bird.currentIndexChanged.connect(lambda _i: self._save())
        self.doppler.toggled.connect(lambda _on: self._save())
        self.min_el.valueChanged.connect(lambda _v: self._save())

    def _fill_birds(self):
        s = self.page.settings().sat
        self.bird.blockSignals(True)
        self.track.blockSignals(True)
        self.bird.clear()
        for norad, b in sorted(self.page.ctl().rigx.sat.birds().items()):
            self.bird.addItem(f"{b.name} ({norad})", norad)
        i = self.bird.findData(int(s.tracking)) if s.tracking else 0
        self.bird.setCurrentIndex(max(0, i))
        self.track.setChecked(bool(s.tracking))
        self.bird.blockSignals(False)
        self.track.blockSignals(False)

    def _save(self):
        s = self.page.settings().sat
        s.tracking = int(self.bird.currentData() or 0) if self.track.isChecked() else 0
        s.doppler = self.doppler.isChecked()
        s.min_elevation_deg = float(self.min_el.value())
        self._passes_at = 0.0

    def _update(self):
        if self._updating is not None and self._updating.is_alive():
            return
        store = self.page.ctl().rigx.sat.store
        self._update_msg = self.tr("Fetching orbital elements…")

        def go():
            from ...sat.tle import TleError
            try:
                n = store.update()
                self._update_msg = self.tr("%1 satellites' elements updated.").replace("%1", str(n))
            except TleError as e:
                self._update_msg = self.tr("The elements could not be updated: %1").replace("%1", str(e))
            self._passes_at = 0.0
        self._updating = threading.Thread(target=go, name="tle update", daemon=True)
        self._updating.start()

    def _station_for(self):
        w = self.page.window
        b = self.page.ctl().rigx.sat.birds().get(int(self.bird.currentData() or 0))
        if b is not None:
            w.apply_settings(self.page.ctl().rigx.sat.station_for(w.settings, b))
            self._fill_birds()

    def _station_back(self):
        w = self.page.window
        w.apply_settings(self.page.ctl().rigx.sat.station_back(w.settings))

    def refresh(self):
        tracker = self.page.ctl().rigx.sat
        bird = tracker.bird() or tracker.birds().get(int(self.bird.currentData() or 0))
        age = tracker.store.age_days(bird.norad) if bird is not None else None
        text = (self.tr("No orbital elements yet: press Update.") if age is None else
                self.tr("Elements %1 days old.").replace("%1", fmt.number(age, 1)))
        if self._update_msg:
            text += " " + self._update_msg
        self.elements.setText(text)
        self.back.setEnabled(bool(self.page.settings().sat.saved))
        sess = tracker.session
        if tracker.position() is None:
            self.live.setText(texts.reason("sat-no-locator"))
        elif sess is not None and sess.look is not None:
            look = sess.look
            line = self.tr("Azimuth %1°, elevation %2°, %3 km away.").replace("%1", fmt.number(look.az_deg, 0)) \
                .replace("%2", fmt.number(look.el_deg, 1)).replace("%3", fmt.number(look.range_km, 0))
            if sess.active and sess.doppler and sess.rx_hz:
                line += " " + self.tr("Doppler: receiving %1, sending %2.").replace("%1", fmt.mhz(sess.rx_hz)) \
                    .replace("%2", fmt.mhz(sess.tx_hz))
            why = sess.tx_refusal()
            line += " " + (texts.reason(why) if why else self.tr("Transmit allowed: in a pass."))
            if tracker.error:
                line += " " + texts.reason(tracker.error)
            self.live.setText(line)
        else:
            self.live.setText(self.tr("Not tracking."))
        if time.time() - self._passes_at > 60:
            self._passes_at = time.time()
            rows = []
            try:
                sat = tracker.store.find(bird.norad) if bird is not None else None
                pos = tracker.position()
                if sat is not None and pos is not None:
                    s = self.page.settings().sat
                    for p in sat.passes(time.time(), 24, pos[0], pos[1], s.altitude_m):
                        rows.append((fmt.utc_datetime(p.aos), f"{fmt.number(p.max_el_deg, 0)}°",
                                     fmt.number(p.duration_s / 60, 1), f"{fmt.number(p.aos_az_deg, 0)}°",
                                     f"{fmt.number(p.los_az_deg, 0)}°"))
            except Exception:                       # noqa: BLE001 - a decayed TLE; shown as no passes
                rows = []
            tables.fill(self.passes, rows)


class DdTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        warn = widgets.card()
        wl = QVBoxLayout(warn)
        wl.addWidget(widgets.label(self.tr(
            "<b>Experimental.</b> In DD the IC-9700 sends to air every network frame that reaches its LAN port. "
            "Connect it to this computer by its own cable or second network port (or the controller's Radio "
            "port), with a fixed address on a private subnet, never to your home network. Packet then runs between "
            "the two computers at 128 kbit/s."), "warn", wrap=True))
        lay.addWidget(warn)
        form = QFormLayout()
        self.channel = QComboBox()
        from shack_packet.radios.dd import UK_DD_CHANNELS_HZ
        for hz in UK_DD_CHANNELS_HZ:
            self.channel.addItem(fmt.mhz(hz), hz)
        self.local, self.peer = QLineEdit(), QLineEdit()
        self.power = QSpinBox()
        self.power.setRange(-1, 100)
        self.power.setSpecialValueText(self.tr("Leave as it is"))
        self.power.setSuffix(" %")
        form.addRow(self.tr("Channel"), self.channel)
        form.addRow(self.tr("This computer's DD address"), self.local)
        form.addRow(self.tr("The other station's DD address"), self.peer)
        form.addRow(self.tr("RF power in DD"), self.power)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.go = QPushButton()
        self.go.clicked.connect(self._toggle)
        row.addWidget(self.go)
        row.addStretch(1)
        lay.addLayout(row)
        self.state = widgets.label("", None, wrap=True)
        lay.addWidget(self.state)
        self.steps = widgets.label("", "dim", wrap=True)
        lay.addWidget(self.steps)
        lay.addStretch(1)
        d = page.settings().dd
        self.channel.setCurrentIndex(max(0, self.channel.findData(int(d.channel_hz))))
        self.local.setText(d.local)
        self.peer.setText(d.peer)
        self.power.setValue(-1 if d.power_percent is None else int(d.power_percent))

    def _save(self):
        d = self.page.settings().dd
        d.channel_hz = int(self.channel.currentData())
        d.local, d.peer = self.local.text().strip(), self.peer.text().strip()
        d.power_percent = None if self.power.value() < 0 else int(self.power.value())

    def _toggle(self):
        w = self.page.window
        self._save()
        new = w.settings.copy()
        if new.radio.kind == DD_KIND:
            new.radio.kind = new.dd.previous_kind or "ic-9700"
            new.dd.previous_kind = ""
        else:
            new.dd.previous_kind = new.radio.kind
            new.radio.kind = DD_KIND
        w.apply_settings(new)

    def refresh(self):
        ctl = self.page.ctl()
        kind = ctl.settings.radio.kind
        on = kind == DD_KIND
        self.go.setText(self.tr("Stop DD") if on else self.tr("Use DD"))
        self.go.setEnabled(on or kind == "ic-9700")
        for w in (self.channel, self.local, self.peer, self.power):
            w.setEnabled(not on)
        if not on:
            self.state.setText(self.tr("Off.") if kind == "ic-9700" else
                               self.tr("DD needs an IC-9700 as the station's radio."))
            self.steps.setText("")
            return
        link = ctl.link
        st = ctl.link_state
        if st == "open" and link is not None:
            stats = getattr(link, "stats", {})
            self.state.setText(self.tr("DD on %1: %2 frames sent, %3 received.")
                               .replace("%1", fmt.mhz(ctl.settings.dd.channel_hz))
                               .replace("%2", str(stats.get("sent", 0))).replace("%3", str(stats.get("received", 0))))
            self.steps.setText(self.tr(
                "Now press TRANSMIT on the IC-9700 so it may transmit in DD (it starts with TX INHIBIT on, and the "
                "app cannot change that). Press it again before you stop, and the app puts the radio back as it "
                "was."))
        elif st == "failed":
            self.state.setText(texts.reason(ctl.link_error) + (f" ({ctl.link_error_detail})"
                                                               if ctl.link_error_detail else ""))
            self.steps.setText("")
        else:
            self.state.setText(self.tr("Switching the radio to DD…"))


class RigPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.spectrum = SpectrumTab(self)
        self.power = PowerTab(self)
        self.sats = SatTab(self)
        self.dd = DdTab(self)
        self.tabs.addTab(self.spectrum, self.tr("Spectrum"))
        self.tabs.addTab(self.power, self.tr("Power"))
        self.tabs.addTab(self.sats, self.tr("Satellites"))
        self.tabs.addTab(self.dd, self.tr("23 cm DD"))
        lay.addWidget(self.tabs, 1)
        self.message = widgets.label("", "dim", wrap=True)
        lay.addWidget(self.message)

    def ctl(self):
        return self.window.ctl

    def settings(self):
        return self.window.settings

    def status(self, text: str) -> None:
        self.message.setText(text)

    def reset(self):
        """New settings (window.apply_settings): the tabs read them again."""
        self.spectrum.feed.detach()
        self.spectrum._icom = None
        self.spectrum._load()
        self.sats._fill_birds()

    def refresh(self):
        self.tabs.currentWidget().refresh()
