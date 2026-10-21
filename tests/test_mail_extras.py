"""0.3.0 workstream E on the simulator: packet mail delivered to M0SIM's
mailbox and collected from the pretend home BBS, Winlink email through the
pretend gateway with an attachment, the notices they make, and the APRS
extras - a typed-in position beaconing, the iGate both ways against a
fake APRS-IS, objects on the air - each only once transmit is armed."""
from __future__ import annotations

import json
import socket

import pytest

from conftest import station_settings, wait_for
from shack_packet import b2f
from shack_packet_app import notify
from shack_packet_app.controller import Controller
from shack_packet_app.settings_extra import WINLINK_SECRET
from shack_packet_app.simband import FIXED, SIM_BBS, SIM_RMS, SimBand


def band():
    return SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0), beacon_every_s=120)


@pytest.fixture
def ctl():
    made = []

    def make(s=None, secrets=None, **kw):
        c = Controller(s or station_settings(), secrets=secrets or {}, band_factory=band, **kw)
        made.append(c)
        c.start()
        assert wait_for(lambda: c.link_state == "open", 5)
        return c
    yield make
    for c in made:
        c.stop()


def drain(c) -> list:
    out = []
    while not c.events.empty():
        out.append(c.events.get_nowait())
    return out


def test_packet_mail_waits_for_arming_then_goes_straight_to_m0sim(ctl):
    c = ctl()
    m = c.mailclient.compose("m0sim", "Sked", "2 m at 2000?")
    wait_for(lambda: False, 2.0)
    assert c.mailclient.outbox.get(m.id).state == "queued"          # not armed: nothing goes
    c.arm(5)
    assert wait_for(lambda: c.mailclient.outbox.get(m.id).state == "delivered", 60)
    assert c.mailclient.outbox.get(m.id).route == "M0SIM-1"
    got = c.band.friend_mailbox.store.all()
    assert [(x.sender, x.subject, x.body) for x in got] == [("GM4BRI", "Sked", "2 m at 2000?")]
    events = [e for e in drain(c) if e.kind == "mail" and e.reason == "mail-delivered"]
    assert events and events[0].detail["to"] == "M0SIM" and events[0].detail["route"] == "M0SIM-1"
    n = notify.notice_for(events[0], "GM4BRI")
    assert n == notify.Notice("mail_status", "M0SIM", "Sked", "delivered") and n.page == "mailbox"


def test_mail_is_collected_from_the_home_bbs_and_notifies(ctl):
    c = ctl(station_settings(mail__home_bbs=f"{SIM_BBS}-1"))
    c.band.bbs_store.post("P", FIXED, "GM4BRI", SIM_BBS, "Waiting for you", "Collected from the BBS")
    assert c.mailclient.collect_now() == "not-armed"
    c.arm(5)
    assert wait_for(lambda: any(m.subject == "Waiting for you" for m in c.mail()), 60)
    # The library keeps the message before it emits mail-received, so the
    # event can be a moment behind what c.mail() shows: wait for it rather
    # than draining once (failed 1 run in 2 under a loaded full suite).
    received = []
    assert wait_for(lambda: received.extend(e for e in drain(c) if e.kind == "mail" and e.reason == "mail-received")
                    or bool(received), 10)
    assert received[-1].detail["for_me"]
    assert notify.notice_for(received[-1], "GM4BRI").kind == "mailbox"


