"""The path test as the station runs it (pathrun.py), on ohs-libs' simulated
frames channel with two adaptive radios: one station sends numbered frames
at each mode, the other counts them, and the counts, the best mode, the
report and "use for adaptation" come out of what was actually heard. And
the refusals: nothing goes out disarmed, out of the band plan or off the
ladder, and a disarm or Stop ends a run with nothing more sent."""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from conftest import station_settings
from shack_packet.channel import ChannelConfig
from shack_packet.datalink import LinkConfig
from shack_packet.engine import Engine
from shack_packet.sim import Medium, SimClock, Simulation
from shack_packet_app.pathrun import PathTest
from shack_packet_app.txarm import ArmedStation

LADDER = ("4fsk76800", "gfsk76800", "g3ruh38400", "g3ruh19200", "g3ruh9600")
WIDE_OK_HZ = 1_299_525_000          # a 150 kHz wide-data channel, UK plan
NARROW_HZ = 1_298_500_000           # 1298-1299: 20 kHz channels only


def _settings(frequency_hz=WIDE_OK_HZ, kind="pluto", adapt=True):
    s = station_settings(radio__kind=kind, channel__frequency_hz=frequency_hz)
    s.fast.adapt = adapt
    return s


class Rig:
    """Two stations on one simulated frequency; `cn0` dB-Hz each way."""

    def __init__(self, cn0=62.0, frequency_hz=WIDE_OK_HZ, seed=3):
        self.clock = SimClock()
        self.medium = Medium(self.clock, "frames", seed=seed, quality=lambda a, b, t: cn0)
        self.sim = Simulation(self.clock, self.medium)
        self.ctl = {}
        for call in ("GM4BRI", "GM0OTB"):
            radio = self.medium.radio(call, modes=LADDER)
            station = ArmedStation(clock=self.clock)
            station.set_callsign(call)
            engine = Engine(station, radio, clock=self.clock, channel=ChannelConfig(persist=255, txdelay_s=0.1),
                            link=LinkConfig(k=32, n1=256, modulo=128, srej=True), rng=random.Random(call))
            self.sim.add(engine)
            ctl = SimpleNamespace(engine=engine, station=station, link=radio, link_state="open",
                                  settings=_settings(frequency_hz))
            ctl.pathtest = PathTest(ctl)
            self.ctl[call] = ctl
        self.tx, self.rx = self.ctl["GM4BRI"], self.ctl["GM0OTB"]

    def run_until_sent(self, limit_s=600):
        assert self.sim.run(limit_s, lambda: self.tx.pathtest.role == "")


def test_a_run_counts_every_mode_and_finds_the_best_the_path_carries():
    rig = Rig(cn0=62.0)
    modes = ["4fsk76800", "gfsk76800", "g3ruh9600"]
    assert rig.rx.pathtest.start_receiving(modes, 20) == ""
    rig.tx.station.arm(10)
    assert rig.tx.pathtest.start_sending(modes, count=20, gap_s=0.3) == ("", "")
    rig.run_until_sent()
    rig.sim.run(3)
    assert rig.tx.pathtest.ended == "finished"
    assert rig.tx.pathtest.progress()["sent"] == 60
    rows = {r["mode"]: r for r in rig.rx.pathtest.rows()}
    # Fastest first, as the table shows them.
    assert [r["mode"] for r in rig.rx.pathtest.rows()] == modes
    # 62 dB-Hz: under 4fsk76800's 69.5, over gfsk76800's 61.2 (adapt.py).
    assert rows["4fsk76800"]["pct"] < 50
    assert rows["gfsk76800"]["pct"] >= 90 and rows["g3ruh9600"]["pct"] == 100.0
    for r in rows.values():
        assert r["decoded"] + r["missing"] == 20 and r["from"] == "GM4BRI"
    assert rows["gfsk76800"]["cn0_dbhz"] == pytest.approx(62.0, abs=2)
    # The SNR equivalent is the C/N0 in the mode's own bandwidth: a wider
    # mode at the same level has the lower SNR.
    assert rows["g3ruh9600"]["snr_db"] > rows["gfsk76800"]["snr_db"]
    assert rig.rx.pathtest.best_mode() == "gfsk76800"
    report = rig.rx.pathtest.report()
    assert "from GM4BRI heard by GM0OTB on 1299.5250 MHz" in report
    assert "best mode this way: gfsk76800" in report
    # Into adaptation: stored, and on the running engine for new links.
    assert rig.rx.pathtest.use_for_adaptation() == ("", "gfsk76800")
    assert rig.rx.settings.fast.adapt_start == "gfsk76800"
    assert rig.rx.engine.link_config.adapt_start == "gfsk76800"


