"""Sending and receiving files from the window: the Send file dialog (with
a picture's size and quality choices and what each costs on the air), the
prompt for an incoming offer, and the hooks that put both on the Connect /
Chat page - a "Send file…" button beside the tabs and the tabs as a drop
target (files.py has the station side, ui/pages/files.py the list).

The chat page is workstream B's; it is reached only through Qt's own API
(the tab widget's corner, an event filter for drops, `currentWidget()` and
its `sess`), so the page itself needed no edit.

An incoming offer interrupts: a non-modal question with the sender, name,
size and type, where it would be saved and that it will not be opened.
Nothing is written before Accept. A resume of a file already accepted
does not ask again (the Files page's setting).
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPalette, QPixmap
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QInputDialog, QLabel, QMessageBox, QPushButton, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from . import fmt, tables, texts

log = logging.getLogger(__name__)


def size_text(n: int) -> str:
    if n < 1024:
        return QCoreApplication.translate("Files", "%1 bytes").replace("%1", str(n))
    if n < 1024 * 1024:
        kb = fmt.number(n / 1024, 1 if n < 10 * 1024 else 0)
        return QCoreApplication.translate("Files", "%1 KB").replace("%1", kb)
    return QCoreApplication.translate("Files", "%1 MB").replace("%1", fmt.number(n / (1024 * 1024), 1))


def span_text(seconds: float) -> str:
    """"45 s", "2 min 10 s" (to 5 s below ten minutes), "25 min",
    "1 h 20 min": as precise as an estimate is."""
    s = max(1, round(seconds))
    if s < 60:
        return QCoreApplication.translate("Files", "%1 s").replace("%1", str(s))
    if s < 600:
        m, rest = divmod(5 * round(s / 5), 60)
        if rest == 0:
            return QCoreApplication.translate("Files", "%1 min").replace("%1", str(m))
        return QCoreApplication.translate("Files", "%1 min %2 s").replace("%1", str(m)).replace("%2", str(rest))
    if s < 3600:
        return QCoreApplication.translate("Files", "%1 min").replace("%1", str(round(s / 60)))
    h, m = divmod(round(s / 60), 60)
    if m == 0:
        return QCoreApplication.translate("Files", "%1 h").replace("%1", str(h))
    return QCoreApplication.translate("Files", "%1 h %2 min").replace("%1", str(h)).replace("%2", str(m))


def duration_text(seconds: float) -> str:
    """An estimate: "about 2 min 10 s"."""
    return QCoreApplication.translate("Files", "about %1").replace("%1", span_text(seconds))


def mode_name(mode: str) -> str:
    """A mode as the estimate table names it: the modulation and its rate."""
    return {
        "afsk1200": QCoreApplication.translate("Files", "AFSK 1200"),
        "g3ruh9600": QCoreApplication.translate("Files", "G3RUH 9600"),
        "g3ruh19200": QCoreApplication.translate("Files", "G3RUH 19 200"),
        "g3ruh38400": QCoreApplication.translate("Files", "G3RUH 38 400"),
        "gfsk76800": QCoreApplication.translate("Files", "GFSK 76 800"),
        "gfsk153600": QCoreApplication.translate("Files", "GFSK 153 600"),
        "4fsk38400": QCoreApplication.translate("Files", "4-FSK 38 400 (76.8 kbit/s)"),
        "4fsk76800": QCoreApplication.translate("Files", "4-FSK 76 800 (153.6 kbit/s)"),
        "dd128k": QCoreApplication.translate("Files", "23 cm DD 128 kbit/s"),
    }.get(mode, mode)


def _radio_short(kind: str) -> str:
    return {
        "simulator": QCoreApplication.translate("Files", "the simulator"),
        "ic-9700": QCoreApplication.translate("Files", "IC-9700"),
        "ic-705": QCoreApplication.translate("Files", "IC-705"),
        "ftx-1": QCoreApplication.translate("Files", "FTX-1"),
        "tm-v71": QCoreApplication.translate("Files", "TM-V71"),
        "soundcard": QCoreApplication.translate("Files", "a sound card radio"),
        "hackrf": QCoreApplication.translate("Files", "a HackRF"),
        "pluto": QCoreApplication.translate("Files", "a Pluto"),
    }.get(kind, kind)


def why_text(why: str, kind: str) -> str:
    """Why a mode is greyed out (airtime.availability's keys)."""
    return {
        "afsk-only": QCoreApplication.translate("Files", "not available on %1").replace(
            "%1", _radio_short(kind)),
        "radio-modes": QCoreApplication.translate("Files", "not available on %1").replace(
            "%1", _radio_short(kind)),
        "dd-off": QCoreApplication.translate(
            "Files", "not available on IC-9700 until 23 cm DD is turned on (Radio page)"),
        "dd-ic9700": QCoreApplication.translate("Files", "not available on %1: IC-9700 only").replace(
            "%1", _radio_short(kind)),
        "dd-only": QCoreApplication.translate("Files", "not available while 23 cm DD is on"),
        "receive-only": QCoreApplication.translate("Files", "not available: this radio only receives"),
        "no-radio": QCoreApplication.translate("Files", "not available: no radio is set up"),
    }.get(why, why)


def measured_text(seconds: float, m) -> str:
    if m.kind == "linktest":
        what = QCoreApplication.translate("Files", "link test")
    else:
        what = QCoreApplication.translate("Files", "file")
    return (QCoreApplication.translate("Files", "measured with %1: %2 (%3 %4)").replace("%1", m.peer)
            .replace("%2", duration_text(seconds)).replace("%3", what)
            .replace("%4", fmt.utc_date(m.when) if m.when else ""))


class EstimateTable:
    """The estimate table (airtime.table's rows): every mode, how long
    the file takes on a good link and with retries, greyed where this
    radio cannot send it and highlighted where it sends now. A measurement
    with the station is preferred and named."""

    def __init__(self):
        self.table = tables.make([QCoreApplication.translate("Files", "Mode"),
                                  QCoreApplication.translate("Files", "Good link"),
                                  QCoreApplication.translate("Files", "With retries"),
                                  QCoreApplication.translate("Files", "Note")])
        self.table.setSelectionMode(self.table.SelectionMode.NoSelection)
        self.table.setMinimumHeight(self.table.verticalHeader().defaultSectionSize() * 10 + 8)
        self.rows: list = []

    def fill(self, rows, kind: str) -> None:
        self.rows = list(rows)
        out = []
        pal = self.table.palette()
        highlight = QColor(pal.highlight().color())
        highlight.setAlpha(60)
        # Disabled items alone are not grey in every style (the app's dark
        # theme draws them as bright as the rest): the colour is set too.
        grey = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        if grey == pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text):
            grey = QColor(pal.color(QPalette.ColorRole.Text))
            grey.setAlpha(110)
        for r in self.rows:
            if not r.available:
                note = why_text(r.why, kind)
            elif r.measured:
                note = measured_text(*r.measured)
                if r.current:
                    note = QCoreApplication.translate("Files", "this station's mode; %1").replace("%1", note)
            elif r.current:
                note = QCoreApplication.translate("Files", "this station's mode")
            else:
                note = ""
            if r.unmeasured:
                note = (note + "; " if note else "") + QCoreApplication.translate("Files", "estimate, unmeasured")
            items = [QTableWidgetItem(t) for t in (mode_name(r.mode), duration_text(r.good_s),
                                                    duration_text(r.retries_s), note)]
            items[3].setToolTip(note)
            items[0].setToolTip(texts.fast_mode(r.mode) if r.mode != "dd128k" else "")
            for it in items:
                if not r.available:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    it.setForeground(grey)
                if r.current:
                    f = QFont(it.font())
                    f.setBold(True)
                    it.setFont(f)
                    it.setBackground(highlight)
            out.append(items)
        tables.fill(self.table, out)

    def row(self, mode: str):
        return next((r for r in self.rows if r.mode == mode), None)