def test_winlink_through_the_simulated_gateway(ctl):
    s = station_settings(winlink__gateways=[f"{SIM_RMS}-10"], winlink__gateway=f"{SIM_RMS}-10")
    c = ctl(s)
    assert c.mailclient.winlink_connect() == "no-password"
    c.secrets[WINLINK_SECRET] = "secret"
    msg = c.mailclient.winlink_compose(["friend@example.com"], "From the shack", "Hello by radio",
                                       [b2f.Attachment("notes.txt", b"x" * 900)])
    with pytest.raises(ValueError):
        c.mailclient.winlink_compose(["a@b.c"], "Too big", "", [b2f.Attachment("big.bin", b"x" * 30_000)])
    assert [i.mid for i in c.mailclient.winlink.items("outbox")] == [msg.mid]
    assert c.mailclient.winlink_connect() == "not-armed"
    c.arm(5)
    assert c.mailclient.winlink_connect() == ""
    assert wait_for(lambda: c.mailclient.wl_last.get("state") in ("done", "failed"), 120)
    assert c.mailclient.wl_last["state"] == "done", c.mailclient.wl_last
    assert (c.mailclient.wl_last["sent"], c.mailclient.wl_last["received"]) == (1, 1)
    sent = c.band.rms.received[0]
    assert sent.to == ["SMTP:friend@example.com"] and sent.files[0].data == b"x" * 900
    assert c.mailclient.winlink.items("outbox") == []
    assert [i.mid for i in c.mailclient.winlink.items("sent")] == [msg.mid]
    inbox = c.mailclient.winlink.items("inbox")
    assert inbox[0].subject == "Welcome to the simulated gateway" and not inbox[0].read
    got = [e for e in drain(c) if e.kind == "mail" and e.reason == "winlink-received"]
    assert notify.notice_for(got[0], "GM4BRI") == notify.Notice("mailbox", "test@example.com",
                                                                 "Welcome to the simulated gateway")


def test_the_winlink_password_is_never_in_settings_or_on_the_air(ctl):
    from shack_packet_app import diagnostics
    s = station_settings(winlink__gateways=[f"{SIM_RMS}-10"], winlink__gateway=f"{SIM_RMS}-10")
    c = ctl(s, secrets={WINLINK_SECRET: "Hunter2Secret"})
    c.arm(5)
    c.mailclient.winlink_compose(["G4SIM"], "Hi", "body")
    assert c.mailclient.winlink_connect() == ""
    assert wait_for(lambda: c.mailclient.wl_last.get("state") in ("done", "failed"), 120)
    with c._lock:
        on_air = " ".join(r.text for r in c.rows)
    assert "Hunter2Secret" not in on_air and ";PR:" in on_air
    files = diagnostics.contents(c)
    assert all("Hunter2Secret" not in v for v in files.values())


class FakeAprsIs:
    """aprsis.AprsIsClient's shape, with no network: lines sent are kept,
    lines from "the server" are handed in by the test."""

    made: list = []

    def __init__(self, call, code, *, on_line, filter="", server=None, connect=None, **kw):
        self.call, self.code, self.on_line, self.filter, self.server = call, code, on_line, filter, server
        self.sent: list[str] = []
        self.state = "idle"
        self.error = ""
        FakeAprsIs.made.append(self)

    def start(self):
        self.state = "verified"

    def stop(self):
        self.state = "idle"

    def send(self, line):
        self.sent.append(line)
        return True


def test_igate_gates_what_it_hears_and_sends_messages_for_locals_only_when_armed(ctl):
    from shack_packet_app import aprsextras
    FakeAprsIs.made.clear()
    s = station_settings(igate__enabled=True, igate__tx=True, igate__filter="m/25")
    c = Controller(s, band_factory=band)
    c.aprsx = aprsextras.AprsExtras(c, aprs_is=FakeAprsIs)
    try:
        c.start()
        assert wait_for(lambda: c.link_state == "open", 5)
        client = FakeAprsIs.made[0]
        assert (client.call, client.code, client.filter) == ("GM4BRI", 21156, "m/25")
        assert client.server == ("rotate.aprs2.net", 14580)
        c.band.aprs[FIXED].send_message("G0XYZ", "hello via the igate")
        assert wait_for(lambda: any(line.startswith("G4SIM>") for line in client.sent), 30)
        assert any(",qAR,GM4BRI:" in line for line in client.sent)
        line = "W1AW>APRS,TCPIP*,qAC,T2X::G4SIM    :from the internet{7"
        client.on_line(line)
        wait_for(lambda: False, 2.0)
        with c._lock:
            tx = [r.text for r in c.rows if r.direction == "tx"]
        assert not any("from the internet" in t for t in tx)            # not armed: refused at the channel
        c.arm(5)
        client.on_line(line.replace("{7", "{8"))
        assert wait_for(lambda: any("}W1AW>APRS,TCPIP,GM4BRI*::G4SIM    :from the internet{8" in r.text
                                    for r in list(c.rows) if r.direction == "tx"), 15)
        # Position reports from the internet never go on the air.
        client.on_line("W1AW>APRS,TCPIP*:!4140.00N/07200.00W-far away")
        wait_for(lambda: False, 1.0)
        assert not any("far away" in r.text for r in list(c.rows) if r.direction == "tx")
    finally:
        c.stop()


