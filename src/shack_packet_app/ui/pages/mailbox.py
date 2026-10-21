"""Mailbox: a mail client for two networks, each on its own tab.

**Packet mail** (amateur stations, store-and-forward; mailclient.py):

- *Inbox*: what callers left in this station's mailbox (ohs-libs'
  personal mailbox: a TNC-style PMS on the SSID chosen in Settings, off
  until switched on here), what other stations' mail clients delivered to
  it, and what was collected from the home BBS - one list.
- *Outbox and sent*: what this station wrote, each with where it is:
  queued, sending, delivered (and through whom), or failed (and why).
  Queued mail goes by itself while transmit is armed: to the addressee's
  own station, their BBS (CALL@BBS) or the home BBS (Settings › Mail).
- New, Reply, Delete; Send now (now, not at its retry time); Collect now
  (from the home BBS).

**Winlink email** (Winlink's network, through an RMS packet gateway;
b2f.py): callsigns and internet addresses, attachments up to the limit in
Settings › Winlink. Written any time; sent and collected when the operator
presses Connect to a gateway, which needs transmit armed and the account's
password (kept in secrets.json; only a challenge's answer goes on air).

Everything goes over the air in the clear: the compose dialog says so.

On the simulator a button has G4SIM leave a test message (simband's
leave_mail), so the page can be tried without a friend on the air. It is
shown only there, and only does anything when pressed.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QSplitter, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from shack_packet import b2f

from .. import fmt, tables
from ..compose import ComposeDialog


def _reason(widget, key: str) -> str:
    """The mail client's and the library's reason keys, in words."""
    tr = widget.tr
    return {
        "not-armed": tr("Arm transmit first: mail goes over the air."),
        "no-home": tr("There is no home BBS (Settings › Mail)."),
        "no-gateway": tr("Choose a gateway (Settings › Winlink)."),
        "no-password": tr("The Winlink password is not set (Settings › Winlink)."),
        "busy": tr("Already connected to a gateway."),
        "no-callsign": tr("The station needs its callsign (Station › Setup check)."),
        "no-connect": tr("The connection could not be made."),
        "no-answer": tr("no answer"),
        "timeout": tr("it stopped answering"),
        "protocol": tr("the exchange went wrong"),
        "not-a-bbs": tr("it does not forward mail"),
        "refused": tr("refused"),
        "not-taken": tr("not taken"),
        "no-route": tr("nowhere to send it"),
        "had-it": tr("it already had it"),
        "no-b2": tr("not a Winlink gateway"),
        "login-failed": tr("the gateway refused the password"),
        "remote-error": tr("the gateway reported an error"),
        "lost": tr("the link was lost"),
    }.get(key, key)


class PacketMail(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self._sig = None
        self._out_sig = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.enable = QCheckBox(self.tr("Accept messages left for me"))
        self.enable.toggled.connect(self._toggled)
        self.where = QLabel()
        self.where.setObjectName("dim")
        self.where.setWordWrap(True)
        top.addWidget(self.enable)
        top.addWidget(self.where, 1)
        lay.addLayout(top)
        bar = QHBoxLayout()
        self.folder = QComboBox()
        self.folder.addItem(self.tr("Inbox"), "inbox")
        self.folder.addItem(self.tr("Outbox and sent"), "outbox")
        self.folder.currentIndexChanged.connect(self._folder_changed)
        self.new = QPushButton(self.tr("New…"))
        self.new.clicked.connect(lambda: self.compose())
        self.reply = QPushButton(self.tr("Reply…"))
        self.reply.clicked.connect(self._reply)
        self.send_now = QPushButton(self.tr("Send now"))
        self.send_now.clicked.connect(self._send_now)
        self.collect = QPushButton(self.tr("Collect now"))
        self.collect.clicked.connect(self._collect)
        for w in (self.folder, self.new, self.reply, self.send_now, self.collect):
            bar.addWidget(w)
        bar.addStretch(1)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = tables.make([self.tr("Received (UTC)"), self.tr("From"), self.tr("To"), self.tr("Read"),
                                  self.tr("Subject")])
        self.table.itemSelectionChanged.connect(self._open)
        self.out_table = tables.make([self.tr("Written (UTC)"), self.tr("To"), self.tr("State"), self.tr("Subject")])
        self.out_table.itemSelectionChanged.connect(self._open_out)
        self.out_table.hide()
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.table)
        hl.addWidget(self.out_table)
        split.addWidget(holder)
        split.addWidget(self.body)
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.sim_mail = QPushButton(self.tr("Ask G4SIM to leave a test message"))
        self.sim_mail.setToolTip(self.tr("Simulator only: a pretend station connects to your mailbox and leaves a "
                                         "message. Your station answers it only while transmit is armed."))
        self.sim_mail.clicked.connect(self._sim_mail)
        self.note = QLabel()
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        self.retry = QPushButton(self.tr("Try again"))
        self.retry.clicked.connect(self._retry)
        self.delete = QPushButton(self.tr("Delete"))
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.sim_mail)
        row.addWidget(self.note, 1)
        row.addWidget(self.retry)
        row.addWidget(self.delete)
        lay.addLayout(row)

    @property
    def ctl(self):
        return self.page.window.ctl

    def outbox_shown(self) -> bool:
        return self.folder.currentData() == "outbox"

    # -- actions ------------------------------------------------------------------
    def _toggled(self, on: bool):
        ctl = self.ctl
        if ctl.settings.mailbox.enabled != on:
            ctl.set_mailbox(on)
            self.page.window.save_settings()

    def compose(self, to: str = "", subject: str = "", body: str = "", reply_to: int | None = None) -> bool:
        dlg = ComposeDialog(self, to=to, subject=subject, body=body)
        if self.page.dialog_hook is not None:
            self.page.dialog_hook(dlg)
        elif not dlg.exec():
            return False
        if dlg.check():
            return False
        try:
            self.ctl.mailclient.compose(dlg.addresses()[0], dlg.subject.text(), dlg.body.toPlainText(),
                                        reply_to=reply_to)
        except (ValueError, RuntimeError):
            self.note.setText(self.tr("The station needs its callsign before it can send mail."))
            return False
        armed = bool(self.ctl.status().get("armed"))
        self.note.setText(self.tr("Queued: it goes as soon as a route answers.") if armed else
                          self.tr("Queued: it goes once transmit is armed."))
        self.folder.setCurrentIndex(1)
        self._out_sig = None
        self.refresh()
        return True

    def _reply(self):
        mb = self.ctl.mailbox
        ident = self._selected(self.table)
        if self.outbox_shown() or ident is None or mb is None:
            return
        msg = mb.store.get(ident)
        if msg is None:
            return
        subject = msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}"
        quoted = "\n".join("> " + line for line in msg.body.split("\n"))
        self.compose(msg.sender, subject, "\n\n" + quoted)

    def _send_now(self):
        why = self.ctl.mailclient.send_now()
        self.note.setText(_reason(self, why) if why else self.tr("Trying the outbox now."))

    def _collect(self):
        why = self.ctl.mailclient.collect_now()
        self.note.setText(_reason(self, why) if why else self.tr("Collecting from %1.")
                          .replace("%1", self.ctl.settings.mail.home_bbs.upper()))

    def _retry(self):
        ident = self._selected(self.out_table)
        if ident is not None:
            self.ctl.mailclient.retry(ident)
            self._out_sig = None

    def _sim_mail(self):
        ctl = self.ctl
        if not ctl.settings.mailbox.enabled:
            self.note.setText(self.tr("Switch the mailbox on first."))
            return
        ok = ctl.simulated_mail(self.tr("Test from the simulator"),
                                self.tr("Hello from G4SIM, a pretend station inside the app.\n73"))
        if not ok:
            self.note.setText(self.tr("The mailbox is not running (it needs your callsign)."))
        elif not ctl.status()["armed"]:
            self.note.setText(self.tr("G4SIM is calling. Arm transmit so your mailbox can answer."))
        else:
            self.note.setText(self.tr("G4SIM is calling your mailbox."))

    @staticmethod
    def _selected(table):
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        return table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _open(self):
        ident = self._selected(self.table)
        mb = self.ctl.mailbox
        if ident is None or mb is None:
            self.body.clear()
            return
        msg = mb.store.get(ident)
        if msg is None:
            return
        self.body.setPlainText(msg.body)
        if not msg.read:
            mb.store.mark_read(ident)

    def _open_out(self):
        ident = self._selected(self.out_table)
        box = self.ctl.mailclient.outbox
        m = box.get(ident) if box is not None and ident is not None else None
        if m is None:
            self.body.clear()
            return
        self.body.setPlainText(self.tr("To: %1").replace("%1", m.address) + "\n"
                               + self.tr("Subject: %1").replace("%1", m.subject) + "\n\n" + m.body)

    def _delete(self):
        if self.outbox_shown():
            ident = self._selected(self.out_table)
            if ident is not None and self.ctl.mailclient.delete_outgoing(ident):
                self.body.clear()
                self._out_sig = None
            return
        ident = self._selected(self.table)
        mb = self.ctl.mailbox
        if ident is not None and mb is not None:
            mb.store.delete(ident)
            self.body.clear()

    def _folder_changed(self):
        out = self.outbox_shown()
        self.table.setVisible(not out)
        self.out_table.setVisible(out)
        self.retry.setVisible(out)
        self.reply.setEnabled(not out)
        self.body.clear()
        self._sig = self._out_sig = None
        self.refresh()

    # -- drawing ------------------------------------------------------------------------
    def state_text(self, m) -> str:
        if m.state == "delivered":
            return self.tr("Delivered via %1").replace("%1", m.route) if m.error != "had-it" else \
                self.tr("Delivered via %1 (already there)").replace("%1", m.route)
        if m.state == "failed":
            return self.tr("Failed: %1").replace("%1", _reason(self, m.error))
        if m.state == "sending":
            return self.tr("Sending to %1").replace("%1", m.route)
        if m.rounds or m.error:
            return self.tr("Queued (last try: %1)").replace("%1", _reason(self, m.error))
        return self.tr("Queued")

    def refresh(self):
        ctl = self.ctl
        s = ctl.settings
        self.enable.blockSignals(True)
        self.enable.setChecked(bool(s.mailbox.enabled))
        self.enable.blockSignals(False)
        mb = ctl.mailbox
        self.enable.setEnabled(mb is not None)
        self.sim_mail.setVisible(ctl.band is not None)
        self.retry.setVisible(self.outbox_shown())
        self.collect.setEnabled(bool(s.mail.home_bbs))
        if mb is None:
            self.where.setText(self.tr("The mailbox needs your callsign (Station › Setup check)."))
        elif s.mailbox.enabled:
            self.where.setText(self.tr("Stations connect to %1 to leave you a message.").replace("%1", str(mb.address)))
        else:
            self.where.setText(self.tr("Off: stations connecting to %1 are refused.").replace("%1", str(mb.address)))
        if self.outbox_shown():
            self._refresh_out()
            return
        mail = ctl.mail()
        sig = tuple((m.id, m.read) for m in mail)
        if sig == self._sig:
            return
        self._sig = sig
        self.table.setRowCount(len(mail))
        for i, m in enumerate(mail):
            when = QTableWidgetItem(fmt.utc_datetime(m.time))
            when.setData(Qt.ItemDataRole.UserRole, m.id)
            items = (when, QTableWidgetItem(m.sender), QTableWidgetItem(m.to),
                     QTableWidgetItem(self.tr("yes") if m.read else self.tr("new")), QTableWidgetItem(m.subject))
            for col, it in enumerate(items):
                self.table.setItem(i, col, it)
        tables.fit(self.table)

    def _refresh_out(self):
        out = self.ctl.mailclient.outgoing()
        sig = tuple((m.id, m.state, m.error, m.route, m.rounds) for m in out)
        if sig == self._out_sig:
            return
        self._out_sig = sig
        self.out_table.setRowCount(len(out))
        for i, m in enumerate(out):
            when = QTableWidgetItem(fmt.utc_datetime(m.created))
            when.setData(Qt.ItemDataRole.UserRole, m.id)
            for col, it in enumerate((when, QTableWidgetItem(m.address), QTableWidgetItem(self.state_text(m)),
                                      QTableWidgetItem(m.subject))):
                self.out_table.setItem(i, col, it)
        tables.fit(self.out_table)


class WinlinkMail(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self._sig = None
        lay = QVBoxLayout(self)
        about = QLabel(self.tr("Winlink: email to and from callsigns and internet addresses, through an RMS packet "
                               "gateway. It needs a Winlink account (free, at winlink.org) for your callsign. "
                               "Write messages any time; they go when you connect to a gateway."))
        about.setWordWrap(True)
        about.setObjectName("dim")
        lay.addWidget(about)
        bar = QHBoxLayout()
        self.folder = QComboBox()
        for key, name in (("inbox", self.tr("Inbox")), ("outbox", self.tr("Outbox")), ("sent", self.tr("Sent"))):
            self.folder.addItem(name, key)
        self.folder.currentIndexChanged.connect(lambda: self._reset())
        self.new = QPushButton(self.tr("New…"))
        self.new.clicked.connect(lambda: self.compose())
        self.reply = QPushButton(self.tr("Reply…"))
        self.reply.clicked.connect(self._reply)
        self.gateway = QComboBox()
        self.gateway.setMinimumWidth(160)
        self.connect_btn = QPushButton(self.tr("Connect to gateway"))
        self.connect_btn.clicked.connect(self._connect)
        for w in (self.folder, self.new, self.reply):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.gateway)
        bar.addWidget(self.connect_btn)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = tables.make([self.tr("Date (UTC)"), self.tr("From"), self.tr("To"), self.tr("Read"),
                                  self.tr("Subject")])
        self.table.itemSelectionChanged.connect(self._open)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        split.addWidget(self.table)
        split.addWidget(self.body)
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.note = QLabel()
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        self.save = QPushButton(self.tr("Save attachments…"))
        self.save.clicked.connect(self._save)
        self.delete = QPushButton(self.tr("Delete"))
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.note, 1)
        row.addWidget(self.save)
        row.addWidget(self.delete)
        lay.addLayout(row)

    @property
    def ctl(self):
        return self.page.window.ctl

    def _reset(self):
        self._sig = None
        self.body.clear()
        self.refresh()

    def compose(self, to: str = "", subject: str = "", body: str = "") -> bool:
        w = self.ctl.settings.winlink
        dlg = ComposeDialog(self, winlink=True, to=to, subject=subject, body=body,
                            max_attachments_kb=w.max_attachments_kb)
        if self.page.dialog_hook is not None:
            self.page.dialog_hook(dlg)
        elif not dlg.exec():
            return False
        if dlg.check():
            return False
        cc = [c.strip() for c in dlg.cc.text().split(",") if c.strip()]
        try:
            self.ctl.mailclient.winlink_compose(dlg.addresses(), dlg.subject.text(), dlg.body.toPlainText(),
                                                dlg.files, cc)
        except (ValueError, RuntimeError):
            self.note.setText(self.tr("That message could not be queued."))
            return False
        self.note.setText(self.tr("In the outbox: it goes when you connect to a gateway."))
        self.folder.setCurrentIndex(1)
        self._reset()
        return True

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _message(self):
        box = self.ctl.mailclient.winlink
        mid = self._selected()
        if box is None or mid is None:
            return None
        return box.load(self.folder.currentData(), mid)

    def _open(self):
        m = self._message()
        if m is None:
            self.body.clear()
            return
        head = [self.tr("From: %1").replace("%1", b2f.plain(m.sender)),
                self.tr("To: %1").replace("%1", ", ".join(b2f.plain(t) for t in m.to + m.cc)),
                self.tr("Subject: %1").replace("%1", m.subject)]
        if m.files:
            head.append(self.tr("Attachments: %1").replace("%1", ", ".join(f.name for f in m.files)))
        self.body.setPlainText("\n".join(head) + "\n\n" + m.body)
        if self.folder.currentData() == "inbox":
            self.ctl.mailclient.winlink.mark_read(m.mid)

    def _reply(self):
        m = self._message()
        if m is None or self.folder.currentData() != "inbox":
            return
        subject = m.subject if m.subject.lower().startswith("re:") else f"Re: {m.subject}"
        quoted = "\n".join("> " + line for line in m.body.split("\n"))
        self.compose(b2f.plain(m.sender), subject, "\n\n" + quoted)

    def _save(self):
        m = self._message()
        if m is None or not m.files:
            return
        folder = QFileDialog.getExistingDirectory(self, self.tr("Save the attachments in"))
        if not folder:
            return
        for f in m.files:
            name = Path(f.name).name or "attachment"
            (Path(folder) / name).write_bytes(f.data)
        self.note.setText(self.tr("Saved in %1.").replace("%1", folder))

    def _delete(self):
        mid = self._selected()
        box = self.ctl.mailclient.winlink
        if mid is not None and box is not None and box.delete(self.folder.currentData(), mid):
            self._reset()

    def _connect(self):
        why = self.ctl.mailclient.winlink_connect(self.gateway.currentData() or "")
        self.note.setText(_reason(self, why) if why else self.tr("Calling %1…").replace("%1", self.gateway.currentText()))

    def session_text(self) -> str:
        last = self.ctl.mailclient.wl_last
        if not last:
            return ""
        if last.get("state") == "calling":
            prog = self.ctl.mailclient.winlink_progress()
            if prog:
                return self.tr("%1: %2 of %3 bytes").replace("%1", last["gateway"]) \
                    .replace("%2", str(prog[2])).replace("%3", str(prog[3]))
            return self.tr("Calling %1…").replace("%1", last["gateway"])
        if last.get("state") == "done":
            return self.tr("%1: sent %2, received %3.").replace("%1", last["gateway"]) \
                .replace("%2", str(last.get("sent", 0))).replace("%3", str(last.get("received", 0)))
        return self.tr("%1: %2").replace("%1", last.get("gateway", "")) \
            .replace("%2", _reason(self, last.get("error", "")))

    def refresh(self):
        w = self.ctl.settings.winlink
        chosen = self.gateway.currentData()
        gateways = [str(g) for g in w.gateways]
        if [self.gateway.itemData(i) for i in range(self.gateway.count())] != gateways:
            self.gateway.blockSignals(True)
            self.gateway.clear()
            for g in gateways:
                self.gateway.addItem(g.split()[0], g)
            want = chosen or w.gateway
            if want in gateways:
                self.gateway.setCurrentIndex(gateways.index(want))
            self.gateway.blockSignals(False)
        text = self.session_text()
        if text:
            self.note.setText(text)
        box = self.ctl.mailclient.winlink
        items = box.items(self.folder.currentData()) if box is not None else []
        sig = tuple((i.mid, i.read) for i in items)
        if sig == self._sig:
            return
        self._sig = sig
        self.table.setRowCount(len(items))
        for r, i in enumerate(items):
            when = QTableWidgetItem(fmt.utc_datetime(i.date))
            when.setData(Qt.ItemDataRole.UserRole, i.mid)
            row = (when, QTableWidgetItem(i.sender), QTableWidgetItem(", ".join(i.to)),
                   QTableWidgetItem(self.tr("yes") if i.read else self.tr("new")), QTableWidgetItem(i.subject))
            for col, it in enumerate(row):
                self.table.setItem(r, col, it)
        tables.fit(self.table)


class MailboxPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        #: Tests replace the compose dialog's exec with this (it fills the
        #: dialog in); None runs the dialog for a person.
        self.dialog_hook = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.packet = PacketMail(self)
        self.winlink = WinlinkMail(self)
        self.tabs.addTab(self.packet, self.tr("Packet mail"))
        self.tabs.addTab(self.winlink, self.tr("Winlink email"))
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        lay.addWidget(self.tabs)
        # The 0.2 page's names, which its tests and the notifier use.
        for name in ("enable", "where", "table", "body", "sim_mail", "note", "delete"):
            setattr(self, name, getattr(self.packet, name))

    def refresh(self):
        if self.tabs.currentWidget() is self.winlink:
            self.winlink.refresh()
        else:
            self.packet.refresh()
