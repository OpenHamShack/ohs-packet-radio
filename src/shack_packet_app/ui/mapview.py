"""The APRS map: a built-in basemap that always works offline, the Maidenhead
locator grid over it, optional OpenStreetMap tiles under it, and the
stations - each with its symbol, its track, and how it was heard.

Layers, bottom to top:
1. **Built-in basemap** (basemap.py): Natural Earth coastlines and land
   borders, at 1:50m when a continent is in view and 1:10m below that, and
   GeoNames towns, the largest that fit labelled. Bundled; no fetch, ever.
   Release 1 had only the grid, which needed no data at all; 0.2 adds the
   coast because "IO85" places a station but does not tell a newcomer
   which side of the Forth it is on (Brian, 18/10/2026).
2. **OpenStreetMap tiles**, only when the operator turned them on (tiles.py
   has the rules it keeps). Drawn over the basemap, so wherever a tile has
   not arrived, or the computer is offline, the basemap shows through.
   "© OpenStreetMap contributors" is drawn whenever a tile is on screen.
3. **The locator grid**, as release 1 drew it, and range rings round home.
4. **Stations:**
   - the APRS symbol's shape (ui/symbols.py), with an arrow along the
     course of anything moving;
   - a track for anything that moved (tracks.py, kept for an hour unless
     Settings › Map says otherwise);
   - **how it was heard**: a solid line to home for a station heard direct,
     a dashed one through the digipeater it came through when that
     digipeater's position is known, and a dashed outline on the marker
     either way; faint for every station, bold for the one selected;
   - names placed so none overprints another or a dot.

The credit line for the basemap (Natural Earth; GeoNames, which is CC BY
4.0 and asks for it) is always drawn, bottom right.

Projection: equirectangular about the view's centre (east-west scaled by
the cosine of the latitude), true to a few per cent over the few hundred
kilometres a packet map shows. As an affine map of (lon, lat) it lets the
basemap's lines be drawn through one QTransform. The wheel zooms, a drag
pans, a click selects a station, a double click recentres on home.
"""
from __future__ import annotations

import collections
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PyQt6.QtWidgets import QWidget

from .. import basemap as basemap_mod, grid, tiles as tiles_mod
from . import fmt, symbols, theme

KM_PER_DEG = 111.32
#: Above this half-width the coarser 1:50m lines are drawn.
COARSE_ABOVE_KM = 400.0
#: Town labels in view at most.
TOWN_LABELS = 30
#: At most this many tiles in view are drawn or asked for.
MAX_TILES = 48


