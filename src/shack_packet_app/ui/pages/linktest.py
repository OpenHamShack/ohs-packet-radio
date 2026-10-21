"""Link test: a run with another station by callsign, both directions by
itself (linkrun.py says how and why; 0.3.0 replaced the session, role
and Join of 0.2.1).

Left: the other station's callsign, the test size, Start and Stop, and
an Advanced fold with the Pluto's mode or adaptation and window (the
same Settings.fast that Settings › Fast modes shows, so the two never
disagree; Use rebuilds the station, as Settings does, and the band-plan
verdict is said beside it) and the 0.2.1 relay on a controller instead
of openhamshack.com. Under them, what the operator has to do now, when
anything: arm to send, or (a Pluto) move the antenna and say Done.
Right, live: both directions side by side from both stations' words,
this end's link as it stands, and the other station's receiver from its
telemetry. A summary when the run ends, and Save report.
"""
from __future__ import annotations

import threading
from urllib.parse import urlparse

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ... import fastmodes, linkrun
from .. import texts
from ..widgets import Fold, label

RELAY_PORT = 30480
#: The relay on Brian's controller box (ohs-box, tailnet only), offered
#: under Advanced for a station not paired with a controller: the one the
#: 19/10/2026 on-air test used. A paired controller's own relay comes first.
DEFAULT_RELAY = "ohs-box:30480"
#: The mode row's "adapt" entry: link adaptation over Settings.fast's
#: top-to-floor ladder.
ADAPT = "adaptive"
#: The rows of the side-by-side table, as (key, what the cell shows).
ROWS = ("state", "sent", "time", "rate", "check", "repeats")


def relay_from_controller(url: str) -> str:
    """The relay on the paired controller: its host, port 30480."""
    host = urlparse(url or "").hostname
    return f"{host}:{RELAY_PORT}" if host else ""


class LinkTestPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        box = QGroupBox(self.tr("Link test with another station"))
        form = QFormLayout(box)
        form.addRow(label(self.tr(
            "Both stations type each other's callsign and press Start; they find each other through "
            "openhamshack.com and test the link in both directions, one after the other. The station "
            "receiving never transmits: its acknowledgements go back over the internet. Whoever is sending "
            "arms transmit as usual."), "dim", wrap=True))
        self.peer = QLineEdit()
        self.peer.setPlaceholderText(self.tr("callsign-SSID, e.g. GM0OTB-1"))
        form.addRow(self.tr("Other station's callsign"), self.peer)
        self.kb = QSpinBox()
        self.kb.setRange(1, linkrun.MAX_KB)
        self.kb.setSuffix(self.tr(" KB"))
        self.kb.setToolTip(self.tr("Sent each way. 5 KB takes about a minute at 1200 baud."))
        form.addRow(self.tr("Test size"), self.kb)
        buttons = QHBoxLayout()
        self.start_btn = QPushButton(self.tr("Start"))
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton(self.tr("Stop"))
        self.stop_btn.clicked.connect(lambda: self.window.ctl.linktest.stop_run())
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addStretch(1)
        form.addRow(buttons)
        # What the operator must do now (arm; a Pluto's antenna).
        self.todo = label("", "heading", wrap=True)
        self.done_btn = QPushButton(self.tr("Done - the antenna is moved"))
        self.done_btn.clicked.connect(lambda: self.window.ctl.linktest.run and
                                      self.window.ctl.linktest.run.confirm_antenna())
        form.addRow(self.todo)
        form.addRow(self.done_btn)

        self.advanced = Fold(self.tr("Advanced"))
        af = QFormLayout(self.advanced.body)
        af.setContentsMargins(0, 0, 0, 0)
        modes_row = QHBoxLayout()
        self.modes = QComboBox()
        for m in fastmodes.MODES:
            self.modes.addItem(texts.fast_mode(m), m)
        self.modes.addItem(self.tr("Link adaptation (the ladder in Settings › Fast modes; both stations)"), ADAPT)
        self.window_k = QSpinBox()
        self.window_k.setRange(1, 63)
        self.window_k.setPrefix(self.tr("k "))
        self.window_k.setToolTip(self.tr("The window: frames sent before an acknowledgement is needed"))
        self.apply_modes = QPushButton(self.tr("Use"))
        self.apply_modes.setToolTip(self.tr("Rebuild the station with this mode (Pluto only; disarms)"))
        self.apply_modes.clicked.connect(self.use_modes)
        modes_row.addWidget(self.modes, 1)
        modes_row.addWidget(self.window_k)
        modes_row.addWidget(self.apply_modes)
        af.addRow(self.tr("SDR mode"), modes_row)
        self.modes_now = label("", "dim", wrap=True)
        af.addRow("", self.modes_now)
        self.plan_now = label("", wrap=True)
        af.addRow("", self.plan_now)
        self.use_relay = QCheckBox(self.tr("Use a relay on an OpenHamShack controller instead of openhamshack.com"))
        af.addRow(self.use_relay)
        relay_row = QHBoxLayout()
        self.relay = QLineEdit()
        self.relay.setPlaceholderText(self.tr("host:port, e.g. ohs-box:30480"))
        self.find = QPushButton(self.tr("Find"))
        self.find.setToolTip(self.tr("Look for an OpenHamShack controller on this network"))
        self.find.clicked.connect(self.find_relay)
        relay_row.addWidget(self.relay, 1)
        relay_row.addWidget(self.find)
        af.addRow(self.tr("Relay"), relay_row)
        form.addRow(self.advanced)
        left.addWidget(box)
        left.addStretch(1)
        outer.addLayout(left, 2)

        right = QVBoxLayout()
        res = QGroupBox(self.tr("Results"))
        rl = QVBoxLayout(res)
        self.state = label("", "heading", wrap=True)
        rl.addWidget(self.state)
        self.table = QTableWidget(len(ROWS), 2)
        self.table.setVerticalHeaderLabels([self.tr("State"), self.tr("Acknowledged"), self.tr("Time"),
                                            self.tr("Rate"), self.tr("Received"), self.tr("Repeats")])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        from PyQt6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(200)
        rl.addWidget(self.table)
        self.summary = label("", wrap=True)
        rl.addWidget(self.summary)
        self.save_btn = QPushButton(self.tr("Save report…"))
        self.save_btn.clicked.connect(self.save_report)
        rl.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        right.addWidget(res)
        ours = QGroupBox(self.tr("This end"))
        ol = QVBoxLayout(ours)
        self.ours = label("", wrap=True)
        ol.addWidget(self.ours)
        right.addWidget(ours)
        theirs = QGroupBox(self.tr("The other station's receiver (live, from its telemetry)"))
        tl = QVBoxLayout(theirs)
        self.theirs = label("", wrap=True)
        self.frames = QPlainTextEdit()
        self.frames.setReadOnly(True)
        self.frames.setMaximumBlockCount(400)
        tl.addWidget(self.theirs)
        tl.addWidget(self.frames, 1)
        right.addWidget(theirs, 1)
        outer.addLayout(right, 3)
        self._last_frame = None
        self.reset()

    # -- settings in and out --------------------------------------------------------

    def reset(self):
        """Fill the form from the settings (and the paired controller)."""
        s = self.window.settings
        ls = s.linktest
        self.peer.setText(ls.peer)
        self.kb.setValue(max(1, min(linkrun.MAX_KB, int(ls.kb or 5))))
        self.use_relay.setChecked(ls.via == "relay")
        self.relay.setText(ls.relay or relay_from_controller(s.controller.url) or DEFAULT_RELAY)
        self.modes.setCurrentIndex(max(0, self.modes.findData(ADAPT if s.fast.adapt else s.fast.mode)))
        self.window_k.setValue(int(s.fast.window_for()))
        if ls.via == "relay":
            self.advanced.set_open(True)

    def _store(self):
        ls = self.window.settings.linktest
        ls.peer = self.peer.text().strip().upper()
        ls.kb = int(self.kb.value())
        ls.via = "relay" if self.use_relay.isChecked() else "web"
        ls.relay = self.relay.text().strip()
        self.window.save_settings()
        return ls

    # -- commands --------------------------------------------------------------------------

    def find_relay(self):
        """Look for a controller (up to three seconds, off the GUI thread)."""
        self.find.setEnabled(False)
        found: list = []

        def go():
            try:
                found.extend(self.window.ctl.ohs.discover(3.0))
            except Exception:                       # noqa: BLE001 - no zeroconf, no network: nothing found
                pass
        t = threading.Thread(target=go, daemon=True)
        t.start()
        from PyQt6.QtCore import QTimer

        def check():
            if t.is_alive():
                QTimer.singleShot(200, check)
                return
            self.find.setEnabled(True)
            relays = [relay_from_controller(c.url) for c in found if relay_from_controller(c.url)]
            if relays:
                self.relay.setText(relays[0])
            else:
                QMessageBox.information(self, self.tr("Find"), self.tr(
                    "No OpenHamShack controller answered on this network. Type the relay's address."))
        QTimer.singleShot(200, check)

    def start(self):
        ls = self._store()
        self.frames.clear()
        self._last_frame = None
        ok, why, detail = self.window.ctl.linktest.start_run(ls)
        if not ok:
            QMessageBox.warning(self, self.tr("Link test"), texts.reason(why) + (f"\n\n{detail}" if detail else ""))

    def save_report(self, path: str | None = None):
        run = self.window.ctl.linktest.run
        if run is None:
            return
        rep = run.report()
        if path is None:
            name = f"linktest-{rep['call']}-{rep['peer']}-{linkrun.time.strftime('%Y%m%d-%H%M')}.txt"
            path, _ = QFileDialog.getSaveFileName(self, self.tr("Save report"), name, self.tr("Text (*.txt)"))
            if not path:
                return
        with open(path, "w", encoding="utf-8") as f:
            f.write(linkrun.report_text(rep))

    def use_modes(self):
        """The chosen mode (or adaptation) and window into Settings.fast,
        and the station rebuilt with them, disarmed - as Settings does."""
        new = self.window.settings.copy()
        choice = self.modes.currentData()
        new.fast.adapt = choice == ADAPT
        if choice != ADAPT:
            new.fast.mode = choice
        # The window as shown: left automatic (fastmodes.default_window)
        # when it is what this mode would get anyway, else set by hand.
        k = int(self.window_k.value())
        new.fast.window_auto = True
        if k != new.fast.window_for():
            new.fast.window_auto, new.fast.window = False, k
        if new.fast == self.window.settings.fast:
            return
        self.window.apply_settings(new)

    # -- live --------------------------------------------------------------------------------

    def refresh(self):
        ctl = self.window.ctl
        lt = ctl.linktest
        v = lt.view()
        run = lt.run
        rv = run.view() if run is not None else None
        running = lt.running
        for w in (self.peer, self.kb, self.relay, self.use_relay, self.find, self.modes, self.window_k,
                  self.apply_modes):
            w.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.save_btn.setEnabled(run is not None)
        self._modes_now(ctl.settings)
        self._todo(rv)
        self.state.setText(self._state_text(rv, v))
        bad = rv is not None and rv["phase"] == "failed" or v["state"] == "lost"
        self.state.setObjectName("bad" if bad else "heading")
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self._table(rv)
        self.summary.setText(self._summary(rv))
        self.ours.setText(self._ours(v))
        self.theirs.setText(self._theirs(v))
        frames = v.get("peer_frames") or []
        new = [f for f in frames if self._last_frame is None or f.get("wall", 0) > self._last_frame]
        for f in new:
            self.frames.appendPlainText(_frame_line(f))
        if frames:
            self._last_frame = frames[-1].get("wall", 0)

    def _todo(self, rv) -> None:
        """What this operator has to do now, if anything."""
        text, done = "", False
        if rv is not None and rv["phase"] == "antenna":
            if rv["antenna_wanted"]:
                port = self.tr("TX") if self._sending(rv, rv["antenna_wanted"]) else self.tr("RX")
                text = self.tr("Put the antenna on the Pluto's %1 port, then press Done.").replace("%1", port)
                done = True
            elif rv["peer_move"]:
                text = self.tr("Waiting for %1 to move its antenna.").replace("%1", rv["peer"])
        elif rv is not None and rv["phase"] == "half" and rv["role"] == "rf-sender" and not rv["armed"]:
            text = self.tr("Your turn to send: arm transmit.")
        self.todo.setText(text)
        self.todo.setVisible(bool(text))
        self.done_btn.setVisible(done)

    @staticmethod
    def _sending(rv, half: int) -> bool:
        return (half == 1) == (rv["call"] == rv["first"])

    def _state_text(self, rv, v) -> str:
        if rv is None:
            return self.tr("Not started. Type the other station's callsign and press Start; it does the same "
                           "with yours.")
        p, peer = rv["phase"], rv["peer"]
        if p == "pairing":
            text = self.tr("Waiting for %1 to start the test with your callsign…").replace("%1", peer)
        elif p == "antenna":
            text = self.tr("Getting ready for %1").replace("%1", self._direction(rv, max(1, rv["half"] + 1)
                                                                               if rv["half"] else 1))
        elif p == "half":
            what = self.tr("sending") if rv["role"] == "rf-sender" else self.tr("receiving (never transmits)")
            text = self.tr("%1: this station is %2").replace("%1", self._direction(rv, rv["half"])).replace("%2", what)
        elif p == "done":
            text = self.tr("Finished: both directions tested.")
        elif p == "stopped":
            text = self.tr("Stopped.")
        else:
            text = self.tr("The test ended early: %1").replace("%1", texts.reason(rv["why"]) or rv["why"])
            if rv["detail"]:
                text += f" ({rv['detail']})"
        if v["state"] == "lost":
            text += " " + texts.linktest_state("lost")
        return text

    def _direction(self, rv, half: int) -> str:
        a, b = (rv["call"], rv["peer"]) if self._sending(rv, half) else (rv["peer"], rv["call"])
        return f"{a} → {b}"

    def _table(self, rv) -> None:
        results = rv["results"] if rv else []
        heads = [f"{h['from']} → {h['to']}" for h in results] or [self.tr("First direction"),
                                                                     self.tr("Second direction")]
        self.table.setHorizontalHeaderLabels(heads)
        for col in range(2):
            h = results[col] if col < len(results) else {}
            cells = _cells(self, rv, h)
            for row, key in enumerate(ROWS):
                text = cells.get(key, "")
                item = self.table.item(row, col)
                if item is None:
                    self.table.setItem(row, col, QTableWidgetItem(text))
                elif item.text() != text:
                    item.setText(text)

    def _summary(self, rv) -> str:
        if rv is None or rv["phase"] in ("pairing", "antenna", "half"):
            return ""
        parts = []
        for h in rv["results"]:
            s, g = h.get("sent") or {}, h.get("received") or {}
            if g.get("ok") is True:
                parts.append(self.tr("%1 → %2: %3 bytes received intact at %4 bit/s").replace("%1", h["from"]).replace(
                    "%2", h["to"]).replace("%3", str(g.get("bytes"))).replace("%4", str(s.get("bps", "?"))))
            elif s or g:
                parts.append(self.tr("%1 → %2: incomplete").replace("%1", h["from"]).replace("%2", h["to"]))
            else:
                parts.append(self.tr("%1 → %2: not run").replace("%1", h["from"]).replace("%2", h["to"]))
        if rv["notes"]:
            parts.append(self.tr("Changes during the run, listed in the report: %1").replace(
                "%1", str(len(rv["notes"]))))
        return "\n".join(parts)

    def _modes_now(self, s):
        """What the station runs now, and the band plan's word on it."""
        if s.radio.kind != "pluto":
            self.modes_now.setText(self.tr("The mode applies to a Pluto; this station's radio is not one."))
            self.plan_now.setText("")
            return
        fs = s.fast.for_frequency(s.channel.frequency_hz)
        try:
            now = (" > ".join(fs.ladder()) if fs.adapt else fs.mode)
        except Exception:                           # noqa: BLE001 - a ladder the library refuses: said below
            now = "?"
        self.modes_now.setText(self.tr("In use: %1, window %2").replace("%1", now).replace("%2", str(fs.window)))
        from ..fastpanel import plan_verdict
        text, name, _check = plan_verdict(fs, s.channel.frequency_hz)
        if self.plan_now.text() != text or self.plan_now.objectName() != name:
            self.plan_now.setText(text)
            self.plan_now.setObjectName(name)
            self.plan_now.setStyleSheet("")

    def _ours(self, v) -> str:
        if v["state"] == "idle" and not v.get("link"):
            return self.tr("Nothing connected.")
        lines = [self.tr("%1 as %2 with %3, relay %4").replace("%1", v["session"]).replace(
            "%2", texts.linktest_role(v["role"])).replace("%3", v["peer"]).replace("%4", v["relay"])]
        clock = v.get("clock")
        if clock:
            lines.append(self.tr("Relay round trip %1 ms; our clock %2 ms from the relay's").replace(
                "%1", f"{clock['rtt_s'] * 1000:.0f}").replace("%2", f"{-clock['offset_s'] * 1000:+.0f}"))
        e = v.get("engine") or {}
        lines.append(self.tr("Frames by internet: %1 sent, %2 received, %3 refused").replace(
            "%1", str(e.get("rp_sent", 0))).replace("%2", str(e.get("rp_heard", 0))).replace(
            "%3", str(e.get("rp_refused", 0))))
        link = v.get("link")
        if link:
            lines.append(self.tr("Link %1: V(S) %2 V(A) %3 V(R) %4, SRT %5 s, T1 %6 s, T1 expiries %7, "
                                 "retransmitted %8, REJ %9").replace("%1", link["state"]).replace(
                "%2", str(link["vs"])).replace("%3", str(link["va"])).replace("%4", str(link["vr"])).replace(
                "%5", str(link["srt_s"])).replace("%6", str(link["t1v_s"])).replace(
                "%7", str(link["t1_expired"])).replace("%8", str(link["retransmitted"])).replace(
                "%9", str(link["rej"] + link["srej"])) + (f", {link['tx_mode']}" if link.get("tx_mode") else ""))
        t = v.get("transfer")
        if t:
            lines.append(self.tr("Sent %1 of %2 bytes acknowledged in %3 s, %4 bit/s%5").replace(
                "%1", str(t["acked"])).replace("%2", str(t["size"])).replace("%3", str(t["seconds"])).replace(
                "%4", str(t["bps"])).replace("%5", self.tr(" - done") if t["done"] else ""))
        r = v.get("received")
        if r:
            ok = {True: self.tr("CRC OK"), False: self.tr("CRC WRONG"), None: ""}[r.get("ok")]
            lines.append(self.tr("Received %1 bytes %2").replace("%1", str(r.get("bytes", 0))).replace("%2", ok))
        if v.get("log"):
            lines.append(self.tr("Kept in %1").replace("%1", v["log"]))
        return "\n".join(lines)

    def _theirs(self, v) -> str:
        rx = v.get("peer_rx")
        if not rx:
            return self.tr("Nothing from the other station yet.")
        recv = rx.get("receivers") or {}
        # Frames its engine took (a link that carries frames has no
        # demodulator of ours to count them); FCS failures from its
        # demodulators, when it has any.
        frames = (rx.get("engine") or {}).get("heard", 0)
        bad = sum((r.get("bad_fcs") or 0) for r in recv.values())
        by_mode = ", ".join(f"{m} {n}" for m, n in (rx.get("heard_by_mode") or {}).items()) or "-"
        level = rx.get("level_db")
        lines = [self.tr("Frames heard %1 (by mode: %2); FCS failures %3 (noise counts too); level %4 dB").replace(
            "%1", str(frames)).replace("%2", by_mode).replace("%3", str(bad)).replace(
            "%4", "-" if level is None else f"{level:.1f}")]
        ad = v.get("peer_adapt")
        if ad:
            lines.append(self.tr("It asks for %1 (%2)").replace("%1", str(ad.get("request"))).replace(
                "%2", str(ad.get("why"))))
        env = v.get("peer_env") or {}
        radio = env.get("radio") or {}
        if env:
            lines.append(f"{env.get('app', '')} · {env.get('os', '')} · {radio.get('hw_model', radio.get('kind', ''))} "
                         f"{radio.get('fw_version', '')}")
        return "\n".join(lines)


