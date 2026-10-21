"""A link test by callsign, both directions by itself (linkrun.py), on the
simulator: two stations on one simulated band, openhamshack.com's relay
played by ohs-libs' stand-in (tests/weblinkfake.py) on a local port, or
the 0.2.1 TCP relay for Advanced. The operators' part is played by
`operate`: Arm when it is its turn to send (the run never arms), and the
antenna confirmation a Pluto is asked for."""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

import shack_packet
from conftest import station_settings, wait_for
from shack_packet_app import linkrun
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from test_linkctl import Friend, Relay

LIBS_TESTS = Path(shack_packet.__file__).resolve().parents[2] / "tests"
pytestmark = pytest.mark.skipif(not (LIBS_TESTS / "weblinkfake.py").is_file(),
                                reason="needs an ohs-libs checkout with tests/weblinkfake.py")


def _fake_server_class():
    spec = importlib.util.spec_from_file_location("weblinkfake", LIBS_TESTS / "weblinkfake.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weblinkfake"] = mod
    spec.loader.exec_module(mod)
    return mod.FakeLinkServer


@pytest.fixture
def pair(tmp_path):
    band = SimBand(55.95, -3.2, neighbours=False)
    web = _fake_server_class()()
    bri = Controller(station_settings(callsign="GM4BRI", station__ssid=1), band_factory=lambda: band)
    ray = Controller(station_settings(callsign="GM0OTB", station__ssid=1), band_factory=lambda: Friend(band))
    for c in (bri, ray):
        c.start()
        assert wait_for(lambda c=c: c.link_state == "open", 5)
        ls = c.settings.linktest
        ls.via, ls.url, ls.kb = "web", web.url, 1
    yield bri, ray, web
    for c in (bri, ray):
        c.linktest.leave("stopped")
        c.stop()
    band.stop()
    web.close()


class operate:
    """Both operators in the background: arm when it is their turn to send
    (as a person would after reading the page), confirm the antenna when
    asked, unless told not to."""

    def __init__(self, *ctls, confirm=True):
        self.ctls, self.confirm = ctls, confirm
        self.armed_by_us = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop.wait(0.1):
            for c in self.ctls:
                run = c.linktest.run
                if run is None or not run.running:
                    continue
                if run.role == "rf-sender" and run.phase == "half" and not c.station.armed:
                    if c.arm(5)[0]:
                        self.armed_by_us.append((c.station.callsign.call, run.half))
                if self.confirm and run.antenna_wanted:
                    run.confirm_antenna()

    def stop(self):
        self._stop.set()
        self.thread.join(2)


def _start(ctl, peer):
    ls = ctl.settings.linktest
    ls.peer = peer
    ok, why, detail = ctl.linktest.start_run(ls)
    assert ok, (why, detail)
    return ctl.linktest.run


def test_a_run_by_callsign_goes_both_ways_and_the_first_call_sends_first(pair):
    bri, ray, web = pair
    ops = operate(bri, ray)
    run_b = _start(bri, "gm0otb-1")
    assert wait_for(lambda: run_b.phase == "pairing", 5)
    assert bri.linktest.running
    run_r = _start(ray, "GM4BRI-1")
    try:
        assert wait_for(lambda: run_b.phase in ("done", "failed") and run_r.phase in ("done", "failed"), 150), \
            (run_b.view(), run_r.view())
        assert run_b.phase == run_r.phase == "done", (run_b.why, run_b.detail, run_r.why, run_r.detail)
    finally:
        ops.stop()
    # GM0OTB-1 sorts first, so it sent first; each station armed only for
    # its own sending half, and the run armed nobody (operate did).
    assert ops.armed_by_us == [("GM0OTB", 1), ("GM4BRI", 2)]
    for run in (run_b, run_r):
        res = run.results()
        assert [(h["from"], h["to"]) for h in res] == [("GM0OTB-1", "GM4BRI-1"), ("GM4BRI-1", "GM0OTB-1")]
        for h in res:
            assert h["sent"]["done"] and h["sent"]["acked"] == h["sent"]["size"]
            assert h["received"]["ok"] is True and h["received"]["bytes"] == h["sent"]["size"]
    # Each kept its own sending half as a measurement of the path to the
    # other, for the Send dialog's estimates (airtime.Measurements).
    size = run_b.results()[0]["sent"]["size"]
    for c, other in ((bri, "GM0OTB"), (ray, "GM4BRI")):
        m = c.files.measured.get(other, "afsk1200")
        assert m is not None and m.kind == "linktest" and m.nbytes == size and m.seconds > 0
    # Both ended joined to nothing: no return path, arming free again.
    for c in (bri, ray):
        assert wait_for(lambda c=c: c.linktest.run.ended is not None, 15)
        assert c.engine.return_paths == {} and c.linktest.state == "idle" and not c.linktest.blocks_arming()
        assert c.status()["linktest"] is None
    # Every frame of the receiving half went by internet: what each sent
    # by return path the other heard there.
    assert bri.engine.stats["rp_sent"] > 0 and ray.engine.stats["rp_heard"] == bri.engine.stats["rp_sent"]
    text = linkrun.report_text(run_b.report())
    assert "GM0OTB-1 -> GM4BRI-1" in text and "GM4BRI-1 -> GM0OTB-1" in text and text.count("CRC OK") == 2


