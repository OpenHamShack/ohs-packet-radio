"""The APRS map: the built-in basemap read back from its file, tiles only
when turned on and then by the tile policy's rules, tracks, symbols, how a
station was heard, and the details panel - on the simulator and offscreen,
with no network (a tile server here is a function)."""
from __future__ import annotations

import time

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import basemap, tiles, tracks


# -- the built-in basemap ----------------------------------------------------------------------


def test_the_basemap_reads_back_with_every_point_inside_its_runs_box():
    b = basemap.load()
    assert b is not None
    for key, layer in b.layers.items():
        assert len(layer) > 100, key
        for i in range(0, len(layer), 97):
            pts = layer.run(i)
            w, s, e, n = layer.box[i]
            assert len(pts) >= 2
            assert (pts[:, 0] >= w - 1e-3).all() and (pts[:, 0] <= e + 1e-3).all(), key
            assert (pts[:, 1] >= s - 1e-3).all() and (pts[:, 1] <= n + 1e-3).all(), key


def test_the_basemap_has_the_forth_and_its_towns_largest_first():
    b = basemap.load()
    coast = b.layers["coast10"]
    near = coast.visible(-3.5, 55.9, -2.8, 56.1)          # the Firth of Forth
    assert len(near) > 0
    t = b.towns
    names = [t.name(int(i)) for i in t.visible(-3.5, 55.8, -3.0, 56.1, 5)]
    assert names[0] == "Edinburgh" and len(names) == 5
    assert all(t.pop[a] >= t.pop[b] for a, b in zip(t.visible(-8, 50, 2, 59, 50)[:-1], t.visible(-8, 50, 2, 59, 50)[1:]))


def test_the_basemap_says_where_its_data_came_from_and_under_what_licence():
    b = basemap.load()
    srcs = b.manifest["sources"]
    assert {s["layer"] for s in srcs} == {"coast10", "border10", "coast50", "border50", "towns"}
    for s in srcs:
        assert s["url"].startswith("https://") and len(s["sha256"]) == 64 and s["fetched"]
    towns = next(s for s in srcs if s["layer"] == "towns")
    assert "CC BY 4.0" in towns["licence"] and "geonames" in towns["url"]
    assert all("public domain" in s["licence"].lower() for s in srcs if s["layer"] != "towns")
    assert "GeoNames" in b.attribution


# -- the map view --------------------------------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch):
    calls = []

    def refuse(*a, **k):
        calls.append(a)
        raise OSError("no network in tests")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    return calls


def test_the_map_draws_coast_towns_and_credits_with_no_network(qapp, no_network):
    from shack_packet_app.ui.mapview import MapView
    m = MapView()
    m.resize(700, 500)
    m.set_home((55.95, -3.2))
    m.span_km = 40
    m.grab()
    assert "Edinburgh" in m.towns_drawn
    assert "GeoNames" in m.credits() and "OpenStreetMap" not in m.credits()
    assert m.tiles is None and m.tiles_drawn == 0
    assert no_network == []                              # tiles are off: nothing even tried


def test_town_names_never_cover_a_station_or_its_name(qapp):
    from shack_packet_app.ui.mapview import MapView
    m = MapView()
    m.resize(700, 500)
    m.set_home((55.95, -3.2))
    m.span_km = 40
    # A station right on Edinburgh: its name wins, the town's gives way.
    m.set_stations([{"name": "GB7EDN", "lat": 55.9533, "lon": -3.1883}])
    m.grab()
    assert "Edinburgh" not in m.towns_drawn


def png(colour="red") -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    img = QImage(256, 256, QImage.Format.Format_RGB32)
    img.fill(QColor(colour))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def test_tiles_are_fetched_for_the_view_only_and_credited(qapp, tmp_path):
    from shack_packet_app.ui.mapview import MapView
    asked = []

    def fetch(url):
        asked.append(url)
        return png()
    m = MapView()
    m.resize(600, 400)
    m.set_home((55.95, -3.2))
    m.span_km = 40
    store = tiles.TileStore(tmp_path / "tiles", fetch=fetch)
    m.set_tiles(store)
    m.grab()
    z = m.tile_zoom()
    view = tiles.tiles_for(*m.view_box(), z)
    assert wait_for(lambda: len(asked) >= len(view), 5, app=qapp)
    assert len(asked) == len(view) <= 12                 # the view's tiles, nothing around it
    assert all(f"/{z}/" in u for u in asked)
    m.grab()
    assert m.tiles_drawn == len(view)
    assert m.credits().startswith("© OpenStreetMap contributors")
    m.grab()                                             # cached: not asked again
    time.sleep(0.2)
    assert len(asked) == len(view)
    m.set_tiles(None)


