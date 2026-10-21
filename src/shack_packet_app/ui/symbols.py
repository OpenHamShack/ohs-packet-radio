"""APRS symbols on the map, drawn by this app rather than taken from a
symbol sheet.

Why not the usual sprites: Brian's rule (18/10/2026) was to bundle the
hessu/aprs-symbols set only once its licence was confirmed, and it could
not be. Its COPYRIGHT.md (read 18/10/2026) says most symbols are
vectorised from the old WA8LMF/G4IDE/KH4G bitmaps with "Licensing:
Unknown", some are the author's own under CC BY-SA 2.0, and some are
company logos (Apple, Microsoft, Kenwood) whose owners "may have some
opinions". No licence covers the sheet as a whole, so it is not shipped.

Instead a handful of shapes, drawn with QPainter, cover what a packet map
mostly shows - things that move (with an arrow along their course when
they report one), homes, digipeaters and gateways, weather stations,
aircraft and balloons, boats - and everything else is the dot release 1
drew. The two-character symbol itself is always in the station's details,
and an overlay character (a table other than / or \\, which is how most
UK digipeaters mark themselves) is drawn on the shape.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QFont, QPainter, QPainterPath, QPen, QPolygonF
from shack_packet import aprs

#: aprs.SYMBOLS names -> the shape drawn.
SHAPES = {
    "car": "mover", "truck": "mover", "jeep": "mover", "lorry": "mover", "van": "mover", "bicycle": "mover",
    "motorcycle": "mover", "walker": "mover", "ambulance": "mover", "fire-engine": "mover", "train": "mover",
    "dog": "mover", "house": "house", "digipeater": "digi", "gateway": "gateway", "tcpip": "gateway",
    "repeater": "gateway", "weather-station": "weather", "aircraft": "air", "balloon": "air", "boat": "boat",
    "yacht": "boat",
}


def shape_of(symbol: str) -> str:
    """The shape for a two-character APRS symbol ("" or unknown: "dot").
    An overlaid symbol is the alternate table's: "S#" is a digipeater."""
    if len(symbol or "") != 2:
        return "dot"
    name = aprs.SYMBOLS.get(symbol)
    if name is None and symbol[0] not in "/\\":
        name = aprs.SYMBOLS.get("\\" + symbol[1])
    return SHAPES.get(name or "", "dot")


def overlay_of(symbol: str) -> str:
    return symbol[0] if len(symbol or "") == 2 and symbol[0] not in "/\\" else ""


def draw(p: QPainter, at: QPointF, symbol: str, fill, edge, *, course: float | None = None, r: float = 6.0,
         dashed: bool = False) -> None:
    """One station's marker at `at`. `dashed` draws the outline dashed: a
    station heard only through a digipeater (mapview.py)."""
    shape = shape_of(symbol)
    pen = QPen(edge, 1.4, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
    p.setPen(pen)
    p.setBrush(fill)
    x, y = at.x(), at.y()
    if shape == "house":
        p.drawPolygon(QPolygonF([QPointF(x - r, y + r * 0.8), QPointF(x - r, y - r * 0.1), QPointF(x, y - r),
                                 QPointF(x + r, y - r * 0.1), QPointF(x + r, y + r * 0.8)]))
    elif shape == "digi":
        pts = []
        for k in range(10):
            a = math.radians(-90 + k * 36)
            rr = r * 1.15 if k % 2 == 0 else r * 0.5
            pts.append(QPointF(x + rr * math.cos(a), y + rr * math.sin(a)))
        p.drawPolygon(QPolygonF(pts))
    elif shape == "gateway":
        p.drawPolygon(QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)]))
    elif shape == "weather":
        p.drawEllipse(at, r, r)
        p.drawLine(QPointF(x - r * 0.6, y), QPointF(x + r * 0.6, y))
    elif shape == "air":
        p.drawPolygon(QPolygonF([QPointF(x, y - r * 1.1), QPointF(x + r, y + r * 0.8), QPointF(x - r, y + r * 0.8)]))
    elif shape == "boat":
        path = QPainterPath(QPointF(x - r, y - r * 0.2))
        path.lineTo(QPointF(x + r, y - r * 0.2))
        path.lineTo(QPointF(x + r * 0.6, y + r * 0.7))
        path.lineTo(QPointF(x - r * 0.6, y + r * 0.7))
        path.closeSubpath()
        p.drawPath(path)
    elif shape == "mover":
        p.drawEllipse(at, r * 0.85, r * 0.85)
    else:
        p.drawEllipse(at, r * 0.85, r * 0.85)
    if shape == "mover" and course is not None:
        # An arrow out along the course: which way it is going, at a glance.
        a = math.radians(course - 90)
        tip = QPointF(x + 2.2 * r * math.cos(a), y + 2.2 * r * math.sin(a))
        p.setPen(QPen(edge, 2.0))
        p.drawLine(QPointF(x + r * math.cos(a), y + r * math.sin(a)), tip)
    over = overlay_of(symbol)
    if over:
        f = QFont(p.font())
        f.setPointSizeF(max(6.0, r))
        f.setBold(True)
        p.save()
        p.setFont(f)
        p.setPen(edge)
        p.drawText(QRectF(x - r, y - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, over)
        p.restore()
