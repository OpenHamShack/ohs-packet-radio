"""Connect / Chat: terminal-style AX.25 connected-mode sessions, several at
once, to stations, nodes and BBSs.

Node-friendly in the ways that matter on a node: a line goes when Enter is
pressed, ending in CR as every TNC sends it (never an empty or blank one,
which would key the radio for a bare CR); what arrives is shown as it
comes, including a prompt the node sent without a line end; the up arrow
recalls earlier lines; the path is typed as a node user writes it
("GB7SIM" or "via MB7UC GB7XX"); and an incoming connection opens its own
tab (when the operator allows incoming chats). Frames are only sent while
transmit is armed: a session started disarmed waits, and says so.

0.3.0 adds, around the same raw terminal: helpers for what the far end
is (a node's commands, a BBS's message list and reader, a conference's
participants: ui/sessiontools.py), Continue and Stop buttons at a paging
question, capture to a file, the session's log entry, connect scripts
(the Scripts menu), the hosted room in a tab of its own, and an unread
count on each tab that is not the one in front.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                             QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout,
                             QWidget)

from .. import callinfo, fmt, texts, theme
from ..sessiontools import (ConferencePanel, MailPanel, NodePanel, RoomView, ScriptsDialog, note_text,
                            script_problem)
from ... import scripts as scripts_mod
from ...controller import valid_address


class SessionView(QWidget):
    def __init__(self, page, sess):
        super().__init__()
        self.page = page
        self.sess = sess
        self._shown = 0
        self._rev = -1
        self.unread = 0
        self.history: list[str] = []
        self._hist_at = 0
        outer = QVBoxLayout(self)
        head = QHBoxLayout()
        self.state = QLabel()
        self.state.setObjectName("dim")
        self.who = QLabel()
        self.who.setObjectName("dim")
        self.kind = QComboBox()
        for key, name in (("chat", self.tr("Station")), ("node", self.tr("Node")), ("bbs", self.tr("BBS")),
                          ("conference", self.tr("Conference"))):
            self.kind.addItem(name, key)
        self.kind.setToolTip(self.tr("What is at the other end: picks the helpers beside the terminal. Guessed "
                                     "from what it sends until you choose."))
        self.kind.activated.connect(self._kind_chosen)
        self.capture = QCheckBox(self.tr("Capture"))
        self.capture.setToolTip(self.tr("Append everything in this session to a text file"))
        self.capture.toggled.connect(self._capture)
        self.log_btn = QPushButton(self.tr("Log contact"))
        self.log_btn.clicked.connect(self._log)
        self.stop_script = QPushButton(self.tr("Stop script"))
        self.stop_script.clicked.connect(lambda: page.window.ctl.stop_script(sess.id))
        head.addWidget(self.state, 1)
        head.addWidget(self.who)
        head.addWidget(self.kind)
        head.addWidget(self.capture)
        head.addWidget(self.log_btn)
        head.addWidget(self.stop_script)
        outer.addLayout(head)
        split = QSplitter(Qt.Orientation.Horizontal)
        term = QWidget()
        lay = QVBoxLayout(term)
        lay.setContentsMargins(0, 0, 0, 0)
        self.screen = QPlainTextEdit()
        self.screen.setReadOnly(True)
        self.screen.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.screen.setMaximumBlockCount(5000)
        lay.addWidget(self.screen, 1)
        self.prompt = QLabel()
        self.prompt.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.prompt)
        # A paging question ("<A>bort, <CR> Continue..>", "More (Y/N)?"):
        # its two answers as buttons, because the answer to carry on is
        # often a bare return, which the line below never sends.
        self.paging = QWidget()
        pl = QHBoxLayout(self.paging)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel(self.tr("The far end is waiting before it sends more.")), 1)
        self.more = QPushButton(self.tr("Continue"))
        self.more.clicked.connect(lambda: page.window.ctl.page(sess.id, True))
        self.enough = QPushButton(self.tr("Stop listing"))
        self.enough.clicked.connect(lambda: page.window.ctl.page(sess.id, False))
        pl.addWidget(self.more)
        pl.addWidget(self.enough)
        self.paging.hide()
        lay.addWidget(self.paging)
        row = QHBoxLayout()
        self.line = QLineEdit()
        self.line.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.line.setPlaceholderText(self.tr("Type and press Enter"))
        self.line.returnPressed.connect(self.send)
        self.line.installEventFilter(self)
        self.send_btn = QPushButton(self.tr("Send"))
        self.send_btn.clicked.connect(self.send)
        self.bye = QPushButton(self.tr("Disconnect"))
        self.bye.clicked.connect(lambda: page.window.ctl.disconnect(sess.id))
        row.addWidget(self.line, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.bye)
        lay.addLayout(row)
        split.addWidget(term)
        self.side = QStackedWidget()
        self.panels = {"chat": QWidget(), "node": NodePanel(self), "bbs": MailPanel(self),
                       "conference": ConferencePanel(self)}
        for w in self.panels.values():
            self.side.addWidget(w)
        split.addWidget(self.side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

    @property
    def ctl(self):
        return self.page.window.ctl

    def helper(self, command: str, expect: str = "", number: int = 0) -> None:
        self.ctl.helper(self.sess.id, command, expect, number)

    def _kind_chosen(self, _index):
        with self.ctl._lock:
            self.sess.reader.set_kind(self.kind.currentData(), by_hand=True)
        self.refresh()

    def _capture(self, on: bool):
        if not on:
            self.ctl.capture_stop(self.sess.id)
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Capture to file"), f"{self.sess.remote}.txt",
                                              self.tr("Text files (*.txt)"))
        if not path:
            self.capture.blockSignals(True)
            self.capture.setChecked(False)
            self.capture.blockSignals(False)
            return
        try:
            self.ctl.capture_start(self.sess.id, path)
        except OSError as e:
            QMessageBox.warning(self, self.tr("Capture to file"), str(e))
            self.capture.setChecked(False)

    def _log(self):
        c = self.ctl.log_session(self.sess.id)
        if c is None:
            QMessageBox.information(self, self.tr("Log contact"),
                                    self.tr("This session never connected, so it is not a contact."))
        else:
            self.page.window.statusBar().showMessage(
                self.tr("Logged %1 (Log page).").replace("%1", c.call), 8000)

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if obj is self.line and ev.type() == QEvent.Type.KeyPress and self.history:
            if ev.key() == Qt.Key.Key_Up:
                self._hist_at = max(0, self._hist_at - 1)
                self.line.setText(self.history[self._hist_at])
                return True
            if ev.key() == Qt.Key.Key_Down:
                self._hist_at = min(len(self.history), self._hist_at + 1)
                self.line.setText(self.history[self._hist_at] if self._hist_at < len(self.history) else "")
                return True
        return super().eventFilter(obj, ev)

    def send(self):
        text = self.line.text()
        # An empty or blank line is not sent (the station core refuses it
        # too): Enter on nothing would key the radio for a bare CR. A blank
        # line is left where it is, so the press visibly did nothing.
        if self.sess.state == "disconnected" or not text.strip():
            return
        self.page.window.ctl.send(self.sess.id, text)
        self.history.append(text)
        self._hist_at = len(self.history)
        self.line.clear()

    def _note(self, key: str) -> str:
        kind, _, reason = key.partition(":")
        return "*** " + (note_text(reason) or texts.reason(reason or kind))

    def refresh(self):
        ctl = self.page.window.ctl
        sess = self.sess
        armed = ctl.status()["armed"]
        state = sess.state
        if state == "connected":
            self.state.setText(self.tr("Connected to %1").replace("%1", sess.remote) +
                               (self.tr(" via %1").replace("%1", ", ".join(sess.via)) if sess.via else ""))
        elif state == "disconnected":
            self.state.setText(self.tr("Disconnected from %1").replace("%1", sess.remote))
        elif not armed:
            self.state.setText(self.tr("Waiting for transmit to be armed to call %1").replace("%1", sess.remote))
        else:
            self.state.setText(self.tr("Calling %1…").replace("%1", sess.remote))
        self.bye.setEnabled(state != "disconnected")
        self.send_btn.setEnabled(state != "disconnected")
        self.prompt.setText(ctl.chat_flush(sess))
        reader = sess.reader
        self.paging.setVisible(state == "connected" and reader.prompt_kind == "page")
        if self.kind.currentData() != reader.kind:
            self.kind.setCurrentIndex(self.kind.findData(reader.kind))
        self.side.setCurrentWidget(self.panels[reader.kind])
        self.side.setVisible(reader.kind != "chat")
        panel = self.panels[reader.kind]
        if hasattr(panel, "refresh"):
            panel.refresh()
        run = sess.script
        self.stop_script.setVisible(run is not None and run.state in ("waiting-link", "running"))
        if run is not None and run.state == "running" and run.waiting_for:
            self.stop_script.setToolTip(self.tr("Script %1 is waiting for: %2").replace("%1", run.name)
                                        .replace("%2", run.waiting_for))
        if sess.capture is None and self.capture.isChecked():
            self.capture.blockSignals(True)
            self.capture.setChecked(False)
            self.capture.blockSignals(False)
        tip = callinfo.tooltip(ctl, sess.remote)
        self.who.setText(tip.split("\n", 1)[0] if tip and ":" in tip.split("\n", 1)[0] else "")
        self.who.setToolTip(tip)
        if sess.rev == self._rev:
            return
        self._rev = sess.rev
        with ctl._lock:
            new = sess.lines[self._shown:]
            self._shown = len(sess.lines)
        said = sum(1 for line in new if line.direction == "rx" and line.text.strip())
        if said and not self.page.in_front(self):
            self.unread += said
        for line in new:
            stamp = fmt.utc_time(line.time)
            if line.direction == "note":
                text = self._note(line.text)
            elif line.direction == "tx":
                text = "> " + line.text
            else:
                text = line.text
            self.screen.appendPlainText(f"{stamp}  {text}")
        self.screen.moveCursor(QTextCursor.MoveOperation.End)


class ChatPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.views: dict[int, SessionView] = {}
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.call = QLineEdit()
        self.call.setPlaceholderText(self.tr("Callsign, e.g. GB7SIM"))
        self.call.setMaximumWidth(240)
        self.via = QLineEdit()
        self.via.setPlaceholderText(self.tr("Via (digipeaters, in order; optional)"))
        self.go = QPushButton(self.tr("Connect"))
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.connect)
        self.call.returnPressed.connect(self.connect)
        self.scripts = QPushButton(self.tr("Scripts"))
        self.scripts.setToolTip(self.tr("Run one of your connect scripts, or write one"))
        self.script_menu = QMenu(self)
        self.script_menu.aboutToShow.connect(self._fill_scripts)
        self.scripts.setMenu(self.script_menu)
        self.host = QPushButton(self.tr("Room"))
        self.host.setToolTip(self.tr("Host a conference room other stations connect to"))
        self.host.clicked.connect(self.show_room)
        bar.addWidget(QLabel(self.tr("Connect to")))
        bar.addWidget(self.call)
        bar.addWidget(self.via, 1)
        bar.addWidget(self.go)
        bar.addWidget(self.scripts)
        bar.addWidget(self.host)
        lay.addLayout(bar)
        self.room_view: RoomView | None = None
        self._room_rev = -1
        self.room_unread = 0
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close)
        self.tabs.currentChanged.connect(lambda _i: self._mark_tabs())
        lay.addWidget(self.tabs, 1)
        self.empty = QLabel(self.tr("No sessions. Connect to a station or a node above; stations that "
                                    "connect to you open here too."))
        self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)

    def connect(self):
        call = self.call.text().strip().upper()
        via = self.via.text().strip().upper()
        if via.startswith("VIA "):
            via = via[4:]
        if not call:
            return
        bad = [c for c in [call] + via.replace(",", " ").split() if not valid_address(c)]
        if bad:
            QMessageBox.warning(self, self.tr("Connect"), self.tr("%1 is not a callsign AX.25 can carry.")
                                .replace("%1", bad[0]))
            return
        try:
            sess = self.window.ctl.connect(call, via)
        except Exception as e:                      # noqa: BLE001 - no callsign, not started
            QMessageBox.warning(self, self.tr("Connect"), texts.reason("no-callsign") if "callsign" in str(e)
                                else str(e))
            return
        self._view(sess)
        self.tabs.setCurrentWidget(self.views[sess.id])
        self.views[sess.id].line.setFocus()

    def _view(self, sess) -> SessionView:
        if sess.id not in self.views:
            v = SessionView(self, sess)
            self.views[sess.id] = v
            self.tabs.addTab(v, self._title(v))
        return self.views[sess.id]

    @staticmethod
    def _title(v: SessionView) -> str:
        return v.sess.remote + (" ←" if v.sess.incoming else "")

    def in_front(self, widget) -> bool:
        """Whether the operator can see `widget`'s tab: this page showing,
        the window active, and that tab the current one."""
        w = self.window
        return (w.current_key() == "chat" and w.notifier.active() and self.tabs.currentWidget() is widget)

    def _mark_tabs(self):
        """Each tab's unread count in its title, in the accent colour; the
        tab in front is read."""
        current = self.tabs.currentWidget()
        if isinstance(current, SessionView) and self.window.notifier.active():
            current.unread = 0
        if current is self.room_view and current is not None:
            self.room_unread = 0
        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SessionView):
                n, title = w.unread, self._title(w)
            elif w is self.room_view:
                n, title = self.room_unread, self.tr("Room")
            else:
                continue
            self.tabs.setTabText(i, title + (f" ({n})" if n else ""))
            bar.setTabTextColor(i, theme.colour("accent") if n else theme.colour("ink"))

    def unread_total(self) -> int:
        return sum(v.unread for v in self.views.values()) + self.room_unread

    def show_room(self):
        if self.room_view is None:
            self.room_view = RoomView(self)
            self.tabs.addTab(self.room_view, self.tr("Room"))
        self.tabs.setCurrentWidget(self.room_view)
        self.room_view.refresh()

    def _fill_scripts(self):
        m = self.script_menu
        m.clear()
        for item in self.window.settings.bbs.scripts:
            m.addAction(item.get("name", ""), lambda it=item: self.run_script(it))
        if self.window.settings.bbs.scripts:
            m.addSeparator()
        m.addAction(self.tr("Edit scripts…"), self.edit_scripts)

    def edit_scripts(self):
        dlg = ScriptsDialog(self.window)
        dlg.open()
        self.scripts_dialog = dlg
        return dlg

    def run_script(self, item: dict):
        try:
            sess = self.window.ctl.run_script(scripts_mod.Script(item.get("name", ""), item.get("text", "")))
        except scripts_mod.ScriptError as e:
            QMessageBox.warning(self, self.tr("Connect script"), self.tr("Line %1: %2").replace(
                "%1", str(e.line)).replace("%2", script_problem(e.key)))
            return None
        except Exception as e:                      # noqa: BLE001 - no callsign, not started, a bad call
            QMessageBox.warning(self, self.tr("Connect script"), texts.reason("no-callsign")
                                if "callsign" in str(e) else str(e))
            return None
        self._view(sess)
        self.tabs.setCurrentWidget(self.views[sess.id])
        return sess

    def _close(self, index: int):
        v = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if v is self.room_view and v is not None:
            if self.window.ctl.room.open:
                self.window.ctl.room.stop()
            self.room_view = None
            return
        for sid, view in list(self.views.items()):
            if view is v:
                del self.views[sid]
                self.window.ctl.forget(sid)

    def reset(self):
        """The station was rebuilt: its sessions are gone (and the room
        closed with it)."""
        self.tabs.clear()
        self.views.clear()
        self.room_view = None

    def refresh(self):
        ctl = self.window.ctl
        with ctl._lock:
            sessions = list(ctl.sessions.values())
        for sess in sessions:
            self._view(sess)
        for v in self.views.values():
            v.refresh()
        room = ctl.room
        if room.rev != self._room_rev:
            if self.room_view is None and room.open:
                self.room_view = RoomView(self)
                self.tabs.addTab(self.room_view, self.tr("Room"))
            if self.room_view is not None:
                new = room.lines[self.room_view._shown:]
                if any(ln.kind in ("say", "me", "join") for ln in new) and not self.in_front(self.room_view):
                    self.room_unread += sum(1 for ln in new if ln.kind in ("say", "me", "join"))
            self._room_rev = room.rev
        if self.room_view is not None:
            self.room_view.refresh()
        self._mark_tabs()
        self.empty.setVisible(self.tabs.count() == 0)
        self.tabs.setVisible(self.tabs.count() > 0)