def _cells(page, rv, h: dict) -> dict:
    """One column of the side-by-side table: the sender's and the
    receiver's word on one direction."""
    s, g = (h.get("sent") or {}), (h.get("received") or {})
    tr = page.tr
    if not h:
        return {}
    live = rv is not None and rv["phase"] == "half" and rv["half"] == h["half"]
    out = {}
    if s.get("done") and g.get("ok") is True:
        out["state"] = tr("OK")
    elif s or g:
        out["state"] = tr("Incomplete") + (f" ({s['ended']})" if s.get("ended") else "")
    else:
        out["state"] = tr("Running…") if live else tr("Not yet")
    if s:
        out["sent"] = f"{s.get('acked')} / {s.get('size')} B"
        out["time"] = f"{s.get('seconds')} s"
        out["rate"] = f"{s.get('bps')} bit/s"
        out["repeats"] = tr("%1 frames, T1 %2×, REJ %3").replace("%1", str(s.get("retransmitted", 0))).replace(
            "%2", str(s.get("t1_expired", 0))).replace("%3", str(s.get("rej", 0)))
    if g:
        ok = {True: tr("CRC OK"), False: tr("CRC WRONG"), None: tr("incomplete")}.get(g.get("ok"), "")
        out["check"] = f"{g.get('bytes')} B, {ok}"
    return out


def _frame_line(f: dict) -> str:
    def n(x, fmt):
        return "-" if x is None else format(x, fmt)
    return (f"{f.get('via', '?'):8} {f.get('mode') or '-':11} C/N0 {n(f.get('cn0_dbhz'), '.1f'):>5} "
            f"SNR {n(f.get('snr_db'), '.1f'):>5}  {f.get('frame', '')}")
