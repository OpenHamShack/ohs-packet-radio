"""Who a callsign is: name, QTH and locator from HamQTH's free XML
callbook, and how far away and which way from this station's locator.

HamQTH (hamqth.com) is free with a free account, which is why it is the
one the app uses: QRZ's XML data needs a paid subscription. The operator
puts the account's username and password in Station › Callsign lookup;
both live in secrets.json with the radio password (settings.py says why),
and neither is ever written to a log, a diagnostics bundle or an error
message: a failure is logged by its kind ("login refused", "network"),
and the request URL, which carries the password at login, is never
logged at all.

**The protocol** (hamqth.com/developers.php): GET
`xml.php?u=USER&p=PASS` answers a session id valid for an hour; GET
`xml.php?id=SESSION&callsign=CALL&prg=NAME` answers a <search> with
nick, adr_name, qth, grid, country, latitude, longitude, or an <error>
("Callsign not found", "Session does not exist or expired"). An expired
session is logged in again once and the lookup retried.

**Cache and offline.** Every answer, found or not, is kept on disk
(callbook.json in the data folder) with the time it was fetched. A found
call is fresh for FOUND_DAYS, a not-found one for MISSING_DAYS; a stale
entry is still shown (offline the app works from the cache alone) and
fetched again in the background when the network allows.

**Rate.** One lookup at a time, at most one every `min_gap_s` (2 s),
from one background thread; asking again for a call already queued does
nothing; the queue keeps at most QUEUE_MAX calls (a busy Monitor names
many, and the newest are the ones on screen). After a network failure
nothing is tried for BACKOFF_S; after a refused login nothing is tried
until the credentials change.

Nothing here runs unless the operator has entered an account: without
one `lookup` answers from the cache (empty) and asks nobody.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from shack_packet.terminal import base_call

from . import grid

log = logging.getLogger(__name__)

URL = "https://www.hamqth.com/xml.php"
PROGRAM = "OpenHamShackPacket"
FOUND_DAYS = 30
MISSING_DAYS = 7
SESSION_S = 55 * 60          # HamQTH says an hour; a little under, so we never use a dead one
BACKOFF_S = 60.0
QUEUE_MAX = 30
TIMEOUT_S = 10.0


@dataclass
class Entry:
    call: str
    found: bool
    fetched: float
    name: str = ""
    qth: str = ""
    grid: str = ""
    country: str = ""
    lat: float | None = None
    lon: float | None = None

    def position(self) -> tuple[float, float] | None:
        if self.lat is not None and self.lon is not None:
            return self.lat, self.lon
        if self.grid and grid.valid(self.grid):
            return grid.to_latlon(self.grid)
        return None


class LookupError_(Exception):
    """A lookup that failed: `kind` is "auth", "network" or "session"."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def _https_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": PROGRAM})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:      # noqa: S310 - a fixed https URL
        return r.read(64 * 1024)


def _fields(xml: bytes) -> dict[str, str]:
    """Every leaf element's text by its tag, namespace dropped."""
    root = ET.fromstring(xml)
    out = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if el.text is not None and len(el) == 0:
            out[tag] = el.text.strip()
    return out


