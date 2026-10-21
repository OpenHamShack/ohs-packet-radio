"""The station log: packet contacts kept on disk and exported as ADIF 3.1
(for the operator's main logger, LoTW uploads and awards) or CSV (for a
spreadsheet).

A contact is a connected session (a chat, a BBS or node session, a node
conference) or a station's stay in a room this station hosted, from the
moment the link came up to the moment it went: TIME_ON and TIME_OFF in
UTC. Logged by the operator's "Log this contact" or, per kind of session,
automatically when it ends (Settings.log); never for a session that
never connected, because that was no contact.

**ADIF choices**, and why:

- MODE is PKT. ADIF 3.1's mode list has no submodes for PKT (PAC/PACTOR
  is a different mode with its own), so SUBMODE is left out rather than
  invented, and the speed and modem ("1200 baud AFSK", "9600 baud G3RUH")
  go into COMMENT where a reader sees them.
- No RST_SENT/RST_RCVD: a packet link has no signal report, and an empty
  or made-up 59 would be read as one.
- CALL is the station without its SSID (G0ABC-9 is G0ABC to every log
  and award); the SSID, the path and the session kind go in COMMENT.
- The ADI file is ASCII, as ADIF requires of .adi: accented letters in a
  name or QTH are written without their accents (ADIF puts international
  text only in ADX, which loggers read far less often).
- BAND from the frequency by ADIF's own band table; a frequency outside
  every band leaves BAND out rather than guessing.
"""
from __future__ import annotations

import csv
import io
import json
import re
import os
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ADIF_VERSION = "3.1.4"
PROGRAM_ID = "OpenHamShack Packet Radio"

#: ADIF 3.1 Band enumeration, lower and upper edge in MHz.
BANDS = (("2190m", 0.1357, 0.1378), ("630m", 0.472, 0.479), ("160m", 1.8, 2.0), ("80m", 3.5, 4.0),
         ("60m", 5.06, 5.45), ("40m", 7.0, 7.3), ("30m", 10.1, 10.15), ("20m", 14.0, 14.35),
         ("17m", 18.068, 18.168), ("15m", 21.0, 21.45), ("12m", 24.89, 24.99), ("10m", 28.0, 29.7),
         ("8m", 40.0, 45.0), ("6m", 50.0, 54.0), ("5m", 54.000001, 69.9), ("4m", 70.0, 71.0),
         ("2m", 144.0, 148.0), ("1.25m", 222.0, 225.0), ("70cm", 420.0, 450.0), ("33cm", 902.0, 928.0),
         ("23cm", 1240.0, 1300.0), ("13cm", 2300.0, 2450.0), ("9cm", 3300.0, 3500.0), ("6cm", 5650.0, 5925.0),
         ("3cm", 10000.0, 10500.0))

#: What each session kind is called in a log comment (English: the log is
#: data for other programs, like ADIF's own enumerations).
KINDS = ("chat", "bbs", "node", "conference", "room")


def band(freq_hz: float) -> str:
    mhz = float(freq_hz or 0) / 1e6
    return next((name for name, lo, hi in BANDS if lo <= mhz <= hi), "")


def speed_text(mode: str) -> str:
    """A modem name as a log reader knows it: "1200 baud AFSK"."""
    known = {"afsk1200": "1200 baud AFSK", "afsk300": "300 baud AFSK", "g3ruh9600": "9600 baud G3RUH",
             "fsk9600": "9600 baud G3RUH"}
    if mode in known:
        return known[mode]
    m = re.match(r"^(.*?)(\d+)$", mode or "")
    return f"{m.group(2)} baud {m.group(1).upper()}" if m and m.group(1) else mode


@dataclass
class Contact:
    id: int = 0
    call: str = ""                # without SSID
    station: str = ""             # as connected: G0ABC-9
    time_on: float = 0.0          # UTC seconds
    time_off: float = 0.0
    freq_hz: int = 0
    modem: str = ""               # afsk1200, g3ruh9600...
    kind: str = "chat"
    via: str = ""
    comment: str = ""
    name: str = ""
    qth: str = ""
    grid: str = ""
    my_call: str = ""
    my_grid: str = ""

    @property
    def band(self) -> str:
        return band(self.freq_hz)

    def full_comment(self) -> str:
        parts = [f"Packet {self.kind}" + (f" with {self.station}" if self.station != self.call else "")]
        if self.via:
            parts.append(f"via {self.via}")
        if self.modem:
            parts.append(speed_text(self.modem))
        if self.comment:
            parts.append(self.comment)
        return "; ".join(parts)


def _ascii(text: str) -> str:
    plain = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in plain if 32 <= ord(ch) < 127)


def _field(name: str, value) -> str:
    value = _ascii(value)
    return f"<{name}:{len(value)}>{value} " if value else ""


