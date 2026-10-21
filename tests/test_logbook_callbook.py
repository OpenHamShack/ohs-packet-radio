"""The log's ADIF and CSV, the HamQTH callbook against a fake HamQTH
(its cache, offline use, rate, an expired session, a refused login, and
that the password never reaches a log), and connect scripts' parsing and
running against a fake session."""
from __future__ import annotations

import csv
import io
import logging

import pytest

from shack_packet_app import callbook, grid, logbook, scripts

T0 = 1_792_000_000.0          # 2026-10-14 17:46:40 UTC


def contact(**kw):
    base = dict(call="G0ABC", station="G0ABC-9", time_on=T0, time_off=T0 + 754, freq_hz=144_800_000,
                modem="afsk1200", kind="bbs", via="MB7UC", my_call="GM4BRI", my_grid="IO85JW")
    base.update(kw)
    return logbook.Contact(**base)


def adif_fields(record: str) -> dict:
    """An ADI record read back by its length counts, as a logger reads it."""
    out, i = {}, 0
    while True:
        i = record.find("<", i)
        if i < 0:
            return out
        j = record.find(">", i)
        tag = record[i + 1:j]
        if ":" not in tag:
            i = j
            continue
        name, n = tag.split(":")[:2]
        out[name] = record[j + 1:j + 1 + int(n)]
        i = j + 1 + int(n)


def test_adif_has_what_a_logger_needs_and_nothing_made_up():
    text = logbook.adif([contact(name="José", qth="Edinburgh")], version="0.3.0", now=T0)
    head, _, body = text.partition("<EOH>")
    assert adif_fields(head)["ADIF_VER"] == "3.1.4" and adif_fields(head)["PROGRAMVERSION"] == "0.3.0"
    rec = adif_fields(body.split("<EOR>")[0])
    assert rec["CALL"] == "G0ABC" and rec["MODE"] == "PKT" and "SUBMODE" not in rec
    assert (rec["QSO_DATE"], rec["TIME_ON"], rec["TIME_OFF"]) == ("20261014", "174640", "175914")
    assert rec["BAND"] == "2m" and rec["FREQ"] == "144.800000"
    assert "RST_SENT" not in rec and "RST_RCVD" not in rec
    assert rec["NAME"] == "Jose"                          # ASCII, as an .adi must be
    assert rec["COMMENT"] == "Packet bbs with G0ABC-9; via MB7UC; 1200 baud AFSK"
    assert (rec["STATION_CALLSIGN"], rec["MY_GRIDSQUARE"]) == ("GM4BRI", "IO85JW")


def test_bands_csv_and_the_filter():
    assert [logbook.band(f) for f in (144_800_000, 432_500_000, 50_000_000, 14_105_000, 1_000_000)] == \
        ["2m", "70cm", "6m", "20m", ""]
    assert logbook.speed_text("g3ruh9600") == "9600 baud G3RUH" and logbook.speed_text("4fsk76800") == \
        "76800 baud 4FSK"
    rows = list(csv.DictReader(io.StringIO(logbook.csv_text([contact()]))))
    assert rows[0]["call"] == "G0ABC" and rows[0]["date_on"] == "2026-10-14" and rows[0]["band"] == "2m"
    c = contact(comment="sked")
    assert logbook.matches(c, "g0abc sked") and logbook.matches(c, "2M") and not logbook.matches(c, "G4XYZ")
    assert not logbook.matches(c, since=T0 + 1) and logbook.matches(c, since=T0 - 1, until=T0 + 1)


def test_the_log_survives_a_restart_and_keeps_an_unreadable_file_aside(tmp_path):
    path = tmp_path / "logbook.json"
    book = logbook.LogBook(path)
    a = book.add(contact())
    book.add(contact(call="G4XYZ"))
    assert book.delete(a.id) and book.update(2, comment="edited")
    again = logbook.LogBook(path)
    assert [(c.call, c.comment) for c in again.all()] == [("G4XYZ", "edited")]
    assert again.add(contact()).id == 3
    path.write_text("{not json")
    fresh = logbook.LogBook(path)
    assert fresh.all() == [] and (tmp_path / "logbook.unreadable.json").exists()


