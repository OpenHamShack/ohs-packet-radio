"""Find my radio (findmyradio.py, ui/findradio.py) with every place it
looks faked: the cards it makes from what was found (Ray's GM0OTB station
as the model: an IC-9700 on COM4 beside a second radio's CODEC), the
guidance only where it applies, the /24, the read-only Test, and the box
filling the setup assistant's radio page and Settings › Radio."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import station_settings, wait_for
from shack_packet_app import discover, findmyradio
from shack_packet_app.controller import Controller
from shack_packet_app.plutofind import FindResult, Found as PlutoFound
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.window import MainWindow
from shack_rig import findradio
from shack_rig.icom import civ

RAY_DEVICES = [
    {"name": "IC9700 Microphone (USB Audio CODEC)", "inputs": 1, "outputs": 0},
    {"name": "IC9700 Speakers (USB Audio CODEC)", "inputs": 0, "outputs": 2},
    {"name": "Microphone (2- USB AUDIO  CODEC)", "inputs": 1, "outputs": 0},
    {"name": "Speakers (2- USB AUDIO  CODEC)", "inputs": 0, "outputs": 2},
]


def _scan(*found):
    def scan(cancel=None):
        s = findradio.Scan(found=[findradio.Found(**f) for f in found])
        s.ports = [findradio.PortNote("COM4", "Silicon Labs CP210x USB to UART Bridge (COM4)", "radio", "CI-V"),
                   findradio.PortNote("COM9", "Bluetooth link", "in use", "another program has it open")]
        return s
    return scan


REAL_SEARCH = findmyradio.search


def _search(settings, **kw):
    kw.setdefault("scan", _scan({"kind": "ic-9700", "name": "IC-9700", "port": "COM4", "baud": 19200,
                                 "civ_address": 0xA2, "evidence": "answered CI-V at 0xA2"}))
    kw.setdefault("find_icoms", lambda hosts, timeout_s: [])
    kw.setdefault("find_plutos", lambda: FindResult())
    kw.setdefault("sound_devices", lambda: RAY_DEVICES)
    return REAL_SEARCH(settings, **kw)


def test_a_usb_icom_becomes_a_card_with_every_field_and_its_own_guidance():
    s = station_settings()
    sr = _search(s)
    (c,) = sr.candidates
    assert c.title == "IC-9700 on COM4 (USB)" and c.supported and not c.ask_audio
    assert c.fields["radio"] == {"kind": "ic-9700", "transport": "usb", "civ_device": "COM4", "civ_baud": 19200,
                                 "audio_in": "IC9700 Microphone (USB Audio CODEC)",
                                 "audio_out": "IC9700 Speakers (USB Audio CODEC)"}
    # Answered at 19200: set 115200. A 9700 on USB: transmit over LAN.
    assert c.guidance == ["civ-baud", "9700-usb-tx"]
    new = findmyradio.apply(s, c)
    assert new.radio.civ_device == "COM4" and new.radio.transport == "usb" and s.radio.kind == "simulator"
    assert ("COM9", "Bluetooth link - in use: another program has it open") in sr.notes


def test_a_radio_whose_sound_devices_cannot_be_told_apart_asks():
    sr = _search(station_settings(), scan=_scan({"kind": "ic-705", "name": "IC-705", "port": "/dev/ttyUSB0",
                                                 "baud": 115200, "civ_address": 0xA4}))
    (c,) = sr.candidates
    assert c.ask_audio and c.guidance == []                # 115200 already; not a 9700


def test_the_network_and_plutos_and_the_9700_on_lan_comes_first():
    s = station_settings()
    sr = _search(s, find_icoms=lambda hosts, timeout_s: [discover.FoundIcom("192.0.2.50", 50001, 7)],
                 find_plutos=lambda: FindResult(found=[PlutoFound("usb:1.5.5", "PlutoSDR (ADALM-PLUTO)")]))
    assert [c.how for c in sr.candidates] == ["lan", "usb", "pluto"]
    lan = sr.candidates[0]
    assert lan.fields["radio"] == {"kind": "ic-9700", "transport": "lan", "host": "192.0.2.50", "port": 50001}
    assert lan.guidance == ["lan-user"]
    assert findmyradio.apply(s, sr.candidates[2]).sdr_radio.pluto_uri == "usb:1.5.5"


def test_the_subnet_is_only_ever_a_private_24():
    hosts = findmyradio.subnet_hosts("192.0.2.50")
    assert len(hosts) == 253 and "192.0.2.50" not in hosts and hosts[0] == "192.0.2.1"
    assert findmyradio.subnet_hosts("81.2.69.160") == [] and findmyradio.subnet_hosts("127.0.0.1") == []
    asked = []
    _search(station_settings(radio__host="10.0.0.9"), subnet=True,
            find_icoms=lambda hosts, timeout_s: asked.extend(hosts) or [])
    assert "10.0.0.9" in asked


class Radio9700:
    """A 9700 on the far end of a serial port, for Test."""

    def __init__(self, device, baud):
        self.baud = baud

    def open(self):
        pass

    def close(self):
        pass

    def exchange(self, data, wait):
        if self.baud != 115200:
            return b""
        _to, frm, payload = civ.parse(data)
        if payload == civ.read_freq():
            return civ.frame(b"\x03" + civ.encode_freq(144_800_000), to=frm, frm=0xA2)
        if payload == civ.read_mode():
            return civ.frame(b"\x04\x05\x01", to=frm, frm=0xA2)
        return b""


def test_testing_a_usb_icom_reads_its_frequency_and_mode():
    s = station_settings(radio__kind="ic-9700", radio__transport="usb", radio__civ_device="COM4",
                         radio__civ_baud=115200)
    r = findmyradio.test_connection(s, {}, opener=Radio9700)
    assert r.ok and r.frequency_hz == 144_800_000 and r.mode == "FM"
    s.radio.civ_baud = 9600
    r = findmyradio.test_connection(s, {}, opener=Radio9700)
    assert not r.ok and r.guidance == ["civ-baud"]


def test_testing_a_lan_icom_logs_in_reads_and_says_what_to_check_when_refused():
    made = []

    class Rig:
        def __init__(self, profile, password, ok):
            self.profile, self.password, self.ok = profile, password, ok
            self.state = {"connected": False, "link_state": "connecting"}
            self.stopped = False

        async def start(self):
            pass

        async def connect(self):
            self.state = ({"connected": True, "freq": 432_500_000, "mode_label": "FM", "radio": "IC-9700"}
                          if self.ok else {"connected": False, "link_state": "failed", "error": "login refused"})

        async def stop(self):
            self.stopped = True

    def make_rig(ok):
        def make(profile, password="", tx_enabled=True):
            assert tx_enabled is False
            made.append(Rig(profile, password, ok))
            return made[-1]
        return make
    s = station_settings(radio__kind="ic-9700", radio__transport="lan", radio__host="192.0.2.50",
                         radio__username="Ray")
    r = findmyradio.test_connection(s, {"icom_password": "Secret"}, make_rig=make_rig(True))
    assert r.ok and r.frequency_hz == 432_500_000 and r.radio == "IC-9700"
    assert made[0].password == "Secret" and made[0].profile["username"] == "Ray" and made[0].stopped
    r = findmyradio.test_connection(s, {}, make_rig=make_rig(False))
    assert not r.ok and r.error == "login refused" and r.guidance == ["lan-user", "unlink-remote"]


# -- the box ---------------------------------------------------------------------------


@pytest.fixture
def win(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    band = SimBand(55.95, -3.2, neighbours=False)
    s = station_settings()
    ctl = Controller(s, band_factory=lambda: band)
    w = MainWindow(s, {}, controller=ctl, persist=False)
    w.start(first_run_check=False)
    yield w
    ctl.stop()
    band.stop()


def test_the_setup_page_finds_fills_and_folds(win, qapp, monkeypatch):
    monkeypatch.setattr(findmyradio, "search", lambda s, **kw: _search(s))
    monkeypatch.setattr(discover, "sound_devices", lambda backend=None: RAY_DEVICES)
    win.open_setup()
    wiz = win.wizard
    wiz.next()
    page = wiz.radio
    assert wiz.currentPage() is page and not page.details.opened
    box = page.finder
    box.find(wait=True)
    assert box.cards.count() == 1, box.status.text()
    assert "IC-9700 on COM4" in box.cards.item(0).text()
    assert "115200" in box.guidance.text() and "LAN to transmit" in box.guidance.text()
    box.use()
    # Every field filled in on the page itself, ready for Next.
    assert page.kind.currentData() == "ic-9700" and page.transport.currentData() == "usb"
    assert page._port(page.civ) == "COM4"
    assert page.icom_out.currentText() == "IC9700 Speakers (USB Audio CODEC)"
    assert wiz.s.radio.civ_baud == 19200 and box.test_btn.isEnabled()
    wiz.reject()


def test_settings_has_a_radio_tab_whose_pick_is_kept_on_ok(win, qapp, monkeypatch):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    monkeypatch.setattr(findmyradio, "search", lambda s, **kw: _search(
        s, scan=_scan(), find_icoms=lambda hosts, timeout_s: [discover.FoundIcom("192.0.2.50", 50001, 7)]))
    released = []
    monkeypatch.setattr(win.ctl, "apply", lambda s, secrets=None: released.append(s.radio.kind))
    d = SettingsDialog(win.settings, win, tab="radio")
    assert d.tabs.tabText(d.tabs.currentIndex()) == "Radio"
    d.finder.find(wait=True)
    assert released == ["none", win.settings.radio.kind]        # let go while looking, then taken back
    d.finder.use()
    assert "192.0.2.50" in d.radio_now.text()
    s = d.result_settings()
    assert (s.radio.kind, s.radio.transport, s.radio.host) == ("ic-9700", "lan", "192.0.2.50")
    assert win.settings.radio.kind == "simulator"                # nothing changes until OK
