"""Satellites: the vendored SGP4 against Vallado's published vectors, the
station geometry, passes and Doppler, the elements store, and a pass's
transmit gate and Doppler steps against a stand-in radio."""
from __future__ import annotations

import math

import pytest

from shack_packet_app.sat import session, tle, track

# Vallado's SGP4-VER.TLE satellite 00005 and tcppver.out (sgp4 2.27 sdist):
# minutes from epoch -> TEME km.
VANGUARD = ("00005", "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
            "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667")
VANGUARD_KM = {0.0: (7022.46529266, -1400.08296755, 0.03995155),
               360.0: (-7154.03120202, -3783.17682504, -3536.19412294),
               1440.0: (-938.55923943, -6268.18748831, -4294.02924751)}

# An ISS-like orbit (51.64 deg, 15.5 rev/day) with its epoch at 2026-10-20
# 00:00 UTC, so every test is about a fixed day.
ISS = ("ISS (ZARYA)", "1 25544U 98067A   26293.00000000  .00016717  00000+0  30000-3 0  9991",
       "2 25544  51.6400 200.0000 0005000  90.0000 270.0000 15.50000000400005")
EDINBURGH = (55.95, -3.19)


def test_sgp4_reproduces_vallados_vectors():
    sat = track.Satellite(*VANGUARD)
    for minutes, want in VANGUARD_KM.items():
        r, _v = sat.teme(sat.epoch_unix + minutes * 60.0)
        assert all(abs(a - b) < 1e-3 for a, b in zip(r, want)), (minutes, r)


def test_greenwich_sidereal_time_at_j2000():
    j2000 = (2451545.0 - track.JD_UNIX0) * 86400.0
    assert math.degrees(track.gmst_rad(j2000)) == pytest.approx(280.46061837, abs=1e-6)


def test_a_satellite_straight_overhead_is_at_ninety_degrees():
    sat = track.Satellite(*ISS)
    t = sat.epoch_unix + 1234.0
    r, _v = sat.teme(t)
    th = track.gmst_rad(t)
    x, y = math.cos(th) * r[0] + math.sin(th) * r[1], -math.sin(th) * r[0] + math.cos(th) * r[1]
    # The point under the satellite, geocentric latitude being near enough
    # at the equator-ish latitudes an ISS track has at this moment.
    lon = math.degrees(math.atan2(y, x))
    lat_gc = math.degrees(math.atan2(r[2], math.hypot(x, y)))
    lat = math.degrees(math.atan(math.tan(math.radians(lat_gc)) / (1 - track.EARTH_F) ** 2))
    look = sat.look(t, lat, lon)
    assert look.el_deg > 89.5 and 380 < look.range_km < 460


def test_passes_rise_peak_and_set_and_doppler_changes_sign_at_the_top():
    sat = track.Satellite(*ISS)
    passes = sat.passes(sat.epoch_unix, 24, *EDINBURGH)
    assert 3 <= len(passes) <= 8, passes
    for p in passes:
        assert p.aos < p.tca < p.los and 60 < p.duration_s < 13 * 60
        assert abs(sat.look(p.aos, *EDINBURGH).el_deg) < 0.2 and abs(sat.look(p.los, *EDINBURGH).el_deg) < 0.2
        samples = [sat.look(p.aos + i * 5.0, *EDINBURGH).el_deg for i in range(int(p.duration_s / 5))]
        assert p.max_el_deg == pytest.approx(max(samples), abs=0.2)
        early, late = sat.look(p.aos + 30, *EDINBURGH), sat.look(p.los - 30, *EDINBURGH)
        assert early.range_rate_km_s < -1 and late.range_rate_km_s > 1
        rx, tx = early.doppler(145_825_000)
        assert 145_825_000 < rx < 145_830_000 and 145_820_000 < tx < 145_825_000, "approaching: hear high, send low"
    assert sat.passes(sat.epoch_unix, 24, *EDINBURGH, min_el_deg=90) == []


