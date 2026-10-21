"""Monitor: every frame heard and sent, on the radio's channel and on every
channel the SDR monitor hears, TNC-style, with filters.

A table model over the controller's rows (it keeps the last 3000); the
filters are a proxy's job, so filtering costs nothing while frames arrive
and a frame never moves under the operator's pointer unless the list is
following the end (it stops following when they scroll up, and Pause
holds it still).
"""
from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QTableView,
                             QVBoxLayout, QWidget)

from .. import callinfo, fmt, texts, theme

COLUMNS = 5

#: Protocol names, as packet people write them (not translated: they are
#: names, like a callsign).
PIDS = {0xCC: "IP", 0xCD: "ARP", 0xCF: "NET/ROM", 0x08: "segment"}


class MonitorModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []
        self.channel_names: dict[str, str] = {}
        #: call -> tooltip text (callinfo.tooltip), set by the page.
        self.describe = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return COLUMNS

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        return [self.tr("Time (UTC)"), self.tr("Dir"), self.tr("Channel"), self.tr("Type"), self.tr("Frame")][section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return fmt.utc_time(r.time)
            if col == 1:
                return texts.direction(r.direction)
            if col == 2:
                if r.channel == "internet":
                    # A link test's return path (linkctl.py): never on the air.
                    return self.tr("Internet (link test)")
                return self.channel_names.get(r.channel, r.channel) if r.channel != "radio" else self.tr("Radio")
            if col == 3:
                return (r.aprs_kind or r.type) + (" FX.25" if r.fx25 else "")
            text = r.text.replace("\r", "⏎")
            if r.pid is not None:
                text += self.tr(" [%1, %2 bytes]").replace("%1", PIDS.get(r.pid, f"PID {r.pid:#04x}")) \
                    .replace("%2", str(r.info_len))
            return text
        if role == Qt.ItemDataRole.ForegroundRole and col == 1:
            return theme.colour({"tx": "tx", "rx": "rx", "sdr": "info"}.get(r.direction, "ink"))
        if role == Qt.ItemDataRole.ToolTipRole and col == 0:
            return fmt.local_datetime(r.time)
        if role == Qt.ItemDataRole.ToolTipRole and col == 4 and r.source and self.describe is not None:
            # Who sent it, from HamQTH (ui/callinfo.py): asked only on
            # hover, so a busy channel does not queue a lookup per frame.
            return self.describe(r.source) or None
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class MonitorFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.direction = ""
        self.channel = ""
        self.text = ""

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        if self.direction and r.direction != self.direction:
            return False
        if self.channel and r.channel != self.channel:
            return False
        if self.text and self.text not in r.text.upper():
            return False
        return True


class MonitorPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._rev = -1
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.direction = QComboBox()
        for key, name in (("", self.tr("All frames")), ("rx", self.tr("Received")), ("tx", self.tr("Sent")),
                          ("sdr", self.tr("SDR monitor"))):
            self.direction.addItem(name, key)
        self.channel = QComboBox()
        self.channel.addItem(self.tr("All channels"), "")
        self.channel.addItem(self.tr("Radio"), "radio")
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Filter by callsign or text"))
        self.pause = QCheckBox(self.tr("Pause"))
        for w in (self.direction, self.channel, self.search, self.pause):
            bar.addWidget(w)
        lay.addLayout(bar)
        self.model = MonitorModel(self)
        self.model.describe = lambda call: callinfo.tooltip(self.window.ctl, call)
        self.proxy = MonitorFilter(self)
        self.proxy.setSourceModel(self.model)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.verticalHeader().hide()
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setAlternatingRowColors(True)
        h = self.view.horizontalHeader()
        for i in range(COLUMNS - 1):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(True)
        lay.addWidget(self.view)
        self.direction.currentIndexChanged.connect(self._filters)
        self.channel.currentIndexChanged.connect(self._filters)
        self.search.textChanged.connect(self._filters)

    def _filters(self):
        self.proxy.direction = self.direction.currentData() or ""
        self.proxy.channel = self.channel.currentData() or ""
        self.proxy.text = self.search.text().strip().upper()
        self.proxy.invalidateFilter()

    def _channels(self, ctl):
        sdr = ctl.sdr
        if sdr is None:
            return
        for c in sdr.plan:
            name = c.label or fmt.mhz(c.hz)
            self.model.channel_names[c.id] = name
            if self.channel.findData(c.id) < 0:
                self.channel.addItem(self.tr("SDR %1").replace("%1", name), c.id)

    def refresh(self):
        ctl = self.window.ctl
        if self.pause.isChecked() or ctl.rows_rev == self._rev:
            return
        self._channels(ctl)
        sb = self.view.verticalScrollBar()
        following = sb.value() >= sb.maximum() - 2
        with ctl._lock:
            rows = list(ctl.rows)
            self._rev = ctl.rows_rev
        self.model.set_rows(rows)
        if following:
            self.view.scrollToBottom()