def state_text(state: str) -> str:
    table = {
        "offering": QCoreApplication.translate("Files", "Waiting for an answer"),
        "offered": QCoreApplication.translate("Files", "Waiting for you"),
        "sending": QCoreApplication.translate("Files", "Sending"),
        "receiving": QCoreApplication.translate("Files", "Receiving"),
        "checking": QCoreApplication.translate("Files", "Checking"),
        "done": QCoreApplication.translate("Files", "Done"),
        "failed": QCoreApplication.translate("Files", "Failed"),
        "declined": QCoreApplication.translate("Files", "Declined"),
        "cancelled": QCoreApplication.translate("Files", "Cancelled"),
        "interrupted": QCoreApplication.translate("Files", "Interrupted (can resume)"),
    }
    return table.get(state, state)


def reason_text(key: str) -> str:
    table = {
        "declined": QCoreApplication.translate("Files", "The other station declined the file."),
        "too-big": QCoreApplication.translate("Files", "The file is bigger than the other station accepts."),
        "cancelled": QCoreApplication.translate("Files", "Cancelled."),
        "peer-cancelled": QCoreApplication.translate("Files", "The other station cancelled."),
        "link-lost": QCoreApplication.translate("Files", "The link dropped. Connect again and it can resume."),
        "timeout": QCoreApplication.translate(
            "Files", "Nothing was heard from the other station for two minutes. It can resume."),
        "stopped": QCoreApplication.translate("Files", "The station was restarted. It can resume."),
        "no-answer": QCoreApplication.translate(
            "Files", "No answer: the other station may not run OpenHamShack. Try YAPP."),
        "not-decided": QCoreApplication.translate("Files", "Nobody answered the offer in time."),
        "hash-mismatch": QCoreApplication.translate(
            "Files", "The file arrived damaged and was thrown away. Sending again starts afresh."),
        "bad-offset": QCoreApplication.translate(
            "Files", "The other station asked for a part of the file that does not exist."),
        "write-failed": QCoreApplication.translate("Files", "The file could not be written."),
        "read-failed": QCoreApplication.translate("Files", "The file could not be read."),
        "encrypted": QCoreApplication.translate(
            "Files", "The file is encrypted, and encrypted messages may not be sent (licence clause 11(2))."),
        "protocol": QCoreApplication.translate(
            "Files", "The other station sent something the protocol does not allow."),
        "not-connected": QCoreApplication.translate("Files", "Connect to the station first."),
        "no-session": QCoreApplication.translate("Files", "Connect to the station first."),
    }
    return table.get(key, key)


