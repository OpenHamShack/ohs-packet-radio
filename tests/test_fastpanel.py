"""Settings › Fast modes and the drive-level helper (0.2.1): the panel's
band-plan verdict as the mode and frequency change, the PTT outputs a
Pluto's firmware allows, a drive cap kept per band, the setup check
remembering which firmware it found, and the drive-level helper stepping
the gain only through the station's own arming. No radio is keyed: the
helper runs on a stand-in station core, and the real Controller's burst is
only shown refusing."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import fastmodes
from shack_packet_app.settings import FastRadioSettings
from test_sdr_radio import FakeTools, _ident, radio_page  # noqa: F401 - the fixture


def _panel(qapp, fast=None, family="adi", hz=1_298_500_000, **kw):
    from shack_packet_app.ui.fastpanel import FastPanel
    box = {"hz": hz}
    p = FastPanel(fast or FastRadioSettings(), family=family, frequency=lambda: box["hz"],
                  set_frequency=lambda f: box.update(hz=f), **kw)
    return p, box


def test_a_wide_mode_out_of_plan_is_said_and_the_suggested_channel_fixes_it(qapp):
    p, box = _panel(qapp)
    p.mode.setCurrentIndex(p.mode.findData("gfsk76800"))
    assert p.plan_text.objectName() == "bad" and "wider than the band plan allows" in p.plan_text.text()
    assert not p.use_suggested.isHidden()
    p.use_suggested.click()
    assert box["hz"] == fastmodes.SUGGESTED_HZ
    assert p.plan_text.objectName() == "good" and "high speed data" in p.plan_text.text()
    assert "LO leakage is at 1299.425" in p.plan_text.text()      # 83 kHz: the LO 100 kHz down
    assert "image at 1299.325" in p.plan_text.text()
    assert p.use_suggested.isHidden()


def test_a_wide_mode_low_in_the_segment_warns_that_its_image_falls_outside(qapp):
    p, _ = _panel(qapp, FastRadioSettings(mode="4fsk76800"), hz=1_299_225_000)
    assert p.plan_text.objectName() == "warn"
    assert "1299.025" in p.plan_text.text() and "1298.825" in p.plan_text.text()
    assert "IQ image falls outside" in p.plan_text.text()


def test_adaptation_judges_its_widest_mode_and_greys_out_the_single_mode(qapp):
    p, _ = _panel(qapp, hz=fastmodes.SUGGESTED_HZ)
    p.adapt.setChecked(True)
    assert not p.mode.isEnabled() and p.top.isEnabled() and p.floor.isEnabled()
    p.top.setCurrentIndex(p.top.findData("gfsk153600"))
    assert p.plan_text.objectName() == "bad"                       # 166 kHz in a 150 kHz channel
    fs = p.result()
    assert fs.adapt and fs.top_mode == "gfsk153600" and fs.floor_mode == "g3ruh9600"


@pytest.mark.parametrize("family, offered", [("adi", ["none", "gpo0", "gpo1", "gpo2", "gpo3"]),
                                             ("tezuka", ["none", "firmware"]),
                                             ("plutodvb2", ["none", "firmware"]), ("", ["none"])])
def test_the_ptt_outputs_offered_follow_the_checked_firmware(qapp, family, offered):
    p, _ = _panel(qapp, family=family)
    assert [p.ptt.itemData(i) for i in range(p.ptt.count())] == offered
    if not family:
        assert "Check this Pluto" in p.ptt_note.text()


def test_a_saved_output_this_firmware_is_not_offered_is_kept_and_said(qapp):
    p, _ = _panel(qapp, FastRadioSettings(ptt_output="gpo2"), family="tezuka")
    assert p.ptt.currentData() == "gpo2" and "drives its GPOs itself" in p.ptt_note.text()
    assert p.result().ptt_output == "gpo2"


def test_the_drive_cap_is_kept_per_band(qapp):
    p, box = _panel(qapp, FastRadioSettings(drive_caps={"70cm": -4.0}), hz=fastmodes.SUGGESTED_HZ)
    assert "23cm" in p.cap_on.text() and not p.cap_on.isChecked()
    p.cap_on.setChecked(True)
    p.cap.setValue(-18.5)
    box["hz"] = 432_500_000
    p.refresh()                                                    # the channel moved to 70 cm
    assert "70cm" in p.cap_on.text() and p.cap.value() == -4.0
    assert p.result().drive_caps == {"23cm": -18.5, "70cm": -4.0}
    p.cap_on.setChecked(False)
    assert p.result().drive_caps == {"23cm": -18.5}


def test_the_amplifier_notices_are_on_the_panel(qapp):
    from PyQt6.QtWidgets import QLabel
    p, _ = _panel(qapp)
    bad = [lbl.text() for lbl in p.findChildren(QLabel) if lbl.objectName() == "bad"]
    assert any("band-pass filter AFTER the amplifier" in t for t in bad)
    assert any("only for transmit runs" in t and "2450 MHz" in t for t in bad)


def test_settings_has_a_fast_modes_tab_that_follows_the_channel_and_saves(qapp):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    s = station_settings(sdr_radio__pluto_family="tezuka")
    s.radio.kind = "pluto"
    d = SettingsDialog(s, None, "fast")
    assert d.tabs.currentWidget() is d.fast_panel
    assert d.fast_panel.find_drive.isHidden()                      # no window, no station to run it on
    d.fast_panel.mode.setCurrentIndex(d.fast_panel.mode.findData("4fsk76800"))
    assert d.fast_panel.plan_text.objectName() == "bad"            # 144.800
    d.fast_panel.use_suggested.click()
    assert d.preset.currentData()[0] == "custom" and round(d.freq.value(), 3) == 1299.525
    assert d.fast_panel.plan_text.objectName() == "good"
    d.fast_panel.ptt.setCurrentIndex(d.fast_panel.ptt.findData("firmware"))
    out = d.result_settings()
    assert out.fast.mode == "4fsk76800" and out.fast.ptt_output == "firmware"
    assert out.channel.frequency_hz == 1_299_525_000


def test_the_setup_check_remembers_the_firmware_it_found_and_offers_its_outputs(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    page.sdr.tools = FakeTools(_ident(fw_version="tezuka-v0.3.21", family="tezuka"), reach=True, enable="")
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_family == "tezuka"
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(0, lambda: page.sdr.fast_dialog.accept())
    page.sdr.edit_fast()
    panel = page.sdr.fast_dialog.panel
    assert [panel.ptt.itemData(i) for i in range(panel.ptt.count())] == ["none", "firmware"]
    # Another address is another Pluto, not yet checked.
    page.sdr.pluto_uri.setText("ip:192.168.3.1")
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_family == ""


# -- the drive-level helper ---------------------------------------------------------------------

class FakeStation:
    armed = False


class FakeCtl:
    """The station core as DriveSession uses it, recording everything."""

    def __init__(self, arm_refusal=("", "")):
        self.station = FakeStation()
        self.applied, self.bursts, self.arms = [], [], []
        self.refusal = arm_refusal

    def apply(self, s, secrets=None):
        self.applied.append(s)
        self.station.armed = False                              # a rebuild starts disarmed

    def arm(self, minutes):
        self.arms.append(minutes)
        if self.refusal[0]:
            return False, *self.refusal
        self.station.armed = True
        return True, "", ""

    def drive_burst(self, gain):
        self.bursts.append(gain)
        return ""


def _session(ctl, **kw):
    s = station_settings(channel__frequency_hz=fastmodes.SUGGESTED_HZ)
    s.radio.kind = "pluto"
    s.fast.drive_caps = {"70cm": -3.0}
    return fastmodes.DriveSession(ctl, s, **kw), s


def test_each_drive_step_rebuilds_the_station_with_gain_and_cap_at_the_step():
    ctl = FakeCtl()
    sess, s = _session(ctl, step_db=4.0)
    assert sess.steps[:3] == [-30.0, -26.0, -22.0] and sess.steps[-1] == -2.0
    assert sess.next_step() == -30.0
    step = ctl.applied[-1]
    assert step.sdr_radio.pluto_tx_gain_db == -30.0 and step.fast.drive_caps == {"70cm": -3.0, "23cm": -30.0}
    assert s.fast.drive_caps == {"70cm": -3.0}                       # the settings it was given are untouched
    assert sess.burst() == ("", "") and ctl.arms == [1] and ctl.bursts == [-30.0]
    assert sess.good() == -26.0 and not ctl.station.armed           # the next step starts disarmed
    sess.burst()
    assert ctl.arms == [1, 1] and ctl.bursts == [-30.0, -26.0]
    assert sess.finish() == -30.0                                    # -26 was sent, never called good
    assert ctl.applied[-1].sdr_radio.pluto_tx_gain_db == s.sdr_radio.pluto_tx_gain_db


def test_a_drive_burst_the_arming_refuses_is_not_sent():
    ctl = FakeCtl(arm_refusal=("fast-out-of-plan", "gfsk76800 at 1298.5000 MHz..."))
    sess, _ = _session(ctl)
    sess.next_step()
    assert sess.burst() == ("fast-out-of-plan", "gfsk76800 at 1298.5000 MHz...") and ctl.bursts == []
    assert sess.finish() is None


def test_the_drive_dialog_steps_and_saves_only_on_stop(qapp):
    from shack_packet_app.ui.fastpanel import DriveDialog
    ctl = FakeCtl()
    sess, _ = _session(ctl)
    d = DriveDialog(None, sess)
    assert "Step 1 of" in d.state.text() and not d.stop.isEnabled()
    d.send.click()
    assert ctl.bursts == [-30.0] and "Burst sent" in d.said.text()
    d.good.click()
    assert "Step 2 of" in d.state.text() and d.stop.isEnabled()
    d.stop.click()
    assert d.cap == -30.0
    d2 = DriveDialog(None, _session(FakeCtl())[0])
    d2.send.click()
    d2.good.click()
    d2.reject()
    assert d2.cap is None


def test_the_station_sends_no_drive_burst_unless_armed_on_a_pluto():
    from shack_packet_app.controller import Controller
    from test_ui import quiet_band
    c = Controller(station_settings(), band_factory=quiet_band)
    c.start()
    try:
        assert c.drive_burst(-30.0) == "not-armed"
        c.arm(1)
        assert c.station.armed and c.drive_burst(-30.0) == "no-radio"      # the simulator is not a Pluto
    finally:
        c.stop()


def test_settings_offers_the_pluto_transmit_gain_under_the_bands_cap(qapp):
    """0.2.3: the setup check's gain dropdown in Settings › Channel too,
    for a Pluto only; steps above the band's drive cap greyed, the cap
    followed when the frequency moves to another band, and what OK saves
    never above the cap."""
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    s = station_settings(sdr_radio__pluto_tx_gain_db=-20.0)
    s.radio.kind = "pluto"
    s.fast.drive_caps = {"23cm": -30.0}
    d = SettingsDialog(s, None, "channel")
    assert d.tx_gain.currentData() == -20.0 and not d.tx_gain.isHidden()
    assert all(d.tx_gain.model().item(i).isEnabled() for i in range(d.tx_gain.count()))   # 2 m: no cap
    d._set_frequency(1_299_525_000)
    assert d.tx_gain.currentData() == -30.0                        # clamped to the 23 cm cap
    enabled = {d.tx_gain.itemData(i): d.tx_gain.model().item(i).isEnabled() for i in range(d.tx_gain.count())}
    assert not enabled[-28.0] and enabled[-30.0] and enabled[-40.0]
    assert "23" in d.tx_gain_note.text()
    d.tx_gain.setCurrentIndex(d.tx_gain.findData(-40.0))
    assert d.result_settings().sdr_radio.pluto_tx_gain_db == -40.0
    # Not a Pluto: no gain row, and the saved gain is left alone.
    s2 = station_settings(sdr_radio__pluto_tx_gain_db=-12.0)
    d2 = SettingsDialog(s2, None, "channel")
    assert d2.tx_gain.parent() is None
    assert d2.result_settings().sdr_radio.pluto_tx_gain_db == -12.0


def test_1200_baud_afsk_is_said_to_occupy_its_real_bandwidth(qapp):
    """The Link test page told GM0OTB that 1200 baud AFSK "occupies 0 kHz"
    (19/10/2026): the library had no figure for it. It is 9.2 kHz through
    the Pluto (ohs-libs modem.py), inside a 12.5 kHz channel."""
    from shack_packet_app.ui.fastpanel import plan_verdict
    s = station_settings()
    s.fast.mode, s.fast.adapt = "afsk1200", False
    text, _name, check = plan_verdict(s.fast.for_frequency(144_800_000), 144_800_000)
    assert check is not None and 8_000 <= check.occupied_hz <= 10_000
    assert "occupies 9 kHz" in text.replace("\xa0", " ")


def test_the_window_is_best_for_the_mode_until_set_by_hand(qapp):
    p, _ = _panel(qapp, FastRadioSettings(mode="g3ruh9600"))
    assert p.window_auto.isChecked() and not p.window.isEnabled() and p.window.value() == 31
    p.mode.setCurrentIndex(p.mode.findData("g3ruh38400"))
    assert p.window.value() == 63
    assert p.result().window_auto
    p.window_auto.setChecked(False)
    p.window.setValue(20)
    fs = p.result()
    assert not fs.window_auto and fs.window == 20 and fs.window_for() == 20
