"""The store app's HTTP side: the sysop page and its API, behind the
OpenHamShack controller's front door (CONTROLLER-CONTRACT 5.3).

Every request must carry this app's proxy secret, compared in constant
time before any other header is read; without it the answer is 401
`proxy_required`, whoever is asking (the container's port is reachable by
every other app on the controller). The platform's own calls - the
broker's release request and the busy gate - arrive with level `none` and
reach only their two routes.

| Route | Level |
|---|---|
| `GET /` and the page's files | view |
| `GET api/status`, `api/users`, `api/messages`, `api/room`, `api/log` | view |
| `GET api/messages/<n>` (a bulletin) | view; personal mail needs control |
| `POST api/users/<call>` {barred}, `api/messages/<n>/kill`, `api/room/say`, `api/room/topic`, `api/room/remove`, `api/forward`, `api/radio/release`, `api/radio/claim`, `api/tx/disarm` | control |
| `POST api/tx/arm` | transmit |
| `POST api/shack/release`, `GET api/shack/busy` | the platform |

URLs in the page are relative (the app is served under a prefix on one
address and at a subdomain's root on another), and nothing assumes HTTPS.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .i18n import _
from .shack import level_at_least, shack_identity

log = logging.getLogger(__name__)

PAGE = Path(__file__).resolve().parent / "static"
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}
#: The page's files, listed once: a request names one of these or nothing,
#: so no path from a request ever reaches the filesystem.
STATIC = {"/" + p.relative_to(PAGE).as_posix(): (p.relative_to(PAGE).as_posix(), _TYPES[p.suffix])
          for p in PAGE.rglob("*") if p.is_file() and p.suffix in _TYPES}
STATIC["/"] = STATIC.pop("/index.html")
_CALL = re.compile(r"^[A-Z0-9]{3,7}$")


class BoxServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, box, secret: str):
        self.box = box
        self.secret = secret
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: BoxServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):            # the journal has the BBS's own log
        log.debug("http %s", fmt % args)

    # ---------------------------------------------------------- answers
    def _send(self, status: int, body: dict | list) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, message: str, code: str, **extra) -> None:
        self._send(status, {"error": message, "code": code, **extra})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            raise ValueError(_("The request is too large."))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ValueError(_("The request is not JSON.")) from None
        if not isinstance(data, dict):
            raise ValueError(_("The request is not a JSON object."))
        return data

    def _who(self) -> dict | None:
        return shack_identity(self.headers.get, self.server.secret)

    def _need(self, who: dict, level: str) -> bool:
        if level_at_least(who["level"], level):
            return True
        self._error(403, _("This needs the {level} level.").format(level=level.capitalize()), "level_too_low",
                    level=who["level"], needs=level)
        return False

    # ---------------------------------------------------------- routing
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        u = urlsplit(self.path)
        path, query = u.path.rstrip("/") or "/", parse_qs(u.query)
        who = self._who()
        if who is None:
            self._error(401, _("This app answers only through the OpenHamShack controller."), "proxy_required")
            return
        box = self.server.box
        try:
            if who["level"] == "none":
                if path == "/api/shack/release" and method == "POST":
                    self._send(*box.release_request(self._body()))
                elif path == "/api/shack/busy" and method == "GET":
                    self._send(200, box.busy())
                else:
                    self._error(403, _("This needs the View level."), "level_too_low", level="none", needs="view")
                return
            if method == "GET" and path in STATIC:
                if self._need(who, "view"):
                    self._static(*STATIC[path])
                return
            for (m, pattern), (level, fn) in ROUTES.items():
                if m != method:
                    continue
                match = re.fullmatch(pattern, path)
                if match:
                    if self._need(who, level):
                        fn(self, who, query, *match.groups())
                    return
            self._error(404, _("There is nothing here."), "not_found")
        except ValueError as e:
            self._error(400, str(e), "bad_request")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _static(self, name: str, ctype: str) -> None:
        raw = (PAGE / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # ---------------------------------------------------------- views
    def get_status(self, who, q):
        self._send(200, {**self.server.box.status(), "you": {"user": who["user"], "level": who["level"]}})

    def get_users(self, who, q):
        store = self.server.box.store
        self._send(200, {"users": [u.public() for u in store.users()]})

    def get_messages(self, who, q):
        store = self.server.box.store
        kind = (q.get("type") or [""])[0].upper() or None
        states = ("new", "read", "forwarded")
        msgs = store.messages(type_=kind if kind in ("P", "B") else None, states=states)
        self._send(200, {"messages": [m.public() for m in reversed(msgs[-500:])], "counts": store.counts()})

    def get_message(self, who, q, number):
        msg = self.server.box.store.get(int(number))
        if msg is None:
            self._error(404, _("There is no such message."), "not_found")
            return
        if msg.type == "P" and not self._need(who, "control"):
            return
        self._send(200, {"message": msg.public(with_body=True),
                         "deliveries": self.server.box.store.deliveries(msg.number)})

    def get_room(self, who, q):
        room = self.server.box.room
        self._send(200, {"name": room.name, "topic": room.topic, "members": room.calls(),
                         "lines": [{"t": t, "text": text} for t, text in list(room.log)[-200:]]})

    def get_log(self, who, q):
        self._send(200, {"log": list(self.server.box.log)[-500:]})

    # ---------------------------------------------------------- changes
    def _audit(self, who, what: str) -> None:
        self.server.box.note("sysop", f"{what} (by {who['user']}, {who['device']})")

    def post_user(self, who, q, call):
        call = call.upper()
        if not _CALL.match(call):
            raise ValueError(_("That is not a callsign."))
        body = self._body()
        barred = bool(body.get("barred"))
        self.server.box.store.update_user(call, barred=barred)
        self._audit(who, f"{'barred' if barred else 'unbarred'} {call}")
        self._send(200, {"call": call, "barred": barred})

    def post_kill(self, who, q, number):
        ok = self.server.box.store.kill(int(number))
        if ok:
            self._audit(who, f"killed message {number}")
        self._send(200 if ok else 404, {"killed": ok})

    def post_room_say(self, who, q):
        text = str(self._body().get("text") or "").strip()
        if not text:
            raise ValueError(_("Say something."))
        self.server.box.room.say(text, "SYSOP")
        self._send(200, {"said": True})

    def post_room_topic(self, who, q):
        topic = str(self._body().get("topic") or "").strip()
        self.server.box.room.set_topic(topic, "SYSOP")
        self._audit(who, f"room topic: {topic}")
        self._send(200, {"topic": topic})

    def post_room_remove(self, who, q):
        call = str(self._body().get("call") or "").upper()
        ok = self.server.box.room.remove(call)
        if ok:
            self._audit(who, f"removed {call} from the room")
        self._send(200 if ok else 404, {"removed": ok})

    def post_forward(self, who, q):
        bbs = self.server.box.bbs
        called = bbs.forward_now() if bbs is not None else []
        self._send(200, {"called": called})

    def post_radio_release(self, who, q):
        self._audit(who, "released the radio")
        self.server.box.radio_down(_("released by the sysop"), state="released")
        self._send(200, self.server.box.status())

    def post_radio_claim(self, who, q):
        ok = self.server.box.radio_up()
        self._audit(who, "claimed the radio")
        self._send(200 if ok else 409, self.server.box.status())

    def post_arm(self, who, q):
        why = self.server.box.arm(who)
        if why:
            self._error(409, why, "not_armed")
            return
        self._send(200, self.server.box.status())

    def post_disarm(self, who, q):
        self.server.box.disarm(_("by {user}").format(user=who["user"]))
        self._send(200, self.server.box.status())


ROUTES = {
    ("GET", r"/api/status"): ("view", _Handler.get_status),
    ("GET", r"/api/users"): ("view", _Handler.get_users),
    ("GET", r"/api/messages"): ("view", _Handler.get_messages),
    ("GET", r"/api/messages/(\d+)"): ("view", _Handler.get_message),
    ("GET", r"/api/room"): ("view", _Handler.get_room),
    ("GET", r"/api/log"): ("view", _Handler.get_log),
    ("POST", r"/api/users/([A-Za-z0-9]+)"): ("control", _Handler.post_user),
    ("POST", r"/api/messages/(\d+)/kill"): ("control", _Handler.post_kill),
    ("POST", r"/api/room/say"): ("control", _Handler.post_room_say),
    ("POST", r"/api/room/topic"): ("control", _Handler.post_room_topic),
    ("POST", r"/api/room/remove"): ("control", _Handler.post_room_remove),
    ("POST", r"/api/forward"): ("control", _Handler.post_forward),
    ("POST", r"/api/radio/release"): ("control", _Handler.post_radio_release),
    ("POST", r"/api/radio/claim"): ("control", _Handler.post_radio_claim),
    ("POST", r"/api/tx/arm"): ("transmit", _Handler.post_arm),
    ("POST", r"/api/tx/disarm"): ("control", _Handler.post_disarm),
}


def serve(box, secret: str, host: str = "0.0.0.0", port: int = 8080) -> BoxServer:
    """Start serving on a thread; returns the server (shutdown() to stop)."""
    srv = BoxServer((host, port), box, secret)
    threading.Thread(target=srv.serve_forever, name="box-http", daemon=True).start()
    return srv
