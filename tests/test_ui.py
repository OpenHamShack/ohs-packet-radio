"""The window, offscreen, on the simulator: it starts, the setup check walks
through to the end, transmit stays off until it is set up and armed, a chat
and an APRS message render, diagnostics save, and nothing listens."""
from __future__ import annotations

import json
import socket
import zipfile

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox, QTextBrowser

from conftest import station_settings, wait_for
from shack_packet_app import settings as settings_mod
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.window import PAGES, MainWindow


def quick_band():
    # First beacons 2 s apart (0.5-1.0 s, 2.5-3.0 s, 4.5-5.0 s): started
    # together, the three collided on a loaded machine (SimBand._cfg).
    return SimBand(55.95, -3.2, first_beacon_s=(0.5, 1.0), stagger_s=2.0, beacon_every_s=60)


def quiet_band():
    return SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0), beacon_every_s=60)


@pytest.fixture
def window(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    made = []

    def make(s=None, band=quiet_band, persist=True, secrets=None, **kw):
        s = s if s is not None else station_settings()
        ctl = Controller(s, secrets=secrets or {}, band_factory=band, **kw)
        win = MainWindow(s, secrets or {}, controller=ctl, persist=persist)
        win.messages_shown = shown
        win.show()
        made.append(win)
        return win
    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def test_the_window_starts_on_the_simulator_and_draws_every_page(window, qapp):
    win = window(station_settings(sdr__source="synthetic", ip__enabled=True, ip__address="44.131.1.1"))
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.tick()
    assert win.bar.call.text() == "GM4BRI"
    assert "Connected" in win.bar.radio.text()
    assert win.bar.arm.text() == "Arm transmit"
    for key in PAGES:
        win.nav.setCurrentRow(PAGES.index(key))
        win.tick()
    win.nav.setCurrentRow(PAGES.index("monitor"))

    def rows():
        win.tick()
        return win.pages["monitor"].model.rowCount()
    # The synthetic SDR band's stations, decoded, in the Monitor list.
    assert wait_for(lambda: rows() > 0, 20, app=qapp)


def test_setup_check_walks_through_and_leaves_transmit_off(window, qapp, folders):
    from shack_packet_app.settings import Settings
    win = window(Settings(), band=quick_band)
    win.start()
    assert wait_for(lambda: win.wizard is not None, 2, app=qapp)
    wiz = win.wizard
    page = wiz.station
    # Not callsigns: a bad SSID, aliases and the placeholder call.
    for bad in ("2E0XYZ-5x", "WIDE1-1", "N0CALL", "APRS", "GM4BRI-16"):
        page.call.setText(bad)
        assert not page.isComplete(), bad
        assert "not a callsign" in page.feedback.text()
    page.call.setText("GM4BRI-7")
    page.locator.setText("IO85JW")
    page._classes()
    page.licence.setCurrentIndex(page.licence.findData("full"))
    assert page.isComplete()
    wiz.next()
    assert wiz.currentPage() is wiz.radio
    wiz.radio.kind.setCurrentIndex(wiz.radio.kind.findData("simulator"))
    wiz.next()
    assert wiz.currentPage() is wiz.radio_menu
    wiz.next()
    assert wiz.currentPage() is wiz.audio
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    wiz.next()
    assert wiz.currentPage() is wiz.receive
    # The simulated stations beacon within a couple of seconds.
    assert wait_for(lambda: len(wiz.receive.heard()) >= 1, 15, app=qapp)
    wiz.receive.tick()
    assert "decoded" in wiz.receive.count.text()
    wiz.next()
    tx = wiz.transmit
    assert wiz.currentPage() is tx
    assert tx.allow.isEnabled() and not tx.allow.isChecked()
    # The simulator is not a radio: no turnaround to measure, ticked or not.
    tx.present.setChecked(True)
    tx.dummy.setChecked(True)
    assert not tx.measure.isEnabled()
    wiz.accept()
    qapp.processEvents()
    saved = settings_mod.load()
    assert saved.setup_done and saved.station.callsign == "GM4BRI" and saved.station.ssid == 7
    assert saved.tx.set_up is False
    # Transmit is off: Arm says so instead of arming.
    win.toggle_arm()
    assert not win.ctl.status()["armed"]
    assert win.messages_shown and "not been set up" in win.messages_shown[-1]


def test_no_callsign_means_transmit_cannot_even_be_offered(window, qapp):
    from shack_packet_app.settings import Settings
    win = window(Settings())
    win.start()
    assert wait_for(lambda: win.wizard is not None, 2, app=qapp)
    wiz = win.wizard
    wiz.next()                                       # no callsign: listening only
    for _ in range(4):
        wiz.next()
    assert wiz.currentPage() is wiz.transmit
    assert not wiz.transmit.allow.isEnabled()
    wiz.reject()


def test_the_setup_check_lists_what_to_set_on_the_radio_chosen(window, qapp):
    from shack_packet_app.settings import Settings
    win = window(Settings())
    win.start()
    assert wait_for(lambda: win.wizard is not None, 2, app=qapp)
    wiz = win.wizard
    wiz.station.call.setText("GM4BRI")
    wiz.next()
    page = wiz.radio_menu

    def choose(kind, **kw):
        if wiz.currentPage() is page:
            wiz.back()
        wiz.radio.kind.setCurrentIndex(wiz.radio.kind.findData(kind))
        for combo, value in kw.items():
            box = getattr(wiz.radio, combo)
            box.setCurrentIndex(box.findData(value))
        wiz.next()
        assert wiz.currentPage() is page
        return page.yours.text()

    # Nothing on this page opens the radio: the link is still the old one.
    text = choose("ic-705", transport="usb")
    assert "DATA MOD: USB" in text and "CI-V USB Port" in text
    assert "&nbsp;*" in text and not page.unchecked.isHidden()     # unconfirmed on a real 705
    assert win.ctl.settings.radio.kind == "simulator"
    text = choose("ic-9700", transport="lan")
    assert "Network User1" in text and "DATA MOD" not in text
    assert "DATA MOD" in page.app_does.text()                     # the 9700's link sets it itself
    text = choose("ftx-1", y_ptt="rts")
    assert "RPTT SELECT: the same line chosen here" in text and "CAT-1 RATE: 38400" in text
    text = choose("ftx-1", y_ptt="cat")
    assert "RPTT SELECT: OFF" in text
    text = choose("soundcard", s_ptt="vox")
    assert "VOX on" in text and "Tune the radio" in text
    text = choose("simulator")
    assert text == "Nothing to set." and page.unchecked.isHidden()
    wiz.reject()


def test_arming_from_the_bar_counts_down_and_disarms(window, qapp):
    win = window()
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    QTest.mouseClick(win.bar.arm, Qt.MouseButton.LeftButton)
    win.tick()
    assert win.ctl.status()["armed"]
    assert win.bar.arm.text() in ("ARMED 15:00", "ARMED 14:59")
    QTest.mouseClick(win.bar.arm, Qt.MouseButton.LeftButton)
    win.tick()
    assert not win.ctl.status()["armed"]
    assert win.bar.arm.text() == "Arm transmit"


def test_the_bar_shows_an_expiry_the_moment_the_station_notices_it(window, qapp):
    class JumpClock:
        offset = 0.0

        def __call__(self):
            import time
            return time.monotonic() + self.offset
    clock = JumpClock()
    win = window(clock=clock)
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.ctl.arm(2)
    win.tick()
    assert win.bar.arm.text() in ("ARMED 2:00", "ARMED 1:59") and win.bar.arm.isChecked()
    clock.offset += 90
    win.tick()
    assert win.bar.arm.text() in ("ARMED 0:30", "ARMED 0:29")
    clock.offset += 31
    win.tick()
    assert win.bar.arm.text() == "Arm transmit" and not win.bar.arm.isChecked()
    win.tick()                                       # the expiry event, drained
    assert win.statusBar().currentMessage() == "The transmit arming expired."


def test_a_chat_with_the_node_renders(window, qapp):
    win = window()
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.ctl.arm(5)
    win.nav.setCurrentRow(PAGES.index("chat"))
    page = win.pages["chat"]
    page.call.setText("GB7SIM")
    QTest.mouseClick(page.go, Qt.MouseButton.LeftButton)
    view = next(iter(page.views.values()))

    def screen():
        win.tick()
        return view.screen.toPlainText()
    assert wait_for(lambda: "Welcome to GB7SIM" in screen(), 20, app=qapp)
    QTest.keyClicks(view.line, "hello node")
    QTest.keyClick(view.line, Qt.Key.Key_Return)
    assert wait_for(lambda: "You said: hello node" in screen(), 20, app=qapp)
    assert "> hello node" in screen()
    assert view.state.text().startswith("Connected to GB7SIM")


def test_enter_on_a_blank_chat_line_sends_nothing(window, qapp):
    win = window()
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.ctl.arm(5)
    win.nav.setCurrentRow(PAGES.index("chat"))
    page = win.pages["chat"]
    page.call.setText("GB7SIM")
    QTest.mouseClick(page.go, Qt.MouseButton.LeftButton)
    view = next(iter(page.views.values()))
    assert wait_for(lambda: view.sess.connected, 20, app=qapp)
    calls = []
    win.ctl.send = lambda *a: calls.append(a)
    QTest.keyClick(view.line, Qt.Key.Key_Return)
    QTest.keyClicks(view.line, "   ")
    QTest.keyClick(view.line, Qt.Key.Key_Return)
    assert calls == [] and view.history == []


def test_an_aprs_message_shows_its_acknowledgement(window, qapp):
    win = window()
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.ctl.arm(5)
    win.nav.setCurrentRow(PAGES.index("messages"))
    page = win.pages["messages"]
    page.to.setText("G4SIM")
    page.text.setText("testing the app")
    QTest.mouseClick(page.send_btn, Qt.MouseButton.LeftButton)

    def states():
        win.tick()
        return [page.thread.item(r, 3).text() for r in range(page.thread.rowCount())]
    assert wait_for(lambda: "Acknowledged" in states(), 20, app=qapp)
    assert wait_for(lambda: "Received" in states(), 20, app=qapp)


def test_a_message_sent_disarmed_waits_and_says_so(window, qapp):
    win = window()
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    page = win.pages["messages"]
    page.to.setText("G4SIM")
    page.text.setText("not yet")
    page.send()
    assert "armed" in page.note.text()
    wait_for(lambda: False, 1.5, app=qapp)
    assert not any(r.direction == "tx" for r in win.ctl.rows)


def test_the_aprs_page_lists_and_maps_the_simulated_stations(window, qapp):
    from shack_packet_app.simband import FIXED, NODE, TRACKER
    win = window(band=quick_band)
    win.start(first_run_check=False)
    win.nav.setCurrentRow(PAGES.index("aprs"))
    page = win.pages["aprs"]

    def calls():
        win.tick()
        return {page.table.item(r, 0).text() for r in range(page.table.rowCount())}
    assert wait_for(lambda: {TRACKER, FIXED, NODE} <= calls(), 15, app=qapp)
    # Every one of them beacons a position: each is on the map, with a
    # distance from the operator's locator in the list.
    assert {st["name"] for st in page.map.stations} >= {TRACKER, FIXED, NODE}
    assert page.map.home is not None
    row = next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).text() == NODE)
    assert " km at " in page.table.item(row, 4).text()
    page.table.selectRow(row)
    node = next(st for st in page.map.stations if st["name"] == NODE)
    assert page.map.centre == pytest.approx((node["lat"], node["lon"]))
    # Double click: a message to that station.
    page.table.cellDoubleClicked.emit(row, 0)
    assert win.stack.currentIndex() == PAGES.index("messages")
    assert win.pages["messages"].to.text() == NODE