# -- HamQTH ---------------------------------------------------------------------------------

SESSION = b"""<?xml version="1.0"?><HamQTH version="2.8" xmlns="https://www.hamqth.com">
<session><session_id>abc123</session_id></session></HamQTH>"""
REFUSED = b"""<?xml version="1.0"?><HamQTH version="2.8" xmlns="https://www.hamqth.com">
<session><error>Wrong user name or password</error></session></HamQTH>"""
EXPIRED = b"""<?xml version="1.0"?><HamQTH version="2.8" xmlns="https://www.hamqth.com">
<session><error>Session does not exist or expired</error></session></HamQTH>"""
NOT_FOUND = b"""<?xml version="1.0"?><HamQTH version="2.8" xmlns="https://www.hamqth.com">
<session><error>Callsign not found</error></session></HamQTH>"""


def found(call, grid_="IO91WM", name="Alan", qth="London"):
    return f"""<?xml version="1.0"?><HamQTH version="2.8" xmlns="https://www.hamqth.com">
<search><callsign>{call.lower()}</callsign><nick>Al</nick><adr_name>{name}</adr_name><qth>{qth}</qth>
<country>England</country><grid>{grid_.lower()}</grid><latitude>51.5</latitude><longitude>-0.12</longitude>
</search></HamQTH>""".encode()


class FakeHamQth:
    def __init__(self):
        self.urls = []
        self.login = SESSION
        self.down = False
        self.expire_next = False

    def __call__(self, url):
        self.urls.append(url)
        if self.down:
            raise OSError("network is unreachable")
        if "u=" in url:
            return self.login
        if self.expire_next:
            self.expire_next = False
            return EXPIRED
        if "callsign=G0ABC" in url:
            return found("G0ABC")
        return NOT_FOUND


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def book(tmp_path, fake, clock, **kw):
    return callbook.Callbook(tmp_path / "callbook.json", "gm4bri", "s3cret-pw", fetch=fake, clock=clock,
                             threaded=False, **kw)


def test_a_call_is_looked_up_once_cached_and_used_offline(tmp_path, caplog):
    fake, clock = FakeHamQth(), Clock()
    cb = book(tmp_path, fake, clock)
    assert cb.lookup("G0ABC-9") is None                   # queued, by its base call
    assert cb.lookup("G0ABC") is None and list(cb.queue) == ["G0ABC"]
    assert cb.run_one()
    e = cb.lookup("G0ABC-7")
    assert (e.found, e.name, e.qth, e.grid) == (True, "Alan", "London", "IO91WM")
    assert len(fake.urls) == 2                            # a login and one search
    d = cb.describe("G0ABC", "IO85JW")
    km, brg = grid.distance_bearing(grid.to_latlon("IO85JW"), (51.5, -0.12))
    assert d["km"] == pytest.approx(km) and 500 < d["km"] < 600 and 140 < d["bearing"] < 170
    # A second app start, offline, answers from the disk cache and asks nobody.
    fake.down = True
    cb2 = book(tmp_path, fake, clock)
    assert cb2.lookup("G0ABC").name == "Alan" and not cb2.queue
    # Stale after FOUND_DAYS: still shown, fetched again; a network
    # failure backs off and keeps the call queued.
    clock.t += (callbook.FOUND_DAYS + 1) * 86400
    assert cb2.lookup("G0ABC").name == "Alan" and list(cb2.queue) == ["G0ABC"]
    with caplog.at_level(logging.DEBUG):
        assert cb2.run_one()
    assert cb2.problem == "network" and list(cb2.queue) == ["G0ABC"] and not cb2.run_one()
    assert "s3cret-pw" not in caplog.text and "hamqth.com" not in caplog.text


