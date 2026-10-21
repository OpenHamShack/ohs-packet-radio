"""The SDR monitor's channels: the operator's choice from the band plan, on
2 m or 70 cm, refused when it is wider than the SDR captures."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import sdrplan
from shack_packet_app.settings import SdrSettings


def gb():
    return station_settings().station.profile()


def ids(chans):
    return [c.id for c in chans]


def test_a_uk_station_has_2m_and_70cm_packet_channels_from_the_band_plan():
    two = sdrplan.candidates(gb(), "2m")
    assert [(p.id, p.hz) for p in two] == [("aprs", 144_800_000), ("node-144925", 144_925_000),
                                          ("node-1449375", 144_937_500), ("node-144950", 144_950_000),
                                          ("iss", 145_825_000)]
    seventy = sdrplan.candidates(gb(), "70cm")
    # RSGB 2026 p.22: APRS 433.800 where 144.800 cannot be used, and the two
    # Digital communications segments.
    assert [p.hz for p in seventy] == [433_800_000, 432_625_000, 432_650_000, 432_675_000,
                                       433_625_000, 433_650_000, 433_675_000]


def test_every_preset_by_default_and_only_the_ticked_ones_when_chosen():
    s = SdrSettings()
    assert ids(sdrplan.plan(s, gb())) == ["aprs", "node-144925", "node-1449375", "node-144950", "iss"]
    s.channels = ["aprs", "iss"]
    s.extra_hz = [145_300_000, 433_500_000]          # the 70 cm one is not on this band: left out
    plan = sdrplan.plan(s, gb())
    assert [(c.id, c.hz, c.kind) for c in plan] == [("aprs", 144.8e6, "aprs"), ("iss", 145.825e6, "satellite"),
                                                    ("hz-145300000", 145.3e6, "other")]


def test_70cm_fits_a_2_4_ms_capture_until_a_far_channel_is_added():
    s = SdrSettings(band="70cm")
    plan = sdrplan.plan(s, gb())
    assert len(plan) == 7 and plan.centre_for(s.rate) is not None
    s.extra_hz = [438_025_000]                        # IARU digital communications, 4.4 MHz away
    with pytest.raises(sdrplan.PlanError) as e:
        sdrplan.plan(s, gb())
    assert e.value.reason == "plan-too-wide"
    f = sdrplan.fit(sdrplan.channels(s, gb()), s.rate)
    assert not f.ok and f.span_hz > f.usable_hz == pytest.approx(1.92e6)


def test_a_narrower_capture_refuses_what_a_wider_one_takes():
    s = SdrSettings(rate=1_024_000)                   # an RTL-SDR at 1.024 MS/s: 819 kHz usable
    with pytest.raises(sdrplan.PlanError):
        sdrplan.plan(s, gb())
    s.channels = ["aprs", "node-144925", "node-1449375", "node-144950"]
    assert len(sdrplan.plan(s, gb())) == 4


def test_nothing_chosen_is_refused_and_a_station_with_no_presets_keeps_the_old_default():
    s = SdrSettings(channels=["-"])
    with pytest.raises(sdrplan.PlanError) as e:
        sdrplan.plan(s, gb())
    assert e.value.reason == "no-channels"
    # No callsign yet: the dataset cannot tell the country, and the only
    # preset valid everywhere is the ISS. The monitor keeps its old 2 m plan.
    from shack_packet import sdr
    assert ids(sdrplan.plan(SdrSettings(), {})) == ids(sdr.default_plan())


def test_the_controller_opens_nothing_for_a_plan_too_wide():
    from shack_packet_app.controller import Controller
    s = station_settings(sdr__source="synthetic", sdr__band="70cm", sdr__extra_hz=[438_025_000])
    opened = []
    ctl = Controller(s, sdr_factory=lambda *a: opened.append(a))
    ctl.start_sdr()
    assert ctl.sdr.error == "plan-too-wide" and not ctl.sdr.running and opened == []


def test_settings_chooses_the_channels_and_refuses_ok_when_they_do_not_fit(qapp):
    from PyQt6.QtCore import Qt
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    dlg = SettingsDialog(station_settings(), None, "sdr")
    assert dlg.ok_button.isEnabled() and dlg.sdr_chans.count() == 5
    assert "5 channels spread over" in dlg.sdr_fit.text()
    dlg.sdr_band.setCurrentIndex(dlg.sdr_band.findData("70cm"))
    assert dlg.sdr_chans.count() == 7 and dlg.ok_button.isEnabled()
    dlg.sdr_chans.item(0).setCheckState(Qt.CheckState.Unchecked)        # not APRS 433.800
    dlg.sdr_extra.setText("438.025")
    assert not dlg.ok_button.isEnabled() and "choose fewer" in dlg.sdr_fit.text()
    dlg.sdr_extra.setText("432.700")
    assert dlg.ok_button.isEnabled()
    s = dlg.result_settings()
    dlg.reject()
    assert s.sdr.band == "70cm" and "aprs-70cm" not in s.sdr.channels and len(s.sdr.channels) == 6
    assert s.sdr.extra_hz == [432_700_000]


def test_the_sdr_page_watches_the_chosen_70cm_channels(qapp):
    from shack_packet_app.controller import Controller
    from shack_packet_app.ui.window import PAGES, MainWindow
    s = station_settings(sdr__source="synthetic", sdr__band="70cm")
    win = MainWindow(s, {}, controller=Controller(s), persist=False)
    win.show()
    try:
        win.start(first_run_check=False)
        win.nav.setCurrentRow(PAGES.index("sdr"))
        win.ctl.start_sdr()
        page = win.pages["sdr"]

        def labels():
            win.tick()
            return [page.channels.item(r, 0).text() for r in range(page.channels.rowCount())]
        assert wait_for(lambda: len(labels()) == 7, 15, app=qapp)
        assert labels()[0] == "APRS 433.800" and "432.625" in labels()
    finally:
        win.close()