def test_g4sim_leaves_a_message_in_the_mailbox_and_it_reads(window, qapp):
    win = window(station_settings(mailbox__enabled=True))
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.nav.setCurrentRow(PAGES.index("mailbox"))
    page = win.pages["mailbox"]
    win.tick()
    assert not page.sim_mail.isHidden()
    assert "GM4BRI-1" in page.where.text()
    QTest.mouseClick(page.sim_mail, Qt.MouseButton.LeftButton)
    assert "Arm transmit" in page.note.text()        # the mailbox answers over the air
    win.ctl.arm(5)

    def rows():
        win.tick()
        return page.table.rowCount()
    assert wait_for(lambda: rows() == 1, 30, app=qapp)
    assert page.table.item(0, 1).text() == "G4SIM" and page.table.item(0, 4).text() == "Test from the simulator"
    assert page.table.item(0, 3).text() == "new"
    page.table.selectRow(0)
    assert "Hello from G4SIM" in page.body.toPlainText()
    win.tick()
    assert page.table.item(0, 3).text() == "yes"
    QTest.mouseClick(page.delete, Qt.MouseButton.LeftButton)
    assert rows() == 0


def test_the_mailbox_button_is_for_the_simulator_only(window, qapp):
    win = window(station_settings(radio__kind="none", mailbox__enabled=True))
    win.start(first_run_check=False)
    win.nav.setCurrentRow(PAGES.index("mailbox"))
    win.tick()
    assert win.pages["mailbox"].sim_mail.isHidden()