def test_the_receiving_half_never_arms(pair):
    bri, ray, _web = pair
    ops = operate(ray)                                # only GM0OTB's operator; GM4BRI never arms
    run_b, run_r = _start(bri, "GM0OTB-1"), _start(ray, "GM4BRI-1")
    try:
        assert wait_for(lambda: run_b.half == 1 and run_b.role == "rf-receiver" and run_b.phase == "half", 20)
        assert bri.arm(5)[:2] == (False, "linktest-receiver")
        assert wait_for(lambda: run_b.half == 2 and run_r.phase == "half", 120), (run_b.view(), run_r.view())
        # GM4BRI's turn to send, and it waits for its operator to arm.
        time.sleep(1.0)
        assert run_b.role == "rf-sender" and not bri.station.armed and not run_b.results()[1]["sent"]
        assert bri.engine.channel.stats.transmissions == 0
    finally:
        ops.stop()


def test_a_pluto_is_asked_to_move_its_antenna_and_the_other_station_waits(pair, monkeypatch):
    bri, ray, _web = pair
    monkeypatch.setattr(linkrun, "needs_move", lambda s: s is bri.settings)
    ops = operate(bri, ray, confirm=False)
    run_b, run_r = _start(bri, "GM0OTB-1"), _start(ray, "GM4BRI-1")
    try:
        assert wait_for(lambda: run_b.antenna_wanted == 1, 10)
        time.sleep(1.0)
        # GM0OTB's radio has one socket: nothing to confirm, but it waits.
        assert run_r.antenna_wanted == 0 and run_r.phase == "antenna" and run_r.view()["peer_move"]
        run_b.confirm_antenna()
        assert wait_for(lambda: run_b.antenna_wanted == 2 and run_r.phase == "antenna", 120), run_b.view()
        assert run_r.results()[0]["received"]["ok"] is True
        time.sleep(1.0)
        assert run_r.phase == "antenna" and run_r.half == 1
        run_b.confirm_antenna()
        assert wait_for(lambda: run_b.phase == "done" and run_r.phase == "done", 120), (run_b.view(), run_r.view())
        assert [n["text"] for n in run_b.notes] == ["antenna confirmed on the RX port for half 1",
                                                    "antenna confirmed on the TX port for half 2"]
    finally:
        ops.stop()


def test_stopping_at_one_end_ends_the_run_at_both(pair):
    bri, ray, _web = pair
    run_b, run_r = _start(bri, "GM0OTB-1"), _start(ray, "GM4BRI-1")
    assert wait_for(lambda: run_b.phase == "half" and run_r.phase == "half", 20)
    ray.linktest.stop_run()
    assert wait_for(lambda: run_r.phase == "stopped" and run_b.phase == "failed", 10), (run_b.view(), run_r.view())
    assert run_b.why == "peer-stopped"
    for c in (bri, ray):
        assert wait_for(lambda c=c: c.engine.return_paths == {} and c.linktest.state == "idle", 5)


def test_the_site_going_away_mid_transfer_disarms_the_sender(pair):
    bri, ray, web = pair
    for c in (bri, ray):
        c.settings.linktest.kb = 20
    ops = operate(ray)
    run_b, run_r = _start(bri, "GM0OTB-1"), _start(ray, "GM4BRI-1")
    try:
        assert wait_for(lambda: (bri.linktest.received or {}).get("bytes", 0) > 300, 60)
        ops.stop()
        web.kill()
        # weblink's silence limit (8 s), then the link is dropped without
        # transmitting and the sender disarmed.
        assert wait_for(lambda: run_r.phase == "failed", 15), run_r.view()
        assert run_r.why == "relay-silent" and not ray.station.armed
        sent = ray.engine.channel.stats.transmissions
        time.sleep(2)
        assert ray.engine.channel.stats.transmissions <= sent + 1          # at most the over under way
        assert wait_for(lambda: run_b.phase == "failed", 15)
    finally:
        ops.stop()