def test_a_typed_in_position_moves_the_station_and_objects_go_out_when_armed(ctl):
    s = station_settings(gps__source="manual", gps__manual="55.9000, -3.1000", aprs__beacon=True)
    c = ctl(s)
    assert c.position() == (55.9, -3.1)
    beacon = c.aprs._beacons[0].beacon
    assert beacon.source is not None and beacon.source().lat == 55.9
    assert c.aprsx.place({"name": "NET", "lat": 55.95, "lon": -3.19, "symbol": "/r", "comment": "Net 2000"}) == ""
    assert c.aprsx.place({"name": "much too long a name", "lat": 0, "lon": 0}) == "bad-object"
    assert [d["name"] for d in c.settings.objects.items] == ["NET"]
    wait_for(lambda: False, 1.0)
    assert not any(r.text.startswith("GM4BRI>APZOHS") and ";NET" in r.text for r in list(c.rows))
    c.arm(5)
    assert wait_for(lambda: any(";NET      *" in r.text for r in list(c.rows) if r.direction == "tx"), 30)
    c.aprsx.kill("NET")
    assert c.settings.objects.items == []
    assert wait_for(lambda: any(";NET      _" in r.text for r in list(c.rows) if r.direction == "tx"), 60)


def test_weather_from_a_file_and_telemetry_deltas(ctl, tmp_path):
    wx = tmp_path / "wx.json"
    wx.write_text(json.dumps({"temp_c": 11.5, "humidity": 70, "wind_mph": 10}))
    c = ctl(station_settings(weather__source="file", weather__file=str(wx), telemetry__enabled=True))
    assert wait_for(lambda: c.aprsx.status()["wx"], 5)
    lat, lon, readings, when = c.aprsx._weather_now()
    assert readings == {"temp_c": 11.5, "humidity_pct": 70.0, "wind_kmh": 16.1} and (lat, lon) == c.position()
    first = c.aprsx._telemetry_now()
    c.aprsx._counts.rx += 5
    assert c.aprsx._telemetry_now()[0] == 5 and len(first) == 5


def test_nothing_listens_with_everything_on(ctl, monkeypatch, tmp_path):
    listened = []
    real_listen = socket.socket.listen
    monkeypatch.setattr(socket.socket, "listen", lambda self, *a: (listened.append(self), real_listen(self, *a)))
    from shack_packet_app import aprsextras
    FakeAprsIs.made.clear()
    s = station_settings(igate__enabled=True, gps__source="manual", gps__manual="55.9,-3.1",
                         weather__source="file", weather__file=str(tmp_path / "none.json"), telemetry__enabled=True,
                         mail__home_bbs=f"{SIM_BBS}-1", mail__collect_every_min=1)
    c = Controller(s, band_factory=band)
    c.aprsx = aprsextras.AprsExtras(c, aprs_is=FakeAprsIs)
    try:
        c.start()
        c.arm(5)
        c.mailclient.collect_now()
        wait_for(lambda: False, 2.0)
    finally:
        c.stop()
    assert listened == []