def test_the_ip_page_pings_the_simulated_node(window, qapp):
    from shack_packet_app.simband import NODE_IP
    win = window(station_settings(ip__enabled=True, ip__address="44.131.1.1", ip__network="44.131.0.0/16"))
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    win.ctl.arm(5)
    win.nav.setCurrentRow(PAGES.index("ip"))
    page = win.pages["ip"]
    win.tick()
    assert page.state.text() == "On, datagram mode"
    assert "later release" in page.os_if.text()
    page.target.setText(NODE_IP)
    page.count.setValue(1)
    QTest.mouseClick(page.go, Qt.MouseButton.LeftButton)

    def result():
        win.tick()
        return page.results.item(0, 2).text() if page.results.rowCount() else ""
    assert wait_for(lambda: result().startswith("reply in"), 30, app=qapp)
    # ARP learnt the node's call for its address on the way.
    arp = {page.arp.item(r, 0).text(): page.arp.item(r, 1).text() for r in range(page.arp.rowCount())}
    assert arp.get(NODE_IP) == "GB7SIM"


def test_the_sdr_page_draws_the_synthetic_band(window, qapp):
    win = window(station_settings(sdr__source="synthetic"))
    win.start(first_run_check=False)
    win.nav.setCurrentRow(PAGES.index("sdr"))
    page = win.pages["sdr"]

    def frames():
        win.tick()
        return sum(int(page.channels.item(r, 2).text()) for r in range(page.channels.rowCount()))
    assert wait_for(lambda: frames() > 0, 30, app=qapp)
    assert page.toggle.text() == "Stop" and page.state.text().startswith("Receiving at")
    assert page.waterfall.image is not None and page.heard.rowCount() > 0
    # The channel names over the waterfall: none overprints another, the
    # three 12.5 kHz neighbours (144.925, .9375, .950) included.
    labels = page.waterfall.label_layout(page.waterfall.fontMetrics())
    assert len(labels) >= 5
    spans = [(row, x - w / 2, x + w / 2) for _c, x, row, w in labels]
    for i, (row, a0, a1) in enumerate(spans):
        for r2, b0, b1 in spans[i + 1:]:
            assert row != r2 or a1 <= b0 or b1 <= a0
    QTest.mouseClick(page.toggle, Qt.MouseButton.LeftButton)
    win.tick()
    assert page.toggle.text() == "Start" and win.bar.sdr.text() == ""


