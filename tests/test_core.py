"""The pieces under the window: arming, settings, locators, presets."""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from shack_packet_app import grid, presets, settings
from shack_packet_app.txarm import ArmedStation


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_arming_expires_on_reading_and_says_so():
    clock = Clock()
    told = []
    st = ArmedStation(clock=clock, on_change=told.append)
    st.set_callsign("GM4BRI")
    assert st.refusal() == "transmit-disabled"
    st.arm(2)
    assert st.refusal() is None
    assert st.remaining_s() == pytest.approx(120)
    clock.t += 119.9
    assert st.refusal() is None
    clock.t += 0.2
    # Nothing ran in between: the expiry is noticed by the question itself.
    assert st.refusal() == "transmit-disabled"
    assert told == ["armed", "expired"]
    assert not st.armed


def test_arming_is_bounded_and_disarm_is_immediate():
    clock = Clock()
    st = ArmedStation(clock=clock)
    st.set_callsign("GM4BRI-7")
    st.arm(600)                       # asked for ten hours
    assert st.remaining_s() == pytest.approx(60 * 60)
    st.disarm()
    assert st.refusal() == "transmit-disabled"


def test_no_callsign_refuses_even_when_armed():
    st = ArmedStation(clock=Clock())
    st.arm(5)
    assert st.refusal() == "no-callsign"


def test_settings_round_trip_keeps_unknown_keys_and_never_saves_arming(folders):
    s = settings.Settings()
    s.station.callsign = "GM4BRI"
    s.radio.kind = "ftx-1"
    s.radio.cat_device = "/dev/ttyUSB0"
    settings.save(s)
    data = json.loads(settings.settings_path().read_text())
    data["future_feature"] = {"x": 1}
    data["radio"]["future_key"] = 3
    settings.settings_path().write_text(json.dumps(data))
    back = settings.load()
    assert back.station.callsign == "GM4BRI" and back.radio.cat_device == "/dev/ttyUSB0"
    settings.save(back)
    again = json.loads(settings.settings_path().read_text())
    assert again["future_feature"] == {"x": 1}
    assert "armed" not in json.dumps(again).lower()


def test_fast_settings_keep_afsk_by_default_and_cap_drive_per_band(folders):
    s = settings.Settings()
    fast = s.fast.for_frequency(144_800_000)
    # Never opened: still the mode every packet station hears.
    assert fast.mode == "afsk1200" and not fast.adapt and fast.max_tx_gain_db is None
    s.fast.mode = "gfsk76800"
    s.fast.drive_caps = {"23cm": -12.5}
    settings.save(s)
    back = settings.load()
    assert back.fast.for_frequency(1_299_525_000).max_tx_gain_db == -12.5
    # A cap set up on 23 cm is not the 70 cm amplifier's.
    assert back.fast.for_frequency(432_650_000).max_tx_gain_db is None
    assert back.fast.for_frequency(1_299_525_000).problems(1_299_525_000) == []
    assert back.fast.for_frequency(1_298_500_000).problems(1_298_500_000) == ["fast-out-of-plan"]


def test_damaged_settings_are_set_aside_not_lost(folders):
    path = settings.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    s = settings.load()
    assert s.station.callsign == ""
    assert path.with_suffix(".json.damaged").read_text() == "{not json"


def test_the_password_is_kept_out_of_settings(folders):
    s = settings.Settings()
    settings.save(s)
    settings.save_secrets({"icom_password": "hunter22"})
    assert "hunter22" not in settings.settings_path().read_text()
    assert settings.load_secrets() == {"icom_password": "hunter22"}
    if not sys.platform.startswith("win"):
        assert stat.S_IMODE(os.stat(settings.secrets_path()).st_mode) == 0o600


@pytest.mark.parametrize("loc,lat,lon", [("IO85JW", 55.94, -3.21), ("FN31PR", 41.73, -72.71), ("JJ00AA", 0.02, 0.04)])
def test_locators_both_ways(loc, lat, lon):
    la, lo = grid.to_latlon(loc)
    assert la == pytest.approx(lat, abs=0.03) and lo == pytest.approx(lon, abs=0.05)
    assert grid.from_latlon(la, lo) == loc
    assert not grid.valid("ZZ99")


def test_presets_follow_the_profile_not_constants():
    uk = {p.id: p for p in presets.presets({"callsign": "GM4BRI", "licence": {"class": "foundation"}})}
    us = {p.id: p for p in presets.presets({"callsign": "K1ABC", "licence": {"class": "extra"}})}
    assert uk["aprs"].hz == 144_800_000 and us["aprs"].hz == 144_390_000
    assert "node-144925" in uk and "node-144925" not in us
    # Listed, but disabled, with the dataset's sentence: never hidden.
    assert uk["iss"].enabled is False and uk["iss"].why


def test_band_plan_judges_the_licence():
    full = presets.judge({"callsign": "GM4BRI", "licence": {"class": "full"}}, 144_800_000)
    none = presets.judge({"callsign": "GM4BRI"}, 144_800_000)
    nov = presets.judge({"callsign": "GM4BRI", "licence": {"class": "foundation"}}, 146_800_000)
    assert full["allowed"]
    assert not none["allowed"] and "licence class" in none["refusal"]
    assert not nov["allowed"]


@pytest.mark.skipif(not os.path.isdir("/proc/self/task"), reason="counts threads through Linux's /proc")
def test_numpy_after_the_app_starts_no_openblas_threads():
    # OpenBLAS's idle threads spin after every FX.25 tag search and tripled
    # the fast receivers' CPU (shack_packet_app/__init__.py). numpy loaded
    # after the app must start none; on a many-core host it starts one per
    # core otherwise.
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    code = "import shack_packet_app, numpy, os; print(len(os.listdir('/proc/self/task')))"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) == 1
