"""The helpers beside a Connect/Chat session's terminal (0.3.0): a node's
commands, a BBS's message list and reader, a conference's participants;
the hosted room's tab; and the dialogs for connect scripts and a new BBS
message.

Every helper button types the command it stands for into the session, as
the operator could (bbs.py says why the terminal stays raw); none sends
anything by itself.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                             QSpinBox, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import scripts as scripts_mod
from . import callinfo, fmt, tables


def _mono(w):
    w.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return w


class NodePanel(QWidget):
    """A node's everyday commands."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for label, command, tip in (
                (self.tr("Nodes"), "N", self.tr("Types N: the nodes this node knows (they go to the Nodes page)")),
                (self.tr("Routes"), "R", self.tr("Types R: this node's neighbours")),
                (self.tr("BBS"), "BBS", self.tr("Types BBS: the node's mailbox, if it has one")),
                (self.tr("Info"), "I", self.tr("Types I: the node's information text"))):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, cmd=command: view.helper(cmd))
            lay.addWidget(b)
        self.conf = QPushButton(self.tr("Join conference"))
        self.conf.setToolTip(self.tr("Types the conference command (Settings: CHAT unless you changed it)"))
        self.conf.clicked.connect(lambda: view.ctl.join_conference(view.sess.id))
        lay.addWidget(self.conf)
        lay.addStretch(1)


class ConferencePanel(QWidget):
    """Who is in the conference, read from its lines (terminal.Participants)."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self._rev = -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(self.tr("In the conference")))
        self.people = QListWidget()
        lay.addWidget(self.people, 1)
        row = QHBoxLayout()
        who = QPushButton(self.tr("Who"))
        who.setToolTip(self.tr("Types /w: the conference lists who is there"))
        who.clicked.connect(lambda: view.helper("/w"))
        leave = QPushButton(self.tr("Leave"))
        leave.setToolTip(self.tr("Types /q: back to the node"))
        leave.clicked.connect(lambda: view.helper("/q"))
        row.addWidget(who)
        row.addWidget(leave)
        lay.addLayout(row)

    def refresh(self):
        r = self.view.sess.reader
        if r.participants_rev == self._rev:
            return
        self._rev = r.participants_rev
        self.people.clear()
        for call in r.participants.calls:
            item = QListWidgetItem(call)
            item.setToolTip(callinfo.tooltip(self.view.ctl, call))
            self.people.addItem(item)


class MailPanel(QWidget):
    """A BBS's message list (L, LM), the message read (R n), Kill and a
    new message, over the raw session."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self._mail_rev = self._read_rev = -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.list_all = QPushButton(self.tr("List"))
        self.list_all.setToolTip(self.tr("Types L: the newest messages"))
        self.list_all.clicked.connect(lambda: view.helper("L", "list"))
        self.list_mine = QPushButton(self.tr("Mine"))
        self.list_mine.setToolTip(self.tr("Types LM: messages to and from you"))
        self.list_mine.clicked.connect(lambda: view.helper("LM", "list"))
        self.read = QPushButton(self.tr("Read"))
        self.read.clicked.connect(self._read)
        self.kill = QPushButton(self.tr("Kill"))
        self.kill.clicked.connect(self._kill)
        self.new = QPushButton(self.tr("New…"))
        self.new.clicked.connect(self._new)
        for b in (self.list_all, self.list_mine, self.read, self.kill, self.new):
            row.addWidget(b)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = tables.make([self.tr("No."), self.tr("Date"), self.tr("Type"), self.tr("To"),
                                  self.tr("From"), self.tr("Subject")])
        self.table.doubleClicked.connect(lambda _i: self._read())
        self.reader = _mono(QPlainTextEdit())
        self.reader.setReadOnly(True)
        split.addWidget(self.table)
        split.addWidget(self.reader)
        lay.addWidget(split, 1)

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _read(self):
        n = self._selected()
        if n is not None:
            self.view.helper(f"R {n}", "read", n)

    def _kill(self):
        n = self._selected()
        if n is None:
            return
        if QMessageBox.question(self, self.tr("Kill message"),
                                self.tr("Delete message %1 on the BBS?").replace("%1", str(n))) \
                == QMessageBox.StandardButton.Yes:
            self.view.helper(f"K {n}", "kill", n)

    def _new(self):
        dlg = ComposeDialog(self)
        if dlg.exec():
            to, subject, body, bulletin = dlg.values()
            self.view.ctl.send_mail(self.view.sess.id, to, subject, body, bulletin)

    def refresh(self):
        r = self.view.sess.reader
        if r.mail_rev != self._mail_rev:
            self._mail_rev = r.mail_rev
            rows = []
            for m in r.mail:
                num = QTableWidgetItem(str(m.number))
                num.setData(Qt.ItemDataRole.UserRole, m.number)
                frm = QTableWidgetItem(m.sender)
                frm.setToolTip(callinfo.tooltip(self.view.ctl, m.sender))
                rows.append([num, m.date, m.type + m.status, m.to + (f" @{m.at}" if m.at else ""), frm, m.subject])
            tables.fill(self.table, rows)
        if r.read_rev != self._read_rev:
            self._read_rev = r.read_rev
            self.reader.setPlainText(r.read_text)