def test_diagnostics_zip_has_everything_but_the_password(window, qapp, folders):
    s = station_settings(radio__kind="simulator", radio__username="op")
    win = window(s, secrets={"icom_password": "s3cr3t-pass"})
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    import logging
    from shack_packet_app import applog
    # The program log the app installs at start (__main__), here too: the
    # bundle's log.txt must have something in it to leak.
    logging.getLogger().addHandler(applog.ring)
    logging.getLogger("test").warning("connecting with password=s3cr3t-pass")
    from pathlib import Path
    logging.getLogger("test").warning("could not open %s", Path.home() / "radio.json")
    win.ctl.arm(5)
    win.ctl.send_message("G4SIM", "for the log")
    assert wait_for(lambda: any(r.direction == "tx" for r in win.ctl.rows), 10, app=qapp)
    try:
        out = win.save_diagnostics(str(folders / "diag.zip"))
    finally:
        logging.getLogger().removeHandler(applog.ring)
    assert out is not None
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"summary.json", "settings.json", "log.txt", "frames.jsonl", "timing.json", "radio-events.jsonl",
                "window.png"} <= names
        everything = b"".join(z.read(n) for n in names if n != "window.png")
        summary = json.loads(z.read("summary.json"))
        frames = z.read("frames.jsonl").decode().splitlines()
        timing = json.loads(z.read("timing.json"))
    assert b"s3cr3t-pass" not in everything
    assert str(Path.home()).encode() not in everything and b"could not open ~/radio.json" in everything
    assert b"password=<redacted>" in everything
    assert summary["versions"]["shack-packet-app"] and summary["status"]["callsign"] == "GM4BRI"
    assert any(json.loads(f)["dir"] == "tx" for f in frames)
    assert timing["txdelayS"] >= 0.3 and timing["transmissions"] >= 1


