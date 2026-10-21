"""An SDR as the station's radio (0.2, PLAN-0.2 §3.6): the settings, the
links made for each kind (a Pluto's guard only when transmit is set up, a
USB Pluto receive-only), arming refused without the band filter or with
the Pluto's guard down, and the setup check's SDR panels - the Pluto check,
the AD9364 enable behind both confirmations, the crystal correction - on
stand-in Pluto tools. No SDR is opened by any test here."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet.radios.base import AudioRadioLink
from shack_packet_app import links, plutosetup
from shack_packet_app import settings as settings_mod
from shack_packet_app.controller import Controller
from shack_packet_app.settings import SdrRadioSettings, Settings


# -- settings ------------------------------------------------------------------------------

def test_sdr_settings_round_trip_and_an_older_file_gets_the_defaults():
    s = Settings()
    s.radio.kind = "pluto"
    s.sdr_radio.pluto_uri = "ip:192.168.3.1"
    s.sdr_radio.filter_confirmed = True
    s.sdr_radio.rx_gain_db = 30.0
    back = settings_mod.from_dict(settings_mod.to_dict(s))
    assert back.sdr_radio == s.sdr_radio and back.radio.kind == "pluto"
    old = settings_mod.to_dict(Settings())
    del old["sdr_radio"]
    assert settings_mod.from_dict(old).sdr_radio == SdrRadioSettings()
    assert "pluto_password" not in str(settings_mod.to_dict(s))       # the password is a secret


# -- the links ----------------------------------------------------------------------------------

def _make(kind, transmit, sdr=None, secrets=None, tmp_path=None):
    s = Settings()
    s.radio.kind = kind
    return links.make_link(s.radio, station=None, frequency_hz=144_800_000, transmit=transmit,
                           sdr=sdr or SdrRadioSettings(), secrets=secrets or {}, data_dir=tmp_path)


def test_a_pluto_gets_its_guard_only_when_transmit_is_set_up(tmp_path):
    from shack_pluto.guard import Guard
    link = _make("pluto", True, secrets={"pluto_password": "s3cret"}, tmp_path=tmp_path)
    assert isinstance(link.guard, Guard) and link.transmit
    assert link.guard.runner.host == "192.168.2.1" and link.guard.runner.password == "s3cret"
    assert link.guard.max_key_s == 15.0
    quiet = _make("pluto", False, tmp_path=tmp_path)
    assert quiet.guard is None and not quiet.transmit
    assert callable(quiet.xo_correction)                               # looked up for the Pluto plugged in


def test_a_pluto_on_usb_opens_with_a_guard_that_cannot_start(tmp_path):
    link = _make("pluto", True, sdr=SdrRadioSettings(pluto_uri="usb:1.7.5"), tmp_path=tmp_path)
    with pytest.raises(Exception) as e:
        link.guard.start()
    assert e.value.reason == "guard-no-network" and not link.guard.confirmed


def test_the_xo_lookup_finds_the_value_kept_for_that_serial(tmp_path):
    from shack_pluto.xo import XoStore
    XoStore(tmp_path).put("serial:03d7635e", -19.1, "test")
    look = links.xo_lookup(tmp_path)
    assert look({"hw_serial": "03D7635E"}) == 39_999_236
    assert look({"hw_serial": "ffffffffffffffff"}) is None and look({}) is None


def test_hackrf_and_receive_only_links_are_made_from_their_settings(tmp_path):
    h = _make("hackrf", False, sdr=SdrRadioSettings(hackrf_tx_vga_db=12, hackrf_serial="0000abcd"))
    assert h.tx_vga_db == 12 and h.serial == "0000abcd" and not h.amp and not h.transmit
    r = _make("sdr-rx", True, sdr=SdrRadioSettings(rx_source="rtl_tcp", rx_host="10.0.0.5", rx_port=1235))
    assert r.RECEIVE_ONLY and r.source.kind == "rtl_tcp" and (r.source.host, r.source.port) == ("10.0.0.5", 1235)
    assert _make("sdr-rx", True, sdr=SdrRadioSettings(rx_source="sdrplay")).source.kind == "sdrplay"
    assert _make("sdr-rx", True).source.kind == "rtlsdr"


def test_a_bundled_libhackrf_is_given_to_the_link_by_its_full_path(tmp_path, monkeypatch):
    # conda-forge's Windows DLL is hackrf-0.dll, a name the engine's own
    # list lacks, so the bundle's copy must reach HackRFLink as lib_path.
    import sys
    from shack_packet_app import host
    (tmp_path / "hackrf").mkdir()
    dll = tmp_path / "hackrf" / "hackrf-0.dll"
    dll.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(host, "WINDOWS", True)
    assert host.hackrf_lib() == dll
    assert _make("hackrf", False, tmp_path=tmp_path).lib_path == dll
    dll.unlink()
    assert host.hackrf_lib() is None and _make("hackrf", False, tmp_path=tmp_path).lib_path is None


def test_power_status_for_an_sdr_says_its_gain_is_its_power():
    s = Settings()
    for kind in ("pluto", "hackrf"):
        s.radio.kind = kind
        assert links.power_status(s.radio, object()) == ("sdr", "")
    s.radio.kind = "sdr-rx"
    assert links.power_status(s.radio, object()) is None


# -- arming --------------------------------------------------------------------------------------

class FakeSdrLink(AudioRadioLink):
    """Stands in for PlutoLink/HackRFLink: opens, never touches hardware."""
    rate = 12_000

    def __init__(self, **kw):
        super().__init__(**kw)
        self.guard_state = "running"
        self.unguarded_why = None
        self.tx_refused_why = None

    def _radio_key(self):
        pass

    def _radio_unkey(self):
        pass

    def _radio_audio(self, pcm):
        return True


@pytest.fixture
def sdr_ctl():
    made = []

    def make(kind, **kw):
        s = station_settings(**kw)
        s.radio.kind = kind
        fakes = []

        def factory(radio, **lk):
            return links.make_link(radio, **lk, sdr_factory=lambda *a, **k: fakes.append(
                FakeSdrLink(station=k["station"])) or fakes[-1])
        c = Controller(s, link_factory=factory)
        made.append(c)
        c.start()
        assert wait_for(lambda: c.link_state == "open", 5)
        return c, fakes[-1]
    yield make
    for c in made:
        c.stop()


def test_an_sdr_will_not_arm_without_the_band_filter_confirmed(sdr_ctl):
    c, _ = sdr_ctl("pluto")
    assert c.arm()[1] == "sdr-filter"
    c2, _ = sdr_ctl("pluto", sdr_radio__filter_confirmed=True)
    ok, why, _ = c2.arm(1)
    assert ok and why == ""


def test_a_pluto_whose_guard_is_down_will_not_arm_and_says_why(sdr_ctl):
    c, link = sdr_ctl("pluto", sdr_radio__filter_confirmed=True)
    link.guard_state, link.unguarded_why = "failed", "guard-no-ssh: root@192.168.2.1: auth failed"
    assert c.arm() == (False, "tx-unguarded", "guard-no-ssh: root@192.168.2.1: auth failed")


def test_an_old_hackrf_will_not_arm_and_a_receive_only_sdr_never_does(sdr_ctl):
    c, link = sdr_ctl("hackrf", sdr_radio__filter_confirmed=True)
    link.tx_refused_why = "firmware 2021.03.1 (USB API 0x0104)"
    assert c.arm()[1] == "tx-firmware"
    c2, _ = sdr_ctl("sdr-rx")
    assert c2.arm()[1] == "receive-only"


# -- the setup check's SDR panels ---------------------------------------------------------------

class FakeTools:
    """plutosetup.Tools without a Pluto: what a check finds is given."""

    def __init__(self, ident, reach=False, enable="offer", xo=None):
        self.ident, self.reach, self.enable_state, self.xo = ident, reach, enable, xo
        self.calls = []

    def check(self, uri, password, hz):
        self.calls.append(("check", uri, password, hz))
        return plutosetup.CheckResult(self.ident, hz, self.reach, self.enable_state, self.xo)

    def enable(self, ident, password, *, operator_confirmed):
        from shack_pluto.ad9364 import EnableResult
        self.calls.append(("enable", ident.uri, operator_confirmed))
        return EnableResult(backup="/data/pluto-env/x.env", quiet_after_s=0.12)

    def save_xo(self, key, ppm, how):
        from shack_pluto.xo import correction_for
        self.calls.append(("save", key, round(ppm, 2), how))
        return correction_for(ppm)

    def forget_xo(self, key):
        self.calls.append(("forget", key))


def _ident(**kw):
    from shack_pluto.identity import PlutoIdentity
    base = dict(uri="ip:192.168.2.1", hw_model="Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)", fw_version="v0.38",
                serial="104473dc59", chip="ad9363", family="adi", tx_range=(325e6, 3.8e9), rx_range=(325e6, 3.8e9))
    base.update(kw)
    return PlutoIdentity(**base)


@pytest.fixture
def radio_page(qapp):
    from test_ui import quiet_band
    from shack_packet_app.ui.window import MainWindow
    s = station_settings()

    def factory(radio, **lk):                       # no test opens a real SDR
        return links.make_link(radio, **lk, sdr_factory=lambda *a, **k: FakeSdrLink(station=k["station"]))
    ctl = Controller(s, secrets={}, band_factory=quiet_band, link_factory=factory)
    win = MainWindow(s, {}, controller=ctl, persist=False)
    win.show()
    win.start()
    win.open_setup()
    assert wait_for(lambda: win.wizard is not None, 2, app=qapp)
    wiz = win.wizard
    wiz.next()
    page = wiz.radio
    page.kind.setCurrentIndex(page.kind.findData("pluto"))
    yield wiz, page, win
    wiz.reject()
    win.close()


def test_the_radio_list_offers_the_sdrs_and_shows_their_panel(radio_page):
    wiz, page, _ = radio_page
    kinds = [page.kind.itemData(i) for i in range(page.kind.count())]
    assert kinds[-4:] == ["pluto", "hackrf", "sdr-rx", "none"]
    assert page.stack.currentWidget() is page.pages["pluto"]
    page.kind.setCurrentIndex(page.kind.findData("hackrf"))
    assert page.stack.currentWidget() is page.pages["hackrf"]
    assert "transmit experimental" in page.kind.currentText(), "HackRF transmit is untested on air (0.2.0)"
    assert "experimental" not in page.kind.itemText(page.kind.findData("pluto"))
    from PyQt6.QtWidgets import QLabel
    shown = " ".join(lbl.text() for lbl in page.pages["hackrf"].findChildren(QLabel))
    assert "Transmit is experimental" in shown


def test_a_stock_pluto_on_adi_firmware_is_offered_the_enable_behind_both_confirmations(radio_page, qapp):
    wiz, page, _ = radio_page
    tools = page.sdr.tools = FakeTools(_ident())
    released, restored, asked = [], [], []
    page.sdr.release_radio = lambda: released.append(1)
    page.sdr.restore_radio = lambda: restored.append(1)
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    assert tools.calls[0][3] == 144_800_000                        # the APRS channel the check tunes to
    assert "out of reach" in page.sdr.check_text.text() and "AD9364" in page.sdr.check_text.text()
    assert not page.sdr.enable_btn.isHidden()
    page.sdr.confirm_enable = lambda: asked.append(1) or False    # the operator does not confirm
    page.sdr.enable()
    assert asked and not released and not [c for c in tools.calls if c[0] == "enable"]
    page.sdr.confirm_enable = lambda: True
    page.sdr.enable()
    assert wait_for(lambda: restored, 3, app=qapp)
    assert released == [1] and ("enable", "ip:192.168.2.1", True) in tools.calls
    assert "Done" in page.sdr.check_text.text() and "0.12" in page.sdr.check_text.text()


def test_the_enable_dialog_needs_both_boxes(qapp):
    from shack_packet_app.ui.sdrpages import EnableDialog
    from PyQt6.QtWidgets import QDialogButtonBox
    d = EnableDialog(None)
    ok = d.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    d.dummy.setChecked(True)
    assert not ok.isEnabled()
    d.present.setChecked(True)
    assert ok.isEnabled() and d.confirmed()


def test_tezuka_is_not_offered_the_enable_and_is_told_why(radio_page, qapp):
    wiz, page, _ = radio_page
    page.sdr.tools = FakeTools(_ident(fw_version="tezuka-v0.3.21", family="tezuka"), enable="refused")
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    assert page.sdr.enable_btn.isHidden()
    assert "has not been checked with the AD9364 setting" in page.sdr.check_text.text()


def test_a_pluto_that_does_not_answer_is_said(radio_page, qapp):
    wiz, page, _ = radio_page

    class Gone(FakeTools):
        def check(self, uri, password, hz):
            raise OSError("Connection refused")
    page.sdr.tools = Gone(None)
    page.sdr.check()
    assert wait_for(lambda: "No Pluto answered" in page.sdr.check_text.text(), 3, app=qapp)
    assert not page.sdr.xo_save.isEnabled()


def test_the_crystal_correction_is_measured_saved_and_forgotten_for_the_checked_pluto(radio_page, qapp):
    wiz, page, _ = radio_page
    tools = page.sdr.tools = FakeTools(_ident(chip="ad9361", tx_range=(46.9e6, 6e9), rx_range=(46.9e6, 6e9)),
                                       reach=True, enable="")
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    assert "None saved" in page.sdr.xo_now.text() and page.sdr.xo_save.isEnabled()
    page.sdr.xo_mode.setCurrentIndex(page.sdr.xo_mode.findData("tx"))
    page.sdr.xo_expected.setValue(1298.5)
    page.sdr.xo_measured.setValue(1298.475200)                    # the nano's carrier, 24.8 kHz low
    assert "-19.1" in page.sdr.xo_ppm.text()
    page.sdr.save_xo()
    assert tools.calls[-1][:3] == ("save", "serial:104473dc59", -19.1)
    assert "-19.1" in page.sdr.xo_now.text()
    page.sdr.forget_xo()
    assert tools.calls[-1] == ("forget", "serial:104473dc59") and "None saved" in page.sdr.xo_now.text()


def test_the_pluto_panel_saves_into_settings_and_the_password_into_secrets(radio_page):
    wiz, page, _ = radio_page
    page.sdr.pluto_uri.setText("ip:192.168.3.1")
    page.sdr.pluto_password.setText("letmein")
    page.sdr.pluto_tx.setCurrentIndex(page.sdr.pluto_tx.findData(-20.0))
    page.sdr.pluto_filter.setChecked(True)
    page.validatePage()
    assert wiz.s.radio.kind == "pluto" and wiz.s.sdr_radio.pluto_uri == "ip:192.168.3.1"
    assert wiz.s.sdr_radio.pluto_tx_gain_db == -20.0 and wiz.s.sdr_radio.filter_confirmed
    assert wiz.secrets["pluto_password"] == "letmein"


# -- the transmit gain dropdown and the panel's layout (0.2.2) ---------------------------------

def test_a_saved_gain_opens_on_the_step_at_or_below_it_and_never_above_the_cap():
    from shack_packet_app.ui.sdrpages import TX_GAIN_STEPS, tx_gain_step
    assert TX_GAIN_STEPS[0] == 0.0 and TX_GAIN_STEPS[-1] == -60.0 and len(TX_GAIN_STEPS) == 31
    assert tx_gain_step(-9.5) == -10.0 and tx_gain_step(-10.0) == -10.0 and tx_gain_step(-11.0) == -12.0
    assert tx_gain_step(0.4) == 0.0 and tx_gain_step(-0.25) == -2.0
    assert tx_gain_step(-10.0, cap_db=-20.0) == -20.0 and tx_gain_step(-10.0, cap_db=-9.5) == -10.0
    assert tx_gain_step(-30.0, cap_db=-20.0) == -30.0
    assert tx_gain_step(-70.25) == -70.25, "below the lowest step: kept, never raised to -60"


def _gains(page):
    c = page.sdr.pluto_tx
    return [(c.itemData(i), c.itemText(i), c.model().item(i).isEnabled()) for i in range(c.count())]


def test_the_transmit_gain_is_a_dropdown_of_2_db_steps_starting_a_new_pluto_at_minus_30(radio_page):
    wiz, page, _ = radio_page
    gains = _gains(page)
    assert [g for g, _, _ in gains] == [float(-n) for n in range(0, 61, 2)]
    assert gains[15][1] == "\u221230 dB" and gains[0][1] == "0 dB" and all(on for _, _, on in gains)
    assert page.sdr.pluto_tx.currentData() == -30.0 and not page.sdr.pluto_tx.isEditable()
    assert SdrRadioSettings().pluto_tx_gain_db == -30.0
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_tx_gain_db == -30.0


def _reload(wiz, page, **sdr):
    for k, v in sdr.items():
        setattr(wiz.s.sdr_radio, k, v)
    page.sdr.load(wiz.s, wiz.secrets)


def test_a_0_2_1_gain_loads_on_its_step_and_the_old_default_only_moves_for_a_station_new_to_the_pluto(radio_page):
    wiz, page, _ = radio_page
    wiz.s.radio.kind = "pluto"
    _reload(wiz, page, pluto_tx_gain_db=-9.5)
    assert page.sdr.pluto_tx.currentData() == -10.0
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_tx_gain_db == -10.0
    _reload(wiz, page, pluto_tx_gain_db=-10.0)                       # a Pluto station's own choice: kept
    assert page.sdr.pluto_tx.currentData() == -10.0
    wiz.s.radio.kind = "simulator"                                   # 0.2.1's untouched default
    _reload(wiz, page, pluto_tx_gain_db=-10.0)
    assert page.sdr.pluto_tx.currentData() == -30.0
    _reload(wiz, page, pluto_tx_gain_db=-75.0)                       # below the list: kept as it is
    assert page.sdr.pluto_tx.currentData() == -75.0 and _gains(page)[-1][1] == "\u221275 dB"


def test_gains_above_the_bands_drive_cap_cannot_be_chosen_and_the_saved_gain_comes_down_to_it(radio_page):
    from shack_packet_app.sdrplan import band_of
    wiz, page, _ = radio_page
    wiz.s.radio.kind = "pluto"
    band = band_of(page.sdr._channel_hz())
    wiz.s.fast.drive_caps = {band: -21.0}
    _reload(wiz, page, pluto_tx_gain_db=-10.0)
    assert page.sdr.pluto_tx.currentData() == -22.0
    enabled = {g: on for g, _, on in _gains(page)}
    assert not enabled[-20.0] and not enabled[0.0] and enabled[-22.0] and enabled[-60.0]
    assert not page.sdr.tx_cap_note.isHidden() and band in page.sdr.tx_cap_note.text()
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_tx_gain_db == -22.0
    # A cap set in Fast modes and amplifier applies to the dropdown at once.
    from shack_packet_app.settings import FastRadioSettings

    class Panel:
        @staticmethod
        def result():
            return FastRadioSettings(drive_caps={band: -40.0})

    class Accepting:
        def __init__(self, *a, **k):
            self.panel = Panel()

        def exec(self):
            return 1
    import shack_packet_app.ui.fastpanel as fastpanel
    real, fastpanel.FastDialog = fastpanel.FastDialog, Accepting
    try:
        page.sdr.edit_fast()
    finally:
        fastpanel.FastDialog = real
    assert page.sdr.pluto_tx.currentData() == -40.0 and not {g: on for g, _, on in _gains(page)}[-38.0]


def test_the_pluto_panel_lays_out_without_overlaps_at_the_setup_checks_size(radio_page, qapp):
    from PyQt6.QtCore import QRect
    wiz, page, _ = radio_page
    wiz.resize(720, 560)
    for _ in range(5):
        qapp.processEvents()
    sdr = page.sdr
    box = sdr.xo_save.parentWidget()
    from PyQt6.QtWidgets import QLabel
    notes = [w for w in box.findChildren(QLabel) if w.wordWrap()]
    column = [sdr.xo_now, sdr.xo_mode, sdr.xo_expected, sdr.xo_measured, sdr.xo_ppm, sdr.xo_save, notes[-1]]
    rects = [QRect(w.mapTo(box, w.rect().topLeft()), w.size()) for w in column]
    for (a, wa), (b, wb) in zip(zip(rects, column), zip(rects[1:], column[1:])):
        assert a.bottom() < b.top(), (wa, wb, a, b)
    for n in notes:
        assert n.height() >= n.heightForWidth(n.width()), n.text()
    assert not sdr.xo_save.geometry().intersects(sdr.xo_forget.geometry())
    assert wiz.height() <= 600, "the panel scrolls rather than stretching the setup check"


def test_the_crystal_frequencies_start_blank_and_a_check_fills_in_the_channel(radio_page, qapp):
    wiz, page, _ = radio_page
    sdr = page.sdr
    assert sdr.xo_expected.value() == 0.0 and sdr.xo_expected.text().strip() == ""
    assert sdr.xo_measured.text().strip() == "" and sdr.xo_ppm.text() == ""
    sdr.tools = FakeTools(_ident(chip="ad9361", tx_range=(46.9e6, 6e9), rx_range=(46.9e6, 6e9)), reach=True, enable="")
    sdr.check()
    assert wait_for(lambda: sdr.checked is not None, 3, app=qapp)
    assert sdr.xo_expected.value() == pytest.approx(sdr._channel_hz() / 1e6)
    assert sdr.xo_measured.value() == 0.0 and sdr.xo_ppm.text() == ""
    sdr.xo_expected.setValue(1298.5)                                  # typed: a second check keeps it
    sdr.check()
    assert wait_for(lambda: sdr.check_btn.isEnabled(), 3, app=qapp)
    assert sdr.xo_expected.value() == 1298.5


def test_the_radio_page_lists_the_sdr_warnings(radio_page):
    from shack_packet_app.ui import radiomenu
    pluto = " ".join(s.text for s in radiomenu.steps("pluto"))
    assert "band-pass filter" in pluto and "dummy load" in pluto and "guard" in pluto
    hackrf = radiomenu.steps("hackrf")
    assert any("weak carrier" in s.text and not s.checked for s in hackrf)
    assert "experimental" in hackrf[0].text and "not been tested on the air" in hackrf[0].text
    assert "SDRplay's own API" in radiomenu.steps("sdr-rx", source="sdrplay")[0].text
    assert "Zadig" in radiomenu.steps("sdr-rx", source="rtlsdr")[0].text


def test_transmit_cannot_be_set_up_for_an_sdr_without_the_filter_or_for_a_receive_only_one(radio_page, qapp):
    wiz, page, win = radio_page
    t = wiz.transmit
    wiz.s.radio.kind = "pluto"
    wiz.s.sdr_radio.filter_confirmed = False
    t.initializePage()
    assert not t.allow.isEnabled() and "band-pass filter" in t.why.text()
    wiz.s.sdr_radio.filter_confirmed = True
    t.initializePage()
    assert t.allow.isEnabled() and "harmonics" in t.why.text()
    wiz.s.radio.kind = "sdr-rx"
    t.initializePage()
    assert not t.allow.isEnabled() and not t.allow.isChecked()
    t.cleanupPage()


def test_the_listen_page_says_whether_the_plutos_guard_is_running(radio_page, qapp):
    wiz, page, win = radio_page
    page.validatePage()                               # Pluto chosen
    wiz.audio.initializePage()
    assert wait_for(lambda: win.ctl.link_state == "open" and isinstance(win.ctl.link, FakeSdrLink), 5, app=qapp)
    wiz.audio.tick()
    assert "running on the Pluto" in wiz.audio.state.text()
    win.ctl.link.guard_state, win.ctl.link.unguarded_why = "failed", "guard-no-ssh: wrong password"
    wiz.audio.tick()
    assert "NOT running" in wiz.audio.state.text() and "wrong password" in wiz.audio.state.text()
    wiz.audio.cleanupPage()
