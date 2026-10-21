"""Workstream F's station features on the controller: adaptive power's
decisions and its radio, the satellite pass gate (arming, the band plan's
satellite segment, the disarm at loss of signal) and Doppler on FakeIcom,
and 23 cm DD as the station's radio. Ticks are driven by the test."""
from __future__ import annotations

import socket

import pytest

from conftest import station_settings, wait_for
from test_controller import LoopIcom, icom  # noqa: F401 - the fixture
from test_sat import EDINBURGH, ISS
from shack_packet_app import adaptpower, rigfeatures
from shack_packet_app.adaptpower import Evidence, PowerAdapter
from shack_packet_app.controller import Controller
from shack_packet_app.sat import tle, track
from shack_packet_app.simband import SimBand


@pytest.fixture(autouse=True)
def no_ticker(monkeypatch):
    """The test ticks; the features' own thread never does."""
    monkeypatch.setattr(rigfeatures.RigFeatures, "TICK_S", 3600.0)


GOOD = Evidence(sent=20, retried=0)
BAD = Evidence(sent=20, retried=6)
VERY_BAD = Evidence(sent=20, retried=12)


def test_the_adapter_walks_down_while_it_works_and_back_up_when_it_does_not():
    a = PowerAdapter(cap=50, floor=5, step=10)
    assert a.level == 50
    assert [a.window(GOOD) for _ in range(3)] == [None, None, (40, "good")]
    assert a.window(Evidence(sent=1)) is None, "too little traffic to judge"
    for _ in range(9):
        a.window(GOOD)
    assert a.level == 10
    assert a.window(BAD) == (20, "bad")
    # Down then straight back up: the next step down waits twice as long.
    assert [a.window(GOOD) for _ in range(6)][-1] == (10, "good")
    assert a.window(VERY_BAD) == (30, "very-bad") and a.good_needed == 12
    for _ in range(40):
        a.window(VERY_BAD)
    assert a.level == 50, "never above the cap"
    b = PowerAdapter(cap=20, floor=5, step=10, start=90)
    assert b.level == 20
    for _ in range(30):
        b.window(GOOD)
    assert b.level == b.floor == 5 and b.window(GOOD) is None, "never below the floor"


def test_snr_reports_count_as_evidence():
    assert Evidence(snr_db=3.0).verdict() == "bad"
    assert Evidence(snr_db=20.0).verdict() == "good"
    assert Evidence(sent=20, retried=0, snr_db=9.0).verdict() == "mixed"
    assert Evidence(sent=2).verdict() == "quiet"
    assert adaptpower.band_of(1_299_525_000) == "23cm" and adaptpower.level_of(50) == 128


class Conn:
    def __init__(self):
        self.stats = {"sent_I": 0, "retransmitted": 0, "t1_expired": 0}


def test_adaptive_power_on_an_ic9700_logs_every_change_and_puts_it_back(icom):
    radio = icom.radio
    s = icom.settings()
    s.radio.minimum_power_for_test = False
    s.power.enabled, s.power.cap_percent, s.power.step_percent = True, 50, 10
    c = Controller(s, secrets={"icom_password": "secret"})
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 15)
        found = radio.rf_power_level
        pc = c.rigx.power
        pc.window_s = 0.0
        conn = Conn()
        pc._connections = lambda: [conn]
        c.rigx.tick()
        assert pc.state == "running" and radio.rf_power_level == 128, "the cap, 50%"
        for _ in range(3):
            conn.stats["sent_I"] += 20
            c.rigx.tick()
        assert radio.rf_power_level == adaptpower.level_of(40)
        assert s.power.per_band["2m"] == 40
        conn.stats["sent_I"] += 20
        conn.stats["retransmitted"] += 6
        c.rigx.tick()
        assert radio.rf_power_level == adaptpower.level_of(50)
        assert [h["why"] for h in pc.history] == ["start", "good", "bad"]
        # Somebody turns the knob: adaptation stops at the next window and
        # leaves it alone.
        radio.rf_power_level = 200
        conn.stats["sent_I"] += 20
        c.rigx.tick()
        assert pc.state == "manual" and radio.rf_power_level == 200
        radio.rf_power_level = adaptpower.level_of(50)
    finally:
        c.stop()
    assert radio.rf_power_level == adaptpower.level_of(50), "the operator's level, not put back over"
    del found


def test_adaptive_power_stays_off_by_default_and_with_the_test_step(icom):
    s = icom.settings()
    c = Controller(s, secrets={"icom_password": "secret"})
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 15)
        c.rigx.tick()
        assert c.rigx.power.state == "off" and icom.radio.rf_power_writes == []
        s.power.enabled = True
        c.rigx.tick()
        assert c.rigx.power.state == "test-step", "the minimum-power step holds the radio; leave it"
    finally:
        c.stop()


# -- satellites --------------------------------------------------------------------

def _store(tmp_path):
    store = tle.TleStore(tmp_path / "sat.tle", fetch=lambda url: ("\n".join(ISS) + "\n").encode())
    store.update()
    return store