def test_a_tile_server_that_fails_is_not_hammered_and_the_basemap_shows(qapp, tmp_path):
    from shack_packet_app.ui.mapview import MapView
    asked = []

    def fetch(url):
        asked.append(url)
        raise OSError("offline")
    m = MapView()
    m.resize(400, 300)
    m.set_home((55.95, -3.2))
    m.set_tiles(tiles.TileStore(tmp_path / "tiles", fetch=fetch))
    m.grab()
    assert wait_for(lambda: len(asked) > 0, 5, app=qapp)
    first = len(asked)
    time.sleep(0.2)
    for _ in range(3):
        m.grab()
    time.sleep(0.2)
    assert len(asked) == first                            # each failed tile waits RETRY_S
    assert m.tiles_drawn == 0 and "OpenStreetMap" not in m.credits()
    m.set_tiles(None)


def test_tile_cache_rules(tmp_path):
    now = [time.time()]                                  # the files' own times are real
    asked = []
    store = tiles.TileStore(tmp_path, fetch=lambda url: asked.append(url) or b"x" * 1000, clock=lambda: now[0],
                            cap_bytes=3500)
    try:
        store.want([(10, 1, 1), (10, 1, 2)])
        assert wait_for(lambda: store.get(10, 1, 2) is not None and store.get(10, 1, 1) is not None, 5)
        assert len(asked) == 2 and store.fresh(10, 1, 1)
        assert "tile.openstreetmap.org/10/1/1.png" in asked[0] or "tile.openstreetmap.org/10/1/2.png" in asked[0]
        store.want([(10, 1, 1)])
        time.sleep(0.1)
        assert len(asked) == 2                            # fresh for a week
        now[0] += tiles.KEEP_S + 1
        assert not store.fresh(10, 1, 1) and store.get(10, 1, 1) is not None   # stale, still shown
        store.want([(10, 1, 1)])
        assert wait_for(lambda: len(asked) == 3, 5)
        for y in range(3, 7):
            (store.path(10, 2, y)).parent.mkdir(parents=True, exist_ok=True)
            store.path(10, 2, y).write_bytes(b"y" * 1000)
        assert store.prune() >= 3
        assert sum(p.stat().st_size for p in store.dir.rglob("*.png")) <= 3500
    finally:
        store.close()


def test_the_user_agent_names_the_app_and_the_url_must_have_its_fields():
    assert tiles.user_agent().startswith("OpenHamShack-Packet-Radio/")
    assert tiles.valid_template(tiles.OSM_URL)
    assert not tiles.valid_template("https://example.org/tiles.png")
    assert not tiles.valid_template("file:///{z}/{x}/{y}.png")
    with pytest.raises(ValueError):
        tiles.TileStore(".", "https://example.org/{z}.png")


def test_tiles_for_and_tile_bounds_agree():
    for t in tiles.tiles_for(-3.5, 55.8, -3.0, 56.1, 10):
        w, s, e, n = tiles.tile_bounds(*t)
        assert w <= -3.0 and e >= -3.5 and s <= 56.1 and n >= 55.8


# -- tracks, symbols, how a station was heard ----------------------------------------------------


def test_a_track_grows_only_when_the_station_moves_and_ages_out():
    tr = tracks.Tracks(keep_s=600)
    tr.update("M0X-9", 0, 55.0, -3.0, 90, 40)
    tr.update("M0X-9", 30, 55.0001, -3.0)             # 11 m: not a move
    tr.update("M0X-9", 60, 55.01, -3.0, 0, 50)
    assert [p[0] for p in tr.track("M0X-9")] == [0, 60]
    assert tr.motion("M0X-9") == (0, 50)
    tr.update("M0X-9", 700, 55.02, -3.0)
    assert [p[0] for p in tr.track("M0X-9")] == [700]       # 0 and 60 are over ten minutes old
    tr.update("FIXED", 0, 55.5, -3.5)
    assert len(tr.track("FIXED")) == 1


def test_the_controller_keeps_tracks_of_whatever_page_is_shown():
    from shack_packet.ax25 import Frame

    from shack_packet_app.controller import Controller
    ctl = Controller(station_settings())
    ctl._heard_frame(Frame.from_monitor("M0TRK-9>APRS:!5530.00N/00410.00W>090/030"))
    ctl._heard_frame(Frame.from_monitor("M0TRK-9>APRS:!5531.00N/00405.00W>045/040"))
    track = ctl.tracks.track("M0TRK-9")
    assert [(round(la, 3), round(lo, 3)) for _t, la, lo in track] == [(55.5, -4.167), (55.517, -4.083)]
    course, speed = ctl.tracks.motion("M0TRK-9")
    assert course == 45 and speed == pytest.approx(40 * 1.852, abs=0.5)


