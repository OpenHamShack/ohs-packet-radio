"""Help › Send diagnostics… and the automatic upload's timing: the
window's half of diagupload.py (openhamshack-nb ohscore/ui/diagsend.py at
9f2a3ed, on this window).

- **Off until the operator turns it on in Settings › Internet,** where the
  whole consent text (what_is_sent) sits beside the switch. Turning it on
  there is the only thing that writes the consent stamp.
- **Help › Send diagnostics… works whatever the switch says.** It shows the
  same text and sends only when Send now is pressed: that press is the
  consent for that one report.
- **One look a minute,** on the GUI thread, which only decides; the bundle
  is gathered and sent on a thread with a timeout. Nothing is sent while
  transmitting, on a metered connection (when Qt can tell), or from a
  window that __main__.main() did not start (the tests and the screenshot
  tool never upload).
- **Quiet.** An automatic upload shows nothing; its code goes to the log
  and to Help › Send diagnostics…. A failure is a log line and a retry.
- **Faults** come from three places: notable() wherever the app raises one
  (losing the radio), the radio link's own fault counters, compared at
  each look (diagupload.faults_from_stats: watchdogs, an unconfirmed
  unkey, a failed or timed-out radio call, dropped receive audio), and a
  hard crash an earlier run left in fatal.log.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import __version__, applog, diagnostics, diagupload, host

log = logging.getLogger(__name__)


def what_is_sent() -> str:
    """The consent text: the same words in Settings and in Help › Send
    diagnostics…, so what the operator agreed to is what the dialog shows."""
    return QCoreApplication.translate(
        "DiagSend",
        "What is sent: Packet Radio's diagnostics - its recent log, its settings (never your radio's password), "
        "the last frames heard and sent, what the radio link reported, the program's version and your computer's "
        "operating system - with a random number made for this installation, not taken from the computer. Your "
        "callsign, locator and the callsigns you heard are in it. It goes to openhamshack.com, where only the "
        "people who write Packet Radio read it, and it is deleted after 30 days. Nothing is sent while you "
        "transmit or on a metered connection. To stop, turn this off in Settings › Internet. More: %1"
    ).replace("%1", diagupload.PRIVACY_URL)


def metered_now() -> bool:
    """Whether the system says this connection is metered. False when it
    cannot tell: "unknown" must not stop a station on a home line."""
    try:
        from PyQt6.QtNetwork import QNetworkInformation as NI
        if NI.instance() is None and not NI.loadBackendByFeatures(NI.Feature.Metered):
            return False
        inst = NI.instance()
        return bool(inst is not None and inst.supports(NI.Feature.Metered) and inst.isMetered())
    except (ImportError, AttributeError, RuntimeError):
        return False


class DiagSendController(QObject):
    """One per window: watches for faults, decides when, sends on a thread."""

    _done = pyqtSignal(object)
    TICK_MS = 60_000

    def __init__(self, win, *, url: str | None = None, clock=time.time, poster=diagupload.post,
                 metered=metered_now, timeout: float = diagupload.TIMEOUT_S, state_path: Path | None = None,
                 version: str | None = None):
        super().__init__(win)
        self.win = win
        self.url = url or diagupload.URL
        self.version = version or __version__
        self.clock, self.poster, self.metered, self.timeout = clock, poster, metered, timeout
        self.state = diagupload.State(state_path or host.config_dir() / "diag-upload.json")
        self.plan = diagupload.Plan(self.state, clock())
        self.watcher = diagupload.Watcher(clock)
        self.sending = ""
        self.last_error = ""
        self.timer: QTimer | None = None
        self.dialog: SendDialog | None = None
        self._stats: dict = {}
        self._done.connect(self._on_done)

    # ------------------------------------------------------------ wiring
    def start(self) -> None:
        """Called by __main__.main() once the window is showing."""
        if self.timer is not None:
            return
        self.watcher.install()
        self._check_fatal_file()
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.stop()
            self.timer = None
        self.watcher.remove()

    def enabled(self) -> bool:
        i = self.win.settings.internet
        return bool(i.diag_upload) and diagupload.consented(i.diag_consent)

    def keyed(self) -> bool:
        return bool(self.win.ctl.status().get("transmitting"))

    def _fatal_path(self) -> Path:
        return host.log_dir() / applog.FATAL_NAME

    def _check_fatal_file(self) -> None:
        """A hard crash ends the process before anything can be sent: the
        next run reads what fatal.log gained since the last report, and a
        faulthandler dump there is a crash to report. The first look only
        takes the size."""
        p = self._fatal_path()
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        known = self.state.get("fatalSize")
        if known is None or size < known:
            self.state.set(fatalSize=size)
            return
        if size > known:
            try:
                with open(p, "rb") as f:
                    f.seek(known)
                    new = f.read(200_000).decode("utf-8", "replace")
            except OSError:
                new = ""
            if diagupload.hard_crash_since(new) and self.enabled():
                self.plan.note([("crash", "a hard crash in an earlier run (fatal.log)", self.clock())])
            self.state.set(fatalSize=size)

    def _link_faults(self) -> None:
        link = self.win.ctl.link
        now = dict(getattr(link, "stats", {}) or {}) if link is not None else {}
        for trigger, detail in diagupload.faults_from_stats(self._stats, now):
            diagupload.notable(trigger, detail)
        self._stats = now

    def _logged_since(self) -> float:
        try:
            return (host.log_dir() / applog.LOG_NAME).stat().st_mtime
        except OSError:
            return 0.0

    # ------------------------------------------------------------ deciding
    def tick(self) -> str:
        """One look: take what was heard, send if due. Returns the kind sent ("" for none)."""
        self._link_faults()
        self.plan.note(self.watcher.take())
        if self.sending:
            return ""
        on = self.enabled()
        if not on:
            self.plan.taken()                # off means off: nothing saved up for later
            return ""
        kind = self.plan.due(self.clock(), enabled=on, keyed=self.keyed(), metered=self.metered(),
                             logged_since=self._logged_since())
        if kind:
            self.send(kind)
        return kind

    # ------------------------------------------------------------ sending
    def send(self, kind: str) -> bool:
        if self.sending:
            return False
        if self.keyed():
            self.last_error = self.tr("Not sent: not while transmitting.")
            self._refresh()
            return False
        events = self.plan.taken() if kind == "fault" else []
        if kind == "manual":
            events = self.plan.taken() + self.watcher.take()
        try:
            # Made here, on the GUI thread, as Save diagnostics makes them:
            # the station's lists are read under its lock, briefly.
            texts = diagnostics.contents(self.win.ctl)
        except diagnostics.LeakError as e:
            log.error("diagnostics upload: not sent, a secret was still in %s", e)
            self.last_error = self.tr("Not sent: the report still held a password.")
            self._refresh()
            return False
        self.sending = kind
        self.last_error = ""
        install = self.state.install_id()

        def work():
            try:
                body, info = diagupload.build(texts, version=self.version, install=install, kind=kind, events=events)
                code = self.poster(self.url, body, version=self.version, install=install, timeout=self.timeout)
                self._done.emit((kind, events, code, len(body), info))
            except diagupload.UploadError as e:
                self._done.emit((kind, events, e, 0, {}))
            except Exception as e:                  # noqa: BLE001 - nothing an upload does may reach the radio
                log.warning("diagnostics upload: failed", exc_info=True)
                self._done.emit((kind, events, diagupload.UploadError(f"something went wrong ({e})"), 0, {}))
        threading.Thread(target=work, daemon=True, name="diag-upload").start()
        self._refresh()
        return True

    def _on_done(self, res) -> None:
        kind, events, code, size, info = res
        self.sending = ""
        now = self.clock()
        trigger = diagupload.trigger_text(events, kind)
        if isinstance(code, Exception):
            self.last_error = str(code)
            log.info("diagnostics upload (%s) not sent: %s; tried again later", trigger, code)
            if kind != "manual":
                self.plan.failed(now, kind, str(code), events)
            elif events:
                self.plan.pending = list(events) + self.plan.pending
        else:
            self.plan.sent(now, kind, code, trigger)
            log.info("diagnostics sent to OpenHamShack (%s): reference %s, %d bytes%s", trigger, code, size,
                     ", log cut to fit" if info.get("logCut") else "")
        self._refresh()

    # ------------------------------------------------------------ the dialog
    def open_dialog(self) -> "SendDialog":
        if self.dialog is None:
            self.dialog = SendDialog(self)
            self.dialog.finished.connect(lambda _r: setattr(self, "dialog", None))
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()
        return self.dialog

    def _refresh(self) -> None:
        if self.dialog is not None:
            self.dialog.refresh()

    def status_text(self) -> str:
        if self.sending:
            return self.tr("Sending…")
        if self.last_error:
            return self.tr("The last try did not work: %1. Nothing else is affected.").replace("%1", self.last_error)
        code, at = self.state.get("lastCode"), self.state.get("lastSent")
        if code and at:
            return self.tr("Last sent at %1: reference %2. Quote it when you write to us.") \
                .replace("%1", time.strftime("%Y-%m-%d %H:%M", time.localtime(at))).replace("%2", code)
        return self.tr("Nothing sent yet.")


class SendDialog(QDialog):
    """Help › Send diagnostics…: the text, whether automatic sending is on,
    the last reference code, and Send now."""

    def __init__(self, ctl: DiagSendController):
        super().__init__(ctl.win)
        self.ctl = ctl
        self.setWindowTitle(self.tr("Send diagnostics"))
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)
        head = QLabel(self.tr("Send diagnostics to OpenHamShack"))
        head.setObjectName("heading")
        lay.addWidget(head)
        self.summary = QLabel(what_is_sent())
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.summary)
        self.auto = QLabel("")
        self.auto.setWordWrap(True)
        self.auto.setObjectName("dim")
        lay.addWidget(self.auto)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        privacy = QPushButton(self.tr("Privacy note"))
        privacy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(diagupload.PRIVACY_URL)))
        row.addWidget(privacy)
        row.addStretch(1)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.btn_send = QPushButton(self.tr("Send now"))
        self.btn_send.setObjectName("primary")
        self.btn_send.clicked.connect(lambda: ctl.send("manual"))
        row.addWidget(self.btn_send)
        lay.addLayout(row)

    def refresh(self) -> None:
        c = self.ctl
        if c.enabled():
            self.auto.setText(self.tr("Automatic sending is on (Settings › Internet): after a fault and once a day."))
        else:
            self.auto.setText(self.tr("Automatic sending is off (Settings › Internet). Send now sends this one "
                                      "report only."))
        self.status.setText(c.status_text())
        self.btn_send.setEnabled(not c.sending)
