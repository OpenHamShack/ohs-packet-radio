"""The station behind the window, on the simulator (no radio, no RF), the
synthetic SDR band and shack_rig's FakeIcom."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import discover
from shack_packet_app.controller import Controller
from shack_packet_app.simband import FIXED, NODE, SimBand


def band():
    return SimBand(55.95, -3.2, first_beacon_s=(20.0, 25.0), beacon_every_s=60)


@pytest.fixture
def ctl():
    made = []

    def make(s=None, **kw):
        c = Controller(s or station_settings(), band_factory=band, **kw)
        made.append(c)
        c.start()
        assert wait_for(lambda: c.link_state == "open", 5)
        return c
    yield make
    for c in made:
        c.stop()



def test_transmit_stays_off_until_armed(ctl):
    c = ctl()
    c.send_message(FIXED, "are you there")
    sess = c.connect(NODE)
    # Two seconds of trying: the channel refuses every key, so nothing of
    # ours reaches the air, and the neighbours hear only each other.
    assert wait_for(lambda: c.tx_refusals.get("transmit-disabled", 0) >= 1, 5)
    before = sum(e.stats["heard"] for e in c.band.engines)
    wait_for(lambda: False, 1.5)
    ours = [r for r in c.rows if r.direction == "tx"]
    assert ours == []
    assert not sess.connected
    assert c.status()["armed"] is False
    assert sum(e.stats["heard"] for e in c.band.engines) >= before    # the band still works

    ok, why, _ = c.arm(1)
    assert ok and why == ""
    assert wait_for(lambda: any(r.direction == "tx" for r in c.rows), 5)


def test_arm_refusals_name_the_gate(ctl):
    c = ctl(station_settings(set_up=False))
    assert c.arm()[1] == "not-set-up"
    c2 = ctl(station_settings(callsign=""))
    assert c2.arm()[1] == "no-callsign"
    c3 = ctl(station_settings(licence_class=""))
    ok, why, detail = c3.arm()
    assert not ok and why == "band-plan" and "licence class" in detail


def test_chat_with_the_simulated_node(ctl):
    c = ctl()
    c.arm(5)
    sess = c.connect(NODE)
    assert wait_for(lambda: sess.connected, 15)
    assert wait_for(lambda: any("Welcome to GB7SIM" in l.text for l in sess.lines), 10)
    c.send(sess.id, "hello node")
    assert wait_for(lambda: any(l.text == "You said: hello node" for l in sess.lines), 15)
    c.disconnect(sess.id)
    assert wait_for(lambda: sess.state == "disconnected", 15)


def test_an_empty_or_blank_chat_line_is_never_sent(ctl):
    c = ctl()
    c.arm(5)
    sess = c.connect(NODE)
    assert wait_for(lambda: sess.connected, 15)
    sent = []
    real_send = sess.conn.send
    sess.conn.send = lambda data: (sent.append(data), real_send(data))[1]
    lines_before = len(sess.lines)
    for blank in ("", " ", "\r", "\t \n"):
        assert c.send(sess.id, blank) is False
    assert sent == [] and not any(l.direction == "tx" for l in sess.lines[lines_before:])
    assert c.send(sess.id, "a real line") is True
    assert sent == [b"a real line\r"]


def test_the_node_greets_again_after_a_sabm_resets_the_link():
    # When a caller's first round trip outruns its T1 (a loaded computer
    # does that now and then), its SABM retry crosses the node's UA and
    # resets the link, which throws away the node's queued greeting. Here
    # the caller sends that second SABM on purpose, once connected: the
    # node must greet again rather than leave the caller at a silent node.
    from shack_packet.engine import Engine, Runner
    from shack_packet.identity import Station
    sim = SimBand(55.95, -3.2, first_beacon_s=(60.0, 70.0)).start()
    caller = Engine(Station.of("GM4BRI", tx_enabled=True), sim.radio(), clock=sim.clock)
    runner = Runner(caller).start()
    try:
        got, events = [], []
        conn = caller.connect(NODE)
        conn.on_data = got.append
        conn.on_event = lambda e: events.append(e.kind)
        assert wait_for(lambda: b"Welcome to GB7SIM" in b"".join(got), 15)
        got.clear()
        with caller.lock:
            conn.link.connect()                 # SABM on a connected link
        caller._wake()
        assert wait_for(lambda: b"Welcome to GB7SIM" in b"".join(got), 15)
        assert events.count("connected") >= 2
    finally:
        runner.stop()
        sim.stop()


def test_aprs_message_is_acknowledged_and_answered(ctl):
    c = ctl()
    c.arm(5)
    rows = c.send_message(FIXED, "test message")
    assert rows[0].state in ("queued", "sent")
    assert wait_for(lambda: rows[0].state == "acked", 15)
    assert wait_for(lambda: any(m.direction == "in" and m.peer == FIXED for m in c.messages), 15)


def test_disarming_ends_the_arming_and_expiry_is_reported(ctl):
    c = ctl()
    c.arm(1)
    c.disarm()
    assert not c.status()["armed"]
    kinds = []
    while not c.events.empty():
        e = c.events.get_nowait()
        kinds.append((e.kind, e.reason))
    assert ("tx", "armed") in kinds and ("tx", "disarmed") in kinds


def test_the_guard_refuses_encrypted_ip(ctl):
    c = ctl(station_settings(ip__enabled=True, ip__address="44.131.1.1", ip__network="44.131.0.0/16"))
    c.arm(5)
    # A TCP SYN to port 443: refused before anything is queued.
    import struct
    from shack_packet.ip import ipv4_checksum
    tcp = struct.pack(">HHIIBBHHH", 40000, 443, 1, 0, 0x50, 0x02, 1024, 0, 0)
    hdr = bytearray(struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 1, 0, 64, 6, 0,
                                bytes([44, 131, 1, 1]), bytes([44, 131, 1, 2])))
    hdr[10:12] = struct.pack(">H", ipv4_checksum(bytes(hdr)))
    assert c.ip.send(bytes(hdr) + tcp) is False
    assert wait_for(lambda: len(c.guard_log) == 1, 2)
    assert c.guard_log[0][1] == "denied-port"


def test_ping_reply_is_matched():
    from shack_packet_app.ipnet import Pinger, echo_request

    class FakeIp:
        address = "44.131.1.1"
        stats = {}

        def __init__(self):
            self.sent = []

        def send(self, packet):
            self.sent.append(packet)
            return True
    ip = FakeIp()
    p = Pinger(ip)
    run = p.ping("44.131.1.2", count=1, interval_s=0)
    assert wait_for(lambda: ip.sent, 2)
    req = ip.sent[0]
    # The far station's answer: the same ident and sequence, type 0.
    reply = bytearray(echo_request("44.131.1.2", "44.131.1.1", run.ident, 1, b"OpenHamShack"))
    reply[20] = 0
    assert p.packet_in(bytes(reply))
    assert run.answered == 1 and run.results[0].rtt_s is not None
    assert req[9] == 1 and req[20] == 8


def test_sdr_monitor_on_the_synthetic_band_feeds_the_monitor(ctl):
    c = ctl(station_settings(sdr__source="synthetic"))
    assert wait_for(lambda: any(r.direction == "sdr" for r in c.rows), 20)
    assert wait_for(lambda: any(h.call == "M0TST-9" for h in c.heard()), 20)
    c.stop_sdr()
    assert not c.sdr.running


def test_icom_on_the_lan_is_found_by_are_you_there():
    from shack_rig.icom.fake import FakeIcom
    loop = asyncio.new_event_loop()
    radio = FakeIcom()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(radio.start())
        ready.set()
        loop.run_forever()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    try:
        found = discover.find_icoms([("127.0.0.1", radio.control_port)], broadcast=False, timeout_s=0.5)
        assert [(f.host, f.port) for f in found] == [("127.0.0.1", radio.control_port)]
    finally:
        asyncio.run_coroutine_threadsafe(radio.stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)


def test_nothing_listens(ctl, monkeypatch):
    listened, bound = [], []
    real_listen, real_bind = socket.socket.listen, socket.socket.bind

    def listen(self, *a):
        listened.append(self)
        return real_listen(self, *a)

    def bind(self, addr):
        bound.append(addr)
        return real_bind(self, addr)
    monkeypatch.setattr(socket.socket, "listen", listen)
    monkeypatch.setattr(socket.socket, "bind", bind)
    c = ctl(station_settings(sdr__source="synthetic", ip__enabled=True, ip__address="44.131.1.1"))
    c.arm(5)
    c.connect(NODE)
    wait_for(lambda: False, 1.0)
    c.stop()
    assert listened == []
    assert bound == []


class LinePort:
    """A serial port as the PTT driver sees it, recording the lines."""

    def __init__(self, device):
        self.device = device
        self.opened = self.closed = False
        self.rts = False
        self.history: list[tuple[float, str, bool]] = []

    def open(self):
        self.opened = True

    def set_line(self, name, on):
        if not self.opened or self.closed:
            return False
        if name == "rts":
            self.rts = on
        self.history.append((time.monotonic(), name, on))
        return True

    def close(self):
        self.rts = False
        self.closed = True


class JumpClock:
    """time.monotonic, but able to jump forward the way a long wait would."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset


@pytest.fixture
def soundcard_station():
    """A real SoundcardLink, PTT on RTS, on shack_rig's FakeBackend: the
    keying path a sound-card radio has, with nothing attached."""
    from shack_rig import sound
    made = []

    def make():
        backend = sound.FakeBackend([("USB Audio CODEC", 1, 1, [48000])], realtime=True)
        ports = []

        def open_port(device):
            ports.append(LinePort(device))
            return ports[-1]
        clock = JumpClock()
        s = station_settings(radio__kind="soundcard", radio__audio_in="USB Audio CODEC",
                             radio__audio_out="USB Audio CODEC", radio__ptt="rts", radio__ptt_device="/dev/ttyFAKE0")
        c = Controller(s, clock=clock, sound_backend=backend, open_port=open_port)
        made.append((c, backend))
        c.start()
        assert wait_for(lambda: c.link_state == "open", 5), c.link_error_detail
        assert ports and not ports[0].rts, "PTT is low from the moment the port opens"
        return c, ports[0], clock
    yield make
    for c, backend in made:
        c.stop()
        backend.close()


def test_an_expired_arming_is_refused_at_the_key_not_just_in_the_window(soundcard_station):
    c, port, clock = soundcard_station()
    ok, _, _ = c.arm(1)
    assert ok
    # A minute and a bit passes with nothing asking: no timer disarmed us.
    clock.offset += 61
    c.send_message(FIXED, "after the arming ran out")
    assert wait_for(lambda: c.tx_refusals.get("transmit-disabled", 0) >= 1, 5)
    wait_for(lambda: False, 1.0)
    assert not any(on for _t, line, on in port.history if line == "rts"), "the radio was never keyed"
    assert c.status()["armed"] is False and c.status()["armedRemainingS"] == 0
    kinds = []
    while not c.events.empty():
        e = c.events.get_nowait()
        kinds.append((e.kind, e.reason))
    assert ("tx", "expired") in kinds