def test_symbols_have_shapes_and_overlays():
    from shack_packet_app.ui import symbols
    assert symbols.shape_of("/>") == "mover" and symbols.shape_of("/-") == "house"
    assert symbols.shape_of("S#") == "digi" and symbols.overlay_of("S#") == "S"
    assert symbols.shape_of("\\#") == "digi" and symbols.overlay_of("\\#") == ""
    assert symbols.shape_of("/!") == "dot" and symbols.shape_of("") == "dot"


def test_direct_is_a_line_home_and_digipeated_goes_through_the_digi(qapp):
    from shack_packet_app.ui.mapview import MapView
    m = MapView()
    m.resize(500, 400)
    m.set_home((55.95, -3.2))
    m.set_stations([{"name": "GB7DIG", "lat": 56.0, "lon": -3.0, "direct": True, "symbol": "S#"},
                    {"name": "M0FAR", "lat": 56.3, "lon": -2.6, "direct": False, "via": "GB7DIG"},
                    {"name": "M0LOST", "lat": 55.7, "lon": -3.6, "direct": False, "via": "MB7NOWHERE"},
                    {"name": "G0SDR", "lat": 55.8, "lon": -3.3, "kind": "sdr"}])
    links = {name: (len(pts), direct) for name, pts, direct in m.links()}
    assert links == {"GB7DIG": (2, True), "M0FAR": (3, False)}
    m.set_selected("M0FAR")
    m.grab()


# -- the APRS page ----------------------------------------------------------------------------------


def test_the_details_panel_and_a_click_on_the_map(qapp, monkeypatch):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QMessageBox

    from shack_packet_app.controller import Controller
    from shack_packet_app.simband import NODE, TRACKER, SimBand
    from shack_packet_app.ui.window import PAGES, MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    s = station_settings()
    band = lambda: SimBand(55.95, -3.2, first_beacon_s=(0.5, 1.0), stagger_s=2.0)  # noqa: E731
    win = MainWindow(s, {}, controller=Controller(s, band_factory=band), persist=False)
    win.resize(1200, 800)
    win.show()
    try:
        win.start(first_run_check=False)
        win.nav.setCurrentRow(PAGES.index("aprs"))
        page = win.pages["aprs"]

        def calls():
            win.tick()
            return {page.table.item(r, 0).text() for r in range(page.table.rowCount())}
        assert wait_for(lambda: {NODE, TRACKER} <= calls(), 15, app=qapp)
        assert "Choose a station" in page.details.toPlainText()
        node = next(st for st in page.map.stations if st["name"] == NODE)
        assert node["symbol"] == "/B" and node["direct"] is True
        page.map.span_km = 60
        page.map.centre = (node["lat"], node["lon"])
        page.map.repaint()
        at = page.map.to_xy(node["lat"], node["lon"])
        QTest.mouseClick(page.map, Qt.MouseButton.LeftButton, pos=QPoint(round(at.x()), round(at.y())))
        assert page.selected_call() == NODE and page.map.selected == NODE
        text = page.details.toPlainText()
        assert NODE in text and "direct" in text and " km at " in text and "IO" in text
        assert "GB7SIM node" in text                     # its comment, and its packet in the list
        # The list rebuilding (a new packet) keeps the choice and the panel.
        page._sig = None
        win.tick()
        assert page.selected_call() == NODE
    finally:
        win.close()


def test_map_settings_turn_tiles_on_and_off(qapp, monkeypatch, no_network):
    from shack_packet_app.controller import Controller
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    from shack_packet_app.ui.window import PAGES, MainWindow
    s = station_settings()
    win = MainWindow(s, {}, controller=Controller(s), persist=False)
    win.show()
    try:
        win.start(first_run_check=False)
        win.nav.setCurrentRow(PAGES.index("aprs"))
        page = win.pages["aprs"]
        win.tick()
        assert page.map.tiles is None
        dlg = SettingsDialog(win.settings, win, "map")
        assert dlg.tabs.currentIndex() == SettingsDialog.TABS.index("map")
        assert not dlg.tiles_on.isChecked()
        assert "fetches map pictures from the internet" in dlg.tiles_on.text()
        dlg.tiles_on.setChecked(True)
        dlg.tile_url.setText("not a url")
        new = dlg.result_settings()
        dlg.reject()
        assert new.map.tiles and new.map.tile_url == tiles.OSM_URL
        win.apply_settings(new)
        win.tick()
        assert page.map.tiles is not None and page.map.tiles.url == tiles.OSM_URL
        new = win.settings.copy()
        new.map.tiles = False
        win.apply_settings(new)
        win.tick()
        assert page.map.tiles is None
    finally:
        win.close()


