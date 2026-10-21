"""The sysop page's API as the controller's proxy reaches it: nothing
without this app's proxy secret, each route at its level, the platform's
release request and busy gate, and an arm that makes the arming person's
device the acting identity for every over. A real HTTP server on a
loopback port of its own; the radio is the simulator's, the broker fake."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from test_box_station import SYSOP, Caller, FakeBroker, World, make_box

from shack_packet_box.server import serve

SECRET = "s3cret-for-the-test"


@pytest.fixture
def served():
    world, broker = World(), FakeBroker()
    box, keyed, _ = make_box(world, broker)
    srv = serve(box, SECRET, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    yield world, broker, box, base
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None, *, level="view", device="dev_PHONE", secret=SECRET, user="brian"):
    headers = {"X-OpenHamShack-User": user, "X-OpenHamShack-Level": level, "X-OpenHamShack-Device": device,
               "X-OpenHamShack-Auth": "device", "X-OpenHamShack-Admin": "1", "X-OpenHamShack-Reason": "ok",
               "X-OpenHamShack-Transmit": "full" if level == "transmit" else "none"}
    if secret is not None:
        headers["X-OpenHamShack-Proxy-Secret"] = secret
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode()


def test_nothing_without_the_proxy_secret(served):
    world, broker, box, base = served
    assert call(base, "api/status", secret=None)[1]["code"] == "proxy_required"
    assert call(base, "api/status", secret="wrong")[0] == 401
    assert call(base, "", secret=None)[0] == 401


def test_the_page_and_status_at_view(served):
    world, broker, box, base = served
    status, page = call(base, "")
    assert status == 200 and "Packet BBS</title>" in page and 'src="app.js"' in page
    assert call(base, "app.js")[0] == 200 and call(base, "style.css")[0] == 200
    status, s = call(base, "api/status")
    assert status == 200 and s["bbs"] == "GM4BRI-1" and s["radio"]["reserved"] and not s["transmit"]["armed"]


def test_levels_guard_changes_and_private_mail(served):
    world, broker, box, base = served
    box.store.post("P", "G0ABC", "G4XYZ", "GM4BRI", "Private", "for G4XYZ")
    box.store.post("B", "G0ABC", "ALL", "GBR", "Rally", "Sunday")
    assert [m["subject"] for m in call(base, "api/messages")[1]["messages"]] == ["Rally", "Private"]
    assert call(base, "api/messages/1")[1]["code"] == "level_too_low"
    assert call(base, "api/messages/2")[1]["message"]["body"] == "Sunday"
    assert call(base, "api/messages/1", level="control")[1]["message"]["body"] == "for G4XYZ"
    assert call(base, "api/messages/2/kill", {})[0] == 403
    assert call(base, "api/messages/2/kill", {}, level="control")[1] == {"killed": True}
    assert call(base, "api/users/G0BAD", {"barred": True}, level="control")[1]["barred"] is True
    assert box.store.user("G0BAD").barred
    assert call(base, "api/tx/arm", {}, level="control")[0] == 403
    assert any("barred G0BAD (by brian, dev_PHONE)" in e["text"] for e in box.log)


def test_arm_by_api_acts_for_the_arming_device(served):
    world, broker, box, base = served
    status, s = call(base, "api/tx/arm", {}, level="transmit", device="dev_SYSOP")
    assert status == 200 and s["transmit"]["armed"] and s["transmit"]["armedBy"] == "brian"
    c = Caller(world, world.station("G0ABC"), "GM4BRI-1")
    c.wait_for("de GM4BRI-1>")
    assert {e[1] for e in broker.onair_log if e[0] == "on"} == {"dev_SYSOP"}
    assert call(base, "api/tx/disarm", {}, level="control")[1]["transmit"]["armed"] is False


def test_the_room_from_the_page(served):
    world, broker, box, base = served
    c = Caller(world, world.station("G0ABC"), "GM4BRI-2")
    box.arm(SYSOP)
    c.wait_for("/help")
    assert call(base, "api/room/say", {"text": "hello all"}, level="control")[0] == 200
    c.wait_for("SYSOP: hello all")
    call(base, "api/room/topic", {"topic": "Sunday net"}, level="control")
    c.wait_for("set the topic: Sunday net")
    room = call(base, "api/room")[1]
    assert room["members"] == ["G0ABC"] and room["topic"] == "Sunday net"
    assert call(base, "api/room/remove", {"call": "G0ABC"}, level="control")[1] == {"removed": True}


def test_the_platform_routes(served):
    world, broker, box, base = served
    assert call(base, "api/shack/busy", level="none")[1] == {"busy": False, "reasons": []}
    assert call(base, "api/status", level="none")[0] == 403
    status, body = call(base, "api/shack/release", {"device": "ic9700-1", "for": "shack-icom"}, level="none")
    assert status == 409 and body["code"] == "reserved"
    status, body = call(base, "api/shack/release", {"device": "ic9700-1", "stop": True}, level="none")
    assert status == 202 and broker.claims == {}
    status, body = call(base, "api/radio/claim", {}, level="control")
    assert status == 200 and body["radio"]["claim"] == "held" and broker.claims


def test_the_page_catalog_and_the_gettext_template_are_current(served):
    world, broker, box, base = served
    status, cat = call(base, "i18n/en.json")
    assert status == 200 and cat["title"] == "Packet BBS" and cat["badge"]["armed"] == "ARMED by {who}"
    assert call(base, "../server.py")[0] == 404 and call(base, "static/app.js")[0] == 404
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("box_pot", Path(__file__).resolve().parent.parent / "tools" / "box_pot.py")
    box_pot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(box_pot)
    assert box_pot.main(["--check"]) == 0
