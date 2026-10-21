"""The main window: a side list of the app's screens, the screen, and the
station strip along the bottom.

The window owns one Controller and draws it: a 200 ms timer drains the
controller's events (in order, on the GUI thread) and refreshes the
screen in view and the station strip; screens out of view are refreshed
when they come into view. Nothing in the window talks to a radio, the
engine's internals or a socket.

Closing the window stops the station - disarmed first, the radio unkeyed
and put back, the SDR stopped - before the application quits (GUIDE §5: a
desktop app's children must not outlive its window).
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
                             QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, diagnostics, settings as settings_mod
from ..controller import Controller
from . import texts
from .pages.aprs import AprsPage
from .pages.chat import ChatPage
from .pages.files import FilesPage
from .pages.ip import IpPage
from .pages.linktest import LinkTestPage
from .pages.log import LogPage
from .pages.mailbox import MailboxPage
from .pages.messages import MessagesPage
from .pages.monitor import MonitorPage
from .pages.nodes import NodesPage
from .pages.pathtest import PathTestPage
from .pages.sdr import SdrPage
from .statusbar import StationBar
from .diagsend import DiagSendController
from .update import UpdateBanner, UpdateController

log = logging.getLogger(__name__)

PAGES = ("monitor", "aprs", "messages", "chat", "files", "nodes", "mailbox", "log", "ip", "sdr", "pathtest", "linktest")


class LinkTestBanner(QFrame):
    """The strip that says a link test is running and that its
    acknowledgements go by internet (linkctl.py)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("linktest")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel("")
        self.label.setObjectName("linktest")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.state: dict | None = None
        self.hide()

    def show_state(self, s: dict | None) -> None:
        if s == self.state:
            return
        self.state = dict(s) if s else None
        if not s:
            self.hide()
            return
        where = self.tr("relay %1, session %2").replace("%1", s["relay"]).replace("%2", s["session"])
        if s["state"] == "lost":
            text = self.tr("LINK TEST: the internet return path was LOST (%1). The link to %2 was dropped and "
                           "nothing more is sent. Leave the test, then join again.").replace(
                "%1", texts.reason(s.get("why", ""))).replace("%2", s["peer"])
        elif s["role"] == "rf-receiver":
            text = self.tr("LINK TEST - acknowledgements are going via the INTERNET, not the radio (%1). This "
                           "station is the RF receiver: it never transmits.").replace("%1", where)
        else:
            text = self.tr("LINK TEST - %1's acknowledgements reach this station via the INTERNET, not the "
                           "radio (%2). Only this station transmits.").replace("%1", s["peer"]).replace("%2", where)
        if s["state"] == "waiting" and s.get("why") in ("peer-left", "peer-silent"):
            text += " " + self.tr("%1 left: the link was dropped without transmitting. Still joined, waiting for "
                                  "it to join again.").replace("%1", s["peer"])
        self.label.setText(text)
        self.show()


# 0.3.0 workstream F: the IC-9700 page (spectrum, power, satellites, DD),
# last, added apart from PAGES so the other workstreams' pages merge apart.
PAGES += ("rig",)