class Callbook:
    def __init__(self, path: Path | None, user: str = "", password: str = "", *, fetch=None,
                 clock=time.time, min_gap_s: float = 2.0, on_result=None, threaded: bool = True):
        self.path = Path(path) if path is not None else None
        self._user, self._password = user.strip(), password
        self._fetch = fetch or _https_get
        self.clock = clock
        self.min_gap_s = min_gap_s
        self.on_result = on_result
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.entries: dict[str, Entry] = {}
        self.queue: collections.deque[str] = collections.deque()
        self._session = ""
        self._session_at = 0.0
        self._last_request = -1e9
        self._blocked_until = 0.0
        #: "", "auth" (login refused: stop until new credentials) or "network".
        self.problem = ""
        self.requests = 0
        self.rev = 0
        self._load()
        self._thread = None
        if threaded:
            self._thread = threading.Thread(target=self._run, name="callbook", daemon=True)
            self._thread.start()

    # -- the cache ---------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for call, e in data.get("entries", {}).items():
                self.entries[call] = Entry(**e)
        except (OSError, ValueError, TypeError):
            log.warning("callbook cache unreadable; starting empty")
            self.entries = {}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"entries": {k: asdict(v) for k, v in self.entries.items()}},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.warning("callbook cache not saved")

    def _fresh(self, e: Entry) -> bool:
        days = FOUND_DAYS if e.found else MISSING_DAYS
        return self.clock() - e.fetched < days * 86400

    # -- what the window asks ----------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._user and self._password)

    def set_credentials(self, user: str, password: str) -> None:
        with self._lock:
            if (user.strip(), password) != (self._user, self._password):
                self._user, self._password = user.strip(), password
                self._session, self.problem, self._blocked_until = "", "", 0.0
        self._wake.set()

    def lookup(self, call: str) -> Entry | None:
        """What the cache knows of `call` (None if nothing), and a fetch
        queued in the background if that is missing or stale."""
        key = base_call(call)
        if not key:
            return None
        with self._lock:
            e = self.entries.get(key)
            if (e is None or not self._fresh(e)) and self.configured and self.problem != "auth" \
                    and key not in self.queue:
                self.queue.append(key)
                while len(self.queue) > QUEUE_MAX:
                    self.queue.popleft()                # the oldest asked is the least likely on screen
                self._wake.set()
        return e

    def describe(self, call: str, home_locator: str = "") -> dict | None:
        """name, qth, grid, country, km, bearing for a tooltip; None while
        unknown. km/bearing need the station's locator and the call's."""
        e = self.lookup(call)
        if e is None:
            return None
        out = {"call": e.call, "found": e.found, "name": e.name, "qth": e.qth, "grid": e.grid,
               "country": e.country, "km": None, "bearing": None}
        pos = e.position()
        if e.found and pos is not None and home_locator and grid.valid(home_locator):
            km, brg = grid.distance_bearing(grid.to_latlon(home_locator), pos)
            out["km"], out["bearing"] = km, brg
        return out

    # -- the network -------------------------------------------------------------------

    def _get(self, params: dict) -> dict[str, str]:
        self.requests += 1
        try:
            return _fields(self._fetch(URL + "?" + urllib.parse.urlencode(params)))
        except ET.ParseError:
            raise LookupError_("network") from None
        except Exception:                           # noqa: BLE001 - urllib's many errors; never echo the URL
            raise LookupError_("network") from None

    def _login(self) -> str:
        got = self._get({"u": self._user, "p": self._password})
        sid = got.get("session_id", "")
        if not sid:
            raise LookupError_("auth")
        self._session, self._session_at = sid, self.clock()
        return sid

    def _search(self, call: str) -> Entry:
        for attempt in (1, 2):
            if not self._session or self.clock() - self._session_at > SESSION_S:
                self._login()
            got = self._get({"id": self._session, "callsign": call, "prg": PROGRAM})
            err = got.get("error", "")
            if err and "session" in err.lower() and attempt == 1:
                self._session = ""
                continue
            if err or not got.get("callsign"):
                return Entry(call, False, self.clock())

            def num(key):
                try:
                    return float(got[key])
                except (KeyError, ValueError):
                    return None
            name = got.get("adr_name") or got.get("nick", "")
            return Entry(call, True, self.clock(), name=name, qth=got.get("qth", ""),
                         grid=got.get("grid", "").upper(), country=got.get("country", ""),
                         lat=num("latitude"), lon=num("longitude"))
        return Entry(call, False, self.clock())

    def run_one(self) -> bool:
        """Fetch the next queued call (the thread's step; the tests call it
        directly with threaded=False). False when there was nothing to do."""
        with self._lock:
            if not self.queue or not self.configured or self.problem == "auth" \
                    or self.clock() < self._blocked_until or self.clock() < self._last_request + self.min_gap_s:
                return False
            call = self.queue.popleft()
            # The gap is between lookups (a login and its search are one).
            self._last_request = self.clock()
        try:
            entry = self._search(call)
        except LookupError_ as e:
            with self._lock:
                self.problem = e.kind
                if e.kind == "network":
                    self._blocked_until = self.clock() + BACKOFF_S
                    self.queue.appendleft(call)
                self._session = ""
            log.warning("callbook lookup failed: %s", "login refused" if e.kind == "auth" else e.kind)
            return True
        with self._lock:
            self.problem = ""
            self.entries[call] = entry
            self.rev += 1
            self._save()
        if self.on_result is not None:
            self.on_result(entry)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.run_one():
                gap = self._last_request + self.min_gap_s - self.clock()
                self._wake.wait(gap if self.queue and gap > 0 else 5.0)
                self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
