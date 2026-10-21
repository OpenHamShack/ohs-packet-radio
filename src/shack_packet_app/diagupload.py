"""Automatic diagnostics upload: the half with no window in it.

The family's opt-in upload (GUIDE §5, "Opt-in diagnostics upload"; Brian,
12/10/2026: "fully automatic, with opt in"), lifted from openhamshack-nb
ohscore/diagupload.py at 9f2a3ed. An operator who switches it on
(Settings › Internet, off by default, the consent text beside the switch)
has Packet Radio send its diagnostics to openhamshack.com by itself - once
a day when anything new was logged, and soon after a fault - and anyone
can send one by hand from Help › Send diagnostics… whether it is on or
not. Each upload gets a reference code to quote, SHACK-7KQ4 (never OHS-:
the brand never uses those initials).

Why: Ray's IC-9700 problems in October took a day each round trip,
because the zip was only made after Brian asked for it and by then the run
that went wrong had rotated out of the log. A report sent minutes after
the fault is the one that still holds it.

What is NB's, unchanged: consent is a record, not just a switch (the
switch AND a stamp naming CONSENT_VERSION, written only by the Settings
dialog); a client POST and never a server; never while transmitting or on
a metered connection, a failure being a log line and a later retry; gzip'd
JSON of at most MAX_BYTES, the log cut from its oldest end; a random
install id, never a hardware one; a fault report waits SETTLE_S for the
lines after the fault, then at most one per DEBOUNCE_S. The Watcher, State,
Plan, pack() and post() are NB's code.

What is this app's: the bundle is diagnostics.contents() - exactly the
texts Help › Save diagnostics… puts in its zip, already scrubbed (home
folder as ~, the LAN password and any token redacted, and refused whole if
one is still in it) - minus the window picture. The faults are this
station's (TRIGGERS), raised with notable() where the app sees them.

The endpoint must know the app: openhamshack-os server/diag/lib.php's
OHS_APPS lists only nb and ham until its owner adds "packet" (PLAN-0.2 D8);
until then every upload is answered 400 "unknown app", which is logged and
retried like any other failure. Test builds are 0.2.0+betaN, which its
version pattern accepts (it refuses -beta.N).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

URL = "https://openhamshack.com/api/diag/upload.php"
PRIVACY_URL = "https://openhamshack.com/privacy/"
APP = "packet"
SCHEMA = 1
# What the operator agreed to. Raise it when what is sent changes in a way
# the consent text has to say: every installation then asks again.
CONSENT_VERSION = 1
MAX_BYTES = 2_000_000
DEBOUNCE_S = 30 * 60.0
DAILY_S = 24 * 3600.0
SETTLE_S = 60.0
FIRST_DAILY_S = 10 * 60.0
RETRY_S = 15 * 60.0
RETRY_MAX_S = 6 * 3600.0
TIMEOUT_S = 30.0
RESPONSE_MAX = 4096
CODE = re.compile(r"^SHACK-[0-9A-HJKMNP-TV-Z]{4,8}$")
INSTALL = re.compile(r"^[0-9a-f]{32}$")

#: The faults worth a report of their own (PLAN-0.2 §3.1), each raised by
#: notable() where the app sees it:
#:   watchdog           the radio link's watchdog or hard watchdog unkeyed the radio
#:   unkey-unconfirmed  the radio never confirmed an unkey
#:   radio-error        a radio call failed or timed out (an IcomLink _call timeout lands here)
#:   radio-lost         the radio went away
#:   rx-dropped         received audio was dropped because the receiver fell behind
#:   crash              an unhandled exception (applog's "crash" logger), or a
#:                      hard crash an earlier run left in fatal.log
#:   pluto-guard        a Pluto's transmit guard could not be confirmed (0.2's SDR work raises it)
TRIGGERS = ("watchdog", "unkey-unconfirmed", "radio-error", "radio-lost", "rx-dropped", "crash", "pluto-guard")
NOTABLE_LOGGER = "shack_packet_app.notable"
CRASH_LOGGER = "crash"                       # applog.install_crash_hooks


class UploadError(Exception):
    """An upload that did not happen; str() is a sentence for the log and
    for Help › Send diagnostics…."""


def notable(trigger: str, detail: str = "") -> None:
    """Report a fault where it happens: logged (so the bundle has it in
    order with everything else) and heard by the Watcher, which hands it to
    the uploader. Costs a log line when uploading is off."""
    if trigger not in TRIGGERS:
        raise ValueError(f"{trigger!r} is not one of diagupload.TRIGGERS")
    logging.getLogger(NOTABLE_LOGGER).warning("notable: %s%s", trigger, f": {detail}" if detail else "",
                                              extra={"ohs_trigger": trigger})


# ------------------------------------------------------------------ the triggers
class Watcher(logging.Handler):
    """Hears notable() and uncaught exceptions from any thread and keeps
    them until the uploader takes them."""

    def __init__(self, clock=time.time):
        super().__init__(logging.WARNING)
        self.clock = clock
        self.lock_ = threading.Lock()
        self.seen: deque = deque(maxlen=100)

    def emit(self, record: logging.LogRecord) -> None:
        trigger = getattr(record, "ohs_trigger", None) or ("crash" if record.name == CRASH_LOGGER else "")
        if trigger:
            with self.lock_:
                self.seen.append((trigger, record.getMessage()[:300], self.clock()))

    def take(self) -> list[tuple[str, str, float]]:
        with self.lock_:
            out = list(self.seen)
            self.seen.clear()
        return out

    def install(self) -> "Watcher":
        for name in (NOTABLE_LOGGER, CRASH_LOGGER):
            logging.getLogger(name).addHandler(self)
        return self

    def remove(self) -> None:
        for name in (NOTABLE_LOGGER, CRASH_LOGGER):
            logging.getLogger(name).removeHandler(self)


# ------------------------------------------------------------------ what is kept
class State:
    """diag-upload.json beside the settings: the install id, when reports
    went, the codes they got, what is waiting. Lost or unreadable means a
    fresh start (a new id), never a failure."""

    HISTORY = 10

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, **kv) -> None:
        self.d.update(kv)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.d, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.info("diagnostics upload: could not save %s: %s", self.path, e)

    def install_id(self) -> str:
        v = self.d.get("install")
        if not (isinstance(v, str) and INSTALL.match(v)):
            # secrets, not uuid1 or anything read from the machine: the id
            # must say nothing about the computer it came from.
            v = secrets.token_hex(16)
            self.set(install=v)
        return v

    def history(self) -> list[dict]:
        h = self.d.get("history")
        return [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []


def consented(stamp) -> bool:
    """A consent stamp ("<version>@<time>") for the current CONSENT_VERSION."""
    if not isinstance(stamp, str) or "@" not in stamp:
        return False
    return stamp.split("@", 1)[0] == str(CONSENT_VERSION)


def consent_stamp(now: float | None = None) -> str:
    now = time.time() if now is None else now
    return f"{CONSENT_VERSION}@{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}"


class Plan:
    """When a report is due. Pure: the caller says what time it is and
    what the radio and the network are doing (NB's, unchanged)."""

    def __init__(self, state: State, started: float):
        self.state = state
        self.started = started
        self.pending: list[tuple[str, str, float]] = []

    def note(self, events) -> None:
        for e in events:
            if e[0] in TRIGGERS and len(self.pending) < 50:
                self.pending.append(e)

    def due(self, now: float, *, enabled: bool, keyed: bool, metered: bool, logged_since: float) -> str:
        """"fault", "daily" or "" (nothing now)."""
        if not enabled or keyed or metered:
            return ""
        if now < (self.state.get("retryAt") or 0.0):
            return ""
        if self.pending:
            first = min(t for _k, _d, t in self.pending)
            if now - first >= SETTLE_S and now - (self.state.get("lastFault") or 0.0) >= DEBOUNCE_S:
                return "fault"
        if (now - self.started >= FIRST_DAILY_S and now - (self.state.get("lastDaily") or 0.0) >= DAILY_S
                and logged_since > (self.state.get("lastSent") or 0.0)):
            return "daily"
        return ""

    def taken(self) -> list[tuple[str, str, float]]:
        out, self.pending = self.pending, []
        return out

    def sent(self, now: float, kind: str, code: str, trigger: str) -> None:
        h = (self.state.history() + [{"code": code, "at": now, "trigger": trigger, "kind": kind}])[-State.HISTORY:]
        kv = {"lastSent": now, "lastCode": code, "history": h, "retryAt": 0.0, "failures": 0, "lastError": ""}
        if kind == "fault":
            kv["lastFault"] = now
        if kind in ("daily", "fault"):
            kv["lastDaily"] = now
        self.state.set(**kv)

    def failed(self, now: float, kind: str, error: str, events) -> None:
        n = int(self.state.get("failures") or 0) + 1
        self.state.set(failures=n, retryAt=now + min(RETRY_MAX_S, RETRY_S * 2 ** (n - 1)), lastError=error[:300])
        if kind == "fault":
            self.pending = list(events) + self.pending


def trigger_text(events, kind: str) -> str:
    if kind == "fault" and events:
        return events[0][0]
    return kind


# ------------------------------------------------------------------ the bundle
def pack(payload: dict, log_text: str, cap: int = MAX_BYTES) -> tuple[bytes, dict]:
    """gzip'd JSON of `payload` plus the log, at most `cap` bytes: the log
    is cut from its oldest end until it fits (NB's)."""
    text = log_text
    info = {"logBytes": len(log_text.encode("utf-8")), "logCut": False}
    while True:
        doc = dict(payload, log=text, logCut=info["logCut"])
        body = gzip.compress(json.dumps(doc, ensure_ascii=False, default=str).encode("utf-8"), 6)
        if len(body) <= cap:
            info["sentLogBytes"] = len(text.encode("utf-8"))
            return body, info
        if not text:
            raise UploadError(f"the report is {len(body)} bytes without any log, more than {cap}")
        info["logCut"] = True
        keep = text[len(text) // 2:] if len(text) > 64 else ""
        nl = keep.find("\n")
        text = keep[nl + 1:] if nl >= 0 else keep


def build(texts: dict[str, str], *, version: str, install: str, kind: str, events,
          now: float | None = None) -> tuple[bytes, dict]:
    """The upload: diagnostics.contents()' texts (already scrubbed), as the
    endpoint's report document (openhamshack-os docs/DIAGNOSTICS.md)."""
    now = time.time() if now is None else now

    def iso(t):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

    def as_json(name):
        try:
            return json.loads(texts.get(name) or "null")
        except ValueError:
            return texts.get(name)
    summary = as_json("summary.json") or {}
    payload = {
        "schema": SCHEMA, "app": APP, "version": version, "install": install, "created": iso(now),
        "trigger": trigger_text(events, kind), "kind": kind,
        "triggers": [{"trigger": k, "detail": d, "at": iso(t)} for k, d, t in events],
        "report": texts.get("summary.json", ""),
        "status": [f"{k}: {v}" for k, v in (summary.get("status") or {}).items()],
        "config": as_json("settings.json"), "ui": None,
        "attachments": {name: texts.get(name, "") for name in ("timing.json", "radio-events.jsonl", "frames.jsonl")},
        "faults": texts.get("fatal.txt", ""),
    }
    # The file reaches back past this run, to the one that crashed, and
    # holds the ring's lines too; the ring alone only when there is no file
    # (logging to a file failed, or a run that never opened one).
    log_text = texts.get("log-file.txt") or ""
    return pack(payload, log_text if log_text.strip() else texts.get("log.txt", ""))


# ------------------------------------------------------------------ sending
def post(url: str, body: bytes, *, version: str, install: str, timeout: float = TIMEOUT_S,
         opener=urllib.request.urlopen) -> str:
    """POST the bundle; the reference code the site gave it. Raises
    UploadError for anything else (NB's)."""
    q = urllib.parse.urlencode({"app": APP, "version": version, "install": install})
    req = urllib.request.Request(f"{url}?{q}", data=body, method="POST",
                                 headers={"Content-Type": "application/gzip", "User-Agent": f"{APP}/{version}"})
    try:
        with opener(req, timeout=timeout) as r:
            data = r.read(RESPONSE_MAX + 1)
    except urllib.error.HTTPError as e:
        why = ""
        try:
            why = json.loads(e.read(RESPONSE_MAX).decode("utf-8")).get("error", "")
        except (ValueError, OSError, AttributeError):
            pass
        raise UploadError(f"the site answered {e.code}{': ' + why if why else ''}") from None
    except (urllib.error.URLError, OSError) as e:
        raise UploadError(f"could not reach the site ({getattr(e, 'reason', e)})") from None
    try:
        code = json.loads(data[:RESPONSE_MAX].decode("utf-8")).get("code", "")
    except (ValueError, UnicodeDecodeError, AttributeError):
        code = ""
    if not (isinstance(code, str) and CODE.match(code)):
        raise UploadError("the site's answer had no reference code")
    return code


#: Link counters that mean a fault (radios.base keeps them per link) and
#: the trigger each raises when it rises. rx_dropped is in blocks of
#: received audio; a few at start-up are normal, so it takes RX_DROPPED_REPORT.
FAULT_STATS = {"watchdog": "watchdog", "hard-watchdog": "watchdog", "unkey-unconfirmed": "unkey-unconfirmed",
               "radio-error": "radio-error"}
RX_DROPPED_REPORT = 25


def faults_from_stats(before: dict, now: dict) -> list[tuple[str, str]]:
    """(trigger, detail) for each fault counter that rose between two looks
    at a link's stats."""
    out = []
    for key, trigger in FAULT_STATS.items():
        n = int(now.get(key, 0) or 0) - int(before.get(key, 0) or 0)
        if n > 0:
            out.append((trigger, f"{key} +{n}"))
    n = int(now.get("rx_dropped", 0) or 0) - int(before.get("rx_dropped", 0) or 0)
    if n >= RX_DROPPED_REPORT:
        out.append(("rx-dropped", f"rx_dropped +{n}"))
    return out


def hard_crash_since(text: str) -> bool:
    """Whether fatal.log's new text records a hard crash. Every start
    writes a "--- started" line there, so growth alone means nothing;
    faulthandler's dump starts with "Fatal Python error" (or names the
    threads it walked)."""
    return "Fatal Python error" in text or "Current thread" in text
