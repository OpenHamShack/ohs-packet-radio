"""IP: IP over AX.25 as release 1 has it - the engine's stack, its tables,
the encryption guard's refusals, and a ping built into the app.

No operating-system network interface is made in release 1 (Brian,
18/10/2026: IP over AX.25 is used inside the app; the TUN/utun/Wintun
interface, which needs administrator rights, comes in release 2;
ipnet.py has the seam). The page says so, so a tester does not look for
one or think it broken.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from .. import fmt, tables, texts


class IpPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        status = QGroupBox(self.tr("Status"))
        form = QFormLayout(status)
        self.state = QLabel()
        self.address = QLabel()
        self.os_if = QLabel(self.tr("None yet: in this release IP over AX.25 works inside this app (the tools on "
                                    "this page). Other programs on this computer using it, system-wide, comes in "
                                    "a later release."))
        self.os_if.setWordWrap(True)
        self.os_if.setObjectName("dim")
        self.counters = QLabel()
        form.addRow(self.tr("IP over AX.25"), self.state)
        form.addRow(self.tr("Address"), self.address)
        form.addRow(self.tr("System interface"), self.os_if)
        form.addRow(self.tr("Packets"), self.counters)
        top = QHBoxLayout()
        top.addWidget(status, 1)
        ping = QGroupBox(self.tr("Ping"))
        pl = QVBoxLayout(ping)
        row = QHBoxLayout()
        self.target = QLineEdit()
        self.target.setPlaceholderText(self.tr("IPv4 address, e.g. 44.131.1.2"))
        self.count = QSpinBox()
        self.count.setRange(1, 20)
        self.count.setValue(4)
        self.go = QPushButton(self.tr("Ping"))
        self.go.clicked.connect(self.ping)
        row.addWidget(self.target, 1)
        row.addWidget(self.count)
        row.addWidget(self.go)
        pl.addLayout(row)
        self.results = tables.make([self.tr("Target"), self.tr("Seq"), self.tr("Result")])
        pl.addWidget(self.results)
        top.addWidget(ping, 1)
        lay.addLayout(top)
        boxes = QHBoxLayout()
        arp_box = QGroupBox(self.tr("ARP"))
        al = QVBoxLayout(arp_box)
        self.arp = tables.make([self.tr("IP"), self.tr("Callsign"), self.tr("Via"), self.tr("Mode"), self.tr("Kind")])
        al.addWidget(self.arp)
        route_box = QGroupBox(self.tr("Routes"))
        rl = QVBoxLayout(route_box)
        self.routes = tables.make([self.tr("Network"), self.tr("Gateway"), self.tr("Mode")])
        rl.addWidget(self.routes)
        guard_box = QGroupBox(self.tr("Refused by the encryption guard"))
        gl = QVBoxLayout(guard_box)
        self.guard = tables.make([self.tr("Time (UTC)"), self.tr("Why"), self.tr("Bytes")])
        gl.addWidget(self.guard)
        # ARP has five columns to the others' three: an even split gave it a
        # sideways scroll bar at 1280 wide.
        for b, share in ((arp_box, 3), (route_box, 2), (guard_box, 2)):
            boxes.addWidget(b, share)
        lay.addLayout(boxes, 1)

    def ping(self):
        ctl = self.window.ctl
        target = self.target.text().strip()
        try:
            ctl.ping(target, self.count.value())
        except ValueError:
            QMessageBox.warning(self, self.tr("Ping"), self.tr("%1 is not an IPv4 address.").replace("%1", target))
        except RuntimeError:
            QMessageBox.information(self, self.tr("Ping"), self.tr("IP over AX.25 is off. Switch it on in Settings."))

    def refresh(self):
        ctl = self.window.ctl
        ip = ctl.ip
        s = ctl.settings.ip
        self.go.setEnabled(ip is not None)
        if ip is None:
            self.state.setText(self.tr("Off") if not s.enabled else self.tr("Not running (needs a callsign "
                                                                            "and an address)"))
            self.address.setText(s.address or "-")
            self.counters.setText("")
        else:
            self.state.setText(self.tr("On, %1 mode").replace("%1", s.mode))
            self.address.setText(f"{ip.address}" + (f"  ({s.network})" if s.network else ""))
            st = ip.stats
            self.counters.setText(self.tr("%1 sent, %2 received, %3 refused").replace(
                "%1", str(st["sent_datagram"] + st["sent_circuit"]))
                .replace("%2", str(st["received_datagram"] + st["received_circuit"]))
                .replace("%3", str(sum(v for k, v in st.items() if k.startswith("refused_")))))
            with ctl.engine.lock:
                arp = [(str(e.ip), str(e.call), ",".join(str(v) for v in e.via), e.mode or s.mode,
                        self.tr("static") if e.expires is None else self.tr("learnt"))
                       for e in ip.arp.values()]
                routes = [(str(r.network), str(r.gateway or self.tr("direct")), r.mode or s.mode) for r in ip.routes]
            tables.fill(self.arp, arp)
            tables.fill(self.routes, routes)
        tables.fill(self.guard, [(fmt.utc_time(t), texts.reason(why), n) for t, why, n in list(ctl.guard_log)])
        pinger = ctl.pinger
        rows = []
        if pinger is not None:
            for run in pinger.runs[-3:]:
                for r in run.results:
                    if r.refused:
                        res = texts.reason(r.refused)
                    elif r.rtt_s is not None:
                        res = self.tr("reply in %1 s").replace("%1", fmt.number(r.rtt_s, 2))
                    else:
                        res = self.tr("waiting")
                    rows.append((run.target, r.seq, res))
        tables.fill(self.results, rows)
