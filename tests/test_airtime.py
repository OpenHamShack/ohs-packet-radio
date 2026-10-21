"""The transfer-time model (airtime.py) against what was measured: on the
air (1200 AFSK GM4BRI -> GM0OTB 19/10/2026; Pluto -> nano at 9600, 19 200
and 38 400 on 18/10/2026), and the library's own simulated channel with
the app's settings at each mode (tools/airtime-sim.py, run here). Then
which modes a radio offers, the bytes that go on the air, and a
measurement with a station taking over from the model."""
from __future__ import annotations

import random
import runpy
from pathlib import Path

import pytest

from conftest import station_settings
from shack_packet_app import airtime
from shack_packet_app.airtime import DEFAULT_LINK, PLUTO, RIG, LinkParams, transfer_s

TOOL = Path(__file__).resolve().parents[1] / "tools" / "airtime-sim.py"


def keyed(window: int) -> LinkParams:
    """The 18/10 on-air harness's settings ("as keyed": PACLEN 256,
    TXDELAY 200 ms, REJ)."""
    return LinkParams(window=window, paclen=256, modulo=128 if window > 7 else 8, txdelay_s=0.2,
                      max_frames=window + 1)


def test_1200_afsk_matches_the_run_with_gm0otb():
    # 5142 bytes in 42.8 s, k 4, PACLEN 256, an IC-9700 over LAN, its
    # acknowledgements by internet (the link test).
    s = transfer_s(5142, "afsk1200", DEFAULT_LINK, RIG, acks="internet", handshake=False, protocol="raw")
    assert s == pytest.approx(42.8, rel=0.10)


@pytest.mark.parametrize("mode, window, measured", [("g3ruh38400", 16, 5.5), ("g3ruh19200", 7, 11.0)])
def test_the_fast_modes_match_the_pluto_runs(mode, window, measured):
    s = transfer_s(10 * 1024, mode, keyed(window), PLUTO, handshake=False, protocol="raw")
    assert s == pytest.approx(measured, rel=0.15)


def test_the_9600_run_with_retries_is_inside_the_range():
    # 33.5 s for 10 KB with 28 of its 40 frames sent again: slower than a
    # good link, and the retries model at that loss comes close.
    good = transfer_s(10 * 1024, "g3ruh9600", keyed(7), PLUTO, handshake=False, protocol="raw")
    assert good < 33.5 * 0.7
    loss = 0.184                                    # REJ at k 7 sends 28 of 40 again at this loss
    bad = transfer_s(10 * 1024, "g3ruh9600", keyed(7), PLUTO, handshake=False, protocol="raw", loss=loss)
    assert bad == pytest.approx(33.5, rel=0.20)


def test_the_fastest_mode_moves_tens_of_kbit_with_the_apps_settings():
    s = station_settings(radio__kind="pluto")
    params, timing = airtime.link_params(s, "gfsk153600")
    assert params.window == 63 and params.paclen == 512 and params.srej
    secs = transfer_s(200_000, "gfsk153600", params, timing)
    assert 40_000 < 200_000 * 8 / secs < 153_600
    # ... and 1200 baud is the 0.8 kbit/s it always was.
    params, timing = airtime.link_params(s, "afsk1200")
    assert 600 < 200_000 * 8 / transfer_s(200_000, "afsk1200", params, timing) < 1000


@pytest.fixture(scope="module")
def sim():
    return runpy.run_path(str(TOOL), run_name="airtime_sim")


@pytest.mark.parametrize("mode, kb", [("afsk1200", 20), ("g3ruh9600", 100), ("g3ruh38400", 100),
                                      ("gfsk153600", 100), ("dd128k", 50)])
def test_the_model_agrees_with_the_simulator_at_the_apps_settings(sim, mode, kb):
    # 9600 included: k 63 at PACLEN 512 makes two 10 s overs back to back,
    # and the far end talks over the second (channel.MAX_BUSY_WAIT_S).
    # (A file of a few KB at 153 600 is over in three seconds, most of it
    # the offer and the result, which the model reckons a little long.)
    simulated = sim["simulate"](mode, kb * 1024)
    assert sim["model"](mode, kb * 1024) == pytest.approx(simulated, rel=0.20)


def test_the_retries_figure_is_slower_and_compression_shortens_both():
    s = station_settings(radio__kind="pluto")
    rows = airtime.table(s, 100_000)
    assert all(r.retries_s > r.good_s for r in rows)
    smaller = airtime.table(s, 30_000, chunks=98)          # the same file, compressed
    assert all(a.good_s < b.good_s for a, b in zip(smaller, rows))
    assert [r.mode for r in rows] == list(airtime.ROWS)


# -- what each radio can do -------------------------------------------------------------------


def available(s) -> dict:
    return {r.mode: r.why for r in airtime.table(s, 10_000)}


