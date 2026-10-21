"""Notifications on the desktop (notify.py decides what, notifysound.py
plays the sound): a message from the system tray, a sound, and the
sidebar entry in bold with its unread count until the page is looked at.
Clicking the notification brings the window forward on that page.

**The tray icon** is made the first time there is something to say, not at
start-up: an operator who never gets a message never gets an icon. It is
the one way Qt shows a desktop notification on all three systems
(QSystemTrayIcon.showMessage: Notification Centre on macOS, the
notification area's balloons/toasts on Windows, the freedesktop
notification service on Linux). Where there is no tray at all (some Linux
desktops) the window asks for attention instead (QApplication.alert: the
taskbar entry flashes or the Dock icon bounces) and the status bar says
it.

Everything the tests replace is a constructor argument: the tray, the
sound player and whether the window counts as the active one.
"""
from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import QCoreApplication, QObject
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import notify, notifysound

log = logging.getLogger(__name__)

#: How long the desktop notification stays up, ms (a hint: the system decides).
SHOW_MS = 8000


def phrase(kind: str) -> str:
    """What the spoken announcement says for each kind (notifysound.py)."""
    return {
        "mailbox": QCoreApplication.translate("Notifier", "You've got mail!"),
        "messages": QCoreApplication.translate("Notifier", "You've got a message!"),
        "chat": QCoreApplication.translate("Notifier", "New chat!"),
    }.get(kind, "")


def title(n: notify.Notice) -> str:
    if n.kind == "mail_status":
        return (QCoreApplication.translate("Notifier", "Mail delivered to %1") if n.what == "delivered" else
                QCoreApplication.translate("Notifier", "Mail to %1 could not be delivered")).replace("%1", n.sender)
    return {
        "messages": QCoreApplication.translate("Notifier", "APRS message from %1"),
        "chat": QCoreApplication.translate("Notifier", "%1 in Connect / Chat"),
        "mailbox": QCoreApplication.translate("Notifier", "Mailbox: message from %1"),
    }[n.kind].replace("%1", n.sender)


class Notifier(QObject):
    def __init__(self, window, *, tray: Callable[[], object] | None = None, player=None,
                 active: Callable[[], bool] | None = None, tray_available: Callable[[], bool] | None = None):
        super().__init__(window)
        self.window = window
        self.unread = notify.Unread()
        self.player = player or notifysound.Player(beep=QApplication.beep)
        self.active = active or (lambda: window.isActiveWindow() and not window.isMinimized())
        self._make_tray = tray or self._qt_tray
        self._tray_available = tray_available or QSystemTrayIcon.isSystemTrayAvailable
        self.tray = None
        self.last: notify.Notice | None = None
        #: Every notice shown, newest last (the tests read it).
        self.shown: list[notify.Notice] = []

    # -- what arrives ----------------------------------------------------------------

    def handle(self, event) -> notify.Notice | None:
        """A Controller event (window._event): the notice it makes, if any,
        counted as unread unless its page is in front of the operator,
        and shown with its sound if the settings want it and the operator
        cannot already see it."""
        station = self.window.ctl.station
        own = str(station.address()) if station is not None and station.callsign else None
        n = notify.notice_for(event, own)
        if n is None:
            return None
        interrupts = notify.interrupts(n, window_active=self.active(), page=self.window.current_key())
        if interrupts:
            self.unread.add(n.page)
            self.window.show_unread(n.page, self.unread.count(n.page))
        ns = self.window.settings.notify
        if interrupts and notify.wanted(n, ns):
            self._show(n)
            if ns.sound:
                self.player.play(ns.sound_kind, phrase(n.kind), ns.sound_file, notice=n.kind)
        return n

    def page_shown(self, page: str) -> None:
        """The operator is looking at `page`: its count goes."""
        if self.unread.seen(page):
            self.window.show_unread(page, 0)

    # -- showing it ------------------------------------------------------------------

    def _show(self, n: notify.Notice) -> None:
        self.last = n
        self.shown.append(n)
        tray = self._tray()
        if tray is not None:
            tray.showMessage(title(n), n.text, QSystemTrayIcon.MessageIcon.Information, SHOW_MS)
        else:
            QApplication.alert(self.window, 0)
            self.window.statusBar().showMessage(f"{title(n)}: {n.text}", 20_000)

    def _tray(self):
        if self.tray is None:
            try:
                if not self._tray_available():
                    return None
                self.tray = self._make_tray()
            except Exception:                       # noqa: BLE001 - no tray: alert instead
                log.exception("system tray")
                return None
        return self.tray

    def _qt_tray(self):
        tray = QSystemTrayIcon(QApplication.windowIcon(), self)
        tray.setToolTip(QCoreApplication.translate("Notifier", "OpenHamShack Packet Radio"))
        menu = QMenu()
        menu.addAction(QCoreApplication.translate("Notifier", "Show the window"), lambda: self.bring_forward(None))
        tray.setContextMenu(menu)
        self._menu = menu                           # kept: the tray does not own it
        tray.messageClicked.connect(self.clicked)
        tray.activated.connect(lambda reason: self.bring_forward(None)
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        return tray

    def clicked(self) -> None:
        """The notification was clicked: its page, in front."""
        self.bring_forward(self.last.page if self.last is not None else None)

    def bring_forward(self, page: str | None) -> None:
        w = self.window
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
        if page is not None:
            w.go_to(page)

    def close(self) -> None:
        if self.tray is not None:
            self.tray.hide()
