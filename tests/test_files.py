"""Files and pictures through the station core, on the simulator: G4SIMF
(simfiles.py) takes a file and sends a picture back by the same OHS and
YAPP exchanges as on the air; nothing moves until transmit is armed;
7PLUS through the manager; the airtime arithmetic."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import host, simfiles
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand


def band():
    return SimBand(55.95, -3.2, first_beacon_s=(200.0, 250.0), beacon_every_s=600)


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


def _connected(c):
    sess = c.connect(simfiles.CALL)
    assert wait_for(lambda: sess.connected, 30)
    return sess


def test_a_file_waits_for_arming_then_goes_and_is_checked(ctl, tmp_path):
    c = ctl()
    src = tmp_path / "notes.txt"
    src.write_bytes(b"de GM4BRI, a file for G4SIMF\n" * 300)
    sess = c.connect(simfiles.CALL)
    # Disarmed: the session cannot even connect, so no file can be offered.
    wait_for(lambda: False, 1.0)
    with pytest.raises(ValueError, match="not-connected"):
        c.files.send(sess.id, src)
    c.arm(5)
    assert wait_for(lambda: sess.connected, 30)
    t = c.files.send(sess.id, src)
    assert t.compress and t.protocol == "ohs"
    assert wait_for(lambda: t.state == "done", 60), (t.state, t.reason)

    def said():
        return [ln.text for ln in sess.lines if ln.direction == "rx"]
    # G4SIMF said so in the chat, and the chat never saw a protocol byte.
    assert wait_for(lambda: any(f"Received notes.txt, {src.stat().st_size} bytes, checked" in x for x in said()), 20)
    assert not any("\x01" in x for x in said())
    assert "G4SIMF" in c.files.peers                           # remembered as an OHS station
    events = []
    while not c.events.empty():
        e = c.events.get()
        if e.kind == "file":
            events.append(e.reason)
    assert "started" in events and "done" in events


def test_a_picture_from_the_simulator_waits_for_the_operator_and_lands_in_the_folder(ctl, tmp_path):
    s = station_settings()
    s.files.folder = str(tmp_path / "inbox")
    c = ctl(s)
    c.arm(5)
    sess = _connected(c)
    c.send(sess.id, "PIC")
    assert wait_for(lambda: c.files.offers, 30)
    offer = c.files.offers.popleft()
    assert (offer.peer, offer.name, offer.mime, offer.state) == ("G4SIMF", "g4sim-test-card.png", "image/png",
                                                                 "offered")
    assert not (tmp_path / "inbox").exists()                    # nothing written before the answer
    c.files.accept(offer)
    assert wait_for(lambda: offer.state == "done", 60), (offer.state, offer.reason)
    assert offer.path == tmp_path / "inbox" / "g4sim-test-card.png"
    assert offer.path.read_bytes() == simfiles.test_card()
    assert not offer.compress                                   # a PNG is packed already


def test_yapp_from_the_simulator(ctl, tmp_path):
    c = ctl()
    c.arm(5)
    sess = _connected(c)
    c.send(sess.id, "YAPP")
    assert wait_for(lambda: c.files.offers, 30)
    offer = c.files.offers.popleft()
    assert offer.protocol == "yapp"
    c.files.accept(offer)
    assert wait_for(lambda: offer.state == "done", 60), (offer.state, offer.reason)
    assert offer.path.read_bytes() == simfiles.TEXT
    # The stream is text again afterwards.
    c.send(sess.id, "?")
    assert wait_for(lambda: any("Commands: PIC" in ln.text for ln in sess.lines), 20)


def test_declining_writes_nothing_and_a_too_big_offer_is_never_asked(ctl, tmp_path):
    s = station_settings()
    s.files.folder = str(tmp_path / "inbox")
    s.files.max_mb = 1
    c = ctl(s)
    c.arm(5)
    sess = _connected(c)
    c.send(sess.id, "PIC")
    assert wait_for(lambda: c.files.offers, 30)
    offer = c.files.offers.popleft()
    c.files.decline(offer)
    assert offer.state == "declined"
    wait_for(lambda: False, 1.0)
    assert not (tmp_path / "inbox").exists() or not any((tmp_path / "inbox").iterdir())


def test_sevenplus_through_the_manager(ctl, tmp_path):
    c = ctl(station_settings(files__folder=str(tmp_path / "inbox")))
    import random
    src = tmp_path / "QSL card.png"
    src.write_bytes(random.Random(8).randbytes(5000))
    parts = c.files.sevenplus_encode(src, tmp_path / "out", lines=20)
    assert len(parts) > 1 and all(p.suffix.startswith(".p") for p in parts)
    # Read back as a BBS would hand them over: one message's text each, CR line ends.
    texts = [p.read_bytes().replace(b"\n", b"\r") for p in reversed(parts)]
    result, saved = c.files.sevenplus_decode(texts)
    assert result.complete and saved == tmp_path / "inbox" / "QSL card.png"
    assert saved.read_bytes() == src.read_bytes()
    result, saved = c.files.sevenplus_decode([parts[0]])                  # a Path: read from disk
    assert not result.complete and saved is None and result.missing


def test_estimates_follow_the_radio_and_a_finished_transfer_is_kept_as_a_measurement(ctl, tmp_path):
    from shack_packet.filexfer import Transfer
    c = ctl()
    rows = c.files.estimates(50_000, peer="G4SIMF")
    assert [r.mode for r in rows if r.available] == ["afsk1200"]               # the simulator: 1200 only
    assert [r.mode for r in rows if r.current] == ["afsk1200"]
    assert all(r.measured is None for r in rows)
    text = tmp_path / "log.txt"
    text.write_bytes(b"de GM4BRI the packet station is QRV on 70cm\n" * 500)
    assert c.files.wire_size(text) < text.stat().st_size / 4
    assert c.files.wire_size(text, protocol="yapp") == text.stat().st_size
    # A transfer finishes: 20 KB, half as fast again as the model says.
    model = next(r for r in rows if r.mode == "afsk1200")
    t = Transfer(id="0a0b0c0d", direction="out", peer="G4SIMF", name="x.jpg", size=20_000, sha256="")
    t.wire_bytes = 20_000 + 20 * 20
    t.started = 100.0
    from shack_packet_app import airtime
    params, timing = airtime.link_params(c.settings, "afsk1200")
    t.finished = 100.0 + 1.5 * airtime.transfer_s(20_000, "afsk1200", params, timing)
    t.state = "done"
    c.files._event(t, "done", {})
    row = next(r for r in c.files.estimates(50_000, peer="G4SIMF") if r.mode == "afsk1200")
    assert row.measured is not None and row.best_s == pytest.approx(1.5 * model.good_s, rel=0.02)
    assert (host.data_dir() / "throughput.json").is_file()


def test_disconnecting_part_way_and_connecting_again_resumes_by_itself(ctl, tmp_path):
    import random
    c = ctl()
    c.arm(5)
    sess = _connected(c)
    data = random.Random(4).randbytes(6000)                   # about a minute at 1200 baud
    t = c.files.send(sess.id, tmp_path / "big.bin", data=data)
    assert wait_for(lambda: t.state == "sending" and t.done > 1500, 60), (t.state, t.done)
    c.disconnect(sess.id)
    assert wait_for(lambda: t.state == "interrupted", 60), (t.state, t.reason)
    assert t.resumable and t.done < len(data)
    first_run = t.done
    again = c.connect(simfiles.CALL)
    # Nothing to press: both stations know each other as OHS stations now,
    # say HELLO on connecting, and the file is offered again and taken
    # where G4SIMF's part ends.
    assert wait_for(lambda: t.state == "done", 120), (t.state, t.reason)
    assert t.start_at >= first_run - 1024 and t.start_at > 0
    assert t.session.conn is again.conn
