"""Nodes, BBSs, scripts, conferences, the hosted room and the log, driven
through the station core on the simulator against GB7SIM (ohs-libs'
pretend BPQ node) and the pretend stations: what an operator does, and
what the helpers read back. No radio, no RF."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet.engine import Engine, Runner
from shack_packet.identity import Station
from shack_packet_app import logbook
from shack_packet_app.controller import Controller
from shack_packet_app.scripts import Script
from shack_packet_app.simband import NODE, ONWARD, SimBand


def band():
    return SimBand(55.95, -3.2, first_beacon_s=(60.0, 70.0), beacon_every_s=120, first_nodes_s=0.5)


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


def text(sess):
    return "\n".join(l.text for l in sess.lines if l.direction == "rx")


def sent(sess):
    return [l.text for l in sess.lines if l.direction == "tx"]


BBS_SCRIPT = """# GB7SIM's BBS
connect GB7SIM
wait ? for help
send BBS
wait de GB7SIM>
"""


def test_a_connect_script_waits_for_arming_then_runs_its_steps(ctl):
    c = ctl()
    sess = c.run_script(Script("BBS", BBS_SCRIPT))
    # Disarmed: the connection waits, the script waits for the link, and
    # nothing of ours is sent.
    assert wait_for(lambda: c.tx_refusals.get("transmit-disabled", 0) >= 1, 5)
    assert sess.script.state == "waiting-link" and sent(sess) == []
    assert not any(r.direction == "tx" for r in c.rows)
    c.arm(5)
    assert wait_for(lambda: sess.script.state == "done", 30), (sess.script.state, text(sess))
    assert sent(sess) == ["BBS"] and sess.kind == "bbs"


def test_a_script_whose_wait_never_comes_true_stops_and_sends_no_more(ctl):
    c = ctl()
    c.arm(5)
    sess = c.run_script(Script("x", "connect GB7SIM\ntimeout 2\nwait no such text\nsend BBS\n"))
    assert wait_for(lambda: sess.script.state == "failed", 20)
    assert sess.script.reason == "script-timeout" and sent(sess) == []
    assert any(l.text == "note:script-timeout" for l in sess.lines)


def test_the_node_table_fills_from_broadcasts_and_from_a_nodes_listing(ctl):
    c = ctl()
    # GB7SIM's broadcast, heard with transmit disarmed: listening needs no arming.
    assert wait_for(lambda: c.nodes.find("SJ") is not None, 10)
    assert c.nodes.find(NODE).direct and c.nodes.find(ONWARD).neighbour == NODE
    c.arm(5)
    sess = c.connect(NODE)
    assert wait_for(lambda: "? for help" in text(sess), 20)
    c.send(sess.id, "C GB7SJ")
    assert wait_for(lambda: "Welcome to GB7SJ" in text(sess), 20)
    c.send(sess.id, "N")
    # GB7SJ lists a node GB7SIM does not advertise: from the listing,
    # reached through GB7SJ, the node that printed it.
    assert wait_for(lambda: c.nodes.find("ZZSIM") is not None, 20), text(sess)
    assert c.nodes.find("ZZSIM").neighbour == ONWARD and c.nodes.find("ZZSIM").source == "nodes"
    assert sess.kind == "node"


def test_mail_is_listed_through_the_paging_question_read_sent_and_killed(ctl):
    c = ctl()
    c.band.node.leave_for("GM4BRI", "Sked", "Are you on tonight?")
    c.arm(5)
    sess = c.run_script(Script("BBS", BBS_SCRIPT))
    assert wait_for(lambda: sess.script.state == "done", 30)
    assert wait_for(lambda: sess.reader.prompt_kind == "bbs", 10)
    assert c.page(sess.id) is False                      # not at a paging question: nothing sent
    assert c.helper(sess.id, "L", expect="list")
    assert wait_for(lambda: sess.reader.prompt_kind == "page", 20), text(sess)
    assert c.page(sess.id)                               # a bare CR, only here
    assert sent(sess)[-1] == ""
    assert wait_for(lambda: len(sess.reader.mail) == 15, 30), text(sess)
    first = sess.reader.mail[0]
    assert (first.subject, first.to, first.type) == ("Sked", "GM4BRI", "P")
    assert c.helper(sess.id, f"R {first.number}", expect="read", number=first.number)
    assert wait_for(lambda: "Are you on tonight?" in sess.reader.read_text, 20)
    assert c.send_mail(sess.id, "G0ABC", "Hello", "Two lines\nof text")
    assert wait_for(lambda: sess.script.state == "done", 30), text(sess)
    assert wait_for(lambda: any(m.subject == "Hello" and m.body == "Two lines\rof text"
                                for m in c.band.node.mail), 20)
    mine = next(m for m in c.band.node.mail if m.subject == "Hello")
    assert c.helper(sess.id, f"K {mine.number}", expect="kill", number=mine.number)
    assert wait_for(lambda: mine not in c.band.node.mail, 20)


def test_joining_a_node_conference_lists_who_is_there(ctl):
    c = ctl()
    c.arm(5)
    sess = c.connect(NODE)
    assert wait_for(lambda: "? for help" in text(sess), 20)
    assert c.join_conference(sess.id) and sent(sess) == ["CHAT"]
    assert wait_for(lambda: sess.reader.participants.calls == ["G4SIM", "G8SIM"], 20), text(sess)
    assert sess.kind == "conference"


def test_a_finished_session_is_logged_when_the_operator_asked_for_it(ctl, tmp_path):
    s = station_settings()
    s.log.auto_bbs = True
    c = ctl(s)
    c.arm(5)
    sess = c.run_script(Script("BBS", BBS_SCRIPT))
    assert wait_for(lambda: sess.script.state == "done", 30)
    capture = tmp_path / "capture.txt"
    c.capture_start(sess.id, capture)
    c.send(sess.id, "B")
    assert wait_for(lambda: sess.state == "disconnected", 20)
    assert wait_for(lambda: len(c.logbook.all()) == 1, 5)
    got = c.logbook.all()[0]
    assert (got.call, got.kind, got.band, got.modem, got.my_call) == ("GB7SIM", "bbs", "2m", "afsk1200", "GM4BRI")
    assert got.time_off >= got.time_on > 0
    adi = logbook.adif(c.logbook.all())
    assert "<CALL:6>GB7SIM" in adi and "<MODE:3>PKT" in adi and "<BAND:2>2m" in adi
    assert "> B" in capture.read_text()                  # the capture has both directions
    # A chat that is not logged automatically is still the operator's to log.
    sess2 = c.connect(NODE)
    assert wait_for(lambda: sess2.connected, 20)
    assert c.log_session(sess2.id, comment="by hand").comment == "by hand"
    c.disconnect(sess2.id)
    assert wait_for(lambda: sess2.state == "disconnected", 20)
    assert len(c.logbook.all()) == 2                     # a chat: not logged again by itself


def test_the_room_answers_only_while_open_and_armed_and_relays_lines(ctl):
    s = station_settings()
    s.room.name, s.room.topic, s.log.auto_room = "Shack room", "testing", True
    c = ctl(s)
    sim = c.band
    # A second pretend station on the band, calling the room.
    guest_engine = Engine(Station.of("G0GST", tx_enabled=True), sim.radio_for("G0GST"), clock=sim.clock)
    runner = Runner(guest_engine).start()
    try:
        assert c.room.start() == ""
        got = []
        conn = guest_engine.connect("GM4BRI-4")
        conn.on_data = got.append
        # Disarmed: not answered at all (not even a DM: that would be a
        # transmission), and nothing of ours on the air.
        wait_for(lambda: False, 3)
        assert not conn.connected and got == [] and c.room.members() == ["GM4BRI"]
        assert not any(r.direction == "tx" for r in c.rows)
        # Armed: the caller's next SABM is let in.
        c.arm(5)
        assert wait_for(lambda: b"Welcome to the SHACK ROOM conference" in b"".join(got), 20)
        assert c.room.members() == ["GM4BRI", "G0GST"]
        conn.send(b"hello room\r")
        assert wait_for(lambda: any(l.kind == "say" and l.text == "hello room" for l in c.room.lines), 20)
        assert c.room.say("Welcome")
        assert wait_for(lambda: b"GM4BRI: Welcome" in b"".join(got), 20)
        conn.send(b"/quit\r")
        assert wait_for(lambda: any(l.kind == "leave" for l in c.room.lines), 20)
        assert wait_for(lambda: any(x.kind == "room" and x.call == "G0GST" for x in c.logbook.all()), 5)
        c.room.stop()
        assert not c.room.open
    finally:
        runner.stop()


def test_a_session_into_another_stations_room_is_read_as_a_conference():
    from shack_packet_app.bbs import SessionReader
    r = SessionReader("GM4BRI")
    for line in ["*** Welcome to the G0ABC-4 conference. 3 here: G0ABC-4 M0AAA GM4BRI.",
                 "*** /help for the commands, /quit to leave.", "*** G4XYZ joined", "M0AAA: hello"]:
        r.line(line)
    assert r.kind == "conference" and r.participants.calls == ["G0ABC-4", "M0AAA", "G4XYZ"]
    r.set_kind("chat", by_hand=True)
    r.line("SIM:GB7SIM} Connected to BBS")
    assert r.kind == "chat"                              # chosen by hand: not guessed again