def test_expiry_during_a_transmission_unkeys_the_radio_at_once(soundcard_station):
    c, port, clock = soundcard_station()
    assert c.arm(1)[0]
    c.send_message(FIXED, "a message long enough to be on the air for most of a second " * 2)
    assert wait_for(lambda: port.rts, 5), "armed, the radio keys"
    keyed_at = time.monotonic()
    clock.offset += 61
    assert wait_for(lambda: not port.rts, 2)
    # TXDELAY alone is longer than this: the frame was cut, not finished.
    assert time.monotonic() - keyed_at < 0.25
    assert c.status()["armed"] is False
    wait_for(lambda: False, 1.5)
    ups = [t for t, line, on in port.history if line == "rts" and on]
    assert len(ups) == 1, "nothing keys again after the expiry"


def test_the_turnaround_is_refused_without_both_confirmations(soundcard_station):
    from shack_packet.radios import turnaround
    c, port, _clock = soundcard_station()
    assert c.arm(1)[0]
    for word in ("", "yes", "dummy-load"):
        with pytest.raises(PermissionError):
            c.measure_turnaround(word)
    # The library refuses on its own too, whoever calls it, before it
    # looks at the probe.
    with pytest.raises(Exception, match="dummy load"):
        turnaround.measure(c.link, None)
    wait_for(lambda: False, 0.5)
    assert not any(on for _t, line, on in port.history if line == "rts"), "nothing keyed"


def test_a_turnaround_that_sees_no_carrier_says_so_and_saves_nothing(soundcard_station, monkeypatch):
    # Ray's 0.1.0, 18/10/2026: the meter never showed the carrier, measure()
    # had no key-up, and float(None) raised in the controller.
    from shack_packet.radios import turnaround
    c, _port, _clock = soundcard_station()
    before = c.settings.radio.turnaround_s
    monkeypatch.setattr(turnaround, "RigPowerProbe", lambda link: None)
    monkeypatch.setattr(turnaround, "measure", lambda link, probe, confirm="": turnaround.Measurement())
    c.measure_turnaround(turnaround.CONFIRM).join(5)
    got = []
    while not c.events.empty():
        e = c.events.get_nowait()
        got.append((e.kind, e.reason, dict(e.detail or {})))
    assert ("tx", "turnaround-failed", {"detail": "the radio's power meter never showed the carrier"}) in got
    assert c.settings.radio.turnaround_s == before


