"""The IC-9700 page, offscreen: its spectrum from FakeIcom while it is on
screen and none once it is hidden, a click tuning the radio to a packet
channel, the power switch, the satellites tab with elements fetched by a
stand-in, and DD switched on and off as the station's radio."""
from __future__ import annotations

import socket

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from conftest import station_settings, wait_for
from test_controller import LoopIcom, icom  # noqa: F401 - the fixture
from test_sat import ISS
from test_ui import window  # noqa: F401 - the fixture
from shack_packet_app import rigfeatures
from shack_packet_app.sat import tle
from shack_packet_app.ui.window import PAGES


@pytest.fixture(autouse=True)
def no_ticker(monkeypatch):
    monkeypatch.setattr(rigfeatures.RigFeatures, "TICK_S", 3600.0)


def _open_page(win, qapp, tab):
    win.go_to("rig")
    page = win.pages["rig"]
    page.tabs.setCurrentWidget(getattr(page, tab))
    qapp.processEvents()
    return page


def test_the_page_draws_every_tab_on_the_simulator(window, qapp):
    win = window()
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    assert PAGES[-1] == "rig" and win.nav.item(PAGES.index("rig")).text() == "IC-9700"
    page = _open_page(win, qapp, "spectrum")
    for tab in (page.spectrum, page.power, page.sats, page.dd):
        page.tabs.setCurrentWidget(tab)
        qapp.processEvents()
        win.tick()
    assert "IC-9700" in page.spectrum.state.text()
    assert page.power.state.text() == "Off."
    assert "DD needs an IC-9700" in page.dd.state.text() and not page.dd.go.isEnabled()


def test_the_spectrum_runs_only_on_screen_and_a_click_tunes(window, icom, qapp, monkeypatch):
    from shack_rig.icom.rig import IcomRig
    monkeypatch.setattr(IcomRig, "SCOPE_RELEASE_S", 0.3)
    radio = icom.radio
    s = icom.settings()
    s.channel.preset, s.channel.frequency_hz = "custom", 144_800_000
    win = window(s, secrets={"icom_password": "secret"})
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 15, app=qapp)
    page = _open_page(win, qapp, "spectrum")
    tab = page.spectrum

    def ticked():
        win.tick()
        return tab.feed.rev >= 3
    assert wait_for(ticked, 5, app=qapp) and radio.scope_output
    assert "packet channels in view" in tab.state.text()
    assert [c.hz for c in tab.view.chans] == [144_800_000]
    assert radio.scope_half_span == 50_000, "the page's span, sent as the scope opened"
    # The fake's sweep has a peak that walks; a tall one right on the
    # channel reads busy.
    from shack_packet_app import scope
    row = tab.feed.rows()[-1]
    data = row.data.copy()
    data[len(data) // 2] = 150
    assert scope.busy(scope.Row(row.start, row.end, data), tab.view.chans, 12.0) == {144_800_000: True}
    # A click 2 kHz off the channel tunes to the channel itself.
    win.ctl.arm(5)
    x = tab.view.x_of(144_802_000)
    tab.view.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x, 100), QPointF(x, 100),
                                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                         Qt.KeyboardModifier.NoModifier))
    assert radio.freq == 144_800_000 and not win.ctl.station.armed
    x = tab.view.x_of(144_830_000)
    tab.view.clicked.emit(144_830_000.0)
    assert wait_for(lambda: radio.freq == 144_830_000, 2, app=qapp)
    assert win.settings.channel.frequency_hz == 144_830_000 and "Tuned to" in page.message.text()
    win.go_to("monitor")
    qapp.processEvents()
    assert not tab.feed.attached
    assert wait_for(lambda: not radio.scope_output, 3, app=qapp), "a hidden spectrum costs the radio nothing"


def test_the_power_switch_and_limits_are_the_settings(window, qapp):
    win = window()
    win.start(first_run_check=False)
    page = _open_page(win, qapp, "power")
    page.power.enabled.setChecked(True)
    page.power.cap.setValue(30)
    page.power.floor.setValue(60)
    p = win.settings.power
    assert p.enabled and p.cap_percent == 30 and p.floor_percent == 30, "the floor is never above the cap"
    win.ctl.rigx.tick()
    win.tick()
    assert "IC-9700 or IC-705" in page.power.state.text()


def test_the_satellites_tab_fetches_elements_and_lists_passes(window, qapp, tmp_path):
    s = station_settings(locator="IO85WW")
    win = window(s)
    store = tle.TleStore(tmp_path / "s.tle", fetch=lambda url: ("\n".join(ISS) + "\n").encode())
    win.ctl.rigx.sat = rigfeatures.SatTracker(win.ctl, store=store)
    win.start(first_run_check=False)
    page = _open_page(win, qapp, "sats")
    tab = page.sats
    win.tick()
    assert "No orbital elements" in tab.elements.text()
    tab.update_btn.click()
    assert wait_for(lambda: tab._updating is not None and not tab._updating.is_alive(), 5, app=qapp)
    win.tick()
    assert "1 satellites' elements updated" in tab.elements.text()
    # Elements this old (the test TLE's epoch is fixed) still predict; the
    # table fills either way, from the cache, offline.
    tab.track.setChecked(True)
    assert win.settings.sat.tracking == 25544
    tab.setup.click()
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    assert (win.settings.channel.frequency_hz, win.settings.aprs.path) == (145_825_000, "ARISS")
    win.tick()
    assert tab.back.isEnabled()
    tab.back.click()
    assert win.settings.channel.frequency_hz != 145_825_000 and win.settings.sat.saved == {}


def _port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    n = s.getsockname()[1]
    s.close()
    return n


def test_dd_is_switched_on_and_back_off_from_its_tab(window, icom, qapp):
    radio = icom.radio
    s = icom.settings()
    win = window(s, secrets={"icom_password": "secret"})
    win.start(first_run_check=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 15, app=qapp)
    page = _open_page(win, qapp, "dd")
    tab = page.dd
    tab.local.setText(f"127.0.0.1:{_port()}")
    tab.peer.setText(f"127.0.0.1:{_port()}")
    win.tick()
    assert tab.go.text() == "Use DD" and tab.go.isEnabled()
    tab.go.click()
    assert win.settings.radio.kind == "ic-9700-dd" and win.settings.dd.previous_kind == "ic-9700"
    assert wait_for(lambda: win.ctl.link_state == "open" and radio.mode == 0x22, 15, app=qapp)
    win.tick()
    assert "TRANSMIT" in tab.steps.text() and tab.go.text() == "Stop DD"
    tab.go.click()
    assert win.settings.radio.kind == "ic-9700"
    assert wait_for(lambda: win.ctl.link_state == "open" and radio.mode == 0x05, 15, app=qapp), "FM-D for packet"