class ComposeDialog(QDialog):
    """A new message for the BBS: sent by the BBS's own SP/SB dialogue."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("New BBS message"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.to = QLineEdit()
        self.to.setPlaceholderText(self.tr("Callsign, or ALL for a bulletin"))
        self.subject = QLineEdit()
        self.bulletin = QCheckBox(self.tr("Bulletin (SB) rather than a personal message (SP)"))
        form.addRow(self.tr("To"), self.to)
        form.addRow(self.tr("Subject"), self.subject)
        form.addRow(self.bulletin)
        lay.addLayout(form)
        self.body = _mono(QPlainTextEdit())
        lay.addWidget(self.body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Send"))
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(520, 420)

    def _ok(self):
        if self.to.text().strip() and self.subject.text().strip():
            self.accept()

    def values(self):
        return (self.to.text().strip().upper(), self.subject.text().strip(), self.body.toPlainText(),
                self.bulletin.isChecked())


class ScriptsDialog(QDialog):
    """The operator's connect scripts: a list, and the selected one's steps."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr("Connect scripts"))
        self.items: list[dict] = [dict(s) for s in window.settings.bbs.scripts]
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.names = QListWidget()
        self.names.currentRowChanged.connect(self._show)
        left.addWidget(self.names, 1)
        add = QPushButton(self.tr("New"))
        add.clicked.connect(self._add)
        rename = QPushButton(self.tr("Rename"))
        rename.clicked.connect(self._rename)
        remove = QPushButton(self.tr("Delete"))
        remove.clicked.connect(self._remove)
        for b in (add, rename, remove):
            left.addWidget(b)
        lay.addLayout(left)
        right = QVBoxLayout()
        help_ = QLabel(self.tr("One step a line: connect CALL [via A,B] first, then wait TEXT (until the far end "
                               "sends it; a|b for either), send TEXT (one line; send alone is a bare return), "
                               "timeout SECONDS. Nothing is sent before the link is up, and a wait that does "
                               "not come true stops the script."))
        help_.setWordWrap(True)
        help_.setObjectName("dim")
        right.addWidget(help_)
        self.text = _mono(QPlainTextEdit())
        self.text.textChanged.connect(self._edited)
        right.addWidget(self.text, 1)
        self.problem = QLabel()
        self.problem.setObjectName("dim")
        right.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        lay.addLayout(right, 1)
        for s in self.items:
            self.names.addItem(s.get("name", ""))
        if self.items:
            self.names.setCurrentRow(0)
        self.resize(720, 420)

    def _show(self, row):
        self.text.blockSignals(True)
        self.text.setPlainText(self.items[row].get("text", "") if 0 <= row < len(self.items) else "")
        self.text.blockSignals(False)
        self._check()

    def _edited(self):
        row = self.names.currentRow()
        if 0 <= row < len(self.items):
            self.items[row]["text"] = self.text.toPlainText()
        self._check()

    def _check(self):
        try:
            scripts_mod.parse(self.text.toPlainText())
            self.problem.setText("")
        except scripts_mod.ScriptError as e:
            self.problem.setText(self.tr("Line %1: %2").replace("%1", str(e.line)).replace("%2", script_problem(e.key)))

    def _add(self, name: str | None = None):
        if name is None:
            name, ok = QInputDialog.getText(self, self.tr("New script"), self.tr("Name"))
            if not ok:
                return
        name = (name or "").strip() or self.tr("Script")
        self.items.append({"name": name, "text": "connect GB7SIM\nwait ? for help\nsend BBS\n"})
        self.names.addItem(name)
        self.names.setCurrentRow(len(self.items) - 1)

    def _rename(self):
        row = self.names.currentRow()
        if row < 0:
            return
        name, ok = QInputDialog.getText(self, self.tr("Rename script"), self.tr("Name"),
                                        text=self.items[row]["name"])
        if ok and name.strip():
            self.items[row]["name"] = name.strip()
            self.names.item(row).setText(name.strip())

    def _remove(self):
        row = self.names.currentRow()
        if row >= 0:
            del self.items[row]
            self.names.takeItem(row)

    def save(self):
        self.window.settings.bbs.scripts = [dict(s) for s in self.items]
        self.window.save_settings()
        self.accept()