def test_diagnostics_carry_the_log_file_the_program_still_has_open(window, qapp, folders):
    # As on Windows: the RotatingFileHandler holds shack-packet.log open
    # while the bundle reads it, and a crash from an earlier run is in the
    # file and fatal.log, not the ring.
    import logging
    import logging.handlers
    from pathlib import Path
    from shack_packet_app import applog, host
    host.log_dir().mkdir(parents=True)
    (host.log_dir() / applog.FATAL_NAME).write_text("Fatal Python error: Segmentation fault\n")
    fh = logging.handlers.RotatingFileHandler(host.log_dir() / applog.LOG_NAME, maxBytes=2_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(applog.FORMAT))
    logging.getLogger().addHandler(fh)
    try:
        logging.getLogger("test").error("earlier run: password=hunter22 in %s", Path.home() / "x")
        win = window(secrets={"icom_password": "hunter22"})
        out = win.save_diagnostics(str(folders / "diag.zip"))
    finally:
        logging.getLogger().removeHandler(fh)
        fh.close()
    assert out is not None
    with zipfile.ZipFile(out) as z:
        logfile = z.read("log-file.txt").decode()
        fatal = z.read("fatal.txt").decode()
    assert "earlier run: password=<redacted> in ~" in logfile and "hunter22" not in logfile
    assert "Segmentation fault" in fatal


def test_help_save_diagnostics_asks_where_and_saves(window, qapp, folders, monkeypatch):
    # The menu item itself, as a tester clicks it - 0.1.0 passed QAction's
    # `checked` as the path, never showed the dialog and aborted.
    from PyQt6.QtWidgets import QFileDialog
    asked = []
    target = folders / "from-menu.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (asked.append(a), (str(target), "Zip files (*.zip)"))[1])
    win = window()
    win.act_diag.trigger()
    assert len(asked) == 1 and target.is_file()
    with zipfile.ZipFile(target) as z:
        assert "summary.json" in z.namelist()


def test_a_diagnostics_save_that_fails_says_so_and_the_window_lives(window, qapp, folders, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog
    from shack_packet_app import diagnostics

    def broken(*a, **k):
        raise TypeError("something nobody expected")
    monkeypatch.setattr(diagnostics, "build", broken)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(folders / "x.zip"), ""))
    win = window()
    win.act_diag.trigger()
    assert any("something nobody expected" in m for m in win.messages_shown)
    assert not (folders / "x.zip").exists() and win.isVisible()