def note(parent, title: str, text: str) -> QMessageBox:
    """A message that does not block the window's tick (or a test)."""
    box = QMessageBox(QMessageBox.Icon.Information, title, text, QMessageBox.StandardButton.Ok, parent)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.setWindowModality(Qt.WindowModality.NonModal)
    box.show()
    parent.last_note = text
    return box


def drop_paths(mime) -> list[str]:
    """Local files in a drag (never URLs from elsewhere)."""
    if mime is None or not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and Path(u.toLocalFile()).is_file()]


class SendFileDialog(QDialog):
    """What to send and how: the protocol, and for a picture a size and
    quality, each with its bytes and how long it takes at the station's
    mode; then a table of the time at every mode (EstimateTable), for the
    bytes that would really go - compressed where they would be, or the
    picture as re-encoded for the size chosen."""

    def __init__(self, window, session_id: int, path: str):
        super().__init__(window)
        from .. import pictures
        self.window = window
        self.session_id = session_id
        self.path = Path(path)
        sess = window.ctl.sessions.get(session_id)
        self.peer = sess.remote if sess else ""
        self.setWindowTitle(self.tr("Send a file to %1").replace("%1", self.peer or "?"))
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.size = self.path.stat().st_size
        form.addRow(self.tr("File"), QLabel(f"{self.path.name} ({size_text(self.size)})"))
        self.protocol = QComboBox()
        self.protocol.addItem(self.tr("OpenHamShack (checked, resumable, compressed)"), "ohs")
        self.protocol.addItem(self.tr("YAPP (for other packet software)"), "yapp")
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(window.settings.files.protocol)))
        form.addRow(self.tr("Send by"), self.protocol)
        self.preset = QComboBox()
        self.options: list = []
        self.is_picture = pictures.is_picture(self.path)
        if self.is_picture:
            try:
                self.options = pictures.options(self.path)
            except (ValueError, OSError):
                self.is_picture = False
        #: The bytes that go on the air per protocol, for the file as it is
        #: (compressing it is the slow part: once each).
        self._wire: dict[str, int] = {}
        self.on_air = QLabel()
        if self.is_picture:
            for preset, nbytes, (w, h) in self.options:
                self.preset.addItem("", preset.key)
            self.preset.setCurrentIndex(max(0, self.preset.findData(window.settings.files.picture_preset)))
            form.addRow(self.tr("Picture"), self.preset)
            note = QLabel(self.tr("A smaller copy is made and sent as a JPEG; the original stays as it is. "
                                  "\"Original\" sends the file untouched, with any location and camera "
                                  "details in it."))
            note.setWordWrap(True)
            note.setObjectName("dim")
            form.addRow("", note)
            thumb = pictures.thumbnail(self.path, 200)
            if thumb is not None:
                pic = QLabel()
                pic.setPixmap(QPixmap.fromImage(thumb))
                form.addRow("", pic)
        else:
            self.preset.hide()
        form.addRow(self.tr("On the air"), self.on_air)
        lay.addLayout(form)
        heading = QLabel(self.tr("Time to send, at each mode"))
        heading.setObjectName("heading")
        lay.addWidget(heading)
        self.estimates = EstimateTable()
        lay.addWidget(self.estimates.table)
        busy = QLabel(self.tr("Good link: nothing lost. With retries: one frame in ten lost and sent again. "
                              "Both assume nobody else is using the channel. A transfer or link test with "
                              "this station replaces the estimate with what was measured."))
        busy.setWordWrap(True)
        busy.setObjectName("dim")
        lay.addWidget(busy)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.send_btn = QPushButton(self.tr("Send"))
        self.send_btn.setObjectName("primary")
        buttons.addButton(self.send_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.send)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.transfer = None
        self.error = ""
        self.protocol.currentIndexChanged.connect(self._update)
        self.preset.currentIndexChanged.connect(self._update)
        self._update()

    def _file_wire(self, protocol: str) -> int:
        if protocol not in self._wire:
            try:
                self._wire[protocol] = self.window.ctl.files.wire_size(self.path, protocol=protocol)
            except OSError:
                self._wire[protocol] = self.size
        return self._wire[protocol]

    def on_air_bytes(self) -> tuple[int, int | None]:
        """(bytes on the air, the original's chunks when compressed) for
        the protocol and picture size chosen now."""
        protocol = self.protocol.currentData()
        key = self.preset.currentData() if self.is_picture else "original"
        if key and key != "original":
            # A re-encoded JPEG: compressed already, sent as it is.
            return next(n for p, n, _ in self.options if p.key == key), None
        wire = self._file_wire(protocol)
        return wire, (-(-self.size // 1024) if wire < self.size else None)

    def _rows(self, nbytes: int, chunks: int | None) -> list:
        return self.window.ctl.files.estimates(nbytes, peer=self.peer, protocol=self.protocol.currentData(),
                                               chunks=chunks)

    def _update(self, *_):
        """Re-reckon for the protocol and picture size chosen."""
        nbytes, chunks = self.on_air_bytes()
        rows = self._rows(nbytes, chunks)
        self.estimates.fill(rows, self.window.ctl.settings.radio.kind)
        if chunks is not None:
            self.on_air.setText(self.tr("%1 after compression").replace("%1", size_text(nbytes)))
        else:
            self.on_air.setText(size_text(nbytes))
        if self.is_picture:
            # Each size's time at this station's mode (or as measured), in
            # its own line of the list.
            protocol = self.protocol.currentData()
            for i, (preset, n, (w, h)) in enumerate(self.options):
                if preset.key == "original":
                    n = self._file_wire(protocol)
                    c = -(-self.size // 1024) if n < self.size else None
                else:
                    c = None
                now = next((r for r in self._rows(n, c) if r.current), None)
                name = {"original": self.tr("Original, as it is"), "large": self.tr("Large"),
                        "medium": self.tr("Medium"), "small": self.tr("Small"), "tiny": self.tr("Tiny")}[preset.key]
                text = (self.tr("%1 - %2 × %3, %4, %5 at %6").replace("%1", name).replace("%2", str(w))
                        .replace("%3", str(h)).replace("%4", size_text(n))
                        .replace("%5", duration_text(now.best_s) if now else "?")
                        .replace("%6", mode_name(now.mode) if now else "?"))
                self.preset.setItemText(i, text)

    def send(self):
        from .. import pictures
        files = self.window.ctl.files
        data, name = None, None
        key = self.preset.currentData() if self.is_picture else "original"
        try:
            if key and key != "original":
                data = pictures.reencode(pictures.load(self.path), pictures.PRESET[key])
                name = pictures.jpeg_name(self.path.name)
            self.transfer = files.send(self.session_id, self.path, protocol=self.protocol.currentData(), data=data,
                                       name=name)
        except (ValueError, OSError) as e:
            self.error = str(e)
            note(self, self.windowTitle(), reason_text(str(e)))
            return
        if self.is_picture:
            self.window.settings.files.picture_preset = key
        self.accept()


class OfferPrompt(QMessageBox):
    """An incoming offer, asked about without blocking the window."""

    def __init__(self, window, t):
        super().__init__(window)
        self.window = window
        self.t = t
        files = window.ctl.files
        self.setIcon(QMessageBox.Icon.Question)
        self.setWindowTitle(self.tr("File from %1").replace("%1", t.peer))
        text = self.tr("%1 offers you %2 (%3, %4).").replace("%1", t.peer).replace("%2", t.name).replace(
            "%3", size_text(t.size)).replace("%4", t.mime)
        if t.start_at:
            text += " " + self.tr("You already have %1 of it; it carries on from there.").replace(
                "%1", f"{int(100 * t.start_at / max(1, t.size))} %")
        text += "\n\n" + self.tr("It will be saved in %1 and not opened.").replace("%1", str(files.folder()))
        self.setText(text)
        self.accept_btn = self.addButton(self.tr("Accept"), QMessageBox.ButtonRole.AcceptRole)
        self.decline_btn = self.addButton(self.tr("Decline"), QMessageBox.ButtonRole.RejectRole)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.buttonClicked.connect(self._answered)

    def _answered(self, button):
        files = self.window.ctl.files
        if self.t.state != "offered":
            return
        if button is self.accept_btn:
            files.accept(self.t)
        else:
            files.decline(self.t)


class _ChatDrops(QObject):
    """Drops anywhere on the Connect / Chat page go to the session in
    front. The drop lands on whatever child is under the pointer - a tab's
    terminal, or its line edit, which would otherwise take a file as text
    and type its path - so the filter sits on the page and on every widget
    inside it, and on each one added later (a new session's tab).

    It used to be installed on the whole application, which was simpler
    and crashed the 0.3.0 suite: a Python event filter there is handed
    every object in the process, including ones Qt is in the middle of
    destroying, and wrapping those left PyQt holding wrappers of deleted
    objects; the process then died in QApplication's teardown
    (sip_api_get_address from cleanup_qobject) in test_linkctl,
    test_linkrun and test_notify, after every test had passed."""

    def __init__(self, ui, page):
        super().__init__(page)
        self.ui = ui
        self.page = page
        self.watch(page)

    def watch(self, w: QWidget) -> None:
        w.installEventFilter(self)
        for child in w.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, e):
        t = e.type()
        # ChildPolished, not ChildAdded: a widget is added from inside its
        # own constructor, half made; it is polished once whole, before it
        # is first shown - and so before anything can be dropped on it.
        if t == QEvent.Type.ChildPolished:
            child = e.child()
            if isinstance(child, QWidget):
                self.watch(child)
            return False
        if t not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        if not isinstance(obj, QWidget) or not (obj is self.page or self.page.isAncestorOf(obj)):
            return False
        paths = drop_paths(e.mimeData())
        if not paths:
            return False
        e.acceptProposedAction()
        if t == QEvent.Type.Drop:
            self.ui.send_file(paths[0], prefer_front=True)
        return True


class FilesUi:
    """The window's file hooks: `install(window)` makes one."""

    def __init__(self, window):
        self.window = window
        self.prompts: list[OfferPrompt] = []
        self.dialog: SendFileDialog | None = None
        #: Tests replace these to stand in for the operator's file choices.
        title = QCoreApplication.translate("Files", "Send a file")
        self.choose_file = lambda: QFileDialog.getOpenFileName(window, title)[0]
        self.choose_session = self._ask_session
        chat = window.pages.get("chat")
        if chat is not None:
            self.chat_button = QPushButton(QCoreApplication.translate("Files", "Send file…"))
            self.chat_button.setToolTip(QCoreApplication.translate(
                "Files", "Send a file or picture to the station in the tab in front. You can "
                                           "also drop one on the tab."))
            self.chat_button.clicked.connect(lambda: self.send_file(prefer_front=True))
            chat.tabs.setCornerWidget(self.chat_button, Qt.Corner.TopRightCorner)
            chat.setAcceptDrops(True)
            self.drops = _ChatDrops(self, chat)
        self.timer = QTimer(window)
        self.timer.timeout.connect(self.poll)
        self.timer.start(300)
        # Open prompts and notes are closed before the application goes:
        # left to be deleted with the window at interpreter exit they
        # crashed the process (seen in the tests; the same exit path as
        # quitting with an offer still on screen).
        QApplication.instance().aboutToQuit.connect(self.close_boxes)

    def close(self):
        """The window is closing: stop polling for offers."""
        self.timer.stop()

    def close_boxes(self):
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.parent() is self.window:
                w.close()

    def front_session(self):
        chat = self.window.pages.get("chat")
        w = chat.tabs.currentWidget() if chat is not None else None
        sess = getattr(w, "sess", None)
        return sess if sess is not None and sess.state == "connected" else None

    def connected_sessions(self) -> list:
        return [s for s in self.window.ctl.sessions.values() if s.state == "connected"]

    def _ask_session(self, sessions):
        names = [s.remote for s in sessions]
        name, ok = QInputDialog.getItem(self.window, QCoreApplication.translate("Files", "Send a file"),
                                        QCoreApplication.translate("Files", "To which station?"), names, 0, False)
        return sessions[names.index(name)] if ok else None

    def send_file(self, path: str | None = None, prefer_front: bool = False):
        """Pick the session (the tab in front, the only one, or ask), pick
        the file if not dropped, then the Send dialog. Returns the dialog."""
        sess = self.front_session() if prefer_front else None
        if sess is None:
            sessions = self.connected_sessions()
            if not sessions:
                note(self.window, QCoreApplication.translate("Files", "Send a file"),
                     QCoreApplication.translate("Files", "Connect to a station first (Connect / Chat), then send "
                                                         "it the file."))
                return None
            sess = sessions[0] if len(sessions) == 1 else self.choose_session(sessions)
            if sess is None:
                return None
        if path is None:
            path = self.choose_file()
            if not path:
                return None
        self.dialog = SendFileDialog(self.window, sess.id, path)
        self.dialog.open()
        return self.dialog

    def poll(self):
        files = self.window.ctl.files
        while files.offers:
            t = files.offers.popleft()
            if t.state != "offered":
                continue
            prompt = OfferPrompt(self.window, t)
            self.prompts = [p for p in self.prompts if p.t.state == "offered"] + [prompt]
            prompt.open()
            QApplication.alert(self.window)

    def event(self, e) -> None:
        """A controller event the window drained: file outcomes on the
        status bar."""
        if e.kind != "file":
            return
        d = e.detail
        if e.reason == "done":
            if d.get("direction") == "in":
                msg = QCoreApplication.translate("Files", "Received %1 from %2.")
            else:
                msg = QCoreApplication.translate("Files", "Sent %1 to %2.")
            msg = msg.replace("%1", d.get("name", "")).replace("%2", d.get("peer", ""))
        elif e.reason in ("failed", "declined", "interrupted"):
            msg = QCoreApplication.translate("Files", "%1: %2").replace("%1", d.get("name", "")).replace(
                "%2", reason_text(d.get("reason", "")))
        else:
            return
        self.window.statusBar().showMessage(msg, 15_000)


def install(window) -> FilesUi:
    ui = FilesUi(window)
    window.files_ui = ui
    # The window's own event handler, with file outcomes added: wrapped
    # here rather than another branch in window._event, which every
    # workstream edits.
    inner = window._event

    def _event(e, inner=inner):
        inner(e)
        ui.event(e)
    window._event = _event
    return ui
