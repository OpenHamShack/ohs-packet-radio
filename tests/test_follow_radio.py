"""The station follows the radio's own VFO and mode knob (0.3.2, tuning.py).

Brian, 21/10/2026, on 0.3.1 with his IC-9700 over the LAN, armed on
144.925: "if I change the frequency on the radio it doesn't change on the
program". Against shack_rig's fakes - FakeIcom on the loopback (its dial
turned the way a real 9700 reports it, a CI-V transceive frame), the
TM-V71 and the FTX-1 on ptys - the channel, the window and the arming
follow the radio; nothing meant for the old channel is sent on the new
one; the operator's frequency survives a reconnect, a rebuild and the
close. No radio is touched."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from conftest import station_settings, wait_for
from shack_packet import Frame
from shack_packet.datalink import State
from shack_packet_app import settings as settings_mod
from shack_packet_app import tuning
from shack_packet_app.controller import Controller

pytest.importorskip("shack_rig")
from shack_rig import sound  # noqa: E402
from shack_rig.icom.fake import FakeIcom  # noqa: E402
from shack_rig.kenwood.fake import FakeTmV71  # noqa: E402
from shack_rig.yaesu.fake import FakeFtx1  # noqa: E402

CHANNEL = 144_925_000


class LoopIcom:
    """A FakeIcom on a loop of its own, as a real radio runs apart from us."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.radio = FakeIcom(username="op", password="secret")
        asyncio.run_coroutine_threadsafe(self.radio.start(), self.loop).result(5)

    def turn(self, hz=None, mode=None):
        """The operator's hand on the radio's knob."""
        self.loop.call_soon_threadsafe(self.radio.turn_dial, hz, mode)

    def close(self):
        asyncio.run_coroutine_threadsafe(self.radio.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def icom():
    f = LoopIcom()
    made = []

    def station(hz=CHANNEL):
        s = station_settings(channel__frequency_hz=hz, channel__preset="node-144925")
        s.radio.kind, s.radio.transport = "ic-9700", "lan"
        s.radio.host, s.radio.port, s.radio.username = "127.0.0.1", f.radio.control_port, "op"
        c = Controller(s, secrets={"icom_password": "secret"})
        made.append(c)
        c.start()
        assert wait_for(lambda: c.link_state == "open", 15), (c.link_error, c.link_error_detail)
        return c
    f.station = station
    yield f
    for c in made:
        c.stop()
    f.close()


def events(c, kind=None, reason=None):
    out = []
    while not c.events.empty():
        out.append(c.events.get_nowait())
    return [e for e in out if (kind is None or e.kind == kind) and (reason is None or e.reason == reason)]


def retuned(c, timeout=5):
    """The "retuned" event, once the new frequency has settled."""
    got = []
    wait_for(lambda: got.extend(events(c, "radio", "retuned")) or bool(got), timeout)
    return got[0] if got else None


def test_turning_the_icoms_dial_moves_the_station_and_keeps_it_armed_where_packet_is_allowed(icom):
    c = icom.station()
    ok, why, _ = c.arm(5)
    assert ok, why
    events(c)
    icom.turn(144_800_000)
    assert wait_for(lambda: c.status()["radioHz"] == 144_800_000, 3), "held while it settles"
    e = retuned(c)
    assert e is not None and e.detail["by"] == "radio" and e.detail["from"] == CHANNEL
    assert e.detail["armed"] and not e.detail["refusal"]
    assert (c.settings.channel.frequency_hz, c.settings.channel.preset) == (144_800_000, "aprs")
    st = c.status()
    assert st["frequencyHz"] == 144_800_000 and st["radioHz"] is None and st["armed"]
    assert c.link.frequency == 144_800_000, "a reconnect tunes here now, not back to 144.925"


def test_a_channel_where_packet_is_not_allowed_disarms_with_the_reason(icom, caplog):
    c = icom.station()
    assert c.arm(5)[0]
    events(c)
    icom.turn(145_900_000)                             # the satellite segment
    e = retuned(c)
    assert e is not None and e.detail["refusal"] == "band-plan" and "satellites" in e.detail["detail"]
    assert not c.status()["armed"]
    assert any("DISARMED" in r.getMessage() and "145.9000" in r.getMessage() for r in caplog.records)
    ok, why, _ = c.arm(5)
    assert not ok and why == "band-plan", "and Arm says the same there"


def test_nothing_meant_for_the_old_channel_goes_out_on_the_new_one(icom):
    """The safer of the two choices (tuning.py): what was queued, a
    connection and an APRS message awaiting its ack are cancelled with a
    notice, never re-sent on the new channel."""
    radio = icom.radio
    c = icom.station()
    assert c.arm(5)[0]
    row = c.send_message("G0ABC", "meant for 144.925")[0]
    msg = row.out
    assert wait_for(lambda: msg.state == "sent" and radio.ptt_commands.count(True) >= 1, 10)
    assert wait_for(lambda: not radio.ptt, 5)
    keys_before = radio.ptt_commands.count(True)
    icom.turn(144_800_000)
    assert wait_for(lambda: c.link.tuned()["freq"] == 144_800_000, 3)
    # While it settles: a connect attempt and a beacon reach the link, which
    # refuses to key on a channel nobody has confirmed.
    sess = c.connect("G4XYZ")
    c.engine.send_frame(Frame.from_monitor("GM4BRI>APRS:>queued while the radio moved"))
    e = retuned(c)
    assert e is not None and e.detail["links"] >= 1 and e.detail["messages"] == 1
    assert msg.state == "cancelled" and row.state == "cancelled"
    assert sess.conn.state is State.DISCONNECTED
    time.sleep(1.5)
    assert radio.ptt_commands.count(True) == keys_before, "not one key after the dial moved"
    assert c.tx_refusals["off-channel"] + e.detail["frames"] >= 1, "refused at the key, or cancelled"


def test_a_nudge_that_comes_back_changes_nothing(icom):
    c = icom.station()
    assert c.arm(5)[0]
    events(c)
    icom.turn(144_937_500)
    assert wait_for(lambda: c.status()["radioHz"] == 144_937_500, 3)
    icom.turn(CHANNEL)
    assert wait_for(lambda: c.status()["radioHz"] is None, 3)
    time.sleep(tuning.SETTLE_S + 0.3)
    assert events(c, "radio", "retuned") == []
    assert c.settings.channel.frequency_hz == CHANNEL and c.status()["armed"]


def test_the_mode_knob_off_fm_disarms_and_arm_waits_for_fm(icom):
    c = icom.station()
    assert c.arm(5)[0]
    events(c)
    icom.turn(None, 0x01)                              # USB
    assert wait_for(lambda: not c.status()["armed"], 3)
    # Disarmed a moment before the event is queued: wait for it.
    got = []
    assert wait_for(lambda: got.extend(events(c, "radio", "mode-changed")) or bool(got), 3)
    assert got[0].detail["mode"].startswith("USB") and got[0].detail["disarmed"]
    assert c.status()["radioModeBad"] and c.arm(5)[1] == "radio-mode"
    icom.turn(None, 0x05)                              # FM again
    assert wait_for(lambda: not c.status()["radioModeBad"], 3)
    assert c.arm(5)[0]


def test_the_operators_frequency_survives_a_reconnect_and_the_close(icom):
    radio = icom.radio
    c = icom.station()
    icom.turn(144_800_000)
    assert retuned(c) is not None
    c.reconnect()
    assert wait_for(lambda: c.link_state == "open", 15)
    assert radio.freq == 144_800_000, "not snapped back to the saved channel"
    c.stop()
    assert radio.freq == 144_800_000, "the operator's choice is left at close"
    assert radio.data_mod == 0x00 and (radio.mode, radio.data) == (0x01, 0x01), "the rest put back"
    restored = {ln["key"]: ln["status"] for ln in c.radio_setup["restored"]}
    # Kept at the reconnect's close; the new link then found the radio
    # already on its confirmed channel and had nothing to put back.
    assert restored.get("frequency") in (None, "kept") and restored["data_mod"] == "restored"


def test_a_channel_chosen_in_the_app_still_sets_the_radio_and_goes_back_at_close(icom):
    radio = icom.radio
    c = icom.station()
    new = c.settings.copy()
    new.channel.preset, new.channel.frequency_hz = "aprs", 144_800_000
    c.apply(new)
    assert wait_for(lambda: c.link_state == "open", 15) and radio.freq == 144_800_000
    time.sleep(tuning.SETTLE_S + 0.3)
    assert events(c, "radio", "retuned") == [], "its own tuning is not mistaken for the operator's"
    c.stop()
    assert radio.freq == 144_174_000, "the app's change, undone"


# -- the TM-V71 in the window, the FTX-1 rebuilt ----------------------------------------------------

CARD = "USB Audio Device"


@pytest.fixture
def tmv71():
    radio = FakeTmV71().start()
    be = sound.FakeBackend([(CARD, 1, 1, (48000,)), ("Air", 1, 0, (48000,))], realtime=True)
    port = radio.interface_port("rts")

    def open_port(device, baud=9600, bits=1):
        from shack_rig.serialio import SerialPort
        return port if device == "/dev/ttyDIGIRIG" else SerialPort(device, baud, stop_bits=bits)
    yield radio, be, open_port
    be.close()
    radio.stop()


def test_the_tmv71s_dial_moves_the_status_bar_the_strip_and_the_saved_channel(tmv71, qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from shack_packet_app.ui.window import MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    radio, be, open_port = tmv71
    s = station_settings(radio__kind="tm-v71", radio__cat_device=radio.device, radio__audio_in=CARD,
                         radio__audio_out=CARD, radio__ptt="rts", radio__ptt_device="/dev/ttyDIGIRIG",
                         channel__frequency_hz=144_800_000, channel__preset="aprs")
    c = Controller(s, sound_backend=be, open_port=open_port)
    w = MainWindow(s, {}, controller=c, persist=True)
    w.start(first_run_check=False)
    try:
        assert wait_for(lambda: c.link_state == "open", 10, app=qapp), (c.link_error, c.link_error_detail)
        assert wait_for(lambda: w.control_strip.preset.currentText() == "144.800 APRS"
                        or w.control_strip.preset.currentData() == ("aprs", 144_800_000), 3, app=qapp)
        radio.vfo[0][1] = f"{144_962_500:010d}"           # no preset there
        assert wait_for(lambda: "144.9625" in w.bar.freq.text() and "held" not in w.bar.freq.text(), 6, app=qapp)
        assert w.control_strip.preset.currentText() == "Other frequency (144.9625 MHz)"
        assert "144.9625 MHz" in w.statusBar().currentMessage()
        assert settings_mod.load().channel.frequency_hz == 144_962_500, "saved: the next start tunes here"
        radio.vfo[0][1] = f"{144_925_000:010d}"           # a preset's
        assert wait_for(lambda: w.control_strip.preset.currentData() == ("node-144925", 144_925_000), 6,
                        app=qapp)
        assert c.settings.channel.preset == "node-144925"
        # The data band put on a memory channel: transmit is held there.
        radio.band_mode[0] = 1
        assert wait_for(lambda: "MEMORY" in w.bar.freq.text(), 6, app=qapp)
        assert c.arm(5)[1] == "radio-memory"
        radio.band_mode[0] = 0
        assert wait_for(lambda: "held" not in w.bar.freq.text(), 6, app=qapp)
    finally:
        w.close()
    assert radio.freq(0) == 144_925_000, "left where the operator put it"


def test_the_ftx1_rebuilt_from_the_strip_tunes_to_where_the_operator_went(qapp):
    radio = FakeFtx1().start()
    codec = "FTX-1 USB Audio"
    be = sound.FakeBackend([(codec, 1, 1, (48000,))], realtime=True)
    s = station_settings(radio__kind="ftx-1", radio__cat_device=radio.device, radio__audio_in=codec,
                         radio__audio_out=codec, channel__frequency_hz=144_800_000)
    c = Controller(s, sound_backend=be)
    c.start()
    try:
        assert wait_for(lambda: c.link_state == "open", 10), (c.link_error, c.link_error_detail)
        radio.freq = 144_950_000
        assert retuned(c) is not None and c.settings.channel.frequency_hz == 144_950_000
        # Anything that rebuilds the station (the strip's mode, Settings)
        # starts from the followed channel, so it does not tune back.
        c.apply(c.settings.copy())
        assert wait_for(lambda: c.link_state == "open", 10)
        assert radio.freq == 144_950_000
    finally:
        c.stop()
        be.close()
        radio.stop()
