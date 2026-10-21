"""A Kenwood TM-V71 as the station's radio (0.3.2): its PC port for control
and a sound-card interface on its DATA jack for packet, end to end against
ohs-libs' FakeTmV71 on a pty and a FakeBackend interface whose output
reaches "the air" only while the pretend radio transmits. The station
built at 1200 and at 9600 (G3RUH through the interface's 48 kHz), the
radio set up and put back, keying by the interface's RTS and never over
the PC port; the estimate table's two modes; Find my radio's card and
read-only Test; the control strip's speed, power and busy; the setup
check's radio page. No radio is touched."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import station_settings, wait_for
from shack_packet_app import airtime, findmyradio, radioctl
from shack_packet_app.controller import Controller
from shack_packet_app.ui import radiomenu

pytest.importorskip("shack_rig.kenwood")
from shack_packet import Frame, ax25, fsk  # noqa: E402
from shack_rig import findradio, sound  # noqa: E402
from shack_rig.kenwood.fake import FakeTmV71  # noqa: E402
from shack_rig.serialio import SerialPort  # noqa: E402

CARD = "USB Audio Device"


@pytest.fixture
def bench():
    radio = FakeTmV71().start()
    be = sound.FakeBackend([(CARD, 1, 1, (48000,)), ("Air", 1, 0, (48000,))], realtime=True)
    be.route(CARD, "Air", gate=lambda: radio.transmitting)
    port = radio.interface_port("rts")

    def open_port(device, baud=9600, bits=1):
        return port if device == "/dev/ttyDIGIRIG" else SerialPort(device, baud, stop_bits=bits)
    made = []

    def station(speed=1200, hz=144_800_000):
        s = station_settings(radio__kind="tm-v71", radio__cat_device=radio.device, radio__audio_in=CARD,
                             radio__audio_out=CARD, radio__ptt="rts", radio__ptt_device="/dev/ttyDIGIRIG",
                             radio__data_speed=speed, channel__frequency_hz=hz)
        c = Controller(s, sound_backend=be, open_port=open_port)
        made.append(c)
        c.start()
        return c
    yield radio, be, port, station
    for c in made:
        c.stop()
    be.close()
    radio.stop()


def test_a_9600_station_sets_the_radio_up_keys_by_the_interface_and_puts_it_back(bench):
    radio, be, port, station = bench
    vfo_a, menu = list(radio.vfo[0]), list(radio.menu)
    c = station(9600)
    assert wait_for(lambda: c.link_state == "open", 10), (c.link_error, c.link_error_detail)
    assert c.engine.channel.mode.name == "g3ruh9600"
    assert radio.freq(0) == 144_800_000 and radio.data_band() == 0 and radio.data_speed() == 9600
    assert port.opened and port.history == [], "the interface opened with PTT low"
    air = bytearray()
    listener = sound.Input("Air", air.extend, backend=be)
    try:
        ok, why, _detail = c.arm(1)
        assert ok, why
        frame = Frame.from_monitor("GM4BRI>APRS:>TM-V71 at 9600")
        c.engine.send_frame(frame)
        assert wait_for(lambda: c.link.stats["keyed"] == 1 and not c.link.keyed, 10)
    finally:
        listener.close()
    assert [b for _t, b in radio.tx_history] == [0, None], "band A keyed through PKS, and released"
    rx = fsk.Receiver(48000, 9600)
    got = [h.frame for h in rx.feed(bytes(air))] + [h.frame for h in rx.flush()]
    assert [ax25.parse(f).monitor() for f in got] == [frame.monitor()], "G3RUH at the interface's 48 kHz"
    c.stop()
    assert radio.vfo[0] == vfo_a and radio.menu == menu and radio.band_mode[0] == 1, "all put back"
    assert radio.cat_keys == [] and not radio.transmitting


def test_a_1200_station_runs_afsk_and_leaves_the_menu_at_1200(bench):
    radio, _be, _port, station = bench
    c = station(1200)
    assert wait_for(lambda: c.link_state == "open", 10), (c.link_error, c.link_error_detail)
    assert c.engine.channel.mode.name == "afsk1200" and radio.data_speed() == 1200


def test_a_channel_neither_band_can_reach_fails_with_what_to_do(bench):
    radio, _be, _port, station = bench
    c = station(1200, hz=1_299_100_000)
    assert wait_for(lambda: c.link_state == "failed", 10)
    assert c.link_error == "out-of-range" and "press BAND" in c.link_error_detail
    assert radio.menu_writes == [] and radio.cat_keys == []


def test_the_estimate_offers_the_tmv71s_two_modes_and_no_other():
    s = station_settings(radio__kind="tm-v71")
    assert airtime.current_mode(s) == "afsk1200"
    s.radio.data_speed = 9600
    assert airtime.current_mode(s) == "g3ruh9600"
    rows = {r.mode: r for r in airtime.table(s, 20_000)}
    assert rows["afsk1200"].available and rows["g3ruh9600"].available and rows["g3ruh9600"].current
    for mode in ("g3ruh19200", "g3ruh38400", "gfsk76800", "4fsk38400"):
        assert not rows[mode].available and rows[mode].why == "radio-modes"
    assert rows["g3ruh9600"].good_s < rows["afsk1200"].good_s / 3


def test_find_my_radio_makes_a_tmv71_card_with_its_interface_and_traps(monkeypatch):
    # The interface is on the PC port cable's USB hub (shack_rig's own test
    # holds the matching; here, that the app keeps what it matched).
    real = findradio.match_audio
    monkeypatch.setattr(findradio, "match_audio",
                        lambda f, i, o, others=(): real(f, i, o, others=others, codec_of=lambda port, names: CARD))

    def scan(cancel=None):
        return findradio.Scan(found=[findradio.Found(kind="tm-v71", name="TM-V71", port="/dev/ttyUSB1", baud=9600,
                                                     evidence="answered Kenwood ID with TM-V71 at 9600 baud")])
    s = station_settings()
    sr = findmyradio.search(s, scan=scan, find_icoms=lambda hosts, timeout_s: [],
                            find_plutos=lambda: findmyradio_plutos(),
                            sound_devices=lambda: [{"name": CARD, "inputs": 1, "outputs": 2},
                                                   {"name": "Webcam Microphone", "inputs": 1, "outputs": 0}])
    (c,) = sr.candidates
    assert c.supported and c.title == "TM-V71 on /dev/ttyUSB1 (USB)"
    assert c.fields["radio"] == {"kind": "tm-v71", "cat_device": "/dev/ttyUSB1", "cat_baud": 9600,
                                 "audio_in": CARD, "audio_out": CARD, "ptt": "rts"}
    assert c.guidance == ["tmv71-ptt", "tmv71-band", "tmv71-9600"]
    assert findmyradio.apply(s, c).radio.kind == "tm-v71"


def findmyradio_plutos():
    from shack_packet_app.plutofind import FindResult
    return FindResult()


def test_testing_a_tmv71_reads_its_data_band_and_never_writes(bench):
    radio = bench[0]
    s = station_settings(radio__kind="tm-v71", radio__cat_device=radio.device, radio__cat_baud=9600)
    r = findmyradio.test_connection(s, {})
    assert r.ok and r.frequency_hz == 433_500_000 and r.mode == "FM" and r.radio == "TM-V71, band B"
    assert not any(cmd.startswith(("FO ", "MU ", "TX", "PC 0,", "VM 0,")) and "," in cmd for cmd in radio.commands)
    assert radio.cat_keys == [] and radio.menu_writes == []


class KenwoodStub:
    """What radioctl asks of a KenwoodLink: power by name, squelch read only."""

    def __init__(self):
        self.levels = {"power_level": 0, "squelch": 5}
        self.writes = []
        self.meters = {"busy": False}

    def control_keys(self):
        return ["power_level", "squelch"]

    def control_choices(self, key):
        return [(0, "H"), (1, "M"), (2, "L")] if key == "power_level" else None

    def control_range(self, key):
        return 31 if key == "squelch" else 255

    def control_writable(self, key):
        return key != "squelch"

    def read_control(self, key):
        return self.levels[key]

    def set_control(self, key, raw):
        from shack_rig.datasetup import Line
        self.writes.append((key, raw))
        self.levels[key] = raw
        return Line(key, "set")

    def read_meters(self):
        return self.meters


class Ctl:
    def __init__(self, link):
        self.link = link
        self.settings = station_settings(radio__kind="tm-v71")
        self.notes, self.events = [], []
        self.linktest = None

    def _emit(self, *a):
        self.events.append(a)


def test_power_is_a_choice_and_the_squelch_is_only_read():
    ctl = Ctl(KenwoodStub())
    rc = radioctl.RadioControls(ctl)
    try:
        rc.tick()
        assert rc.wait_idle()
        assert rc.values == {"power_level": 0, "squelch": 16} and rc.choices["power_level"][2] == (2, "L")
        rc.set("power_level", 2)
        rc.set("squelch", 50)
        assert rc.wait_idle()
        assert ctl.link.writes == [("power_level", 2)], "the squelch is never written"
        rc.nudge("power_level", 1)                    # a dial's detent up: L -> M
        assert rc.wait_idle() and ctl.link.writes[-1] == ("power_level", 1)
        rc.nudge("power_level", 5)
        assert rc.wait_idle() and ctl.link.writes[-1] == ("power_level", 0), "no further than H"
    finally:
        rc.close()


@pytest.fixture
def win(qapp, monkeypatch):
    from shack_packet_app.simband import SimBand
    from shack_packet_app.ui.window import MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    band = SimBand(55.95, -3.2, neighbours=False)
    s = station_settings()
    ctl = Controller(s, band_factory=lambda: band)
    w = MainWindow(s, {}, controller=ctl, persist=False)
    w.start(first_run_check=False)
    assert wait_for(lambda: ctl.link_state == "open", 5, app=qapp)
    yield w
    w.close()
    band.stop()


def test_the_strip_offers_both_speeds_power_by_name_and_busy(win, qapp, monkeypatch):
    strip = win.control_strip
    win.show()
    s = win.settings.copy()
    s.radio.kind = "tm-v71"
    win.settings.radio.kind = "tm-v71"           # what the strip draws from, without opening a radio
    strip.reset()
    assert strip.mode.isEnabled() and [strip.mode.itemData(i) for i in range(strip.mode.count())] == [1200, 9600]
    applied = []
    monkeypatch.setattr(win, "apply_settings", lambda new, secrets=None: applied.append(new))
    strip.mode.setCurrentIndex(1)
    strip._mode_chosen(1)
    assert applied and applied[-1].radio.data_speed == 9600
    link = KenwoodStub()
    win.ctl.link = link
    assert wait_for(lambda: (win.tick() or True) and strip.power_choice.count() == 3, 5, app=qapp)
    assert strip.power_choice.isVisible() and strip.power_choice.currentText() == "H"
    assert not strip.cat["rf_gain"].isVisible() and not strip.cat["rf_power"].isVisible()
    assert strip.cat["squelch"].isVisible() and not strip.cat["squelch"].isEnabled(), "read only"
    assert strip.meter.text() == "Channel clear"
    link.meters = {"busy": True}
    strip.controls._last_meter = 0.0
    assert wait_for(lambda: (win.tick() or True) and strip.meter.text() == "Channel busy", 5, app=qapp)
    strip.power_choice.setCurrentIndex(2)
    strip.power_choice.activated.emit(2)
    assert strip.controls.wait_idle() and link.writes[-1] == ("power_level", 2)
    win.ctl.link = None


def test_the_setup_check_says_what_to_set_on_a_tmv71():
    text = " ".join(s.text for s in radiomenu.steps("tm-v71", ptt="rts", speed=1200))
    assert "Menu 519" in text and "40 mV" in text and "PKS (pin 3)" in text and "PR9 (pin 4)" not in text
    nine = radiomenu.steps("tm-v71", ptt="cm108", speed=9600)
    text = " ".join(s.text for s in nine)
    assert "PR9 (pin 4)" in text and "2 V peak to peak" in text and "udev" in text
    app_does = [s for s in nine if s.by_app]
    assert len(app_does) == 1 and "never keys the radio over the PC port" in app_does[0].text


def test_the_setup_assistant_has_a_tmv71_page_that_keeps_its_fields(qapp, monkeypatch):
    from shack_packet_app.settings import Settings
    from shack_packet_app.simband import SimBand
    from shack_packet_app.ui.window import MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    band = SimBand(55.95, -3.2, neighbours=False)
    s = Settings()
    w = MainWindow(s, {}, controller=Controller(s, band_factory=lambda: band), persist=False)
    w.show()
    try:
        w.start()
        assert wait_for(lambda: w.wizard is not None, 2, app=qapp)
        wiz = w.wizard
        wiz.station.call.setText("GM4BRI")
        wiz.next()
        page = wiz.radio
        page.kind.setCurrentIndex(page.kind.findData("tm-v71"))
        assert page.stack.currentWidget() is page.pages["tm-v71"]
        page.k_cat.setCurrentText("/dev/ttyUSB1")
        page.k_baud.setCurrentIndex(page.k_baud.findData(57600))
        page.k_speed.setCurrentIndex(page.k_speed.findData(9600))
        page.k_ptt.setCurrentIndex(page.k_ptt.findData("cm108"))
        page.k_ptt_port.setCurrentText("/dev/hidraw2")
        page.k_gpio.setValue(4)
        wiz.next()
        r = wiz.s.radio
        assert (r.kind, r.cat_device, r.cat_baud, r.data_speed, r.ptt, r.ptt_device, r.cm108_gpio) == \
            ("tm-v71", "/dev/ttyUSB1", 57600, 9600, "cm108", "/dev/hidraw2", 4)
        text = wiz.radio_menu.yours.text()
        assert "PR9 (pin 4)" in text and "udev" in text and "Menu 519" in text
        assert "menu 518" in wiz.radio_menu.app_does.text()
        wiz.reject()
    finally:
        w.close()
        band.stop()


def test_the_turnaround_is_not_offered_on_a_tmv71_and_never_keys_it(bench):
    from shack_packet.radios.turnaround import CONFIRM
    radio, _be, _port, station = bench
    c = station(1200)
    assert wait_for(lambda: c.link_state == "open", 10), (c.link_error, c.link_error_detail)
    c.arm(1)
    with pytest.raises(RuntimeError, match="no power meter"):
        c.measure_turnaround(CONFIRM)
    assert radio.tx_history == []