class MapView(QWidget):
    station_clicked = pyqtSignal(str)
    #: A right click on the map, where (lat, lon): the APRS page offers to
    #: place an object there (0.3.0).
    place_requested = pyqtSignal(float, float)
    _tile_arrived = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.home: tuple[float, float] | None = None
        self.centre: tuple[float, float] = (54.5, -3.0)
        self.span_km = 80.0                       # half the width of the view
        # name, lat, lon, track [(t, lat, lon)], kind, symbol, course, direct, via
        self.stations: list[dict] = []
        self.selected = ""
        self.base = basemap_mod.load()
        self.tiles: tiles_mod.TileStore | None = None
        self._images: collections.OrderedDict = collections.OrderedDict()
        self._paths: dict[tuple[str, int], QPainterPath] = {}
        self.tiles_drawn = 0
        self.towns_drawn: list[str] = []
        self._drag = None
        self._moved = False
        self._tile_arrived.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)

    # -- data ------------------------------------------------------------------

    def set_home(self, pos: tuple[float, float] | None) -> None:
        if pos is not None and self.home is None:
            self.centre = pos
        self.home = pos
        self.update()

    def set_stations(self, stations: list[dict]) -> None:
        self.stations = stations
        if self.home is None and stations and self.centre == (54.5, -3.0):
            self.centre = (stations[0]["lat"], stations[0]["lon"])
        self.update()

    def set_selected(self, name: str) -> None:
        self.selected = name or ""
        self.update()

    def set_tiles(self, store: tiles_mod.TileStore | None) -> None:
        """OpenStreetMap (or the operator's) tiles under the stations, or
        None for the built-in basemap alone."""
        if self.tiles is not None and self.tiles is not store:
            self.tiles.close()
        self.tiles = store
        self._images.clear()
        if store is not None:
            store.on_ready = lambda *_t: self._tile_arrived.emit()
        self.update()

    # -- projection ------------------------------------------------------------------

    def _scale(self) -> float:
        return max(1, self.width()) / (2 * self.span_km)          # pixels per km

    def to_xy(self, lat: float, lon: float) -> QPointF:
        clat, clon = self.centre
        dx = ((lon - clon + 540) % 360 - 180) * math.cos(math.radians(clat)) * KM_PER_DEG
        dy = (lat - clat) * KM_PER_DEG
        s = self._scale()
        return QPointF(self.width() / 2 + dx * s, self.height() / 2 - dy * s)

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        clat, clon = self.centre
        s = self._scale()
        lat = clat - (y - self.height() / 2) / s / KM_PER_DEG
        lon = clon + (x - self.width() / 2) / s / KM_PER_DEG / max(0.05, math.cos(math.radians(clat)))
        return max(-89.9, min(89.9, lat)), ((lon + 180) % 360) - 180

    def _lonlat_transform(self) -> QTransform:
        """to_xy as an affine map of (lon, lat), for drawing whole paths."""
        clat, clon = self.centre
        s = self._scale()
        kx = math.cos(math.radians(clat)) * KM_PER_DEG * s
        ky = KM_PER_DEG * s
        return QTransform(kx, 0.0, 0.0, -ky, self.width() / 2 - clon * kx, self.height() / 2 + clat * ky)

    def view_box(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) of the view."""
        north, west = self.to_latlon(0, 0)
        south, east = self.to_latlon(self.width(), self.height())
        if east < west:                                           # across the 180th meridian: all of it
            west, east = -180.0, 180.0
        return west, south, east, north

    # -- drawing: the basemap ----------------------------------------------------------

    def _lines(self, p: QPainter, key: str, colour, width: float, style=Qt.PenStyle.SolidLine) -> None:
        layer = self.base.layers[key]
        pen = QPen(colour, width, style)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in layer.visible(*self.view_box()):
            path = self._paths.get((key, int(i)))
            if path is None:
                pts = layer.run(int(i))
                path = QPainterPath()
                path.addPolygon(QPolygonF([QPointF(float(lo), float(la)) for lo, la in pts]))
                self._paths[(key, int(i))] = path
            p.drawPath(path)

    def _basemap(self, p: QPainter) -> None:
        if self.base is None:
            return
        coarse = self.span_km > COARSE_ABOVE_KM
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setTransform(self._lonlat_transform())
        self._lines(p, "border50" if coarse else "border10", theme.colour("ink-faint"), 1.0, Qt.PenStyle.DashLine)
        self._lines(p, "coast50" if coarse else "coast10", theme.colour("ink-dim"), 1.3)
        p.restore()

    def _towns(self, p: QPainter, taken: list[QRectF]) -> None:
        """The largest towns in view that have room, each a small dot and its
        name; never over a station, its name, or another town."""
        self.towns_drawn = []
        if self.base is None or self.span_km > 1500:
            return
        t = self.base.towns
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        fm = p.fontMetrics()
        placed = list(taken)
        ink = theme.colour("ink-faint")
        for i in t.visible(*self.view_box()):
            if len(self.towns_drawn) >= TOWN_LABELS:
                break
            name = t.name(int(i))
            pt = self.to_xy(float(t.lat[i]), float(t.lon[i]))
            w, h = fm.horizontalAdvance(name) + 2, fm.height()
            rect = QRectF(pt.x() + 4, pt.y() - h / 2, w, h)
            if any(rect.intersects(q) for q in placed):
                continue
            placed.append(rect.adjusted(-4, -2, 4, 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawEllipse(pt, 1.6, 1.6)
            p.setPen(ink)
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            self.towns_drawn.append(name)

    # -- drawing: tiles ------------------------------------------------------------------

    def tile_zoom(self) -> int:
        """The tile zoom whose tiles come out about 256 pixels wide here."""
        px_per_deg = math.cos(math.radians(self.centre[0])) * KM_PER_DEG * self._scale()
        z = math.log2(max(1e-9, 360.0 * px_per_deg / 256.0))
        return max(0, min(tiles_mod.MAX_ZOOM - 1, int(round(z))))

    def _tiles(self, p: QPainter) -> None:
        self.tiles_drawn = 0
        if self.tiles is None:
            return
        want = tiles_mod.tiles_for(*self.view_box(), self.tile_zoom())[:MAX_TILES]
        self.tiles.want(want)
        for t in want:
            img = self._images.get(t)
            if img is None:
                data = self.tiles.get(*t)
                if data is None:
                    continue
                img = QImage.fromData(data)
                if img.isNull():
                    continue
                self._images[t] = img
                while len(self._images) > 4 * MAX_TILES:
                    self._images.popitem(last=False)
            west, south, east, north = tiles_mod.tile_bounds(*t)
            a, b = self.to_xy(north, west), self.to_xy(south, east)
            p.drawImage(QRectF(a, b), img)
            self.tiles_drawn += 1

    # -- drawing: the grid and rings ------------------------------------------------------

    def _grid(self, p: QPainter) -> None:
        top_lat, left_lon = self.to_latlon(0, 0)
        bottom_lat, right_lon = self.to_latlon(self.width(), self.height())
        lon_span = (right_lon - left_lon) % 360 or 360
        if self.span_km > 600:
            steps = [(20.0, 10.0, 2), (2.0, 1.0, 4)]
        elif self.span_km > 40:
            steps = [(2.0, 1.0, 4), (2 / 24, 1 / 24, 6)]
        else:
            steps = [(2 / 24, 1 / 24, 6), (2 / 240, 1 / 240, 8)]
        faint, strong = QColor(theme.colour("line")), QColor(theme.colour("ink-faint"))
        if self.base is not None:
            # Under a coastline the grid steps back: it is for reading a
            # locator off, not the thing the eye should land on first.
            faint.setAlpha(150)
            strong.setAlpha(170)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        for level, (w, h, chars) in enumerate(steps):
            px_w = abs(self.to_xy(self.centre[0], self.centre[1] + w).x() - self.to_xy(*self.centre).x())
            if px_w < 6:
                continue
            p.setPen(QPen(strong if level == 0 else faint, 1.0))
            lon = math.floor((left_lon + 180) / w) * w - 180
            n = 0
            while lon <= left_lon + lon_span + w and n < 400:
                x = self.to_xy(self.centre[0], lon).x()
                p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
                lon += w
                n += 1
            lat = math.floor((bottom_lat + 90) / h) * h - 90
            n = 0
            while lat <= top_lat + h and n < 400:
                y = self.to_xy(lat, self.centre[1]).y()
                p.drawLine(QPointF(0, y), QPointF(self.width(), y))
                lat += h
                n += 1
            if level == 0 and px_w > 40:
                # Over the basemap the locator labels go faint, so the towns
                # are what reads first; alone (no basemap) they are the map.
                p.setPen(faint if self.base is not None else strong)
                lat = math.floor((bottom_lat + 90) / h) * h - 90
                while lat <= top_lat:
                    lon = math.floor((left_lon + 180) / w) * w - 180
                    while lon <= left_lon + lon_span:
                        c_lat, c_lon = lat + h / 2, lon + w / 2
                        if -90 < c_lat < 90:
                            pt = self.to_xy(c_lat, c_lon)
                            name = grid.from_latlon(c_lat, ((c_lon + 180) % 360) - 180, chars)
                            p.drawText(QRectF(pt.x() - 40, pt.y() - 8, 80, 16), Qt.AlignmentFlag.AlignCenter, name)
                        lon += w
                    lat += h

    def _rings(self, p: QPainter) -> None:
        if self.home is None:
            return
        c = self.to_xy(*self.home)
        s = self._scale()
        p.setPen(QPen(theme.colour("accent"), 1.0, Qt.PenStyle.DotLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for km in (10, 25, 50, 100, 200, 500):
            r = km * s
            if r < 25 or r > 2 * max(self.width(), self.height()):
                continue
            p.drawEllipse(c, r, r)
            # Labelled where the ring crosses north-east, pulled back inside
            # the view when the text would run off its edge (the 100 km
            # label was cut to "100 k" at the right), and left off when that
            # point of the ring is off the view altogether.
            text = self.tr("%1 km").replace("%1", fmt.number(km))
            fm = p.fontMetrics()
            x, y = c.x() + r * 0.7071 + 3, c.y() - r * 0.7071 - 3
            if not (0 <= x <= self.width() and 0 <= y <= self.height()):
                continue
            x = min(x, self.width() - fm.horizontalAdvance(text) - 4)
            y = max(y, fm.ascent() + 2)
            p.drawText(QPointF(x, y), text)

    # -- drawing: stations ------------------------------------------------------------------

    def links(self) -> list[tuple[str, list[QPointF], bool]]:
        """(station, points, direct) for each line saying how a station was
        heard: straight to home when direct, through its digipeater when it
        came through one whose position is known. Stations the SDR heard
        alone have no line: the monitor is not the station's receiver."""
        if self.home is None:
            return []
        home = self.to_xy(*self.home)
        where = {st["name"]: st for st in self.stations}
        out = []
        for st in self.stations:
            if st.get("kind") == "sdr" or st.get("direct") is None:
                continue
            here = self.to_xy(st["lat"], st["lon"])
            if st["direct"]:
                out.append((st["name"], [here, home], True))
            else:
                digi = where.get(st.get("via") or "")
                if digi is not None:
                    out.append((st["name"], [here, self.to_xy(digi["lat"], digi["lon"]), home], False))
        return out

    def _stations(self, p: QPainter) -> list[QRectF]:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for st in self.stations:
            track = st.get("track") or []
            if len(track) > 1:
                path = QPainterPath(self.to_xy(track[0][1], track[0][2]))
                for _t, la, lo in track[1:]:
                    path.lineTo(self.to_xy(la, lo))
                p.setPen(QPen(theme.colour("info"), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        for name, pts, direct in self.links():
            colour = QColor(theme.colour("rx"))
            bold = name == self.selected
            colour.setAlpha(230 if bold else 70)
            p.setPen(QPen(colour, 2.0 if bold else 1.0,
                          Qt.PenStyle.SolidLine if direct else Qt.PenStyle.DashLine))
            p.drawPolyline(QPolygonF(pts))
        for st in self.stations:
            pt = self.to_xy(st["lat"], st["lon"])
            # Our own objects (kind "own") in the accent colour: they are
            # what this station transmits, not what it heard.
            fill = theme.colour({"sdr": "carrier", "own": "accent"}.get(st.get("kind"), "rx"))
            edge = theme.colour("accent") if st["name"] == self.selected else theme.colour("bg")
            symbols.draw(p, pt, st.get("symbol", ""), fill, edge, course=st.get("course"),
                         r=7.5 if st["name"] == self.selected else 6.0, dashed=st.get("direct") is False)
        rects = self.label_rects(p.fontMetrics())
        p.setPen(theme.colour("ink"))
        for st, rect in zip(self.stations, rects):
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, st["name"])
        if self.home is not None:
            pt = self.to_xy(*self.home)
            p.setPen(QPen(theme.colour("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(pt.x() - 6, pt.y() - 6, 12, 12))
        dots = [self.to_xy(st["lat"], st["lon"]) for st in self.stations]
        return rects + [QRectF(d.x() - 8, d.y() - 8, 16, 16) for d in dots]

    def label_rects(self, fm=None) -> list[QRectF]:
        """Where each station's name goes: right of its dot, else left,
        above or below, whichever first clears every dot and the names
        already placed.
        Stations a few hundred metres apart (a home station and the SDR's
        idea of it) otherwise print their calls over each other."""
        if fm is None:
            from PyQt6.QtGui import QFontMetrics
            fm = QFontMetrics(self.font())
        # Every dot first, so a name is not written over another station.
        dots = [self.to_xy(st["lat"], st["lon"]) for st in self.stations]
        taken = [QRectF(d.x() - 6, d.y() - 6, 12, 12) for d in dots]
        placed: list[QRectF] = []
        for st in self.stations:
            pt = self.to_xy(st["lat"], st["lon"])
            w, h = fm.horizontalAdvance(st["name"]) + 2, fm.height()
            spots = [QRectF(pt.x() + 8, pt.y() - h / 2, w, h), QRectF(pt.x() - 8 - w, pt.y() - h / 2, w, h),
                     QRectF(pt.x() - w / 2, pt.y() - 7 - h, w, h), QRectF(pt.x() - w / 2, pt.y() + 7, w, h)]
            rect = next((r for r in spots if not any(r.intersects(q) for q in placed + taken)), spots[0])
            placed.append(rect)
        return placed

    def credits(self) -> str:
        """The credit line: the built-in data always, OSM when a tile shows."""
        parts = []
        if self.tiles_drawn:
            parts.append(self.tr("© OpenStreetMap contributors"))
        if self.base is not None:
            parts.append(self.tr("Natural Earth; towns: GeoNames (CC BY 4.0)"))
        return " · ".join(parts)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.colour("surface"))
        self._basemap(p)
        self._tiles(p)
        self._grid(p)
        self._rings(p)
        taken = self._stations(p)
        if not self.tiles_drawn:
            self._towns(p, taken)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        band = QRectF(0, self.height() - 20, self.width(), 20)
        back = QColor(theme.colour("surface"))
        back.setAlpha(200)
        p.fillRect(band, back)
        p.setPen(theme.colour("ink-dim"))
        p.drawText(band.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.tr("%1 km across").replace("%1", fmt.number(2 * self.span_km)))
        p.drawText(band.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self.credits())

    # -- the mouse ---------------------------------------------------------------------

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        self.span_km = max(2.0, min(5000.0, self.span_km * (0.8 ** steps)))
        self.update()

    def mousePressEvent(self, e):
        self._drag = (e.position(), self.centre)
        self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is None or not e.buttons():
            return
        start, centre = self._drag
        if (e.position() - start).manhattanLength() > 3:
            self._moved = True
        saved = self.centre
        self.centre = centre
        a = self.to_latlon(start.x(), start.y())
        b = self.to_latlon(e.position().x(), e.position().y())
        self.centre = saved
        self.centre = (max(-85, min(85, centre[0] + a[0] - b[0])), ((centre[1] + a[1] - b[1] + 180) % 360) - 180)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None and not self._moved:
            name = self.station_at(e.position())
            if name:
                self.set_selected(name)
                self.station_clicked.emit(name)
        self._drag = None

    def station_at(self, pos: QPointF, within: float = 10.0) -> str:
        best, best_d = "", within
        for st in self.stations:
            d = self.to_xy(st["lat"], st["lon"]) - pos
            dist = math.hypot(d.x(), d.y())
            if dist <= best_d:
                best, best_d = st["name"], dist
        return best

    def contextMenuEvent(self, e):
        lat, lon = self.to_latlon(e.pos().x(), e.pos().y())
        self.place_requested.emit(lat, lon)

    def mouseDoubleClickEvent(self, _e):
        if self.home is not None:
            self.centre = self.home
            self.update()