def test_a_change_during_the_run_is_in_the_report(pair):
    bri, ray, _web = pair
    run_b = _start(bri, "GM0OTB-1")
    bri.linktest.note("RF power 50 % -> 25 %")
    rep = run_b.report()
    assert rep["notes"][0]["text"] == "RF power 50 % -> 25 %"
    assert "RF power 50 % -> 25 %" in linkrun.report_text(rep)
    bri.linktest.stop_run()
    assert wait_for(lambda: not run_b.running, 10)
    bri.linktest.note("after the run")                 # nowhere to go: ignored
    assert len(run_b.notes) == 1


def test_bad_callsigns_are_refused_before_anything_starts(pair):
    bri, _ray, _web = pair
    ls = bri.settings.linktest
    for peer in ("", "NOT A CALL", "GM4BRI-1"):
        ls.peer = peer
        assert bri.linktest.start_run(ls)[:2] == (False, "linktest-peer")
    ls.peer, ls.via, ls.relay = "GM0OTB-1", "relay", "host:port"
    assert bri.linktest.start_run(ls)[:2] == (False, "linktest-relay")


def test_advanced_the_tcp_relay_runs_the_same_two_halves(pair, tmp_path):
    bri, ray, _web = pair
    relay = Relay(tmp_path / "relay")
    try:
        for c in (bri, ray):
            ls = c.settings.linktest
            ls.via, ls.relay = "relay", f"127.0.0.1:{relay.port}"
        ops = operate(bri, ray)
        run_b, run_r = _start(bri, "GM0OTB-1"), _start(ray, "GM4BRI-1")
        try:
            assert wait_for(lambda: run_b.phase in ("done", "failed") and run_r.phase in ("done", "failed"), 150), \
                (run_b.view(), run_r.view())
            assert run_b.phase == run_r.phase == "done", (run_b.why, run_b.detail, run_r.why, run_r.detail)
        finally:
            ops.stop()
        assert all(h["received"]["ok"] for h in run_b.results())
    finally:
        relay.stop()


def test_the_page_runs_it_and_shows_both_directions(pair, qapp, tmp_path):
    from shack_packet_app.ui.window import PAGES, MainWindow
    bri, ray, _web = pair
    w = MainWindow(bri.settings, {}, controller=bri, persist=False)
    w.nav.setCurrentRow(PAGES.index("linktest"))
    page = w.pages["linktest"]
    w.tick()
    assert not page.advanced.opened and page.kb.value() == 1 and page.stop_btn.isEnabled() is False
    page.peer.setText("gm0otb-1")
    page.start()
    assert bri.settings.linktest.peer == "GM0OTB-1" and bri.settings.linktest.via == "web"
    ops = operate(ray)
    _start(ray, "GM4BRI-1")
    try:
        # GM4BRI receives first: the banner says it never transmits.
        assert wait_for(lambda: bri.linktest.run.phase == "half", 20, app=qapp)
        w.tick()
        assert not w.linktest_banner.isHidden() and "never transmits" in w.linktest_banner.label.text()
        assert not page.start_btn.isEnabled() and page.stop_btn.isEnabled()
        # Its turn to send: the page asks its operator to arm, and nothing is sent until then.
        assert wait_for(lambda: bri.linktest.run.half == 2 and bri.linktest.run.phase == "half", 120, app=qapp)
        w.tick()
        assert "arm transmit" in page.todo.text() and not page.todo.isHidden()
        assert bri.arm(5)[0]
        assert wait_for(lambda: bri.linktest.run.ended is not None, 120, app=qapp), bri.linktest.run.view()
    finally:
        ops.stop()
    w.tick()
    assert page.table.horizontalHeaderItem(0).text() == "GM0OTB-1 → GM4BRI-1"
    assert [page.table.item(0, c).text() for c in (0, 1)] == ["OK", "OK"]
    assert "received intact" in page.summary.text() and w.linktest_banner.isHidden()
    out = tmp_path / "report.txt"
    page.save_report(str(out))
    text = out.read_text()
    assert "GM4BRI-1 -> GM0OTB-1" in text and '"phase": "done"' in text