def _iss_station(**kw):
    s = station_settings(locator="IO85WW", **kw)
    s.sat.tracking = 25544
    s.channel.preset, s.channel.frequency_hz = "custom", 145_825_000
    return s


def test_the_iss_uplink_arms_only_during_a_pass_and_disarms_when_it_sets(tmp_path):
    sat = track.Satellite(*ISS)
    s = _iss_station()
    lat, lon = __import__("shack_packet_app").grid.to_latlon(s.station.locator)
    p = sat.passes(sat.epoch_unix, 24, lat, lon, min_el_deg=20)[0]
    now = [p.aos - 300]
    c = Controller(s, band_factory=lambda: SimBand(lat, lon))
    c.rigx.sat = rigfeatures.SatTracker(c, store=_store(tmp_path), clock=lambda: now[0])
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 5)
        ok, why, detail = c.arm(5)
        assert not ok and why == "sat-below-horizon" and detail == "ISS"
        now[0] = p.tca
        ok, why, _ = c.arm(5)
        assert ok, why
        c.rigx.tick()
        assert c.station.armed
        now[0] = p.los + 5
        c.rigx.tick()
        assert not c.station.armed
        assert any(e.kind == "tx" and e.reason == "sat-los" for e in list(c.events.queue))
        # Off the uplink, tracking is no gate, and the satellite segment is
        # the band plan's to refuse again.
        c.settings.channel.frequency_hz = 145_800_000
        assert c.arm(5)[0]
        c.disarm()
        c.settings.channel.frequency_hz = 145_900_000
        assert c.arm(5)[1] == "band-plan"
    finally:
        c.stop()


def test_doppler_runs_the_pass_on_the_radio_and_puts_it_back(icom, tmp_path):
    radio = icom.radio
    sat = track.Satellite(*ISS)
    s = icom.settings()
    s.station.locator = "IO85WW"
    s.sat.tracking = 25544
    s.channel.preset, s.channel.frequency_hz = "custom", 145_825_000
    lat, lon = __import__("shack_packet_app").grid.to_latlon(s.station.locator)
    p = sat.passes(sat.epoch_unix, 24, lat, lon, min_el_deg=20)[0]
    now = [p.aos + 30]
    c = Controller(s, secrets={"icom_password": "secret"})
    c.rigx.sat = rigfeatures.SatTracker(c, store=_store(tmp_path), clock=lambda: now[0])
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 15)
        c.rigx.tick()
        sess = c.rigx.sat.session
        assert sess.active and sess.doppler and radio.split
        assert radio.freq_unselected < 145_825_000 < radio.freq, "approaching: send low, hear high"
        now[0] = p.los - 30
        c.rigx.tick()
        assert wait_for(lambda: radio.freq < 145_825_000 < radio.freq_unselected, 2)
        now[0] = p.los + 60
        c.rigx.tick()
        assert not sess.active and not radio.split and radio.freq_unselected == 145_825_000
        assert radio.freq == 145_825_000, "the packet channel, as the link set it"
    finally:
        c.stop()
    assert radio.freq == 144_174_000 and not radio.split


def test_the_iss_set_up_is_saved_and_put_back():
    from shack_packet_app.settings import Settings
    s = Settings()
    s.channel.preset, s.channel.frequency_hz, s.aprs.path = "aprs", 144_800_000, "WIDE1-1,WIDE2-1"
    t = rigfeatures.SatTracker(None)
    iss = t.station_for(s, tle.BIRDS[25544])
    assert (iss.channel.frequency_hz, iss.aprs.path, iss.sat.tracking) == (145_825_000, "ARISS", 25544)
    assert t.station_for(iss, tle.BIRDS[25544]).sat.saved == iss.sat.saved, "a second set-up keeps the first"
    back = t.station_back(iss)
    assert (back.channel.preset, back.channel.frequency_hz, back.aprs.path) == ("aprs", 144_800_000,
                                                                               "WIDE1-1,WIDE2-1")
    assert back.sat.saved == {}


# -- 23 cm DD ----------------------------------------------------------------------

def _port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    n = s.getsockname()[1]
    s.close()
    return n


def test_dd_as_the_stations_radio_switches_the_9700_and_back(icom, monkeypatch):
    listened = []
    real_listen = socket.socket.listen
    monkeypatch.setattr(socket.socket, "listen", lambda self, *a: listened.append(self) or real_listen(self, *a))
    radio = icom.radio
    s = icom.settings()
    s.radio.kind = "ic-9700-dd"
    s.dd.local, s.dd.peer = f"127.0.0.1:{_port()}", f"127.0.0.1:{_port()}"
    s.dd.channel_hz = 1_299_225_000
    c = Controller(s, secrets={"icom_password": "secret"})
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 15), (c.link_error, c.link_error_detail)
        assert (radio.freq, radio.mode) == (1_299_225_000, 0x22)
        assert c.arm_refusal() == ("not-set-up", "")
    finally:
        c.stop()
    assert radio.freq == 144_174_000 and radio.mode != 0x22
    assert listened == [], "nothing listens: the DD socket is bound to one address and connected to one peer"