def test_lookups_are_spaced_and_an_expired_session_logs_in_again(tmp_path):
    fake, clock = FakeHamQth(), Clock()
    cb = book(tmp_path, fake, clock, min_gap_s=2.0)
    cb.lookup("G0ABC")
    cb.lookup("G4XYZ")
    assert cb.run_one() and not cb.run_one()              # the second waits its turn
    clock.t += 2.0
    assert cb.run_one()
    assert cb.lookup("G4XYZ").found is False
    fake.expire_next = True                               # HamQTH dropped the session early
    clock.t += 3
    cb.lookup("G8NEW")
    assert cb.run_one() and cb.lookup("G8NEW").found is False
    assert sum("u=" in u for u in fake.urls) == 2         # logged in again once, and the lookup made


def test_a_refused_login_stops_lookups_until_the_account_changes(tmp_path, caplog):
    fake, clock = FakeHamQth(), Clock()
    fake.login = REFUSED
    cb = book(tmp_path, fake, clock)
    cb.lookup("G0ABC")
    with caplog.at_level(logging.DEBUG):
        assert cb.run_one()
    assert cb.problem == "auth" and "s3cret-pw" not in caplog.text
    clock.t += 100
    cb.lookup("G4XYZ")
    assert not cb.run_one() and len(fake.urls) == 1
    fake.login = SESSION
    cb.set_credentials("gm4bri", "new-pw")
    cb.lookup("G0ABC")
    clock.t += 100
    assert cb.run_one() and cb.lookup("G0ABC").found


def test_without_an_account_nothing_is_asked(tmp_path):
    fake = FakeHamQth()
    cb = callbook.Callbook(tmp_path / "c.json", "", "", fetch=fake, threaded=False)
    assert cb.lookup("G0ABC") is None and not cb.queue and not cb.run_one() and fake.urls == []


# -- connect scripts ------------------------------------------------------------------------

def test_scripts_are_checked_before_they_run():
    steps = scripts.parse("# c\nconnect GB7AAA via MB7UC, GB7BBB\nwait }\nsend C GB7CCC\nsend\nwait x|Y\n")
    assert [(s.verb, s.arg) for s in steps] == [("connect", "GB7AAA via MB7UC, GB7BBB"), ("wait", "}"),
                                                ("send", "C GB7CCC"), ("send", ""), ("wait", "x|Y")]
    assert scripts.target(steps) == ("GB7AAA", "MB7UC,GB7BBB")
    for bad, key in (("send X", "connect-not-first"), ("connect A\nconnect B", "connect-not-first"),
                     ("connect A\nfly away", "unknown-step"), ("connect A\nwait", "wait-needs-text"),
                     ("connect A\ntimeout soon", "bad-timeout"), ("# only", "empty"), ("connect", "connect-needs-call")):
        with pytest.raises(scripts.ScriptError) as e:
            scripts.parse(bad)
        assert e.value.key == key, bad


def test_a_script_sends_only_its_steps_in_order_and_gives_up_on_a_silent_wait():
    out = []
    run = scripts.ScriptRun("t", scripts.parse("connect A\nsend one\nwait READY|go\nsend two\nwait never\nsend three"),
                            out.append)
    run.heard("READY", 0.0)                               # before the link is up: ignored
    assert out == [] and run.state == "waiting-link"
    run.start(10.0)
    assert out == ["one"] and run.waiting_for == "READY|go"
    run.heard("...re", 11.0)
    run.heard("ady>", 12.0)                               # a prompt split across frames still matches
    assert out == ["one", "two"] and run.next_deadline() == 12.0 + scripts.TIMEOUT_S
    run.poll(12.0 + scripts.TIMEOUT_S)
    assert run.state == "failed" and run.reason == "script-timeout" and out == ["one", "two"]
    run.heard("never", 500.0)
    assert out == ["one", "two"]