def test_the_turnaround_needs_both_confirmations(window, qapp, monkeypatch):
    from shack_rig import sound
    from shack_packet.radios.turnaround import CONFIRM
    backend = sound.FakeBackend([("USB Audio Interface", 1, 1, [48000])], realtime=True)
    s = station_settings(radio__kind="soundcard", radio__audio_in="USB Audio Interface",
                         radio__audio_out="USB Audio Interface", radio__ptt="vox")
    win = window(s, sound_backend=backend)
    win.sound_backend = backend
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    asked = []
    win.open_setup()
    wiz = win.wizard
    for _ in range(5):
        wiz.next()
    tx = wiz.transmit
    assert wiz.currentPage() is tx
    assert tx.measure.text() == "Transmit and measure"
    from PyQt6.QtWidgets import QLabel
    assert "This transmits." in [w.text() for w in tx.findChildren(QLabel)]
    monkeypatch.setattr(win.ctl, "measure_turnaround", lambda confirm, done=None: asked.append(confirm))
    tx.allow.setChecked(True)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    tx._gates()
    assert not tx.measure.isEnabled()
    tx.present.setChecked(True)
    assert not tx.measure.isEnabled()
    tx.dummy.setChecked(True)
    monkeypatch.setattr(wiz.ctl, "measure_turnaround", lambda confirm, done=None: asked.append(confirm))
    assert tx.measure.isEnabled()
    QTest.mouseClick(tx.measure, Qt.MouseButton.LeftButton)
    assert asked == [CONFIRM]
    tx.dummy.setChecked(False)
    assert not tx.measure.isEnabled()
    wiz.ctl.disarm()
    wiz.reject()


def test_the_window_opens_no_listening_socket(window, qapp, monkeypatch):
    listened, bound = [], []
    real_listen, real_bind = socket.socket.listen, socket.socket.bind
    monkeypatch.setattr(socket.socket, "listen", lambda self, *a: (listened.append(self), real_listen(self, *a))[1])
    monkeypatch.setattr(socket.socket, "bind", lambda self, addr: (bound.append(addr), real_bind(self, addr))[1])
    win = window(station_settings(sdr__source="synthetic", ip__enabled=True, ip__address="44.131.1.1"))
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    for key in PAGES:
        win.nav.setCurrentRow(PAGES.index(key))
        win.tick()
    wait_for(lambda: False, 1.0, app=qapp)
    win.close()
    assert listened == [] and bound == []


def test_map_names_of_stations_close_together_do_not_overprint(qapp):
    from shack_packet_app.ui.mapview import MapView
    m = MapView()
    m.resize(500, 400)
    m.set_home((55.95, -3.2))
    # A home station and the SDR's fix of it, 300 m apart: one pixel at
    # this zoom, so both names want the same place.
    m.set_stations([{"name": "G4SIM", "lat": 55.88, "lon": -3.25},
                    {"name": "G0SDR", "lat": 55.8827, "lon": -3.25, "kind": "sdr"},
                    {"name": "GB7SIM", "lat": 56.04, "lon": -3.39}])
    rects = m.label_rects()
    assert not rects[0].intersects(rects[1])
    for st in m.stations:                               # nor over anybody's dot
        dot = m.to_xy(st["lat"], st["lon"])
        assert not any(r.contains(dot) for r in rects)
    assert rects[2].left() > m.to_xy(56.04, -3.39).x()     # far away: its usual place, right of the dot
    m.grab()                                         # and it paints


def test_what_the_app_changed_on_the_radio_is_listed_in_full(window, qapp):
    """Station › What the app changed on the radio, from the report lines a
    rig link sends (shack_rig.datasetup.Line.public)."""
    win = window()
    win.start(first_run_check=False)
    dlg = win.show_radio_changes()
    assert "No radio has been set up yet" in dlg.findChild(QTextBrowser).toPlainText()
    dlg.reject()
    win.ctl.radio_setup = {
        "report": [{"key": "nr", "status": "set", "before": "on", "after": "off", "note": ""},
                   {"key": "data_mod", "status": "set", "before": "USB", "after": "LAN", "note": ""},
                   {"key": "mod_level", "status": "report", "before": "50%", "after": None, "note": ""},
                   {"key": "data_off_mod", "status": "manual", "before": None, "after": None,
                    "note": "set DATA OFF MOD to MIC on the IC-705"}],
        "restored": [], "recovered": [{"key": "afc", "status": "recovered", "before": None, "after": "off",
                                       "note": ""}],
        "warnings": [("usb-tx-slips", "")]}
    text = win.show_radio_changes().findChild(QTextBrowser).toPlainText()
    for words in ("Noise reduction", "on → off", "USB → LAN", "MOD level", "50%", "set DATA OFF MOD to MIC",
                  "AFC", "last session", "network (LAN)"):
        assert words in text, words
    assert text.index("network (LAN)") < text.index("Noise reduction"), "the warning first"
