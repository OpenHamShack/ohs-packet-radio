"""SDR monitor: one receive-only SDR watching every packet channel at once -
a waterfall of the capture with the channels marked, each channel's
level and activity, and who was heard, who behaves like a node, and who
talks to whom.

The SDR never transmits and is not the station's radio: what it hears
goes into the Monitor list and (if chosen) the APRS heard list and map,
never into anything that answers (sdrmon.py).
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSplitter,
                             QTabWidget, QVBoxLayout, QWidget)

from .. import fmt, tables, texts, theme

LO_DB, HI_DB = -75.0, -25.0


def _palette() -> np.ndarray:
    """Dark blue through cyan and amber to white: 256 RGB32 entries."""
    stops = [(0.0, (8, 12, 30)), (0.35, (20, 70, 140)), (0.6, (60, 200, 220)), (0.8, (255, 181, 71)),
             (1.0, (255, 255, 255))]
    x = np.linspace(0, 1, 256)
    out = np.zeros((256, 3))
    for i in range(3):
        out[:, i] = np.interp(x, [s[0] for s in stops], [s[1][i] for s in stops])
    rgb = out.astype(np.uint32)
    return (0xFF000000 | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]).astype(np.uint32)


class Waterfall(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: QImage | None = None
        self.freqs = None
        self.channels: list = []
        self._lut = _palette()
        self._buf = None
        self.setMinimumHeight(180)

    def set_rows(self, rows, freqs, channels) -> None:
        self.freqs = freqs
        self.channels = channels
        if not rows:
            self.image = None
            self.update()
            return
        db = np.stack([r.db for r in rows])[::-1]           # newest at the top
        idx = np.clip((db - LO_DB) / (HI_DB - LO_DB) * 255, 0, 255).astype(np.uint8)
        self._buf = np.ascontiguousarray(self._lut[idx])
        h, w = self._buf.shape
        self.image = QImage(self._buf.data, w, h, 4 * w, QImage.Format.Format_RGB32)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.colour("surface"))
        if self.image is None or self.freqs is None or len(self.freqs) < 2:
            p.setPen(theme.colour("ink-dim"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.tr("No SDR running"))
            return
        fm = p.fontMetrics()
        layout = self.label_layout(fm)
        rows = max([3] + [row + 1 for _c, _x, row, _w in layout])
        line_h = fm.height() + 2
        top = rows * line_h + 2
        p.drawImage(QRectF(0, top, self.width(), self.height() - top), self.image)
        p.setPen(QPen(theme.colour("accent"), 1, Qt.PenStyle.DashLine))
        for c, x, row, _w in layout:
            p.drawLine(int(x), row * line_h + line_h, int(x), self.height())
            p.drawText(QRectF(x - 60, row * line_h, 120, line_h), Qt.AlignmentFlag.AlignCenter,
                       c.label or fmt.mhz(c.hz))

    def label_layout(self, fm, rows: int = 3) -> list:
        """(channel, x, row, width) for each channel's label: in the first
        row where it clears the label before it, with as many rows as that
        takes (three at least, six at most). Channels 12.5 kHz apart sit a
        few pixels apart across a 2.4 MHz capture, and three of them in a
        row (144.925, .9375, .950) overprinted each other when the labels
        only took turns between two rows; with the operator choosing the
        channels (0.2) and the band plan's longer names ("APRS 144.800"),
        three fixed rows were not always enough either."""
        if self.freqs is None or len(self.freqs) < 2:
            return []
        for n in range(rows, 7):
            out, clear = self._labels_in(fm, n)
            if clear:
                break
        return out

    def _labels_in(self, fm, rows: int) -> tuple[list, bool]:
        lo, hi = float(self.freqs[0]), float(self.freqs[-1])
        ends = [-1e9] * rows
        out, clear = [], True
        for c in sorted(self.channels, key=lambda c: c.hz):
            x = (c.hz - lo) / (hi - lo) * self.width()
            w = fm.horizontalAdvance(c.label or fmt.mhz(c.hz)) + 8
            row = next((r for r in range(rows) if ends[r] <= x - w / 2), None)
            if row is None:
                clear = False
                row = min(range(rows), key=lambda r: ends[r])
            ends[row] = x + w / 2
            out.append((c, x, row, w))
        return out, clear


class SdrPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._rev = -1
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QLabel()
        self.state = QLabel()
        self.state.setObjectName("dim")
        self.toggle = QPushButton()
        self.toggle.clicked.connect(self._toggle)
        self.settings_btn = QPushButton(self.tr("SDR settings…"))
        self.settings_btn.clicked.connect(lambda: window.open_settings("sdr"))
        top.addWidget(self.source)
        top.addWidget(self.state, 1)
        top.addWidget(self.settings_btn)
        top.addWidget(self.toggle)
        lay.addLayout(top)
        split = QSplitter(Qt.Orientation.Vertical)
        self.waterfall = Waterfall()
        split.addWidget(self.waterfall)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        self.channels = tables.make([self.tr("Channel"), self.tr("Frequency"), self.tr("Frames"), self.tr("FX.25"),
                                self.tr("Level"), self.tr("Noise")])
        bottom.addWidget(self.channels)
        tabs = QTabWidget()
        self.heard = tables.make([self.tr("Station"), self.tr("Last (UTC)"), self.tr("Frames"), self.tr("Heard via"),
                             self.tr("Channels")])
        self.nodes = tables.make([self.tr("Node"), self.tr("Alias"), self.tr("Why"), self.tr("Last (UTC)")])
        self.links = tables.make([self.tr("Station"), self.tr("Station"), self.tr("Kind"), self.tr("Frames each way")])
        tabs.addTab(self.heard, self.tr("Heard"))
        tabs.addTab(self.nodes, self.tr("Nodes"))
        tabs.addTab(self.links, self.tr("Links"))
        bottom.addWidget(tabs)
        split.addWidget(bottom)
        split.setSizes([300, 300])
        lay.addWidget(split, 1)

    def _toggle(self):
        ctl = self.window.ctl
        if ctl.sdr is not None and ctl.sdr.running:
            ctl.stop_sdr()
        else:
            if ctl.settings.sdr.source == "none":
                self.window.open_settings("sdr")
                return
            ctl.start_sdr()

    def refresh(self):
        ctl = self.window.ctl
        s = ctl.settings.sdr
        running = ctl.sdr is not None and ctl.sdr.running
        self.source.setText(texts.sdr_source(s.source))
        self.toggle.setText(self.tr("Stop") if running else self.tr("Start"))
        if ctl.sdr is not None and ctl.sdr.error and not running:
            self.state.setText(texts.reason(ctl.sdr.error))
        elif running:
            st = ctl.sdr.runner.status() if ctl.sdr.runner is not None else {}
            self.state.setText(self.tr("Receiving at %1 MS/s; %2 samples lost").replace(
                "%1", fmt.number(ctl.sdr.runner.source.rate / 1e6, 2)).replace("%2", str(st.get("droppedSamples", 0))))
        else:
            self.state.setText(self.tr("Off"))
        mon = ctl.sdr.monitor if ctl.sdr is not None else None
        if mon is None:
            self.waterfall.set_rows([], None, [])
            return
        if mon.rev == self._rev:
            return
        self._rev = mon.rev
        plan = list(mon.plan)
        with mon.lock:
            rows = list(mon.spectrum)
            freqs = mon.spectrum_freqs
            chans = [(c, mon.channels[c.id]) for c in plan]
            heard = list(mon.heard.values())[-200:]
            nodes = list(mon.nodes.values())
            links = list(mon.links.values())
        self.waterfall.set_rows(rows, freqs, plan)
        tables.fill(self.channels, [(c.label or c.id, fmt.mhz(c.hz), st.frames, st.fx25,
                               "" if st.level_db is None else fmt.number(st.level_db, 1),
                               "" if st.floor_db is None else fmt.number(st.floor_db, 1)) for c, st in chans])
        names = {c.id: (c.label or c.id) for c in plan}
        tables.fill(self.heard, [(h.call, fmt.utc_time(h.last), h.frames,
                            self.tr("direct") if h.direct and not h.via else ", ".join(h.via) or self.tr("direct"),
                            ", ".join(names.get(k, k) for k in h.channels)) for h in reversed(heard)])
        tables.fill(self.nodes, [(n.call, n.alias, ", ".join(texts.role(r) for r in sorted(n.roles)), fmt.utc_time(n.last))
                           for n in nodes])
        tables.fill(self.links, [(k.a, k.b, k.kind, f"{k.a_to_b} / {k.b_to_a}") for k in links])
