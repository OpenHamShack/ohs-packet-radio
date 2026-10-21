"""Nodes: every NET/ROM node this station knows of, from the NODES
broadcasts heard on the radio and the SDR monitor and from the NODES and
ROUTES listings nodes printed in a session (shack_packet.netrom's
NodeTable), best first; double-click or Connect to reach one.

A node heard directly is connected to as it is. One known only through a
neighbour is reached the way an operator would by hand: connect to that
neighbour and type C and the node's alias - a one-off connect script,
shown in the session like any other, and started only by this click. It
types as soon as the link is up rather than waiting for a greeting,
because nodes greet with whatever text their sysop chose (BPQ's CTEXT,
often none) and a node takes commands from the first frame.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget

from .. import callinfo, fmt, tables


def connect_script(node) -> str:
    """The steps that reach `node` (a netrom.KnownNode)."""
    if node.direct or not node.neighbour:
        return f"connect {node.call}\n"
    return f"connect {node.neighbour}\nsend C {node.alias or node.call}\n"


class NodesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._rev = -1
        lay = QVBoxLayout(self)
        intro = QLabel(self.tr("NET/ROM nodes heard broadcasting on the air, and those that nodes you connect to "
                               "list (type N or R in a node session). Double-click one to connect."))
        intro.setWordWrap(True)
        intro.setObjectName("dim")
        lay.addWidget(intro)
        self.table = tables.make([self.tr("Alias"), self.tr("Callsign"), self.tr("Quality"), self.tr("Reached via"),
                                  self.tr("Known from"), self.tr("Last heard (UTC)")])
        self.table.doubleClicked.connect(lambda _i: self.connect_selected())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.note = QLabel()
        self.note.setObjectName("dim")
        self.go = QPushButton(self.tr("Connect"))
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.connect_selected)
        row.addWidget(self.note, 1)
        row.addWidget(self.go)
        lay.addLayout(row)

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        call = self.table.item(rows[0].row(), 1).data(Qt.ItemDataRole.UserRole)
        return self.window.ctl.nodes.nodes.get(call)

    def connect_selected(self):
        node = self._selected()
        if node is None:
            return None
        chat = self.window.pages["chat"]
        sess = chat.run_script({"name": node.alias or node.call, "text": connect_script(node)})
        if sess is not None:
            self.window.go_to("chat")
        return sess

    def refresh(self):
        ctl = self.window.ctl
        with ctl._lock:
            rev = ctl.nodes.rev
            nodes = ctl.nodes.ranked()
        if rev == self._rev:
            return
        self._rev = rev
        known = {"air": self.tr("broadcast"), "nodes": self.tr("NODES listing"), "routes": self.tr("ROUTES listing")}
        rows = []
        for n in nodes:
            call = QTableWidgetItem(n.call)
            call.setData(Qt.ItemDataRole.UserRole, n.call)
            call.setToolTip(callinfo.tooltip(ctl, n.call))
            via = self.tr("direct") if n.direct else n.neighbour
            rows.append([n.alias, call, str(n.quality), via, known.get(n.source, n.source), fmt.utc_time(n.heard)])
        tables.fill(self.table, rows)
        self.note.setText(self.tr("%1 nodes").replace("%1", str(len(nodes))) if nodes else
                          self.tr("No nodes yet: they broadcast every half hour or so."))