def script_problem(key: str) -> str:
    from PyQt6.QtCore import QCoreApplication
    return {
        "unknown-step": QCoreApplication.translate("Scripts", "not a step (connect, wait, send, timeout)"),
        "connect-not-first": QCoreApplication.translate("Scripts", "connect comes first, once"),
        "connect-needs-call": QCoreApplication.translate("Scripts", "connect needs a callsign"),
        "wait-needs-text": QCoreApplication.translate("Scripts", "wait needs the text to wait for"),
        "bad-timeout": QCoreApplication.translate("Scripts", "timeout needs a number of seconds"),
        "too-long": QCoreApplication.translate("Scripts", "too many steps"),
        "empty": QCoreApplication.translate("Scripts", "the script is empty"),
    }.get(key, key)


def note_text(key: str) -> str | None:
    """Words for the session notes 0.3.0 adds; None for older keys."""
    from PyQt6.QtCore import QCoreApplication
    return {
        "script-started": QCoreApplication.translate("Scripts", "Script started: it runs once the link is up."),
        "script-timeout": QCoreApplication.translate("Scripts", "Script stopped: what it waited for did not come."),
        "capture-started": QCoreApplication.translate("Scripts", "Capturing this session to a file."),
        "capture-failed": QCoreApplication.translate("Scripts", "Capture stopped: the file could not be written."),
    }.get(key)