def test_an_ic9700_offers_1200_only_and_dd_once_turned_on():
    s = station_settings(radio__kind="ic-9700")
    why = available(s)
    assert why["afsk1200"] == "" and why["g3ruh9600"] == "afsk-only" and why["dd128k"] == "dd-off"
    assert [r.mode for r in airtime.table(s, 10_000) if r.current] == ["afsk1200"]
    s.radio.kind = "ic-9700-dd"
    rows = airtime.table(s, 10_000)
    assert {r.mode for r in rows if r.available} == {"dd128k"}
    assert [r.mode for r in rows if r.current] == ["dd128k"]
    assert all(r.unmeasured == (r.mode == "dd128k") for r in rows)


def test_a_pluto_offers_every_fast_mode_and_marks_its_own():
    s = station_settings(radio__kind="pluto")
    s.fast.mode = "g3ruh38400"
    rows = airtime.table(s, 10_000)
    assert {r.mode for r in rows if not r.available} == {"dd128k"}
    assert [r.mode for r in rows if r.current] == ["g3ruh38400"]
    s.fast.adapt, s.fast.adapt_start = True, "gfsk76800"
    assert [r.mode for r in airtime.table(s, 10_000) if r.current] == ["gfsk76800"]


@pytest.mark.parametrize("kind, why", [("sdr-rx", "receive-only"), ("none", "no-radio"), ("ic-705", "afsk-only"),
                                       ("simulator", "afsk-only")])
def test_other_radios(kind, why):
    got = available(station_settings(radio__kind=kind))
    assert got["g3ruh9600"] == why


# -- the bytes ---------------------------------------------------------------------------------


def test_the_bytes_on_the_air_are_compressed_where_the_sender_would():
    text = b"de GM4BRI the packet station is QRV on 70cm, readable 59, thanks for the contact\n" * 400
    assert airtime.wire_size(text, "log.txt") < len(text) / 4
    assert airtime.wire_size(text, "log.txt", protocol="yapp") == len(text)
    assert airtime.wire_size(text, "log.txt", compress=False) == len(text)
    noise = random.Random(1).randbytes(50_000)
    assert airtime.wire_size(noise, "log.txt") == len(noise)               # not worth it: sent as it is
    assert airtime.wire_size(b"\xff\xd8\xff\xe0" + text, "photo.jpg") == len(text) + 4


def test_a_big_file_is_compressed_in_part(tmp_path):
    p = tmp_path / "big.txt"
    p.write_bytes(b"0123456789abcdef" * (airtime.COMPRESS_SAMPLE // 8))      # twice the sample
    assert airtime.file_wire_size(p) < p.stat().st_size / 20


# -- measured with a station ---------------------------------------------------------------------


def test_a_measurement_with_a_station_takes_over_and_is_kept(tmp_path):
    s = station_settings(radio__kind="ic-9700")
    store = airtime.Measurements(tmp_path / "throughput.json")
    params, timing = airtime.link_params(s, "afsk1200")
    model = transfer_s(20_000, "afsk1200", params, timing)
    assert store.add(airtime.Measurement(peer="GM0OTB-7", mode="afsk1200", nbytes=20_000, seconds=model * 1.5))
    assert not store.add(airtime.Measurement(peer="GM0OTB", mode="afsk1200", nbytes=500, seconds=9.0))   # too small
    rows = airtime.table(s, 40_000, peer="gm0otb", measurements=airtime.Measurements(tmp_path / "throughput.json"))
    row = next(r for r in rows if r.mode == "afsk1200")
    assert row.measured is not None and row.measured[1].peer == "GM0OTB"
    assert row.best_s == pytest.approx(row.good_s * 1.5, rel=0.01)
    assert all(r.measured is None for r in airtime.table(s, 40_000, peer="G4ABC", measurements=store))


def test_a_link_test_is_compared_as_one_with_acks_by_internet(tmp_path):
    s = station_settings(radio__kind="ic-9700")
    store = airtime.Measurements(tmp_path / "t.json")
    # The 19/10 run itself: the model's figure for it, so the path is "as
    # modelled" and a file's estimate stays the model's.
    store.add(airtime.Measurement(peer="GM0OTB", mode="afsk1200", nbytes=5142, seconds=42.8, kind="linktest",
                                  protocol="raw"))
    row = next(r for r in airtime.table(s, 50_000, peer="GM0OTB", measurements=store) if r.mode == "afsk1200")
    assert row.best_s == pytest.approx(row.good_s, rel=0.10)


@pytest.mark.parametrize("mode, was_s", [("g3ruh9600", 177.8), ("afsk1200", 1035.3), ("g3ruh19200", 55.5),
                                         ("g3ruh38400", 31.7)])
def test_0_3_2_moves_100_kb_at_9600_far_quicker_and_no_other_mode_slower(sim, mode, was_s):
    # The simulator with the app's settings, 100 KB: 0.3.1 (k 63 at every
    # mode, a receiver that talked over its partner at the busy cap) took
    # `was_s`. 0.3.2's window per mode and partner wait: 9600 about 113 s.
    now_s = sim["simulate"](mode, 100 * 1024)
    if mode == "g3ruh9600":
        assert now_s < 0.7 * was_s
    else:
        assert now_s <= was_s * 1.01
