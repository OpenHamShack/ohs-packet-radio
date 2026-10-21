"""The window photographing itself, for an install proof run over SSH and
for the screenshots in docs.

When $SHACK_DESKTOP_SNAPSHOT names a file, the running program saves a PNG
of its own front window there once, a few seconds after it starts, and
carries on. This is openhamshack-nb's ohscore/ui/snapshot.py contract (the name
the kit's try-dmg.sh asks for, because `screencapture` over SSH on the
Mac mini is refused): QWidget.grab() renders the window's own widgets, so
nothing else on the desktop can end up in the picture.

`save_widget()` is the same for any widget, which tools/screens.py uses to
photograph each page offscreen.
"""
from __future__ import annotations

import os

ENV = "SHACK_DESKTOP_SNAPSHOT"
AFTER_MS = 8000


def front_window():
    from PyQt6.QtWidgets import QApplication
    w = QApplication.activeModalWidget() or QApplication.activeWindow()
    if w is not None:
        return w
    shown = [t for t in QApplication.topLevelWidgets() if t.isVisible() and t.width() > 1]
    return max(shown, key=lambda t: t.width() * t.height(), default=None)


def save_widget(w, path: str) -> bool:
    part = f"{path}.part"
    if not w.grab().save(part, "PNG"):
        return False
    os.replace(part, path)
    return True


def save(path: str) -> bool:
    w = front_window()
    return w is not None and save_widget(w, path)


def arm(app, path: str | None = None, after_ms: int = AFTER_MS):
    path = path or os.environ.get(ENV, "")
    if not path:
        return None
    from PyQt6.QtCore import QTimer
    timer = QTimer(app)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: save(path))
    timer.start(after_ms)
    return timer
