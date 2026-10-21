"""The controller's BBS as the store app runs it, on the simulated channel
and against a fake broker that answers as the Dock360 edition's does
(CONTROLLER-CONTRACT 7): two simulated stations use the BBS and the room
through their own engines, the radio is reserved while the BBS runs and
released when it stops, and nothing is keyed unless a person armed it and
the broker said yes. No real radio anywhere."""
from __future__ import annotations

import random

import pytest

from shack_packet.channel import ChannelConfig
from shack_packet.datalink import LinkConfig, State
from shack_packet.engine import Engine
from shack_packet.identity import Station
from shack_packet.sim import Medium, SimClock, Simulation
from shack_packet_app.ohsclient import radios_of
from shack_packet_box.settings import BoxSettings
from shack_packet_box.shack import BrokerClient
from shack_packet_box.station import BoxStation

T0 = 1_790_000_000.0
PROMPT = "de GM4BRI-1>"


class FakeBroker:
    """The broker's HTTP answers for one app and its devices: claims with
    leases, modes, on air (refused with no acting person, as v1 does),
    release, restart. `calls` records what the app asked."""

    def __init__(self, app="shack-packet"):
        self.app = app
        self.claims: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.onair_log: list[tuple] = []
        self.refuse_onair = ""
        self.restarting = False
        self.n = 0

    def request(self, method, path, body=None, headers=None):
        acting = (headers or {}).get("X-OpenHamShack-Acting-Device", "-")
        self.calls.append((method, path, dict(body or {}), acting))
        parts = path.strip("/").split("/")
        if method == "POST" and parts == ["broker", "claims"]:
            if self.restarting and not body.get("reclaim"):
                return 503, {"error": "restarting", "code": "broker_restarting", "retryAfter": 5}
            held = [c for c in self.claims.values() if c["device"] == body["device"]]
            if held:
                c = held[0]
                return 200, {"claimId": c["id"], "device": c["device"], "mode": c["mode"], "state": "granted",
                             "lease": {"seconds": 10, "heartbeatEvery": 2}}
            self.n += 1
            c = {"id": f"cl_{self.n}", "device": body["device"], "mode": body["mode"], "onAir": False,
                 "reserve": bool(body.get("reserve")), "reason": body.get("reason", ""), "acting": acting}
            self.claims[c["id"]] = c
            return 201, {"claimId": c["id"], "device": c["device"], "mode": c["mode"], "state": "granted",
                         "lease": {"seconds": 10, "heartbeatEvery": 2},
                         "access": {"reach": "network", "address": "198.51.100.70"}}
        cid = parts[2] if len(parts) > 2 else ""
        c = self.claims.get(cid)
        if c is None:
            return 404, {"error": "There is no such claim for this app.", "code": "not_found"}
        if method == "POST" and parts[3:] == ["heartbeat"]:
            return 200, {"state": "granted", "leaseSeconds": 10, "onAir": c["onAir"], "releaseRequested": False}
        if method == "POST" and parts[3:] == ["mode"]:
            c["mode"] = body["mode"]
            return 200, {"mode": c["mode"]}
        if method == "POST" and parts[3:] == ["onair"]:
            if not body.get("on"):
                c["onAir"] = False
                self.onair_log.append(("off", acting))
                return 200, {"onAir": False}
            if c["mode"] != "tx":
                return 409, {"error": "not held for transmit", "code": "not_holder"}
            if acting == "-" or self.refuse_onair:
                return 403, {"error": self.refuse_onair or "The person this app acts for may not transmit with it.",
                             "code": "level_too_low"}
            c["onAir"] = True
            self.onair_log.append(("on", acting, body.get("freqHz"), body.get("mode")))
            return 200, {"onAir": True, "lease": {"seconds": 6, "heartbeatEvery": 2}}
        if method == "DELETE":
            del self.claims[cid]
            return 200, {"released": True, "safeOff": "none"}
        return 400, {"error": "?", "code": "bad_request"}

    def device_rows(self):
        """GET broker/devices as a desktop app sees it."""
        rows = []
        for c in self.claims.values():
            rows.append({"id": c["device"], "class": c["device"].rsplit("-", 1)[0], "label": "IC-9700",
                         "identity": {"address": "198.51.100.70"}, "present": True, "txCapable": True,
                         "holder": {"app": self.app, "claimId": c["id"], "mode": c["mode"], "onAir": c["onAir"]},
                         "queue": []})
        return {"devices": rows}

    def restart(self):
        self.claims.clear()