def test_the_receiver_listens_on_the_whole_ladder_and_stops_listening_after():
    rig = Rig()
    radio = rig.rx.link
    rig.rx.pathtest.start_receiving(LADDER, 5)
    rig.sim.run(0.1)
    assert radio.listen == set(LADDER)
    rig.rx.pathtest.stop()
    rig.sim.run(0.1)
    # Back to the floor and the top only, what the engine listens to idle.
    assert radio.listen == {"4fsk76800", "g3ruh9600"}
    assert rig.rx.pathtest.ended == "stopped"


def test_a_mode_heard_not_at_all_still_has_its_row():
    rig = Rig(cn0=50.0)                # under every mode's threshold but the floor's
    rig.rx.pathtest.start_receiving(["4fsk76800", "g3ruh9600"], 5)
    assert [(r["mode"], r["decoded"], r["missing"]) for r in rig.rx.pathtest.rows()] == \
        [("4fsk76800", 0, 5), ("g3ruh9600", 0, 5)]
    rig.tx.station.arm(10)
    rig.tx.pathtest.start_sending(["4fsk76800"], count=5, gap_s=0.1)
    rig.run_until_sent()
    rig.sim.run(2)
    rows = {r["mode"]: r for r in rig.rx.pathtest.rows()}
    assert rows["4fsk76800"]["decoded"] == 0 and rows["4fsk76800"]["missing"] == 5
    assert rig.rx.pathtest.use_for_adaptation() == ("no-best", "")
    assert rig.rx.settings.fast.adapt_start is None


def test_sending_disarmed_is_refused_and_nothing_goes_out():
    rig = Rig()
    assert rig.tx.pathtest.start_sending(["g3ruh9600"], count=3) == ("not-armed", "")
    rig.sim.run(10)
    assert rig.medium.airtime_s == 0.0 and rig.tx.pathtest.role == ""


def test_a_mode_wider_than_the_band_plan_allows_is_refused():
    rig = Rig(frequency_hz=NARROW_HZ)
    rig.tx.station.arm(10)
    choices = {m: (k, d) for m, k, d in rig.tx.pathtest.choices()}
    assert choices["4fsk76800"][0] == "out-of-plan" and "not inside a band-plan segment" in choices["4fsk76800"][1]
    assert choices["g3ruh9600"] == ("", "")
    why, detail = rig.tx.pathtest.start_sending(["g3ruh9600", "4fsk76800"], count=3)
    assert why == "out-of-plan" and detail.startswith("4fsk76800")
    rig.sim.run(10)
    assert rig.medium.airtime_s == 0.0


def test_gfsk153600_is_for_13_cm_and_a_mode_off_the_ladder_is_refused():
    rig = Rig()
    choices = {m: k for m, k, _d in rig.tx.pathtest.choices()}
    # 1299.525 MHz: 153 600 is wider than the 150 kHz channel; 4fsk38400
    # fits but is not on this ladder (adapt.py leaves it out).
    assert choices["gfsk153600"] == "out-of-plan"
    assert choices["4fsk38400"] == "not-on-ladder"
    rig.tx.station.arm(10)
    assert rig.tx.pathtest.start_sending(["4fsk38400"])[0] == "not-on-ladder"


