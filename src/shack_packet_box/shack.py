"""OpenHamShack platform rules for the store app: who is asking, and the
hardware broker.

A copy of QO-100 NB's ohscore/server/shack.py (itself from the QO-100 WB
app's openhamshacklib/shack.py, proved against the Dock360 edition), kept as
the same functions and class so a fix in one is easy to carry to the
others; the day ohs-libs grows shack_client.server (SYNC.md 9.1), all of
them move onto it. The identity headers are believed only after this
app's own proxy secret matches in constant time (CONTROLLER-CONTRACT
5.3), and the broker client holds claims only while they are heartbeaten,
noticing when the broker takes one back (7.2-7.5).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.error
import urllib.request

from .i18n import _

# Issued by the edition into the container's environment at render time
# (modules._issued_env in Dock360), never settings: a person cannot set or
# read them. The proxy secret is per app (CONTROLLER-CONTRACT 5.3), the token
# is the app's own credential for the broker and the shared services (5.4).
PROXY_SECRET_ENV = "DOCK360_SHACK_PROXY_SECRET"
SHACK_URL_ENV = "DOCK360_SHACK_URL"
SHACK_TOKEN_ENV = "DOCK360_SHACK_TOKEN"

SHACK_LEVELS = ("none", "view", "control", "transmit")
SHACK_SECRET_HEADER = "X-OpenHamShack-Proxy-Secret"
SHACK_IDENTITY_HEADERS = ("X-OpenHamShack-User", "X-OpenHamShack-Level", "X-OpenHamShack-Device",
                    "X-OpenHamShack-Auth", "X-OpenHamShack-Admin", "X-OpenHamShack-Reason",
                    "X-OpenHamShack-Transmit")
#: What a request may transmit (CONTROLLER-CONTRACT 5.5): `full` is the
#: Transmit level; `digital` is a TV the controller's administrator allowed
#: digital-mode transmit only - Enable TX here, never PTT or TX audio.
TRANSMIT_RIGHTS = ("none", "digital", "full")
SHACK_ACTING_HEADER = "X-OpenHamShack-Acting-Device"


def level_at_least(level: str, needed: str) -> bool:
    """Whether `level` reaches `needed`; an unknown level reaches nothing."""
    if level not in SHACK_LEVELS or needed not in SHACK_LEVELS:
        return False
    return SHACK_LEVELS.index(level) >= SHACK_LEVELS.index(needed) and level != "none"


def shack_identity(header, secret: str) -> dict | None:
    """The person the platform's proxy vouches for, or None.

    `header(name)` reads one request header. Nothing else is looked at until
    the proxy secret has matched, compared in constant time: without that
    comparison any process that can reach the container's port - every
    other app on the controller - could claim to be anybody. No secret
    configured is a locked app, never an open one.
    """
    if not secret:
        return None
    given = header(SHACK_SECRET_HEADER) or ""
    if not secrets.compare_digest(given.encode(), secret.encode()):
        return None
    user = (header("X-OpenHamShack-User") or "").strip()
    # The broker's release request and the busy gate carry no level: they
    # are the platform itself (auth "broker", "platform"), and may reach
    # only the two routes meant for them (Handler.platform_gate).
    level = (header("X-OpenHamShack-Level") or "none").strip().lower()
    if not user or level not in SHACK_LEVELS:
        return None
    # A controller from before 5.5 sends no Transmit header: then the level
    # alone decides, which is how it was. An unknown word is nothing.
    transmit = (header("X-OpenHamShack-Transmit") or "").strip().lower()
    if not transmit:
        transmit = "full" if level == "transmit" else "none"
    elif transmit not in TRANSMIT_RIGHTS:
        transmit = "none"
    return {"user": user, "level": level,
            "device": (header("X-OpenHamShack-Device") or "-").strip() or "-",
            "auth": (header("X-OpenHamShack-Auth") or "").strip().lower(),
            "admin": (header("X-OpenHamShack-Admin") or "") == "1",
            "reason": (header("X-OpenHamShack-Reason") or "").strip(),
            "transmit": transmit}


# --------------------------------------------------------------------------
# The broker client

def shack_http(base: str, token: str, timeout: float = 3.0):
    """A request function for BrokerClient and the profile fetch:
    (method, path, body, headers) -> (status, json). OSError when the
    controller cannot be reached."""
    def call(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                              **({"Content-Type": "application/json"} if data else {}),
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
                status = res.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {}
        return status, parsed if isinstance(parsed, dict) else {}
    return call


class BrokerClient:
    """This app's claims on the controller's hardware broker.

    A claim is held only while it is heartbeaten (every 2 s, lease 10 s, 6 s
    on air) and the broker may take it away: an administrator's release,
    "Stop all transmitting", a hand-over a person asked for. `beat()` is
    what notices, and returns the devices that are no longer this app's, so
    the daemon stops using them - above all, unkeys. A controller that
    cannot be reached for longer than the lease counts as gone too: the
    broker will have made the radio safe by then, and carrying on would be
    transmitting on a claim nobody holds.
    """

    def __init__(self, request, clock=time.monotonic):
        self.request = request
        self.clock = clock
        self.lock = threading.Lock()
        self.claims: dict[str, dict] = {}
        self.last_error = ""
        # Why the last claim() was not granted, as the broker said it:
        # {"status", "code", "retryAfter"}, None after a grant. The string
        # claim() returns is for a person; the daemon's re-claim after a
        # broker restart needs the code and retryAfter to know whether to
        # wait (503 broker_restarting, an unreachable controller) or give
        # up the re-claim (another app has the radio now).
        self.refused: dict | None = None

    # ---------------------------------------------------------- views
    def held(self, device: str, mode: str | None = None) -> bool:
        with self.lock:
            c = self.claims.get(device)
            return bool(c) and c["state"] == "granted" and (mode is None or c["mode"] == mode)

    def view(self) -> dict:
        with self.lock:
            return {"claims": {d: {"mode": c["mode"], "state": c["state"], "onAir": c["onAir"]}
                               for d, c in self.claims.items()},
                    "error": self.last_error}

    # ---------------------------------------------------------- calls
    def _call(self, method: str, path: str, body: dict | None = None, acting: str = "-") -> tuple[int, dict]:
        try:
            status, answer = self.request(method, path, body, {SHACK_ACTING_HEADER: acting or "-"})
        except OSError as exc:
            self.last_error = _("The controller's hardware broker did not answer ({why})").format(why=exc)
            return 0, {"error": self.last_error, "code": "service_unavailable"}
        return status, answer

    @staticmethod
    def _why(answer: dict, fallback: str) -> str:
        return str(answer.get("error") or fallback)

    def claim(self, device: str, mode: str = "rx", requester: str = "auto", acting: str = "-",
              reason: str = "", reclaim: bool = False, reserve: bool = False) -> str:
        """Claim a device, or say why not ('' when held). A queued claim is
        withdrawn: the box does not wait in line for a radio, it says who
        has it and lets the person decide. `reserve` asks for a standing
        claim the desktop must leave alone (the BBS, D1); a broker that does
        not know the word ignores it, and the held claim blocks the desktop
        anyway (docs/BBS-DESIGN.md §4)."""
        with self.lock:
            have = self.claims.get(device)
        if have and have["state"] == "granted":
            self.refused = None
            return "" if mode == "rx" or have["mode"] == mode else self.set_mode(device, mode)
        status, answer = self._call("POST", "/broker/claims",
                                    {"device": device, "mode": mode, "requester": requester, "reason": reason,
                                     "reclaim": reclaim, **({"reserve": True} if reserve else {})}, acting)
        if status in (200, 201) and answer.get("claimId"):
            now = self.clock()
            lease = answer.get("lease") or {}
            with self.lock:
                self.claims[device] = {"id": answer["claimId"], "mode": answer.get("mode") or mode,
                                       "state": "granted", "onAir": False, "access": answer.get("access"),
                                       "lease": float(lease.get("seconds") or 10), "every": float(lease.get("heartbeatEvery") or 2),
                                       "beat": now, "ok": now}
            self.last_error = ""
            self.refused = None
            return ""
        retry = answer.get("retryAfter")
        self.refused = {"status": status, "code": str(answer.get("code") or ("queued" if status == 202 else "")),
                        "retryAfter": float(retry) if isinstance(retry, (int, float)) else None}
        if status == 202 and answer.get("claimId"):
            self._call("DELETE", f"/broker/claims/{answer['claimId']}")
            holder = answer.get("holder") or {}
            return _("{app} has this radio ({device}); it was not taken from it").format(
                app=holder.get("app") or _("another app"), device=device)
        return self._why(answer, _("The controller would not lend {device} (HTTP {code})").format(
            device=device, code=status))

    def set_mode(self, device: str, mode: str) -> str:
        with self.lock:
            c = self.claims.get(device)
        if not c:
            return _("{device} is not held").format(device=device)
        status, answer = self._call("POST", f"/broker/claims/{c['id']}/mode", {"mode": mode})
        if status == 200:
            with self.lock:
                c["mode"] = mode
            return ""
        return self._why(answer, _("The controller would not change {device} to {mode} (HTTP {code})").format(
            device=device, mode=mode, code=status))

    def on_air(self, device: str, body: dict, acting: str, digital: bool = False) -> str:
        """Ask to go on the air; '' only when the broker said yes. `digital`
        says the over is a digital mode sent by automatic transmit: the one
        kind a TV allowed digital transmit may go on the air for (CONTRACT
        5.5); the broker refuses that TV anything else."""
        with self.lock:
            c = self.claims.get(device)
        if not c or c["state"] != "granted" or c["mode"] != "tx":
            return _("{device} is not held for transmitting").format(device=device)
        status, answer = self._call("POST", f"/broker/claims/{c['id']}/onair",
                                    {**body, "on": True, "chain": [device],
                                     **({"digital": True} if digital else {})}, acting)
        if status == 200 and answer.get("onAir"):
            with self.lock:
                c["onAir"] = True
                lease = answer.get("lease") or {}
                c["lease"] = float(lease.get("seconds") or 6)
            return ""
        return self._why(answer, _("The controller refused to put {device} on the air (HTTP {code})").format(
            device=device, code=status))

    def off_air(self, device: str) -> None:
        with self.lock:
            c = self.claims.get(device)
            if not c or not c["onAir"]:
                return
            c["onAir"] = False
        self._call("POST", f"/broker/claims/{c['id']}/onair", {"on": False})

    def release(self, device: str) -> None:
        with self.lock:
            c = self.claims.pop(device, None)
        if c:
            self._call("DELETE", f"/broker/claims/{c['id']}")

    def forget(self, device: str) -> None:
        """Drop a claim the broker no longer has, without asking it."""
        with self.lock:
            self.claims.pop(device, None)

    def beat(self) -> tuple[list[tuple[str, str]], list[tuple[str, dict]]]:
        """Heartbeat every claim that is due. Returns the claims that are no
        longer this app's, as (device, why) and already forgotten, and the
        ones somebody has asked for, as (device, releaseRequested), for the
        daemon to give up when it is not on the air (CONTROLLER-CONTRACT
        7.5: the heartbeat is how an app that missed the release request
        still hears of it)."""
        lost, asked = [], []
        with self.lock:
            due = [(d, dict(c)) for d, c in self.claims.items() if self.clock() - c["beat"] >= c["every"]]
        for device, c in due:
            status, answer = self._call("POST", f"/broker/claims/{c['id']}/heartbeat")
            now = self.clock()
            with self.lock:
                live = self.claims.get(device)
                if live is None or live["id"] != c["id"]:
                    continue
                live["beat"] = now
                if status == 200 and answer.get("state") == "granted":
                    live["ok"] = now
                    if answer.get("releaseRequested"):
                        req = answer["releaseRequested"]
                        asked.append((device, req if isinstance(req, dict) else {}))
                    continue
                if status in (404, 410) or (status == 200 and answer.get("state") not in (None, "granted")):
                    why = _("The controller took {device} back").format(device=device)
                elif now - live["ok"] > live["lease"]:
                    why = _("The controller has not answered for {s:.0f} s, so {device} is no longer held").format(
                        s=now - live["ok"], device=device)
                else:
                    continue
                self.claims.pop(device, None)
                lost.append((device, why))
        return lost, asked