class RoomView(QWidget):
    """The room hosted here (room.py): open or close it, its members, what
    is said, and the operator's own line to everyone."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self._rev = -1
        self._shown = 0
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.toggle = QPushButton()
        self.toggle.clicked.connect(self._toggle)
        top.addWidget(self.state, 1)
        top.addWidget(self.toggle)
        lay.addLayout(top)
        form = QHBoxLayout()
        s = page.window.settings.room
        self.name = QLineEdit(s.name)
        self.name.setPlaceholderText(self.tr("Room name"))
        self.topic = QLineEdit(s.topic)
        self.topic.setPlaceholderText(self.tr("Topic"))
        self.topic.editingFinished.connect(self._topic)
        self.ssid = QSpinBox()
        self.ssid.setRange(0, 15)
        self.ssid.setValue(int(s.ssid))
        self.ssid.setPrefix(self.tr("SSID "))
        self.max = QSpinBox()
        self.max.setRange(1, 50)
        self.max.setValue(int(s.max_members))
        self.max.setSuffix(self.tr(" stations"))
        self.idle = QSpinBox()
        self.idle.setRange(1, 240)
        self.idle.setValue(int(s.idle_minutes))
        self.idle.setSuffix(self.tr(" min idle"))
        for w in (self.name, self.topic):
            form.addWidget(w, 1)
        for w in (self.ssid, self.max, self.idle):
            form.addWidget(w)
        lay.addLayout(form)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.screen = _mono(QPlainTextEdit())
        self.screen.setReadOnly(True)
        self.members = QListWidget()
        split.addWidget(self.screen)
        split.addWidget(self.members)
        split.setStretchFactor(0, 4)
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.line = _mono(QLineEdit())
        self.line.setPlaceholderText(self.tr("To everyone in the room"))
        self.line.returnPressed.connect(self.send)
        row.addWidget(self.line, 1)
        lay.addLayout(row)

    @property
    def ctl(self):
        return self.page.window.ctl

    def _store(self):
        s = self.page.window.settings.room
        s.name, s.topic = self.name.text().strip(), self.topic.text().strip()
        s.ssid, s.max_members, s.idle_minutes = self.ssid.value(), self.max.value(), self.idle.value()

    def _topic(self):
        if self.ctl.room.open:
            self.ctl.room.set_topic(self.topic.text())

    def _toggle(self):
        room = self.ctl.room
        if room.open:
            room.stop()
        else:
            self._store()
            self.page.window.save_settings()
            why = room.start()
            if why:
                QMessageBox.information(self, self.tr("Host a room"), room_problem(why))
        self.refresh()

    def send(self):
        text = self.line.text()
        if text.strip() and self.ctl.room.say(text):
            self.line.clear()

    def refresh(self):
        room, armed = self.ctl.room, self.ctl.status()["armed"]
        for w in (self.name, self.ssid, self.max, self.idle):
            w.setEnabled(not room.open)
        self.line.setEnabled(room.open)
        if room.open:
            self.toggle.setText(self.tr("Close the room"))
            self.state.setText((self.tr("Open on %1.").replace("%1", room.address()) + " " +
                                (self.tr("Stations can join.") if armed else
                                 self.tr("Transmit is not armed: nobody is let in until it is."))))
        else:
            self.toggle.setText(self.tr("Open the room"))
            self.state.setText(self.tr("Host a conference room: stations connect to your call with this SSID and "
                                       "everything one says goes to all. Only while you have it open and "
                                       "transmit is armed."))
        names = room.members()
        if [self.members.item(i).text() for i in range(self.members.count())] != names:
            self.members.clear()
            for call in names:
                item = QListWidgetItem(call)
                item.setToolTip(callinfo.tooltip(self.ctl, call))
                self.members.addItem(item)
        if room.rev == self._rev:
            return
        self._rev = room.rev
        new = room.lines[self._shown:]
        self._shown = len(room.lines)
        for ln in new:
            self.screen.appendPlainText(f"{fmt.utc_time(ln.time)}  {room_line(ln)}")
        self.screen.moveCursor(QTextCursor.MoveOperation.End)


def room_line(ln) -> str:
    from PyQt6.QtCore import QCoreApplication
    tr = QCoreApplication.translate
    if ln.kind in ("say", "host"):
        return f"{ln.call}: {ln.text}"
    if ln.kind == "me":
        return f"* {ln.call} {ln.text}"
    words = {
        "join": tr("Room", "*** %1 joined"), "leave": tr("Room", "*** %1 left"),
        "idle": tr("Room", "*** %1 was disconnected after no input"),
    }
    if ln.kind in words:
        return words[ln.kind].replace("%1", ln.call)
    if ln.kind == "topic":
        return tr("Room", "*** %1 set the topic: %2").replace("%1", ln.call).replace("%2", ln.text)
    return {"room-opened": tr("Room", "*** The room is open"),
            "room-closed": tr("Room", "*** The room is closed")}.get(ln.text, ln.text)


def room_problem(key: str) -> str:
    from PyQt6.QtCore import QCoreApplication
    return {
        "no-callsign": QCoreApplication.translate("Room", "The room needs your callsign (Station › Setup check)."),
        "room-ssid-taken": QCoreApplication.translate(
            "Room", "That SSID is your station's or your mailbox's; choose another for the room."),
    }.get(key, key)
