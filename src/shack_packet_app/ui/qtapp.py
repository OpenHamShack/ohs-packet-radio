"""The QApplication's life: made once with the app's names and icon, its
language installed, Python's garbage collector run from the event loop,
and told to quit by a signal the way the operator's Quit does.

The two non-obvious pieces are openhamshack-nb's (ohscore/ui/qtapp.py), where
each cost a crash before it was understood; they are copied rather than
imported because ohscore belongs to the NB repo, and are short:

- **GC on the GUI thread, between events.** A Qt object Python owns that
  ends up in a reference cycle (nearly every window, through its signal
  connections) dies when the collector reaches it - on whatever thread
  allocated last, possibly inside a Qt call that is calling back into
  Python. NB's test suite died in a theme switch that way. Collection is
  switched off and run from a 1 s timer instead, where no Qt call is half
  done.
- **Signals quit through the event loop.** A Python signal handler runs at
  the next bytecode, which in a Qt program is often inside a paint; closing
  the window there crashed NB on a Mac. The handler only sets a flag; a
  timer asks the application to quit. (NB uses a socket pair to wake the
  loop sooner; on Windows Python makes that pair with a listening TCP
  socket, and this app opens no listener at all, so here it is the timer
  everywhere.)
"""
from __future__ import annotations

import gc
import logging
import signal
import sys
from importlib import resources

log = logging.getLogger(__name__)

NAME = "OpenHamShack Packet Radio"
DESKTOP = "com.openhamshack.packet"


class _GuiThreadGc:
    INTERVAL_MS = 1000
    IDLE_TICKS = 10

    def __init__(self, app):
        from PyQt6.QtCore import QTimer
        self.threshold0 = gc.get_threshold()[0]
        self.ticks = 0
        gc.disable()
        self.timer = QTimer(app)
        self.timer.setInterval(self.INTERVAL_MS)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def check(self):
        self.ticks += 1
        c0 = gc.get_count()[0]
        if c0 > self.threshold0 or (c0 and self.ticks >= self.IDLE_TICKS):
            gc.collect()
            self.ticks = 0


class QuitOnSignals:
    SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")
    POLL_MS = 200

    def __init__(self, app):
        from PyQt6.QtCore import QTimer
        self.app = app
        self.got = None
        self.old = {}
        for name in self.SIGNALS:
            num = getattr(signal, name, None)
            if num is None:
                continue
            try:
                self.old[num] = signal.signal(num, self._handler)
            except (OSError, ValueError):
                continue                            # not the main thread
        self.timer = QTimer(app)
        self.timer.timeout.connect(self._poll)
        self.timer.start(self.POLL_MS)

    def _handler(self, signum, _frame):
        self.got = signum

    def _poll(self):
        if self.got is not None:
            self.got = None
            log.info("signal: quitting as the Quit command does")
            self.app.quit()

    def close(self):
        self.timer.stop()
        for num, old in self.old.items():
            try:
                signal.signal(num, old if old is not None else signal.SIG_DFL)
            except (OSError, ValueError):
                pass
        self.old = {}


def icon():
    from PyQt6.QtGui import QIcon, QPixmap
    data = resources.files("shack_packet_app").joinpath("resources/icon.png").read_bytes()
    pm = QPixmap()
    pm.loadFromData(data, "PNG")
    return QIcon(pm)


def make_app(argv=None):
    """The process's QApplication, made on first call."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(list(argv or sys.argv[:1]))
        app.setApplicationName(NAME)
        app.setOrganizationName(DESKTOP)
        app.setDesktopFileName(DESKTOP)
        app.setWindowIcon(icon())
        from . import i18n, snapshot, theme
        i18n.install(app)
        theme.apply(app)
        app._shack_snapshot = snapshot.arm(app)
    if getattr(app, "_shack_gc", None) is None:
        app._shack_gc = _GuiThreadGc(app)
    return app
