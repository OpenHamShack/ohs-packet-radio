"""Small widgets drawn by hand, as the family's instrument windows are."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from . import theme


class Lamp(QWidget):
    """A round indicator: off, or lit in one of the theme's colours."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colour: str | None = None
        self.setFixedSize(QSize(12, 12))

    def set(self, colour: str | None) -> None:
        if colour != self.colour:
            self.colour = colour
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.colour(self.colour) if self.colour else theme.colour("line")
        p.setBrush(c)
        p.setPen(theme.colour("line"))
        p.drawEllipse(QRectF(1, 1, 10, 10))


class LevelMeter(QWidget):
    """A horizontal audio level bar, -60..0 dBFS, with the peak marked and
    the good range (-30..-6 dBFS for packet audio into the modem) shaded."""

    LO, HI = -60.0, 0.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rms: float | None = None
        self.peak: float | None = None
        self.setMinimumSize(QSize(220, 18))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_level(self, level: tuple[float, float] | None) -> None:
        if level is None:
            self.peak = self.rms = None
        else:
            self.peak, self.rms = level
        self.update()

    def _x(self, db: float) -> float:
        f = (min(self.HI, max(self.LO, db)) - self.LO) / (self.HI - self.LO)
        return f * (self.width() - 2) + 1

    def paintEvent(self, _e):
        p = QPainter(self)
        h = self.height()
        p.fillRect(self.rect(), theme.colour("surface"))
        good = QColor(theme.colour("rx"))
        good.setAlpha(40)
        p.fillRect(QRectF(self._x(-30), 1, self._x(-6) - self._x(-30), h - 2), good)
        if self.rms is not None:
            colour = "warn" if self.peak is not None and self.peak > -3 else "accent"
            p.fillRect(QRectF(1, 3, self._x(self.rms) - 1, h - 6), theme.colour(colour))
        if self.peak is not None:
            p.fillRect(QRectF(self._x(self.peak) - 1, 1, 2, h - 2), theme.colour("ink"))
        p.setPen(theme.colour("line"))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))


def card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("card")
    return f


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    if wrap:
        w.setWordWrap(True)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


class Fold(QWidget):
    """A heading that opens and closes what is under it ("Advanced"):
    closed by default, so the fields most people never need are not in
    their way (0.3.0: the link test's relay and modes, Find my radio's
    hand-typed settings). `body` is a plain QWidget to fill."""

    def __init__(self, title: str, parent=None, opened: bool = False):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.button = QToolButton()
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setAutoRaise(True)
        self.body = QWidget()
        lay.addWidget(self.button)
        lay.addWidget(self.body)
        self.button.toggled.connect(self.set_open)
        self.set_open(opened)

    def set_open(self, opened: bool) -> None:
        self.button.setChecked(opened)
        self.button.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
        self.body.setVisible(opened)

    @property
    def opened(self) -> bool:
        return self.button.isChecked()
