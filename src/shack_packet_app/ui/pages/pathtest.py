"""Path test: which fast modes the path to another station carries, one
direction at a time (pathrun.py has the design and why it never changes
the radio's settings itself).

The cable notice is at the top and never scrolls or hides: with one
antenna and no T/R relay, the antenna is moved by hand between the
Pluto's TX and RX ports, and the one mistake that breaks a receiver - a
plain tee joining both - is a mistake made at the bench with this page
open. Both roles share one mode list, so the two operators tick the same
modes and the receiver's table has a row for a mode that never arrived.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

from shack_packet import pathtest

from ...pathrun import COUNT_RANGE, FAST_MODES, GAP_RANGE_S
from .. import fmt, tables, texts, widgets


class PathTestPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        notice = widgets.card()
        nl = QVBoxLayout(notice)
        self.notice = widgets.label(self.tr(
            "<b>One direction at a time, and the cable moved by hand.</b> To SEND, the antenna goes on the "
            "Pluto's TX port. To RECEIVE, it goes on the RX port. Never join the two ports with a plain tee: it "
            "puts the transmit power straight into the receiver. Test one direction, move the cables, then swap "
            "roles for the other."), "warn", wrap=True)
        nl.addWidget(self.notice)
        lay.addWidget(notice)
        self.ready = widgets.label("", "bad", wrap=True)
        lay.addWidget(self.ready)

        top = QHBoxLayout()
        role_box = QGroupBox(self.tr("This station"))
        rl = QVBoxLayout(role_box)
        self.send_role = QRadioButton(self.tr("Sends (antenna on the TX port)"))
        self.recv_role = QRadioButton(self.tr("Receives (antenna on the RX port)"))
        self.recv_role.setChecked(True)
        self.roles = QButtonGroup(self)
        for b in (self.send_role, self.recv_role):
            self.roles.addButton(b)
            rl.addWidget(b)
            b.toggled.connect(lambda _on: self._buttons())
        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(*COUNT_RANGE)
        self.count.setValue(pathtest.COUNT)
        self.gap = QDoubleSpinBox()
        self.gap.setRange(*GAP_RANGE_S)
        self.gap.setDecimals(1)
        self.gap.setSingleStep(0.5)
        self.gap.setValue(pathtest.GAP_S)
        self.gap.setSuffix(self.tr(" s"))
        form.addRow(self.tr("Frames per mode"), self.count)
        form.addRow(self.tr("Gap between frames"), self.gap)
        rl.addLayout(form)
        buttons = QHBoxLayout()
        self.start = QPushButton()
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.start_test)
        self.stop = QPushButton(self.tr("Stop"))
        self.stop.clicked.connect(self.stop_test)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        rl.addLayout(buttons)
        self.state = widgets.label("", "dim", wrap=True)
        rl.addWidget(self.state)
        rl.addStretch(1)
        top.addWidget(role_box, 2)

        modes_box = QGroupBox(self.tr("Modes (tick the same on both stations)"))
        ml = QGridLayout(modes_box)
        self.mode_boxes: dict[str, QCheckBox] = {}
        self.mode_why: dict[str, QLabel] = {}
        for i, m in enumerate(FAST_MODES):
            cb = QCheckBox(m)
            why = widgets.label("", "dim", wrap=True)
            self.mode_boxes[m] = cb
            self.mode_why[m] = why
            ml.addWidget(cb, i, 0)
            ml.addWidget(why, i, 1)
        ml.setColumnStretch(1, 1)
        top.addWidget(modes_box, 3)
        lay.addLayout(top)

        results = QGroupBox(self.tr("Heard here"))
        res = QVBoxLayout(results)
        self.table = tables.make([self.tr("Mode"), self.tr("kbit/s"), self.tr("Heard"), self.tr("Missing"),
                                  self.tr("%"), self.tr("Level (C/N0 dB-Hz)"), self.tr("SNR (dB)"),
                                  self.tr("Offset (Hz)"), self.tr("Bad FCS")])
        res.addWidget(self.table, 1)
        self.best = widgets.label("", wrap=True)
        res.addWidget(self.best)
        actions = QHBoxLayout()
        self.save = QPushButton(self.tr("Save report…"))
        self.save.clicked.connect(lambda: self.save_report())
        self.copy = QPushButton(self.tr("Copy report"))
        self.copy.clicked.connect(self.copy_report)
        self.use = QPushButton(self.tr("Use for adaptation"))
        self.use.clicked.connect(self.use_for_adaptation)
        for b in (self.save, self.copy, self.use):
            actions.addWidget(b)
        actions.addStretch(1)
        res.addLayout(actions)
        self.use_note = widgets.label(self.tr(
            "Use for adaptation saves the best mode heard here as where this station starts: its first request "
            "for the direction it received in is that mode, rather than the top of the ladder, and it adapts "
            "from there. The report goes into Help › Save diagnostics too."), "dim", wrap=True)
        res.addWidget(self.use_note)
        lay.addWidget(results, 1)
        self._choices_for = None
        self.refresh()

    # -- words ---------------------------------------------------------------------

    def _why(self, key: str, detail: str = "") -> str:
        words = {
            "not-started": self.tr("The station is not running."),
            "not-pluto": self.tr("The path test needs a Pluto as this station's radio: choose it in Station › "
                                 "Settings."),
            "not-adaptive": self.tr("The path test runs on link adaptation: switch it on in Station › Settings, "
                                    "with a ladder (top and floor modes) covering the modes to test, on both "
                                    "stations. This page changes no settings itself."),
            "no-radio": self.tr("The radio is not connected."),
            "link-not-adaptive": self.tr("Link adaptation is on in the settings, but the radio was opened without "
                                         "it: reconnect the radio (Station › Reconnect the radio)."),
            "path-busy": self.tr("A path test is already running: stop it first."),
            "no-modes": self.tr("Tick at least one mode."),
            "not-armed": self.tr("Arm transmit first: a path test is sent like any other transmission."),
            "out-of-plan": self.tr("Not allowed here by the band plan: %1"),
            "not-on-ladder": self.tr("Not on this station's adaptation ladder (Station › Settings): %1"),
            "finished": self.tr("Finished: every frame was sent."),
            "path-stuck": self.tr("Stopped: a frame waited 30 s and was not sent (the channel busy, or the radio "
                                  "refused it)."),
            "no-best": self.tr("No mode came through well enough (90 % of its frames) to start at."),
        }
        if key in words:
            return words[key].replace("%1", detail)
        return texts.reason(key)

    # -- commands ------------------------------------------------------------------

    def chosen(self) -> list[str]:
        return [m for m, cb in self.mode_boxes.items() if cb.isChecked() and cb.isEnabled()]

    def start_test(self):
        pt = self.window.ctl.pathtest
        if self.send_role.isChecked():
            why, detail = pt.start_sending(self.chosen(), self.count.value(), self.gap.value())
        else:
            why, detail = pt.start_receiving(self.chosen(), self.count.value()), ""
        if why:
            QMessageBox.information(self, self.tr("Path test"), self._why(why, detail))
        self.refresh()

    def stop_test(self):
        self.window.ctl.pathtest.stop()
        self.refresh()

    def report_text(self) -> str:
        return self.window.ctl.pathtest.report()

    def save_report(self, path: str | None = None):
        text = self.report_text()
        if not text:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, self.tr("Save path test report"), "path-test.txt",
                                                  self.tr("Text files (*.txt)"))
            if not path:
                return None
        try:
            Path(path).write_text(text + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, self.tr("Save path test report"),
                                self.tr("The report could not be saved: %1").replace("%1", str(e)))
            return None
        return path

    def copy_report(self):
        text = self.report_text()
        if text:
            QApplication.clipboard().setText(text)

    def use_for_adaptation(self):
        why, mode = self.window.ctl.pathtest.use_for_adaptation()
        if why:
            QMessageBox.information(self, self.tr("Use for adaptation"), self._why(why, mode))
            return
        self.window.save_settings()
        self.window.statusBar().showMessage(self.tr("Adaptation will start at %1 when receiving.")
                                            .replace("%1", mode), 10_000)
        self.refresh()

    def reset(self):
        """The station was rebuilt (new settings): the modes may differ."""
        self._choices_for = None

    # -- drawing -------------------------------------------------------------------

    def _modes(self, pt):
        choices = pt.choices()
        key = tuple(choices)
        if key == self._choices_for:
            return
        first = self._choices_for is None
        self._choices_for = key
        for m, why, detail in choices:
            cb, lab = self.mode_boxes[m], self.mode_why[m]
            cb.setEnabled(not why)
            if why:
                cb.setChecked(False)
            elif first:
                cb.setChecked(True)
            if why == "not-on-ladder":
                text = self.tr("Not on this station's adaptation ladder")
            else:
                text = self._why(why, detail) if why else ""
            lab.setText(text)
            cb.setToolTip(text)

    def _buttons(self):
        pt = self.window.ctl.pathtest
        running = bool(pt.role)
        self.start.setText(self.tr("Start sending") if self.send_role.isChecked() else self.tr("Start receiving"))
        self.start.setEnabled(not running and not pt.readiness())
        self.stop.setEnabled(running)
        for b in (self.send_role, self.recv_role, self.count, self.gap):
            b.setEnabled(not running)
        self.gap.setEnabled(not running and self.send_role.isChecked())

    def refresh(self):
        pt = self.window.ctl.pathtest
        why = pt.readiness()
        self.ready.setText(self._why(why) if why else "")
        self.ready.setVisible(bool(why))
        self._modes(pt)
        self._buttons()
        if pt.role == "send":
            p = pt.progress()
            self.state.setText(self.tr("Sending: %1 of %2 sent, now %3.").replace("%1", str(p["sent"]))
                               .replace("%2", str(p["total"])).replace("%3", p["mode"] or "-"))
        elif pt.role == "receive":
            self.state.setText(self.tr("Receiving on every mode of the ladder: %1.").replace("%1", ", ".join(
                pt.ladder())))
        elif pt.ended:
            self.state.setText(self._why(pt.ended))
        else:
            self.state.setText("")
        rows = pt.rows()
        tables.fill(self.table, [(r["mode"], fmt.number(r["bitrate"] / 1000, 1), f"{r['decoded']}/{r['count']}",
                                  str(r["missing"]), fmt.number(r["pct"], 1), _num(r["cn0_dbhz"], 1),
                                  _num(r["snr_db"], 1), _num(r["offset_hz"], 0), _num(r["bad_fcs"], 0))
                                 for r in rows])
        has = bool(pt.report())
        self.save.setEnabled(has)
        self.copy.setEnabled(has)
        best = pt.best_mode()
        self.use.setEnabled(best is not None and not pt.role)
        if not rows:
            self.best.setText("")
        else:
            self.best.setText(self.tr("Best mode this way: %1").replace("%1", best) if best else self._why("no-best"))


def _num(v, decimals: int) -> str:
    return "-" if v is None else fmt.number(v, decimals)