class World:
    def __init__(self):
        self.clock = SimClock()
        self.medium = Medium(self.clock, "frames", seed=3)
        self.sim = Simulation(self.clock, self.medium)

    def wall(self):
        return T0 + self.clock.now

    def station(self, call):
        engine = Engine(Station.of(call, tx_enabled=True), self.medium.radio(call), channel=ChannelConfig(),
                        link=LinkConfig(), clock=self.clock, rng=random.Random(call))
        self.sim.add(engine)
        return engine

    def run(self, seconds, until=None):
        return self.sim.run(seconds, until)


def make_box(world, broker=None, **env):
    settings = BoxSettings.from_env({"BBS_CALLSIGN": "GM4BRI", "RADIO_KIND": "ic-9700", "TX_ALLOWED": "true",
                                     "OHS_PACKET_BOX_DATA": "/nonexistent", **env})
    keyed = []

    def factory(s, station, access, band):
        radio = world.medium.radio("GM4BRI")
        real_key = radio.key

        def key():
            keyed.append(world.clock.now)
            real_key()
        radio.key = key
        factory.access = access
        return radio

    from shack_packet.bbsstore import BbsStore
    store = BbsStore(home="GM4BRI", clock=world.wall)
    client = BrokerClient(broker.request, clock=world.clock) if broker else None
    box = BoxStation(settings, broker=client, link_factory=factory, clock=world.clock, wall=world.wall, store=store)
    box.build()
    assert box.radio_up(), box.error
    world.sim.add(box.engine)
    return box, keyed, factory


class Caller:
    def __init__(self, world, engine, target):
        self.world = world
        self.conn = engine.connect(target)
        self.text = bytearray()
        self.conn.on_data = self.text.extend

    def wait_for(self, words, seconds=120):
        ok = self.world.run(seconds, lambda: words.encode() in self.text)
        assert ok, bytes(self.text).decode(errors="replace")[-600:]

    def type(self, line, then=None):
        self.text.clear()
        self.conn.send((line + "\r").encode())
        if then is not None:
            self.wait_for(then)

    def screen(self):
        return bytes(self.text).decode()


SYSOP = {"user": "brian", "device": "dev_SYSOP", "level": "transmit"}


def test_the_bbs_reserves_its_radio_and_the_desktop_leaves_it_alone():
    world, broker = World(), FakeBroker()
    box, keyed, factory = make_box(world, broker)
    claim = next(iter(broker.claims.values()))
    assert (claim["device"], claim["mode"], claim["reserve"]) == ("ic9700-1", "rx", True)
    assert claim["reason"] == "BBS GM4BRI-1 on 144.9500 MHz"
    assert factory.access == {"reach": "network", "address": "198.51.100.70"}
    radio = radios_of(broker.device_rows())[0]
    assert radio.blocks and radio.state == "reserved" and radio.reserved_by == "Packet Radio BBS"
    box.stop()
    assert broker.claims == {}
    assert box.status()["radio"]["claim"] == "none"


def test_two_stations_use_the_bbs_and_the_room_with_transmit_armed():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker)
    assert box.arm(SYSOP) == ""
    assert next(iter(broker.claims.values()))["mode"] == "tx"
    a = Caller(world, world.station("G0ABC"), "GM4BRI-1")
    a.wait_for(PROMPT)
    a.type("SP G4XYZ", then="Subject:")
    a.type("Sked", then="/EX")
    a.type("2 m at 2000?")
    a.type("/EX", then=PROMPT)
    a.type("C", then="/help")
    b = Caller(world, world.station("G4XYZ"), "GM4BRI-1")
    b.wait_for(PROMPT)
    assert "You have 1 new messages" in b.screen()
    b.type("R 1", then=PROMPT)
    assert "2 m at 2000?" in b.screen()
    b.type("CONF", then="/help")
    a.wait_for("*** G4XYZ joined")
    b.type("hello Alan")
    a.wait_for("G4XYZ: hello Alan")
    assert box.status()["roomMembers"] == ["G0ABC", "G4XYZ"]
    # Every over went on the air through the broker, for the arming sysop,
    # and came off it again.
    ons = [e for e in broker.onair_log if e[0] == "on"]
    offs = [e for e in broker.onair_log if e[0] == "off"]
    assert ons and len(ons) == len(keyed) and len(offs) >= len(ons) - 1
    assert {e[1] for e in ons} == {"dev_SYSOP"} and {e[2] for e in ons} == {144_950_000}
    assert any(e["kind"] == "transmit" and "armed by brian" in e["text"] for e in box.log)


