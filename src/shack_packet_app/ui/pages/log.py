"""Log: the packet contacts kept by the app (logbook.py), filtered, and
exported as ADIF 3.1 for the operator's main logger or CSV for a
spreadsheet; and which sessions are logged by themselves when they end.

Exports write exactly the rows the filter shows, so "last weekend's 2 m
contacts" is a filter and an export.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QCheckBox, QDateEdit, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QTableWidgetItem, QVBoxLayout, QWidget)

from ... import __version__, logbook
from .. import callinfo, fmt, tables


class LogPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._sig = None
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(self.tr("Filter: callsign, band, kind, words in the comment"))
        self.filter.textChanged.connect(lambda _t: self._redraw())
        self.since = QDateEdit()
        self.since.setCalendarPopup(True)
        self.since.setDisplayFormat("yyyy-MM-dd")
        self.since.setDate(QDate(2000, 1, 1))
        self.since.dateChanged.connect(lambda _d: self._redraw())
        bar.addWidget(self.filter, 1)
        bar.addWidget(QLabel(self.tr("From")))
        bar.addWidget(self.since)
        lay.addLayout(bar)
        self.table = tables.make([self.tr("Date (UTC)"), self.tr("On"), self.tr("Off"), self.tr("Call"),
                                  self.tr("Band"), self.tr("Kind"), self.tr("Name"), self.tr("Comment")])
        lay.addWidget(self.table, 1)
        auto = QHBoxLayout()
        auto.addWidget(QLabel(self.tr("Log by themselves when they end:")))
        s = window.settings.log
        self.auto = {}
        for key, name in (("auto_chat", self.tr("chats")), ("auto_bbs", self.tr("BBS and node sessions")),
                          ("auto_conference", self.tr("node conferences")),
                          ("auto_room", self.tr("stations in my room"))):
            box = QCheckBox(name)
            box.setChecked(bool(getattr(s, key)))
            box.toggled.connect(lambda on, k=key: self._auto(k, on))
            self.auto[key] = box
            auto.addWidget(box)
        auto.addStretch(1)
        lay.addLayout(auto)
        row = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("dim")
        self.delete = QPushButton(self.tr("Delete"))
        self.delete.clicked.connect(self._delete)
        self.adif = QPushButton(self.tr("Export ADIF…"))
        self.adif.clicked.connect(lambda: self.export("adi"))
        self.csv = QPushButton(self.tr("Export CSV…"))
        self.csv.clicked.connect(lambda: self.export("csv"))
        self.lookup = QPushButton(self.tr("Callsign lookup…"))
        self.lookup.clicked.connect(self._lookup)
        row.addWidget(self.count, 1)
        for b in (self.lookup, self.delete, self.adif, self.csv):
            row.addWidget(b)
        lay.addLayout(row)

    def _auto(self, key: str, on: bool):
        setattr(self.window.settings.log, key, bool(on))
        self.window.save_settings()

    def _lookup(self):
        dlg = callinfo.LookupDialog(self.window)
        dlg.open()
        self.lookup_dialog = dlg

    def shown(self) -> list:
        """The contacts the filter lets through, newest first."""
        since = self.since.date()
        from datetime import datetime, timezone
        t0 = datetime(since.year(), since.month(), since.day(), tzinfo=timezone.utc).timestamp()
        text = self.filter.text()
        rows = [c for c in self.window.ctl.logbook.all() if logbook.matches(c, text, since=t0)]
        return sorted(rows, key=lambda c: c.time_on, reverse=True)

    def _selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _delete(self):
        ident = self._selected_id()
        if ident is not None and QMessageBox.question(self, self.tr("Delete contact"),
                                                      self.tr("Delete this contact from the log?")) \
                == QMessageBox.StandardButton.Yes:
            self.window.ctl.logbook.delete(ident)
            self.refresh()

    def export(self, kind: str, path: str | None = None):
        rows = self.shown()
        if path is None:
            filt = self.tr("ADIF files (*.adi)") if kind == "adi" else self.tr("CSV files (*.csv)")
            path, _ = QFileDialog.getSaveFileName(self, self.tr("Export the log"), f"packet-log.{kind}", filt)
            if not path:
                return None
        text = logbook.adif(rows, version=__version__) if kind == "adi" else logbook.csv_text(rows)
        try:
            Path(path).write_text(text, encoding="ascii" if kind == "adi" else "utf-8", newline="")
        except OSError as e:
            QMessageBox.warning(self, self.tr("Export the log"), str(e))
            return None
        self.window.statusBar().showMessage(self.tr("%1 contacts written to %2").replace(
            "%1", str(len(rows))).replace("%2", str(path)), 10_000)
        return path

    def _redraw(self):
        self._sig = None
        self.refresh()

    def refresh(self):
        ctl = self.window.ctl
        sig = (ctl.logbook.rev, self.filter.text(), self.since.date().toString())
        if sig == self._sig:
            return
        self._sig = sig
        rows = []
        for c in self.shown():
            date = QTableWidgetItem(fmt.utc_datetime(c.time_on).split(" ")[0])
            date.setData(Qt.ItemDataRole.UserRole, c.id)
            call = QTableWidgetItem(c.station or c.call)
            call.setToolTip(callinfo.tooltip(ctl, c.call))
            rows.append([date, fmt.utc_time(c.time_on), fmt.utc_time(c.time_off) if c.time_off else "", call,
                         c.band, c.kind, c.name, c.full_comment()])
        tables.fill(self.table, rows)
        total = len(ctl.logbook.all())
        self.count.setText(self.tr("%1 of %2 contacts").replace("%1", str(len(rows))).replace("%2", str(total)))