def test_disarming_ends_a_run_and_nothing_more_is_sent():
    rig = Rig(cn0=80.0)
    rig.rx.pathtest.start_receiving(["g3ruh9600"], 50)
    rig.tx.station.arm(10)
    rig.tx.pathtest.start_sending(["g3ruh9600"], count=50, gap_s=1.0)
    rig.sim.run(5.5)
    sent = rig.tx.pathtest.progress()["sent"]
    assert 3 <= sent < 50
    rig.tx.station.disarm("halted")
    rig.sim.run(20)
    assert rig.tx.pathtest.role == "" and rig.tx.pathtest.ended == "transmit-disabled"
    assert rig.tx.pathtest.progress()["sent"] == sent
    assert rig.tx.engine.channel.stats.frames == sent


def test_stop_takes_back_a_queued_frame_and_ends_at_once():
    rig = Rig(cn0=80.0)
    rig.tx.station.arm(10)
    rig.tx.pathtest.start_sending(["g3ruh9600"], count=50, gap_s=1.0)
    # One poll: the first frame is queued and not yet on the air.
    rig.tx.engine.services[-1].poll(rig.clock())
    assert len(rig.tx.engine.channel._normal) == 1
    rig.tx.pathtest.stop()
    assert len(rig.tx.engine.channel._normal) == 0 and rig.tx.pathtest.role == ""
    rig.sim.run(10)
    assert rig.medium.airtime_s == 0.0 and rig.tx.pathtest.ended == "stopped"
    # The station stays armed: Stop ends the test, not the arming.
    assert rig.tx.station.armed


@pytest.mark.parametrize("kind, adapt, want", [("simulator", True, "not-pluto"), ("pluto", False, "not-adaptive")])
def test_a_station_that_cannot_run_one_says_what_to_set(kind, adapt, want):
    rig = Rig()
    rig.tx.settings = _settings(kind=kind, adapt=adapt)
    assert rig.tx.pathtest.readiness() == want
    rig.tx.station.arm(10)
    assert rig.tx.pathtest.start_sending(["g3ruh9600"])[0] == want
    assert rig.tx.pathtest.start_receiving() == want


def test_adaptation_on_in_settings_but_not_on_the_open_link_is_said():
    rig = Rig()
    rig.tx.link = SimpleNamespace(modes=None)
    assert rig.tx.pathtest.readiness() == "link-not-adaptive"


# -- the page, on two whole stations ------------------------------------------------------
#
# Two Controllers and two windows, each station's "Pluto" a radio on one
# frames-mode medium run against the wall clock (the Controllers' engines
# run on their own Runner threads, as in the app), so the page is driven
# exactly as an operator drives it: tick modes, arm, Start, read the table.

import threading  # noqa: E402
import time  # noqa: E402

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from conftest import wait_for  # noqa: E402
from shack_packet.sim import SimRadio  # noqa: E402
from shack_packet_app import diagnostics  # noqa: E402
from shack_packet_app.controller import Controller  # noqa: E402
from shack_packet_app.ui.window import PAGES, MainWindow  # noqa: E402


class _WallClock:
    @property
    def now(self):
        return time.monotonic()


class _LiveRadio(SimRadio):
    """A medium radio the Controller can open and close; every touch of
    the shared medium under one lock, as three threads reach it."""

    def open(self):
        pass

    def close(self):
        pass

    def send_frames(self, *a, **k):
        with self.medium.lock:
            return super().send_frames(*a, **k)


class LiveAir:
    def __init__(self, cn0=80.0):
        self.medium = Medium(_WallClock(), "frames", seed=1, quality=lambda a, b, t: cn0)
        self.medium.lock = threading.Lock()
        self.radios = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def radio(self, call):
        # A Pluto's turnaround as PlutoLink reports it until measured
        # (100 ms): the fast settings' TXDELAY is 0, so the over's
        # preamble is that plus the mode's lock bits, as on the air. With
        # the sim radio's own 0 the preamble was a few ms and the medium
        # lost every frame.
        r = self.radios[call] = _LiveRadio(self.medium, call, modes=LADDER, turnaround_s=0.1)
        self.medium.radios.append(r)
        return r

    def _run(self):
        while not self._stop.is_set():
            with self.medium.lock:
                self.medium.poll(time.monotonic())
            self._stop.wait(0.005)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(2)