class MainWindow(QMainWindow):
    TICK_MS = 200

    def __init__(self, settings=None, secrets=None, *, controller: Controller | None = None, persist: bool = True,
                 sound_backend=None, updates: dict | None = None, diagsend: dict | None = None,
                 notifier: dict | None = None):
        super().__init__()
        self.persist = persist
        self.sound_backend = sound_backend
        self.settings = settings if settings is not None else settings_mod.load()
        self.secrets = secrets if secrets is not None else settings_mod.load_secrets()
        self.ctl = controller or Controller(self.settings, secrets=self.secrets, sound_backend=sound_backend)
        self.wizard = None
        self.setWindowTitle(self.tr("OpenHamShack Packet Radio"))
        self.resize(1180, 760)
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Self-update's banner, above everything (ui/update.py); hidden
        # unless there is something to say. `updates` is the tests' way in.
        self.update_banner = UpdateBanner()
        outer.addWidget(self.update_banner)
        self.updates = UpdateController(self, **(updates or {}))
        self.updates.attach(self.update_banner)
        self.diagsend = DiagSendController(self, **(diagsend or {}))
        # A link test's banner (linkctl.py): while one is joined, frames to
        # or from the other station go by internet, and that is never to
        # be a surprise. Hidden otherwise.
        self.linktest_banner = LinkTestBanner()
        outer.addWidget(self.linktest_banner)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(170)
        self.stack = QStackedWidget()
        self.pages = {
            "monitor": MonitorPage(self), "aprs": AprsPage(self), "messages": MessagesPage(self),
            "chat": ChatPage(self), "mailbox": MailboxPage(self), "ip": IpPage(self), "sdr": SdrPage(self),
            "pathtest": PathTestPage(self), "linktest": LinkTestPage(self),
            "files": FilesPage(self), "nodes": NodesPage(self), "log": LogPage(self),
        }
        names = {"monitor": self.tr("Monitor"), "aprs": self.tr("APRS"), "messages": self.tr("Messages"),
                 "chat": self.tr("Connect / Chat"), "mailbox": self.tr("Mailbox"), "ip": self.tr("IP"),
                 "sdr": self.tr("SDR monitor"), "pathtest": self.tr("Path test"), "linktest": self.tr("Link test"),
                 "files": self.tr("Files"), "nodes": self.tr("Nodes"), "log": self.tr("Log")}
        self.nav_names = names
        from .pages.rig import RigPage
        self.pages["rig"] = RigPage(self)
        names["rig"] = self.tr("IC-9700")
        for key in PAGES:
            self.nav.addItem(names[key])
            self.stack.addWidget(self.pages[key])
        # Notifications and the unread counts (ui/notifier.py); `notifier`
        # is the tests' way in (a fake tray, player and active window).
        from .notifier import Notifier
        self.notifier = Notifier(self, **(notifier or {}))
        self.nav.currentRowChanged.connect(self._page_changed)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        # The radio's controls always to hand (0.3.0, ui/controlstrip.py).
        from .controlstrip import ControlStrip
        self.control_strip = ControlStrip(self)
        outer.addWidget(self.control_strip)
        self.bar = StationBar()
        self.bar.arm_clicked.connect(self.toggle_arm)
        outer.addWidget(self.bar)
        self.setCentralWidget(central)
        self.pages["aprs"].message_to.connect(self._message_to)
        # Files: the chat page's Send file button and drops, offers asked
        # about as they arrive (ui/filesend.py).
        from .filesend import install as install_files
        install_files(self)
        self._menus()
        self.nav.setCurrentRow(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.pad = None                             # ui/padlink.py, from start()

    # -- menus ----------------------------------------------------------------

    def _menus(self):
        mb = self.menuBar()
        station = mb.addMenu(self.tr("&Station"))
        self.act_setup = QAction(self.tr("Setup check…"), self)
        self.act_setup.triggered.connect(self.open_setup)
        self.act_settings = QAction(self.tr("Settings…"), self)
        self.act_settings.triggered.connect(lambda: self.open_settings())
        self.act_arm = QAction(self.tr("Arm or disarm transmit"), self)
        self.act_arm.setShortcut(QKeySequence("Ctrl+T"))
        self.act_arm.triggered.connect(self.toggle_arm)
        self.act_reconnect = QAction(self.tr("Reconnect the radio"), self)
        self.act_reconnect.triggered.connect(lambda: self.ctl.reconnect())
        self.act_changes = QAction(self.tr("What the app changed on the radio…"), self)
        self.act_changes.triggered.connect(lambda: self.show_radio_changes())
        self.act_quit = QAction(self.tr("Quit"), self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)
        self.act_lookup = QAction(self.tr("Callsign lookup…"), self)
        self.act_lookup.triggered.connect(lambda: self.open_lookup())
        for a in (self.act_setup, self.act_settings, self.act_arm, self.act_reconnect, self.act_changes,
                  self.act_lookup):
            station.addAction(a)
        station.addSeparator()
        station.addAction(self.act_quit)
        helpm = mb.addMenu(self.tr("&Help"))
        self.act_diag = QAction(self.tr("Save diagnostics…"), self)
        # Through a lambda, as Settings is: QAction.triggered carries `checked`,
        # and PyQt hands it to any slot with a spare parameter - so connected
        # directly, save_diagnostics got path=False, skipped the file dialog
        # and raised in Path(False). An exception in a slot is a qFatal abort
        # in PyQt6, and that was 0.1.0's "the program just closed".
        self.act_diag.triggered.connect(lambda: self.save_diagnostics())
        self.act_send = QAction(self.tr("Send diagnostics…"), self)
        self.act_send.triggered.connect(lambda: self.diagsend.open_dialog())
        self.act_updates = QAction(self.tr("Check for updates…"), self)
        self.act_updates.triggered.connect(lambda: self.updates.open_dialog())
        self.act_about = QAction(self.tr("About"), self)
        self.act_about.triggered.connect(self.about)
        helpm.addAction(self.act_diag)
        helpm.addAction(self.act_send)
        helpm.addAction(self.act_updates)
        helpm.addAction(self.act_about)

    # -- life ------------------------------------------------------------------

    def start(self, first_run_check: bool = True):
        self.ctl.start()
        self.timer.start(self.TICK_MS)
        self.start_pad()
        self.tick()
        if first_run_check and not self.settings.setup_done:
            QTimer.singleShot(0, self.open_setup)

    def start_pad(self, **kw):
        """The AKP03E, if the operator wants it and one can be used here
        (ui/padlink.py). `kw` is for the tests: a fake pad and its folder."""
        from . import padlink
        if self.pad is not None:
            self.pad.close()
        self.pad = padlink.start(self, **kw)
        return self.pad

    def closeEvent(self, e):
        self.timer.stop()
        self.notifier.close()
        self.control_strip.close()
        self.files_ui.close()
        if self.pad is not None:
            self.pad.close()
        self.updates.stop()
        self.diagsend.stop()
        try:
            self.ctl.stop()
        finally:
            self.save_settings()
        super().closeEvent(e)

    def save_settings(self):
        if self.persist:
            try:
                settings_mod.save(self.settings)
                settings_mod.save_secrets(self.secrets)
            except OSError:
                log.exception("saving settings")

    def apply_settings(self, new, secrets=None):
        """New settings for the station: saved, and the station rebuilt
        (disarmed)."""
        self.settings = new
        if secrets is not None:
            self.secrets = secrets
        self.ctl.apply(self.settings, self.secrets)
        self.save_settings()
        if self.pad is not None and not new.pad.enabled:
            self.pad.close()
            self.pad = None
        elif self.pad is not None:
            self.pad.set_maps(new.pad.maps)
        elif new.pad.enabled and self.timer.isActive():
            self.start_pad()
        for page in self.pages.values():
            reset = getattr(page, "reset", None)
            if reset is not None:
                reset()
        self.control_strip.reset()

    # -- the tick ------------------------------------------------------------------

    def _page_changed(self, row):
        self.stack.setCurrentIndex(row)
        self.pages[PAGES[row]].refresh()
        if self.notifier.active():
            self.notifier.page_shown(PAGES[row])

    def current_page(self):
        return self.pages[PAGES[self.stack.currentIndex()]]

    def current_key(self) -> str:
        return PAGES[self.stack.currentIndex()]

    def go_to(self, page: str) -> None:
        self.nav.setCurrentRow(PAGES.index(page))
        self.notifier.page_shown(page)

    def show_unread(self, page: str, count: int) -> None:
        """The sidebar entry for `page`: bold with its count while it has
        unread items (ui/notifier.py), plain once they are seen."""
        item = self.nav.item(PAGES.index(page))
        item.setText(self.nav_names[page] + (f" ({count})" if count else ""))
        font = item.font()
        font.setBold(bool(count))
        item.setFont(font)

    def changeEvent(self, e):
        # Coming back to the window is looking at its page: that page's
        # unread count goes. Others stay until they are opened.
        from PyQt6.QtCore import QEvent
        n = getattr(self, "notifier", None)                 # None while __init__ runs
        if n is not None and e.type() == QEvent.Type.ActivationChange and n.active():
            n.page_shown(self.current_key())
        super().changeEvent(e)

    def tick(self):
        while True:
            try:
                e = self.ctl.events.get_nowait()
            except Exception:                       # noqa: BLE001 - queue.Empty
                break
            self._event(e)
        status = self.ctl.status()
        self.bar.update_status(status)
        self.linktest_banner.show_state(status.get("linktest"))
        self.control_strip.tick()
        self.current_page().refresh()
        if self.pad is not None:
            self.pad.tick()
        self.updates.poll()

    def _event(self, e):
        if self.wizard is not None:
            self.wizard.event_seen(e)
        self.notifier.handle(e)
        if e.kind == "tx" and e.reason in ("expired", "radio-lost", "halted"):
            self.statusBar().showMessage(texts.reason(e.reason), 10_000)
        elif e.kind == "tx" and e.reason == "arm-refused":
            self.statusBar().showMessage(texts.reason(e.detail.get("reason", "")), 10_000)
        elif e.kind == "unkeyed":
            self.statusBar().showMessage(texts.reason(e.reason), 15_000)
        elif e.kind == "radio" and e.reason == "link-failed":
            # A refused login stays on the status bar: nothing tries again
            # by itself (shack_rig IcomRig), so it is still true until the
            # operator does something about it.
            why = e.detail.get("reason", "radio-error")
            self.statusBar().showMessage(texts.reason(why), 0 if why == "radio-auth" else 15_000)
        elif e.kind == "radio" and e.reason == "setup-warning":
            self.statusBar().showMessage(texts.setup_warning(e.detail.get("what", ""), e.detail.get("note", "")),
                                         20_000)
        elif e.kind == "radio" and e.reason == "retuned":
            # The channel moved, by the radio's own VFO (tuning.py) or the
            # scope's click: settings.channel already says where; keep it, so
            # the next start tunes there rather than back.
            self.save_settings()
            self.control_strip.reset()
            self.statusBar().showMessage(texts.retuned(e.detail), 20_000)
        elif e.kind == "radio" and e.reason == "mode-changed":
            msg = texts.reason(e.detail.get("why") or "radio-mode")
            if e.detail.get("disarmed"):
                msg = self.tr("Transmit DISARMED: %1").replace("%1", msg)
            self.statusBar().showMessage(msg, 20_000)
        elif e.kind == "radio" and e.reason == "setup-recovered":
            self.statusBar().showMessage(texts.reason("setup-recovered"), 15_000)
        elif e.kind == "sdr" and e.reason == "failed" and str(e.detail.get("reason", "")).startswith("controller-"):
            self.statusBar().showMessage(texts.reason(e.detail["reason"]), 15_000)
        elif e.kind == "linktest" and e.reason in ("lost", "failed", "peer-left", "run-failed"):
            self.statusBar().showMessage(texts.reason(e.detail.get("why", "")), 20_000)
        elif e.kind == "chat" and e.reason == "incoming":
            self.statusBar().showMessage(self.tr("%1 connected to you").replace("%1", e.detail.get("remote", "")),
                                         10_000)

    # -- commands ---------------------------------------------------------------------

    def toggle_arm(self):
        if self.ctl.status()["armed"]:
            self.ctl.disarm()
        else:
            ok, why, detail = self.ctl.arm(self.settings.tx.arm_minutes)
            if not ok:
                msg = texts.reason(why)
                if detail and why == "band-plan":
                    msg += "\n\n" + detail
                QMessageBox.information(self, self.tr("Arm transmit"), msg)
            else:
                power = self.ctl.status().get("rfPower")
                if power is not None:
                    self.statusBar().showMessage(texts.rf_power_status(*power), 10_000)
        self.bar.update_status(self.ctl.status())

    def show_radio_changes(self):
        """Station › What the app changed on the radio: the set-up report
        (radiochanges.py), every line before and after."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout
        from . import radiochanges
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("What the app changed on the radio"))
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setHtml(radiochanges.html_report(self.ctl.radio_setup)
                     or self.tr("No radio has been set up yet: the simulator and a sound card radio are not "
                                "changed by the app."))
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.resize(640, 520)
        self.changes_dialog = dlg
        dlg.open()
        return dlg

    def _message_to(self, call):
        self.nav.setCurrentRow(PAGES.index("messages"))
        self.pages["messages"].start_to(call)

    def open_lookup(self):
        """Station › Callsign lookup: the HamQTH account (ui/callinfo.py)."""
        from .callinfo import LookupDialog
        dlg = LookupDialog(self)
        dlg.open()
        self.lookup_dialog = dlg
        return dlg

    def open_settings(self, tab: str = "channel"):
        from .settingsdlg import SettingsDialog
        dlg = SettingsDialog(self.settings, self, tab, secrets=self.secrets)
        if dlg.exec():
            self.apply_settings(dlg.result_settings(), dlg.result_secrets())

    def open_setup(self):
        from .wizard import SetupWizard
        if self.wizard is not None:
            self.wizard.raise_()
            return
        self.wizard = SetupWizard(self, self.settings, self.secrets, sound_backend=self.sound_backend)
        self.wizard.open()

    def setup_finished(self, new, secrets):
        self.wizard = None
        self.apply_settings(new, secrets)

    def setup_cancelled(self, old, secrets):
        self.wizard = None
        self.apply_settings(old, secrets)

    def save_diagnostics(self, path: str | None = None):
        from PyQt6.QtCore import QBuffer, QIODevice
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, self.tr("Save diagnostics"), diagnostics.default_name(),
                                                  self.tr("Zip files (*.zip)"))
            if not path:
                return None
        # Everything, not just the OSError and LeakError expected here: this
        # is the one thing a tester does when something has already gone
        # wrong, so it must never be what closes the program. What failed is
        # logged (the next bundle carries it) and shown.
        try:
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            self.grab().save(buf, "PNG")
            out = diagnostics.save(self.ctl, path, screenshot_png=bytes(buf.data()))
        except Exception as e:                      # noqa: BLE001 - see above
            log.exception("saving diagnostics to %s failed", path)
            QMessageBox.warning(self, self.tr("Save diagnostics"), self.tr("The diagnostics could not be saved: %1")
                                .replace("%1", str(e) or type(e).__name__))
            return None
        self.statusBar().showMessage(self.tr("Diagnostics saved to %1. Email it to the person who asked for it.")
                                     .replace("%1", str(out)), 15_000)
        return out

    def about(self):
        QMessageBox.about(self, self.tr("About"), self.tr(
            "<b>OpenHamShack Packet Radio</b> %1<br>APRS, connected-mode chat, a personal mailbox and IP over "
            "AX.25, with an SDR monitor.<br><br>Free software under the GNU GPL, version 3 or later.")
            .replace("%1", __version__))