def _date(t: float) -> str:
    return time.strftime("%Y%m%d", time.gmtime(t))


def _time(t: float) -> str:
    return time.strftime("%H%M%S", time.gmtime(t))


def adif(contacts, version: str = "", now: float | None = None) -> str:
    """An ADI file (ADIF 3.1) of `contacts`."""
    now = time.time() if now is None else now
    contacts = list(contacts)
    out = [f"OpenHamShack Packet Radio log, {len(contacts)} contacts\n",
           _field("ADIF_VER", ADIF_VERSION) + _field("PROGRAMID", PROGRAM_ID) + _field("PROGRAMVERSION", version)
           + _field("CREATED_TIMESTAMP", time.strftime("%Y%m%d %H%M%S", time.gmtime(now))) + "<EOH>\n"]
    for c in contacts:
        rec = (_field("CALL", c.call) + _field("QSO_DATE", _date(c.time_on)) + _field("TIME_ON", _time(c.time_on))
               + (_field("QSO_DATE_OFF", _date(c.time_off)) + _field("TIME_OFF", _time(c.time_off))
                  if c.time_off else "")
               + _field("BAND", c.band) + (_field("FREQ", f"{c.freq_hz / 1e6:.6f}") if c.freq_hz else "")
               + _field("MODE", "PKT") + _field("NAME", c.name) + _field("QTH", c.qth)
               + _field("GRIDSQUARE", c.grid) + _field("STATION_CALLSIGN", c.my_call)
               + _field("MY_GRIDSQUARE", c.my_grid) + _field("COMMENT", c.full_comment()))
        out.append(rec + "<EOR>\n")
    return "".join(out)


CSV_COLUMNS = ("call", "station", "date_on", "time_on", "date_off", "time_off", "band", "freq_mhz", "mode",
               "kind", "via", "name", "qth", "grid", "comment")


def csv_text(contacts) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for c in contacts:
        w.writerow([c.call, c.station, time.strftime("%Y-%m-%d", time.gmtime(c.time_on)),
                    time.strftime("%H:%M:%S", time.gmtime(c.time_on)),
                    time.strftime("%Y-%m-%d", time.gmtime(c.time_off)) if c.time_off else "",
                    time.strftime("%H:%M:%S", time.gmtime(c.time_off)) if c.time_off else "",
                    c.band, f"{c.freq_hz / 1e6:.6f}" if c.freq_hz else "", "PKT", c.kind, c.via, c.name,
                    c.qth, c.grid, c.full_comment()])
    return buf.getvalue()


def matches(c: Contact, text: str = "", since: float | None = None, until: float | None = None) -> bool:
    """The Log page's filter: words found in the call, station, kind, band,
    path, name, QTH or comment (all of them, case ignored), and the
    contact's start between `since` and `until`."""
    if since is not None and c.time_on < since:
        return False
    if until is not None and c.time_on > until:
        return False
    hay = " ".join((c.call, c.station, c.kind, c.band, c.via, c.name, c.qth, c.comment)).upper()
    return all(word in hay for word in str(text or "").upper().split())


class LogBook:
    """Contacts in one JSON file, written to a temporary file and renamed
    into place on every change (as settings and the mailbox are), so a
    crash mid-write leaves the old log. `path` None keeps it in memory."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self.contacts: list[Contact] = []
        self._next = 1
        self.rev = 0
        if self.path is not None and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                known = {f.name for f in fields(Contact)}
                self.contacts = [Contact(**{k: v for k, v in c.items() if k in known})
                                 for c in data.get("contacts", [])]
                self._next = int(data.get("next", len(self.contacts) + 1))
            except (OSError, ValueError, TypeError):
                # Kept aside rather than overwritten: a log is not to be lost.
                self.path.replace(self.path.with_suffix(".unreadable.json"))
                self.contacts = []

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"next": self._next, "contacts": [asdict(c) for c in self.contacts]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, c: Contact) -> Contact:
        with self._lock:
            c.id = self._next
            self._next += 1
            self.contacts.append(c)
            self.rev += 1
            self._save()
        return c

    def delete(self, ident: int) -> bool:
        with self._lock:
            before = len(self.contacts)
            self.contacts = [c for c in self.contacts if c.id != ident]
            gone = len(self.contacts) != before
            if gone:
                self.rev += 1
                self._save()
        return gone

    def update(self, ident: int, **changes) -> bool:
        with self._lock:
            c = next((c for c in self.contacts if c.id == ident), None)
            if c is None:
                return False
            for k, v in changes.items():
                if hasattr(c, k) and k != "id":
                    setattr(c, k, v)
            self.rev += 1
            self._save()
        return True

    def all(self) -> list[Contact]:
        with self._lock:
            return list(self.contacts)