def test_an_icom_on_the_network_with_no_address_is_refused_once():
    from shack_packet_app.links import LinkError, make_link
    from shack_packet_app.settings import RadioSettings
    radio = RadioSettings()
    radio.kind, radio.transport, radio.host = "ic-9700", "lan", "  "
    with pytest.raises(LinkError) as e:
        make_link(radio, station=None, frequency_hz=144_800_000, transmit=False)
    assert e.value.reason == "setup" and "network address" in e.value.detail


# -- the radio set up for data, and put back (PLAN-0.2 §3.3) ----------------------


class LoopIcom:
    """A FakeIcom on a loop of its own, as a real radio runs apart from us."""

    def __init__(self):
        from shack_rig.icom.fake import FakeIcom
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.radio = FakeIcom(username="op", password="secret")
        asyncio.run_coroutine_threadsafe(self.radio.start(), self.loop).result(5)

    def settings(self):
        s = station_settings(set_up=False)
        s.radio.kind, s.radio.transport = "ic-9700", "lan"
        s.radio.host, s.radio.port, s.radio.username = "127.0.0.1", self.radio.control_port, "op"
        return s

    def close(self):
        asyncio.run_coroutine_threadsafe(self.radio.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def icom():
    f = LoopIcom()
    yield f
    f.close()


def test_a_refused_icom_login_fails_at_once_and_is_not_tried_again(icom, folders):
    """GM0OTB, 19/10/2026: a wrong password was tried about every 1.25 s for
    ever. Now the open fails at once with its own reason, the radio sees one
    login, and only Reconnect asks again."""
    tries = []
    accepts = icom.radio.accepts
    icom.radio.accepts = lambda u, p: tries.append(1) or accepts(u, p)
    c = Controller(icom.settings(), secrets={"icom_password": "wrong"})
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "failed", 10)
        assert c.link_error == "radio-auth" and "username and password" in c.link_error_detail
        assert c.status()["linkError"] == "radio-auth"
        time.sleep(3)
        assert len(tries) == 1
        c.reconnect()
        assert wait_for(lambda: len(tries) == 2 and c.link_state == "failed", 10)
    finally:
        c.stop()


def test_the_app_sets_an_icom_up_and_reports_every_setting(icom, folders):
    radio = icom.radio
    c = Controller(icom.settings(), secrets={"icom_password": "secret"})
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 15)
        assert wait_for(lambda: c.radio_setup["report"], 5)
        lines = {ln["key"]: ln for ln in c.radio_setup["report"]}
        assert lines["nr"]["status"] == "set" and (lines["nr"]["before"], lines["nr"]["after"]) == ("on", "off")
        assert lines["data_mod"]["after"] == "LAN"
        assert radio.functions[0x40] == 0 and radio.data_mod == 0x05
        assert list((folders / "data" / "radio-setup").glob("*.json")), "saved under the app's data folder"
    finally:
        c.stop()
    assert radio.functions[0x40] == 1 and radio.data_mod == 0x00, "put back when the app stops"
    assert wait_for(lambda: c.radio_setup["restored"], 5)
    assert {ln["status"] for ln in c.radio_setup["restored"]} == {"restored"}
    assert not list((folders / "data" / "radio-setup").glob("*.json"))


def test_after_a_crash_the_next_start_puts_the_radio_back_first(icom, folders):
    radio = icom.radio
    first = Controller(icom.settings(), secrets={"icom_password": "secret"})
    first.start()
    assert wait_for(lambda: first.link_state == "open", 15)
    first.link._data_setup = None                       # the process dies: nothing is put back
    first.stop()
    assert radio.data_mod == 0x05 and radio.functions[0x40] == 0
    second = Controller(icom.settings(), secrets={"icom_password": "secret"})
    second.start()
    try:
        assert wait_for(lambda: second.link_state == "open", 15)
        assert wait_for(lambda: second.radio_setup["report"], 5)
        assert {ln["key"] for ln in second.radio_setup["recovered"]} >= {"nr", "data_mod"}
        assert lines_with(second.radio_setup["report"], "nr")["before"] == "on", "found as the operator left it"
    finally:
        second.stop()
    assert radio.data_mod == 0x00 and radio.functions[0x40] == 1


def lines_with(lines, key):
    return next(ln for ln in lines if ln["key"] == key)
