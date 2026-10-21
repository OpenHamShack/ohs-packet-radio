"""The radio's controls always to hand (radioctl.py, ui/controlstrip.py):
a CAT radio's power, gain and squelch read and written through the link
(the library puts them back at close: ohs-libs test_radio_controls), a
Pluto's gains under the drive cap, the meters and the SWR-jump warning,
every change in a running link test's report, the strip on the window,
and the pad's dials."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import station_settings, wait_for
from shack_packet_app import padactions, radioctl
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.window import MainWindow


class CatLink:
    """What radioctl asks of an IcomLink, with the radio's levels 0-255."""

    def __init__(self):
        self.levels = {"rf_power": 128, "rf_gain": 255, "squelch": 0}
        self.writes = []
        self.meters = [{"s_db": -30.0}]

    def control_keys(self):
        return list(self.levels)

    def read_control(self, key):
        return self.levels[key]

    def set_control(self, key, raw):
        from shack_rig.datasetup import Line
        self.writes.append((key, raw))
        self.levels[key] = raw
        return Line(key, "set")

    def read_meters(self):
        return self.meters.pop(0) if len(self.meters) > 1 else self.meters[0]


class PlutoStub:
    def __init__(self):
        self.tx_gain_db, self.rx_gain = -30.0, None
        self.calls = []

    def set_tx_gain(self, db):
        self.calls.append(("tx", db))
        self.tx_gain_db = db

    def set_rx_gain(self, db):
        self.calls.append(("rx", db))
        self.rx_gain = db


class Ctl:
    def __init__(self, link, settings=None):
        self.link = link
        self.settings = settings or station_settings()
        self.notes, self.events = [], []
        ctl = self

        class LT:
            def note(self, text):
                ctl.notes.append(text)
        self.linktest = LT()

    def _emit(self, kind, reason, detail=None):
        self.events.append((kind, reason, detail))


def test_a_cat_radios_controls_are_read_as_percent_and_written_at_once():
    ctl = Ctl(CatLink())
    rc = radioctl.RadioControls(ctl)
    try:
        assert rc.kind() == "cat"
        rc.tick()
        assert rc.wait_idle()
        assert rc.values == {"rf_power": 50, "rf_gain": 100, "squelch": 0}
        rc.set("rf_power", 25)
        assert rc.wait_idle()
        assert ctl.link.writes == [("rf_power", 64)] and rc.values["rf_power"] == 25
        # Logged into the run under way.
        assert ctl.notes == ["RF power 50 % -> 25 %"]
        rc.nudge("squelch", 3)
        assert rc.wait_idle() and ctl.link.writes[-1] == ("squelch", 15)       # 6 %
    finally:
        rc.close()


def test_the_meters_and_a_jump_in_swr_between_transmissions():
    link = CatLink()
    link.meters = [{"po_pct": 50.0, "swr": 1.3, "alc_pct": 20.0}, {"s_db": -40.0},
                   {"po_pct": 50.0, "swr": 2.4, "alc_pct": 20.0}, {"s_db": -40.0}]
    ctl = Ctl(link)
    rc = radioctl.RadioControls(ctl)
    try:
        for _ in range(4):
            rc._last_meter = 0.0
            rc.tick()
            assert rc.wait_idle()
        assert "SWR 2.4" in rc.warning and "up from 1.3" in rc.warning
        assert ctl.events and ctl.events[0][:2] == ("radio", "swr-jump")
        assert any("SWR 2.4" in n for n in ctl.notes)
    finally:
        rc.close()


def test_a_plutos_gains_change_live_and_are_kept_in_settings():
    s = station_settings(radio__kind="pluto")
    saved = []
    ctl = Ctl(PlutoStub(), s)
    rc = radioctl.RadioControls(ctl, save=lambda: saved.append(1))
    try:
        assert rc.kind() == "pluto"
        rc.tick()
        assert rc.wait_idle() and rc.values == {"tx_gain": -30.0, "rx_gain": None}
        rc.set("tx_gain", -24.0)
        rc.set("rx_gain", 40.0)
        assert rc.wait_idle()
        assert ctl.link.calls == [("tx", -24.0), ("rx", 40.0)]
        assert s.sdr_radio.pluto_tx_gain_db == -24.0 and s.sdr_radio.pluto_rx_gain_db == 40.0 and saved
        assert ctl.notes == ["TX gain -30 dB -> -24 dB", "RX gain AGC -> 40 dB"]
        # A dial's detent: the next 2 dB step, never above the drive cap.
        rc.values["tx_gain"] = rc.tx_cap() if rc.tx_cap() is not None else -2.0
        before = len(ctl.link.calls)
        rc.nudge("tx_gain", 1)
        assert rc.wait_idle()
        cap = rc.tx_cap()
        assert ctl.link.calls[before][1] <= (cap if cap is not None else 0.0)
    finally:
        rc.close()


def test_the_pad_dials_turn_power_and_squelch_and_never_key():
    calls = []

    class Bridge:
        def __getattr__(self, name):
            return lambda *a: calls.append((name,) + a)
    pa = padactions.PadActions(Bridge())
    pa.perform("power", "D1", 1)
    pa.perform("squelch", "D2", -2)
    pa.perform("power", "D1", 0)                      # no turn, no change
    assert calls == [("adjust", "power", 1), ("adjust", "squelch", -2)]
    decl, layout = padactions.declaration()
    assert layout["ajazz-akp03e"]["D1"]["turn"] == "power" and layout["ajazz-akp03e"]["D2"]["turn"] == "squelch"
    assert not any(a.get("tx") for a in decl["actions"])


@pytest.fixture
def win(qapp, monkeypatch):
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


def test_the_strip_shows_what_the_radio_has_and_changes_the_channel(win, qapp):
    strip = win.control_strip
    win.show()
    win.tick()
    # The simulator has nothing to turn: channel and mode only.
    assert not strip.cat["rf_power"].isVisible() and not strip.tx_gain.isVisible()
    assert not strip.mode.isEnabled()
    old = win.settings.channel.frequency_hz
    other = next(i for i in range(strip.preset.count()) if strip.preset.itemData(i)[1] != old)
    strip.preset.setCurrentIndex(other)
    strip._preset_chosen(other)
    assert win.settings.channel.frequency_hz == strip.preset.itemData(other)[1] != old
    # A CAT radio: its controls appear, filled from the radio.
    link = CatLink()
    win.ctl.link = link
    win.tick()
    assert wait_for(lambda: (win.tick() or True) and strip.cat_labels["rf_power"][1].text() == "50 %", 5, app=qapp)
    assert strip.cat["rf_power"].isVisible() and strip.cat["rf_power"].value() == 50
    assert "Signal S4" in strip.meter.text()
    strip.cat["squelch"].setValue(20)
    strip.cat["squelch"].commit()
    assert strip.controls.wait_idle() and link.writes[-1] == ("squelch", 51)
    strip.dial("power", -5)
    assert strip.controls.wait_idle() and link.writes[-1] == ("rf_power", 102)      # 50 - 10 = 40 %
    win.ctl.link = None
