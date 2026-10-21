"""The Link test page's station side (linkctl.py), on the simulator: two
stations in one process on one simulated band, the relay (ohs-libs
shack_packet tools/packet_relay.py) on a local port. The receiver never
transmits and answers by relay; the sender's transfer arrives whole; the
monitor and the banner say which frames went by internet; losing the relay
disarms the sender. The library's own tests carry the 100 KB fake-Pluto
run and the relay's log."""
from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from pathlib import Path

import pytest

import shack_packet
from conftest import station_settings, wait_for
from shack_packet_app.controller import Controller
from shack_packet_app.simband import LiveRadio, SimBand

RELAY = Path(shack_packet.__file__).resolve().parents[2] / "tools" / "packet_relay.py"
pytestmark = pytest.mark.skipif(not RELAY.is_file(), reason="needs an ohs-libs checkout with tools/packet_relay.py")


def _relay_module():
    spec = importlib.util.spec_from_file_location("packet_relay", RELAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Relay:
    """The relay in a thread of its own; `kill()` as a box restart: every
    connection dropped at once."""

    def __init__(self, log_dir):
        self.relay = _relay_module().Relay(Path(log_dir))
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.server = self.loop.run_until_complete(self.relay.serve("127.0.0.1", 0))
            self.port = self.server.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(5)

    def kill(self):
        def go():
            self.server.close()
            for sess in list(self.relay.sessions.values()):
                for c in list(sess.clients.values()):
                    c.writer.transport.abort()
        self.loop.call_soon_threadsafe(go)

    def stop(self):
        self.kill()
        time.sleep(0.1)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


class Friend:
    """A second operator radio on the same simulated band (SimBand has one
    operator radio; this is the other station's), started and stopped with
    the band's owner."""

    def __init__(self, band):
        self.band = band
        self._radio = None

    def radio(self):
        if self._radio is None:
            self._radio = LiveRadio(self.band, "friend")
            self.band.medium.radios.append(self._radio)
        return self._radio

    def start(self):
        return self

    def stop(self):
        pass


@pytest.fixture
def two(tmp_path):
    band = SimBand(55.95, -3.2, neighbours=False)
    relay = Relay(tmp_path / "relay")
    sender = Controller(station_settings(callsign="GM4BRI", station__ssid=1), band_factory=lambda: band)
    receiver = Controller(station_settings(callsign="GM0OTB", station__ssid=1), band_factory=lambda: Friend(band))
    for c in (sender, receiver):
        c.start()
        assert wait_for(lambda c=c: c.link_state == "open", 5)
    yield sender, receiver, relay
    for c in (sender, receiver):
        c.stop()
    band.stop()
    relay.stop()


def _join(ctl, relay, role, peer):
    ls = ctl.settings.linktest
    ls.relay, ls.session, ls.role, ls.peer = f"127.0.0.1:{relay.port}", "sim-test", role, peer
    ok, why, _ = ctl.linktest.join(ls, wait=True)
    assert ok, why


def _events(ctl):
    out = []
    while not ctl.events.empty():
        out.append(ctl.events.get_nowait())
    return out


def test_a_transfer_with_the_answers_by_relay(two):
    sender, receiver, relay = two
    _join(receiver, relay, "rf-receiver", "GM4BRI-1")
    # The RF receiver may not arm while it is joined: no antenna on its TX port.
    assert receiver.arm(5)[:2] == (False, "linktest-receiver")
    assert receiver.status()["linktest"]["state"] == "waiting"
    _join(sender, relay, "rf-sender", "GM0OTB-1")
    assert wait_for(lambda: sender.linktest.state == "ready" and receiver.linktest.state == "ready", 5)
    assert sender.arm(5)[0]
    ok, why = sender.linktest.send_test(2)
    assert ok, why
    assert wait_for(lambda: (receiver.linktest.view().get("received") or {}).get("ok") is True, 90), \
        receiver.linktest.view()
    assert wait_for(lambda: sender.linktest.view()["transfer"]["done"], 30)
    first = sender.linktest.view()["transfer"]
    assert first["acked"] == first["size"] and 0 < first["bps"] <= 1200
    # A second test on the link still up from the first: its own bytes over
    # its own time (0.2.2 counted both tests' bytes and showed twice the
    # rate, more than the 1200 baud channel can carry).
    ok, why = sender.linktest.send_test(1)
    assert ok, why
    assert wait_for(lambda: sender.linktest.view()["transfer"]["done"], 60)
    second = sender.linktest.view()["transfer"]
    assert second["acked"] == second["size"] < first["size"] and 0 < second["bps"] <= 1200
    assert wait_for(lambda: (receiver.linktest.view().get("received") or {}).get("ok") is True, 30)
    assert receiver.linktest.view()["received"]["bytes"] == second["size"]
    assert receiver.engine.channel.stats.transmissions == 0
    assert receiver.engine.stats["rp_sent"] > 0 and sender.engine.stats["rp_heard"] == receiver.engine.stats["rp_sent"]
    # The monitor at each end marks what went by internet.
    with sender._lock:
        heard = [r for r in sender.rows if r.direction == "rx" and r.channel == "internet"]
    with receiver._lock:
        sent = [r for r in receiver.rows if r.direction == "tx" and r.channel == "internet"]
        on_air = [r for r in receiver.rows if r.direction == "tx" and r.channel == "radio"]
    assert heard and sent and not on_air
    banner = sender.status()["linktest"]
    assert banner == {"state": "ready", "role": "rf-sender", "relay": f"127.0.0.1:{relay.port}",
                      "session": "sim-test", "peer": "GM0OTB-1", "why": ""}
    # The other end's receive side, from its telemetry.
    assert wait_for(lambda: (sender.linktest.view().get("peer_rx") or {}).get("heard_by_mode"), 5)
    assert sender.linktest.view()["peer_frames"]
    receiver.linktest.leave()
    assert receiver.status()["linktest"] is None and receiver.engine.return_paths == {}
    # Each end kept its own log, and a diagnostics bundle carries the newest.
    from shack_packet_app import diagnostics
    text = diagnostics.contents(receiver)["linktest.jsonl"]
    assert '"kind":"rx-frame"' in text and '"kind":"env"' in text


def test_losing_the_relay_disarms_the_sender_and_says_why(two):
    sender, receiver, relay = two
    _join(receiver, relay, "rf-receiver", "GM4BRI-1")
    _join(sender, relay, "rf-sender", "GM0OTB-1")
    assert wait_for(lambda: sender.linktest.state == "ready", 5)
    assert sender.arm(5)[0]
    assert sender.linktest.send_test(50)[0]
    assert wait_for(lambda: (receiver.linktest.view().get("received") or {}).get("bytes", 0) > 500, 60)
    _events(sender)
    relay.kill()
    assert wait_for(lambda: sender.linktest.state == "lost", 3)
    assert not sender.status()["armed"]
    conn = sender.linktest.view()
    assert conn["link"]["state"] == "disconnected"
    kinds = [(e.kind, e.reason) for e in _events(sender)]
    assert ("linktest", "lost") in kinds and ("tx", "linktest-lost") in kinds
    assert sender.status()["linktest"]["state"] == "lost"
    sent = sender.engine.channel.stats.transmissions
    time.sleep(2)
    assert sender.engine.channel.stats.transmissions == sent


def test_a_relay_that_is_not_there_fails_plainly(two, tmp_path):
    sender, _receiver, _relay = two
    ls = sender.settings.linktest
    ls.relay, ls.session, ls.role, ls.peer = "127.0.0.1:9", "nobody", "rf-sender", "GM0OTB-1"
    assert sender.linktest.join(ls, wait=True)[0]
    assert sender.linktest.state == "failed" and sender.linktest.why == "unreachable"
    assert sender.status()["linktest"] is None
    for bad, why in (({"peer": "GM4BRI-1"}, "linktest-peer"), ({"session": "x"}, "linktest-session"),
                     ({"relay": "host:port"}, "linktest-relay"), ({"role": "both"}, "linktest-role")):
        for k, v in {"relay": "127.0.0.1:9", "session": "nobody", "role": "rf-sender", "peer": "GM0OTB-1", **bad}.items():
            setattr(ls, k, v)
        assert sender.linktest.join(ls)[:2] == (False, why)


def test_an_old_link_test_file_with_pluto_modes_loads_into_the_fast_settings():
    """The link-test build before 0.2.1 kept the Pluto's modem as
    sdr_radio.pluto_modes; 0.2.1's Settings.fast replaced it."""
    from shack_packet_app import settings as settings_mod
    old = settings_mod.to_dict(station_settings())
    del old["fast"]
    old["sdr_radio"]["pluto_modes"] = "adaptive"
    s = settings_mod.from_dict(old)
    assert s.fast.adapt and not hasattr(s.sdr_radio, "pluto_modes")
    old["sdr_radio"]["pluto_modes"] = "gfsk76800"
    assert settings_mod.from_dict(old).fast.mode == "gfsk76800"
    old["fast"] = {"mode": "g3ruh9600"}                       # a file that has both keeps its own
    assert settings_mod.from_dict(old).fast.mode == "g3ruh9600"


def test_the_page_sets_the_fast_mode_and_window_and_says_the_band_plan(two, qapp):
    from shack_packet_app.ui.pages.linktest import ADAPT, DEFAULT_RELAY
    from shack_packet_app.ui.window import MainWindow
    sender, _receiver, _relay = two
    w = MainWindow(sender.settings, {}, controller=sender, persist=False)
    page = w.pages["linktest"]
    assert page.relay.text() == DEFAULT_RELAY                      # not paired, nothing typed
    assert page.window_k.value() == 7                              # AFSK 1200's (fastmodes.default_window)
    page.modes.setCurrentIndex(page.modes.findData(ADAPT))
    page.window_k.setValue(40)
    page.use_modes()
    assert w.settings.fast.adapt and w.settings.fast.window == 40 and not w.settings.fast.window_auto
    page.modes.setCurrentIndex(page.modes.findData("gfsk76800"))
    page.use_modes()
    assert not w.settings.fast.adapt and w.settings.fast.mode == "gfsk76800"
    w.settings.radio.kind = "pluto"                                # as it is shown, without opening one
    page._modes_now(w.settings)
    assert "gfsk76800, window 40" in page.modes_now.text()
    assert page.plan_now.objectName() == "bad"                     # 144.800 is no place for 83 kHz


def test_the_relay_comes_from_the_paired_controller_first(two, qapp):
    from shack_packet_app.ui.window import MainWindow
    sender, _receiver, _relay = two
    sender.settings.controller.url = "http://ohs-box.local:8800/"
    w = MainWindow(sender.settings, {}, controller=sender, persist=False)
    assert w.pages["linktest"].relay.text() == "ohs-box.local:30480"


def test_the_receiver_leaving_keeps_the_sender_joined_until_it_rejoins(two):
    """19/10/2026: the RF receiver's bye took the sender out of the session
    too. Now the sender's link is dropped (nothing more is sent) but it
    stays joined, says why, and carries on when the receiver joins again."""
    sender, receiver, relay = two
    _join(receiver, relay, "rf-receiver", "GM4BRI-1")
    _join(sender, relay, "rf-sender", "GM0OTB-1")
    assert wait_for(lambda: sender.linktest.state == "ready", 5)
    assert sender.arm(5)[0]
    assert sender.linktest.send_test(50)[0]
    assert wait_for(lambda: (receiver.linktest.view().get("received") or {}).get("bytes", 0) > 300, 60)
    _events(sender)
    receiver.linktest.leave()
    assert wait_for(lambda: sender.linktest.state == "waiting", 3)
    assert sender.linktest.view()["link"]["state"] == "disconnected"
    banner = sender.status()["linktest"]
    assert banner["state"] == "waiting" and banner["why"] == "peer-left"
    assert ("linktest", "peer-left") in [(e.kind, e.reason) for e in _events(sender)]
    sent = sender.engine.channel.stats.transmissions
    time.sleep(1.5)
    assert sender.engine.channel.stats.transmissions == sent
    assert sender.linktest.client.state == "waiting"
    _join(receiver, relay, "rf-receiver", "GM4BRI-1")
    assert wait_for(lambda: sender.linktest.state == "ready", 5)
    assert sender.status()["linktest"]["why"] == ""
    assert sender.linktest.send_test(1)[0]
    assert wait_for(lambda: (receiver.linktest.view().get("received") or {}).get("ok") is True, 60)