def test_unarmed_the_bbs_never_keys():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker)
    a = Caller(world, world.station("G0ABC"), "GM4BRI-1")
    world.run(120)
    assert PROMPT not in a.screen()
    assert keyed == [] and broker.onair_log == []
    assert box.engine.channel.stats.refused["transmit-disabled"] > 0


def test_arming_needs_the_setting_and_the_broker_can_still_refuse():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker, TX_ALLOWED="false")
    assert "TX_ALLOWED" in box.arm(SYSOP)
    world2, broker2 = World(), FakeBroker()
    box2, keyed2, _ = make_box(world2, broker2)
    broker2.refuse_onair = "The station's licence does not allow this transmission."
    assert box2.arm(SYSOP) == ""
    Caller(world2, world2.station("G0ABC"), "GM4BRI-1")
    world2.run(120)
    assert keyed2 == []
    assert any("licence does not allow" in e["text"] for e in box2.log)


def test_the_arm_expires():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker, ARM_HOURS="1")
    assert box.arm(SYSOP) == ""
    world.run(3601)
    box.tick()
    assert not box.station.armed and next(iter(broker.claims.values()))["mode"] == "rx"


def test_a_person_cannot_take_the_radio_but_an_admin_stop_can():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker)
    box.arm(SYSOP)
    status, body = box.release_request({"device": "ic9700-1", "for": "shack-icom", "stop": False})
    assert status == 409 and body["reserved"] and broker.claims
    status, body = box.release_request({"device": "ic9700-1", "stop": True})
    assert status == 202 and broker.claims == {} and not box.station.armed
    assert box.state == "radio-refused"


def test_a_broker_restart_is_survived_receive_only():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker)
    box.arm(SYSOP)
    broker.restart()
    broker.restarting = True
    world.run(3)
    box.tick()
    assert box.claim_state == "held"            # re-claimed during the grace, as reclaim
    assert not box.station.armed                # and not transmitting until a person arms again
    assert next(iter(broker.claims.values()))["mode"] == "rx"
    assert any(c[2].get("reclaim") for c in broker.calls if c[1] == "/broker/claims")


def test_a_real_radio_without_a_broker_is_refused():
    world = World()
    settings = BoxSettings.from_env({"BBS_CALLSIGN": "GM4BRI", "RADIO_KIND": "pluto"})
    from shack_packet.bbsstore import BbsStore
    box = BoxStation(settings, broker=None, link_factory=lambda *a: pytest.fail("opened"), clock=world.clock,
                     wall=world.wall, store=BbsStore(home="GM4BRI"))
    box.build()
    assert not box.radio_up() and box.state == "failed" and "broker" in box.error


def test_settings_are_checked_and_never_turn_transmit_on():
    s = BoxSettings.from_env({"BBS_CALLSIGN": "not a call", "RADIO_KIND": "flex", "BBS_SSID": "99",
                              "FWD_PARTNERS": "GB7XXX-1 via MB7ABC routes GB7XXX #79, nonsense!", "TX_ALLOWED": "maybe"})
    assert s.callsign == "" and s.radio_kind == "simulator" and s.bbs_ssid == 1 and not s.tx_allowed
    assert [p.call for p in s.partners] == ["GB7XXX-1"] and s.partners[0].via == ["MB7ABC"]
    assert len(s.problems) == 4
    ok = BoxSettings.from_env({"BBS_CALLSIGN": "gm4bri", "RADIO_KIND": "IC-9700", "ROOM_SSID": "1"})
    assert ok.callsign == "GM4BRI" and ok.radio_device == "ic9700-1" and ok.room_ssid == 0
    assert ok.default_id() == "GM4BRI-1 BBS, 144.95 MHz"


def test_arming_again_renews_the_arm():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker)
    assert box.arm(SYSOP) == ""
    first = box.station.expires
    world.run(3600)
    assert box.arm(SYSOP) == "" and box.station.expires == first + 3600
    world.run(23 * 3600)
    box.tick()
    assert box.station.armed                        # 24 h from the renewal, not the first arm
