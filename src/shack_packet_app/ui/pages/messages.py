"""APRS messages: conversations by station, each message with its state.

A message this station sends is numbered and sent again until it is
acknowledged (30 s, then doubling, five times: aprs_station.py), and the
row shows where it is: waiting, sent with no ack yet, acknowledged,
rejected, or never acknowledged. Long text is cut into several messages
at word boundaries by the library, and each part has its own state.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import fmt, texts, theme
from ...controller import valid_address


class MessagesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._sig = None
        self._peers_sig = None
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.peers = QListWidget()
        self.peers.currentItemChanged.connect(lambda *_: self._show())
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.thread = QTableWidget(0, 4)
        self.thread.setHorizontalHeaderLabels([self.tr("Time (UTC)"), self.tr("From"), self.tr("Message"),
                                               self.tr("State")])
        self.thread.verticalHeader().hide()
        self.thread.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.thread.horizontalHeader().setStretchLastSection(False)
        self.thread.horizontalHeader().setSectionResizeMode(2, self.thread.horizontalHeader().ResizeMode.Stretch)
        rl.addWidget(self.thread, 1)
        compose = QHBoxLayout()
        self.to = QLineEdit()
        self.to.setPlaceholderText(self.tr("To (callsign)"))
        self.to.setMaximumWidth(140)
        self.text = QLineEdit()
        self.text.setPlaceholderText(self.tr("Message"))
        self.text.returnPressed.connect(self.send)
        self.send_btn = QPushButton(self.tr("Send"))
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self.send)
        compose.addWidget(self.to)
        compose.addWidget(self.text, 1)
        compose.addWidget(self.send_btn)
        rl.addLayout(compose)
        self.note = QLabel()
        self.note.setObjectName("dim")
        rl.addWidget(self.note)
        split.addWidget(self.peers)
        split.addWidget(right)
        split.setSizes([200, 800])
        lay.addWidget(split)

    def start_to(self, call: str) -> None:
        self.to.setText(call)
        self.text.setFocus()

    def send(self):
        to, text = self.to.text().strip().upper(), self.text.text().strip()
        if not to or not text:
            return
        if not valid_address(to):
            QMessageBox.warning(self, self.tr("Message"), self.tr("%1 is not a callsign AX.25 can carry.")
                                .replace("%1", to))
            return
        ctl = self.window.ctl
        try:
            ctl.send_message(to, text)
        except Exception as e:                      # noqa: BLE001 - no callsign, not started
            QMessageBox.warning(self, self.tr("Message"), texts.reason("no-callsign") if "callsign" in str(e)
                                else str(e))
            return
        self.text.clear()
        if not ctl.status()["armed"]:
            self.note.setText(self.tr("Queued. It goes out when transmit is armed, and is retried until "
                                      "acknowledged."))
        else:
            self.note.setText("")
        self._select(to)

    def _select(self, peer: str):
        for i in range(self.peers.count()):
            if self.peers.item(i).data(Qt.ItemDataRole.UserRole) == peer:
                self.peers.setCurrentRow(i)
                return

    def _show(self):
        item = self.peers.currentItem()
        if item is None:
            return
        peer = item.data(Qt.ItemDataRole.UserRole)
        self.to.setText(peer)
        ctl = self.window.ctl
        rows = [m for m in ctl.messages if m.peer == peer]
        me = ctl.status()["callsign"]
        self.thread.setRowCount(len(rows))
        for i, m in enumerate(rows):
            state = QTableWidgetItem(texts.message_state(m.state))
            state.setForeground(theme.colour({"acked": "rx", "received": "rx", "timeout": "tx", "rejected": "tx"}
                                             .get(m.state, "warn")))
            for col, it in enumerate((QTableWidgetItem(fmt.utc_time(m.time)),
                                      QTableWidgetItem(me if m.direction == "out" else m.peer),
                                      QTableWidgetItem(m.text), state)):
                self.thread.setItem(i, col, it)
        self.thread.resizeColumnToContents(0)
        self.thread.resizeColumnToContents(1)
        self.thread.resizeColumnToContents(3)

    def refresh(self):
        ctl = self.window.ctl
        with ctl._lock:
            msgs = list(ctl.messages)
        peers = []
        for m in msgs:
            if m.peer not in peers:
                peers.append(m.peer)
        if peers != self._peers_sig:
            current = self.peers.currentItem().data(Qt.ItemDataRole.UserRole) if self.peers.currentItem() else None
            self.peers.clear()
            for p in peers:
                it = QListWidgetItem(p)
                it.setData(Qt.ItemDataRole.UserRole, p)
                self.peers.addItem(it)
            self._peers_sig = peers
            if current:
                self._select(current)
            elif peers:
                self.peers.setCurrentRow(0)
        sig = tuple((m.peer, m.text, m.state) for m in msgs)
        if sig != self._sig:
            self._sig = sig
            self._show()
