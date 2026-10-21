"""APRS: who has been heard (on the radio, and through the SDR monitor when
it feeds the list), where they are, and this station's beacon.

The heard list is the engine's APRS station's table: last heard, packets,
heard direct or through which digipeater, distance and bearing from this
station's locator, the last comment or status. Selecting a station, in the
list or by clicking it on the map, centres the map on it and fills the
details panel under the list: its symbol, position and locator, distance
and bearing, course and speed, how it was heard and the whole path, its
comment, and its last few packets. Double clicking starts a message to it.

The map (ui/mapview.py) gets each station's symbol, track (tracks.py, fed
by the controller with every position heard) and whether it was heard
direct or through a digipeater. OpenStreetMap tiles go under it only when
Settings › Map turns them on (tiles.py).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSplitter, QTableWidgetItem, QTextBrowser,
                             QVBoxLayout, QWidget)
from shack_packet import aprs

from ... import grid, host, tiles
from .. import fmt, tables
from ..mapview import MapView

#: Packets listed in the details panel.
DETAIL_PACKETS = 5


class AprsPage(QWidget):
    message_to = pyqtSignal(str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._sig = None
        self._tiles_for = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.beacon = QLabel()
        self.beacon.setObjectName("dim")
        self.beacon.setWordWrap(True)
        self.settings_btn = QPushButton(self.tr("Beacon settings…"))
        self.settings_btn.clicked.connect(lambda: window.open_settings("aprs"))
        self.map_btn = QPushButton(self.tr("Map settings…"))
        self.map_btn.clicked.connect(lambda: window.open_settings("map"))
        self.objects_btn = QPushButton(self.tr("My objects…"))
        self.objects_btn.clicked.connect(self.open_objects)
        top.addWidget(self.beacon, 1)
        top.addWidget(self.objects_btn)
        top.addWidget(self.settings_btn)
        top.addWidget(self.map_btn)
        lay.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QSplitter(Qt.Orientation.Vertical)
        self.table = tables.make([self.tr("Station"), self.tr("Last heard (UTC)"), self.tr("Packets"),
                                  self.tr("Heard via"), self.tr("Distance"), self.tr("Comment")])
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.cellDoubleClicked.connect(self._double)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        left.addWidget(self.table)
        left.addWidget(self.details)
        left.setSizes([420, 220])
        self.map = MapView()
        self.map.station_clicked.connect(self.select)
        self.map.place_requested.connect(self.place_here)
        #: Tests fill the object dialogs in here (ui/objects.py).
        self.objects_hook = None
        split.addWidget(left)
        split.addWidget(self.map)
        split.setSizes([520, 520])
        lay.addWidget(split, 1)
        self._show_details("")

    # -- this station's objects (0.3.0, ui/objects.py) --------------------------------------------

    def open_objects(self):
        from ..objects import ObjectsDialog
        dlg = ObjectsDialog(self.window, edit_hook=self.objects_hook)
        if self.objects_hook is None:
            dlg.exec()
        return dlg

    def place_here(self, lat: float, lon: float):
        """A right click on the map: place an object there."""
        from ..objects import ObjectsDialog
        dlg = ObjectsDialog(self.window, edit_hook=self.objects_hook)
        return dlg.edit(None, lat, lon)

    # -- choosing a station ---------------------------------------------------------------------

    def selected_call(self) -> str:
        """The station chosen in the list ("" for none): the pad's CONNECT
        calls it."""
        rows = self.table.selectionModel().selectedRows()
        item = self.table.item(rows[0].row(), 0) if rows else None
        return item.text() if item is not None else ""

    def select(self, call: str) -> None:
        """Choose `call` in the list (a click on the map does this)."""
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.text() == call:
                self.table.selectRow(r)
                return
        self.map.set_selected(call)
        self._show_details(call)

    def _selected(self):
        call = self.selected_call()
        rows = self.table.selectionModel().selectedRows()
        item = self.table.item(rows[0].row(), 0) if rows else None
        pos = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if pos:
            self.map.centre = pos
        self.map.set_selected(call)
        self._show_details(call)

    def _double(self, row, _col):
        item = self.table.item(row, 0)
        if item is not None:
            self.message_to.emit(item.text())

    # -- the details panel -----------------------------------------------------------------------

    def details_rows(self, call: str) -> list[tuple[str, str]]:
        """(label, value) for the details panel, in order; [] for none."""
        ctl = self.window.ctl
        h = next((x for x in ctl.heard() if x.call == call), None)
        if h is None:
            return []
        out = []
        if h.symbol:
            name = aprs.Packet(position=aprs.Position(0, 0, symbol=h.symbol)).symbol_name
            out.append((self.tr("Symbol"), f"{h.symbol}" + (f" ({name})" if name else "")))
        home = ctl.position()
        if h.position is not None:
            p = h.position
            out.append((self.tr("Position"), self.tr("%1, %2 (%3)").replace("%1", fmt.number(p.lat, 4))
                        .replace("%2", fmt.number(p.lon, 4)).replace("%3", grid.from_latlon(p.lat, p.lon, 6))))
            if home is not None:
                km, brg = aprs.distance_bearing(home[0], home[1], p.lat, p.lon)
                out.append((self.tr("Distance"), self.tr("%1 km at %2°").replace("%1", fmt.number(km, 1))
                            .replace("%2", fmt.number(brg))))
        course, speed = ctl.tracks.motion(call)
        if speed is not None:
            moving = self.tr("%1 km/h").replace("%1", fmt.number(speed))
            if course is not None:
                moving = self.tr("%1, heading %2°").replace("%1", moving).replace("%2", fmt.number(course))
            out.append((self.tr("Moving"), moving))
        out.append((self.tr("Heard"), self.tr("direct") if h.direct else
                    self.tr("through %1").replace("%1", h.heard_from or "?")))
        if h.path:
            out.append((self.tr("Path"), ", ".join(str(a) for a in h.path)))
        if h.comment or h.status:
            out.append((self.tr("Comment"), h.comment or h.status))
        out.append((self.tr("Last heard"), self.tr("%1 UTC, %2 packets").replace(
            "%1", fmt.utc_time(ctl.wall_of(h.last_s))).replace("%2", fmt.number(h.packets))))
        with ctl._lock:
            rows = [r for r in ctl.rows if r.source == call and r.direction in ("rx", "sdr")][-DETAIL_PACKETS:]
        for r in reversed(rows):
            out.append((fmt.utc_time(r.time), r.text))
        return out

    def _show_details(self, call: str) -> None:
        rows = self.details_rows(call) if call else []
        if not rows:
            self.details.setHtml("<p>" + self.tr("Choose a station in the list or on the map.") + "</p>")
            return
        import html
        cells = "".join(f"<tr><td><b>{html.escape(k)}</b>&nbsp;&nbsp;</td><td>{html.escape(v)}</td></tr>"
                        for k, v in rows)
        self.details.setHtml(f"<h3>{html.escape(call)}</h3><table>{cells}</table>")

    def extras_text(self) -> str:
        """GPS, iGate and weather in a sentence each, when they are on."""
        st = self.window.ctl.aprsx.status()
        out = []
        if st["gps"] != "off":
            fix = st["fix"]
            out.append(self.tr("GPS: %1, %2.").replace("%1", fmt.number(fix[0], 4)).replace("%2", fmt.number(fix[1], 4))
                       if fix else self.tr("GPS: no fix yet.") if st["gps"] == "open" else
                       self.tr("GPS: not reachable (%1).").replace("%1", st["gps_error"] or "-"))
        if st["igate"] != "off":
            stats = st["igate_stats"]
            state = {"connecting": self.tr("connecting"), "connected": self.tr("logging in"),
                     "verified": self.tr("connected"), "failed": self.tr("not connected")}.get(st["igate"], st["igate"])
            out.append(self.tr("iGate: %1, %2 gated, %3 sent on air.").replace("%1", state)
                       .replace("%2", str(stats.get("rf_to_is", 0))).replace("%3", str(stats.get("is_to_rf", 0))))
        if st["weather"] != "off":
            out.append(self.tr("Weather: %1 readings.").replace("%1", str(len(st["wx"]))) if st["wx"] else
                       self.tr("Weather: no reading yet."))
        return " ".join(out)

    # -- tiles -------------------------------------------------------------------------------------

    def _tiles(self) -> None:
        """A TileStore while Settings › Map has tiles on, else none at all:
        nothing is fetched, or even looked for, with tiles off."""
        m = self.window.ctl.settings.map
        want = (m.tile_url, int(m.cache_mb)) if m.tiles and tiles.valid_template(m.tile_url) else None
        if want == self._tiles_for:
            return
        self._tiles_for = want
        self.map.set_tiles(None if want is None else tiles.TileStore(
            host.data_dir() / "tiles", want[0], cap_bytes=want[1] * 1024 * 1024))

    def close_tiles(self) -> None:
        self._tiles_for = None
        self.map.set_tiles(None)

    # -- refresh ------------------------------------------------------------------------------------

    def refresh(self):
        ctl = self.window.ctl
        s = ctl.settings
        self._tiles()
        home = ctl.position()
        self.map.set_home(home)
        if s.aprs.beacon and home is not None:
            self.beacon.setText(self.tr("Beaconing this station's position every %1 minutes (when armed) via %2.")
                                .replace("%1", str(s.aprs.every_min)).replace("%2", s.aprs.path or "-"))
        elif home is None:
            self.beacon.setText(self.tr("No beacon: set your locator in the setup check to beacon a position."))
        else:
            self.beacon.setText(self.tr("Beacon off."))
        extra = self.extras_text()
        if extra:
            self.beacon.setText(self.beacon.text() + " " + extra)
        heard = ctl.heard()
        stations = []
        sdr_positions = {}
        mon = ctl.sdr.monitor if ctl.sdr is not None else None
        if mon is not None:
            with mon.lock:
                sdr_positions = {k: (p.lat, p.lon, list(p.track)) for k, p in mon.positions.items()}
        sig = (tuple((h.call, h.last_s, h.packets) for h in heard), tuple(sorted(sdr_positions)))
        # Which receiver heard each station last. The SDR monitor feeds the
        # same heard list (sdrmon.py), where a station it heard looks
        # "direct"; but the SDR is not the station's receiver, so such a
        # station is drawn as the SDR's, with no line saying how this
        # station's radio hears it.
        last_rx = {}
        with ctl._lock:
            for r in reversed(ctl.rows):
                if r.direction in ("rx", "sdr") and r.source not in last_rx:
                    last_rx[r.source] = r.direction
        for h in heard:
            if h.position is not None:
                course, _speed = ctl.tracks.motion(h.call)
                st = {"name": h.call, "lat": h.position.lat, "lon": h.position.lon,
                      "track": ctl.tracks.track(h.call), "symbol": h.symbol, "course": course}
                if last_rx.get(h.call) == "sdr":
                    st["kind"] = "sdr"
                else:
                    st.update(direct=bool(h.direct), via="" if h.direct else h.heard_from)
                stations.append(st)
        for name, (lat, lon, track) in sdr_positions.items():
            if not any(st["name"] == name for st in stations):
                # An object has no track of the controller's (that follows
                # senders): the monitor's own short one stands in.
                stations.append({"name": name, "lat": lat, "lon": lon, "track": ctl.tracks.track(name) or track,
                                 "kind": "sdr"})
        for d in s.objects.items:
            if not any(st["name"] == d["name"] for st in stations):
                stations.append({"name": d["name"], "lat": float(d["lat"]), "lon": float(d["lon"]),
                                 "symbol": d.get("symbol", "/."), "kind": "own"})
        self.map.set_stations(stations)
        if sig == self._sig:
            return
        self._sig = sig
        chosen = self.selected_call()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(heard))
        for i, h in enumerate(sorted(heard, key=lambda h: -h.last_s)):
            call = QTableWidgetItem(h.call)
            if h.position is not None:
                call.setData(Qt.ItemDataRole.UserRole, (h.position.lat, h.position.lon))
            when = QTableWidgetItem(fmt.utc_time(ctl.wall_of(h.last_s)))
            packets = QTableWidgetItem()
            packets.setData(Qt.ItemDataRole.DisplayRole, h.packets)
            via = QTableWidgetItem(self.tr("direct") if h.direct else (h.heard_from or ""))
            dist = QTableWidgetItem("")
            if home is not None and h.position is not None:
                km, brg = aprs.distance_bearing(home[0], home[1], h.position.lat, h.position.lon)
                dist.setText(self.tr("%1 km at %2°").replace("%1", fmt.number(km, 1)).replace("%2", fmt.number(brg)))
                dist.setData(Qt.ItemDataRole.UserRole, km)
            comment = QTableWidgetItem(h.comment or h.status or "")
            for col, item in enumerate((call, when, packets, via, dist, comment)):
                self.table.setItem(i, col, item)
        tables.fit(self.table)
        self.table.setSortingEnabled(True)
        # The rows were rebuilt: the same station stays chosen, without the
        # map jumping back to it (that is only for a new choice).
        for r in range(self.table.rowCount()):
            if chosen and self.table.item(r, 0).text() == chosen:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        if chosen:
            self._show_details(chosen)