def test_a_tle_file_with_rubbish_in_it_keeps_the_good_sets():
    text = "\n".join(["junk"] + list(ISS) + ["BROKEN", "1 xx", "2 yy"] + list(VANGUARD) + [""])
    sats = track.parse_tles(text)
    assert [s.number for s in sats] == [25544, 5]


def test_the_elements_are_replaced_only_by_something_that_parses(tmp_path):
    replies = [b"<html>Please sign in to the hotel wifi</html>", ("\n".join(ISS) + "\n").encode()]
    asked = []

    def fetch(url):
        asked.append(url)
        return replies.pop(0)

    store = tle.TleStore(tmp_path / "amateur.tle", fetch=fetch, clock=lambda: 1_792_454_400.0 + 3 * 86400)
    assert store.satellites() == [] and store.age_days() is None
    with pytest.raises(tle.TleError):
        store.update()
    assert not (tmp_path / "amateur.tle").exists()
    assert store.update() == 1 and asked == [tle.CELESTRAK_AMATEUR] * 2
    assert store.find("iss (zarya)").number == 25544 and store.find(25544) is store.find("25544")
    assert store.age_days() == pytest.approx(3.0, abs=0.01)
    again = tle.TleStore(tmp_path / "amateur.tle")
    assert [s.number for s in again.satellites()] == [25544], "read back from the cache, offline"


def test_birds_the_operator_adds_join_the_iss_and_bad_ones_do_not():
    got = tle.birds([{"norad": 99999, "name": "TESTSAT", "uplink_hz": 145_850_000, "downlink_hz": 435_350_000,
                      "how": "satellite"}, {"norad": "x"}])
    assert set(got) == {25544, 99999} and got[99999].how == "satellite" and got[25544].path == "ARISS"


class Radio:
    """IcomControl's pass calls, recorded."""

    def __init__(self):
        self.calls = []

    def begin_pass(self, how, rx_hz, tx_hz):
        self.calls.append(("begin", how, rx_hz, tx_hz))
        return []

    def retune(self, rx_hz=None, tx_hz=None):
        self.calls.append(("retune", rx_hz, tx_hz))
        return True

    def end_pass(self):
        self.calls.append(("end",))
        return []


def test_a_pass_steps_doppler_holds_transmit_mid_over_and_gates_arming():
    sat = track.Satellite(*ISS)
    p = sat.passes(sat.epoch_unix, 24, *EDINBURGH, min_el_deg=20)[0]
    now = [p.aos - 120.0]
    keyed = [False]
    radio = Radio()
    s = session.PassSession(sat, tle.BIRDS[25544], *EDINBURGH, control=radio, clock=lambda: now[0],
                            transmitting=lambda: keyed[0], age_days=lambda: 1.0)
    assert s.tx_refusal() == "sat-below-horizon"
    now[0] = p.tca
    assert s.tx_refusal() is None
    now[0] = p.aos + 60
    s.start()
    assert radio.calls[0][:2] == ("begin", "split") and radio.calls[0][2] > 145_825_000 > radio.calls[0][3]
    t = p.aos + 60
    while t < p.los - 60:
        t += 5.0
        now[0] = t
        keyed[0] = p.tca - 20 < t < p.tca + 20
        s.tick()
    rx_steps = [c for c in radio.calls if c[0] == "retune" and c[1] is not None]
    assert 8 <= len(rx_steps) <= 60 and s.steps["tx_held"] > 0
    assert all(abs(b[1] - a[1]) >= s.rx_step for a, b in zip(rx_steps, rx_steps[1:]))
    assert s.rx_hz < 145_825_000 < s.tx_hz, "receding at the end"
    s.stop()
    assert radio.calls[-1] == ("end",) and not s.active
    old = session.PassSession(sat, tle.BIRDS[25544], *EDINBURGH, clock=lambda: p.tca, age_days=lambda: 45.0)
    assert old.tx_refusal() == "sat-old-elements"
    assert session.PassSession(None, tle.BIRDS[25544], *EDINBURGH).tx_refusal() == "sat-no-elements"
    assert session.auto_step_hz(145_825_000) == 300 and session.auto_step_hz(435_000_000) == 900