@pytest.fixture
def stations(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    air = LiveAir()
    wins = []

    def make(call, kind="pluto", adapt=True):
        s = station_settings(callsign=call, radio__kind=kind, channel__frequency_hz=WIDE_OK_HZ)
        s.fast.adapt = adapt
        s.sdr_radio.filter_confirmed = True
        radio = air.radio(call)
        ctl = Controller(s, secrets={}, link_factory=lambda *_a, **_k: radio)
        win = MainWindow(s, {}, controller=ctl, persist=False)
        win.messages_shown = shown
        win.show()
        win.start(first_run_check=False)
        wins.append(win)
        win.nav.setCurrentRow(PAGES.index("pathtest"))
        return win
    air.start()
    yield make
    for w in wins:
        w.close()
    air.stop()
    qapp.processEvents()


def _tick(win, modes):
    page = win.pages["pathtest"]
    win.tick()
    for m, cb in page.mode_boxes.items():
        cb.setChecked(m in modes)
    return page


def _cells(page):
    t = page.table
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


def test_the_page_runs_a_test_from_one_station_to_the_other(stations, qapp, tmp_path):
    rx, tx = stations("GM0OTB"), stations("GM4BRI")
    assert wait_for(lambda: rx.ctl.link_state == "open" and tx.ctl.link_state == "open", 5, app=qapp)
    modes = ["gfsk76800", "g3ruh9600"]
    rp, tp = _tick(rx, modes), _tick(tx, modes)
    # The cable notice is there, whatever the role.
    assert "TX port" in rp.notice.text() and "plain tee" in rp.notice.text()
    rp.count.setValue(5)
    tp.count.setValue(5)
    rp.start.click()
    assert rx.ctl.pathtest.role == "receive"
    tp.send_role.setChecked(True)
    tp.gap.setValue(0.0)
    assert tp.start.text() == "Start sending"
    # Disarmed: refused, and said.
    tp.start.click()
    assert tx.ctl.pathtest.role == "" and "Arm transmit first" in tx.messages_shown[-1]
    tx.toggle_arm()
    assert tx.ctl.status()["armed"]
    tp.start.click()
    assert tx.ctl.pathtest.role == "send"
    assert wait_for(lambda: (tx.tick(), tx.ctl.pathtest.role == "")[1], 30, app=qapp)
    assert "Finished" in tp.state.text()

    def heard():
        rx.tick()
        return [row[2] for row in _cells(rp)] == ["5/5", "5/5"]
    assert wait_for(heard, 10, app=qapp), _cells(rp)
    rp.stop.click()
    rx.tick()
    cells = _cells(rp)
    assert [row[0] for row in cells] == modes and all(row[3] == "0" for row in cells)
    assert "gfsk76800" in rp.best.text()
    # Save, Copy, and the diagnostics bundle carry the same report.
    report = rx.ctl.pathtest.report()
    assert "best mode this way: gfsk76800" in report
    out = rp.save_report(str(tmp_path / "pt.txt"))
    assert (tmp_path / "pt.txt").read_text() == report + "\n" and out
    rp.copy.click()
    assert QApplication.clipboard().text() == report
    assert diagnostics.contents(rx.ctl)["path-test.txt"] == report + "\n"
    assert "path-test.txt" not in diagnostics.contents(tx.ctl)
    rp.use.click()
    assert rx.settings.fast.adapt_start == "gfsk76800"


def test_the_page_says_what_to_set_and_offers_nothing_to_start(stations, qapp):
    win = stations("GM4BRI", adapt=False)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    page = _tick(win, [])
    assert page.ready.isVisibleTo(page) and "link adaptation" in page.ready.text()
    assert not page.start.isEnabled()
