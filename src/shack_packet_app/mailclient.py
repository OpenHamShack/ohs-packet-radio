"""The Mailbox page's two kinds of mail, behind the window:

- **Packet mail** between amateur stations, store-and-forward (ohs-libs
  stationmail.py, FORWARDING.md): an outbox that delivers itself to the
  addressee's station, their BBS or the home BBS while transmit is armed,
  and collection from the home BBS. What arrives lands in the station's
  mailbox store, beside what callers left by typing, so there is one
  inbox.
- **Winlink email** through an RMS packet gateway (ohs-libs b2f.py):
  messages to callsigns or internet addresses, composed any time and
  exchanged when the operator connects to a gateway. Kept apart from
  packet mail (its own folders under the data folder), because it is a
  different network with different rules - an internet address can only
  be reached this way, and Winlink needs an account.

Neither transmits by itself unless transmit is armed: the outbox waits,
and a Winlink connect is refused with "not-armed". Neither listens on
anything: packet mail is answered by the station's mailbox, on the air.

Events for the window (Controller AppEvents, kind "mail"): the library's
mail-delivered / mail-failed / mail-collected / mail-session, flattened to
plain values here, and "winlink-session" (state, error, sent, received)
and "winlink-received" (sender, subject).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from shack_packet import b2f
from shack_packet.stationmail import MailConfig, Outbox, Peer, StationMail

from . import host
from .settings_extra import WINLINK_SECRET

log = logging.getLogger(__name__)


def mail_config(settings, version: str = "0.3.0") -> MailConfig:
    m = settings.mail
    partners = []
    for text in m.partners:
        try:
            partners.append(Peer.parse(str(text)))
        except ValueError:
            log.warning("mail partner %r not understood; left out", text)
    via = [v.strip().upper() for v in str(m.home_via).replace(",", " ").split() if v.strip()]
    return MailConfig(home=str(m.home_bbs).strip().upper(), home_via=via, partners=partners,
                      direct=bool(m.direct), direct_ssid=int(m.direct_ssid) if 0 <= int(m.direct_ssid) <= 15 else 1,
                      collect_every_s=max(0, int(m.collect_every_min)) * 60.0, version=version)


def parse_gateway(text: str) -> tuple[str, list[str]]:
    """"GB7XYZ-10 via MB7NXX" to (GB7XYZ-10, [MB7NXX])."""
    words = str(text).replace(",", " ").split()
    if not words:
        raise ValueError("empty")
    via = [w.upper() for w in words[1:] if w.lower() != "via"]
    return words[0].upper(), via


# ---------------------------------------------------------------- Winlink's folders
@dataclass
class WinlinkItem:
    mid: str
    folder: str              # inbox | outbox | sent
    sender: str
    to: list
    subject: str
    date: float
    size: int
    attachments: list        # names
    read: bool = False


class WinopenHamShack:
    """Winlink messages on disk, one .b2f file each (the message's own bytes,
    as Pat keeps them) in inbox/, outbox/ and sent/, and an index of read
    flags. A b2f.Mailroom for the session."""

    FOLDERS = ("inbox", "outbox", "sent")

    def __init__(self, root: Path, *, on_received=None):
        self.root = Path(root)
        self.on_received = on_received
        self._lock = threading.Lock()
        for f in self.FOLDERS:
            (self.root / f).mkdir(parents=True, exist_ok=True)
        self._read = self._load_read()

    def _load_read(self) -> set:
        try:
            return set(json.loads((self.root / "read.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    def _save_read(self) -> None:
        tmp = self.root / "read.json.tmp"
        tmp.write_text(json.dumps(sorted(self._read)), encoding="utf-8")
        os.replace(tmp, self.root / "read.json")

    def _path(self, folder: str, mid: str) -> Path:
        safe = "".join(ch for ch in mid if ch.isalnum() or ch in "_-")[:20]
        return self.root / folder / f"{safe}.b2f"

    def _write(self, folder: str, msg: b2f.WinlinkMessage) -> None:
        path = self._path(folder, msg.mid)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(msg.to_bytes())
        os.replace(tmp, path)

    def load(self, folder: str, mid: str) -> b2f.WinlinkMessage | None:
        try:
            return b2f.WinlinkMessage.from_bytes(self._path(folder, mid).read_bytes())
        except (OSError, ValueError):
            return None

    def items(self, folder: str) -> list[WinlinkItem]:
        out = []
        for path in sorted((self.root / folder).glob("*.b2f")):
            try:
                m = b2f.WinlinkMessage.from_bytes(path.read_bytes())
            except (OSError, ValueError):
                continue
            out.append(WinlinkItem(m.mid, folder, b2f.plain(m.sender), [b2f.plain(t) for t in m.to + m.cc],
                                   m.subject, m.date, path.stat().st_size, [f.name for f in m.files],
                                   m.mid in self._read or folder != "inbox"))
        return sorted(out, key=lambda i: i.date, reverse=True)

    def add_outgoing(self, msg: b2f.WinlinkMessage) -> None:
        with self._lock:
            self._write("outbox", msg)

    def mark_read(self, mid: str) -> None:
        with self._lock:
            self._read.add(mid)
            self._save_read()

    def delete(self, folder: str, mid: str) -> bool:
        with self._lock:
            try:
                self._path(folder, mid).unlink()
                return True
            except OSError:
                return False

    # b2f.Mailroom
    def outgoing(self) -> list[b2f.WinlinkMessage]:
        return [m for m in (self.load("outbox", i.mid) for i in self.items("outbox")) if m is not None]

    def answer(self, mid: str, title: str, size: int) -> str:
        return "-" if self._path("inbox", mid).exists() else "+"

    def received(self, msg: b2f.WinlinkMessage) -> None:
        with self._lock:
            self._write("inbox", msg)
        if self.on_received is not None:
            self.on_received(msg)

    def delivered(self, mid: str, how: str) -> None:
        with self._lock:
            src = self._path("outbox", mid)
            try:
                os.replace(src, self._path("sent", mid))
            except OSError:
                pass


# ---------------------------------------------------------------- the client
class MailClient:
    def __init__(self, ctl):
        self.ctl = ctl
        self.mail: StationMail | None = None
        self.outbox: Outbox | None = None
        self.winlink: WinopenHamShack | None = None
        self.wl_session: b2f.Session | None = None
        self.wl_last: dict = {}

    @property
    def settings(self):
        return self.ctl.settings

    def _armed(self) -> bool:
        st = self.ctl.station
        return st is not None and st.callsign is not None and st.armed

    def start(self, engine, inbox_store, *, data_dir: Path | None = None) -> MailClient:
        base = Path(data_dir) if data_dir is not None else host.data_dir()
        call = engine.station.callsign.call
        try:
            self.outbox = Outbox(base / "outbox.json", call=call, clock=self.ctl.wall)
        except (OSError, ValueError):
            log.exception("outbox.json not readable; mail kept in memory this run")
            self.outbox = Outbox(None, call=call, clock=self.ctl.wall)
        self.mail = StationMail(engine, self.outbox, inbox_store, mail_config(self.settings),
                                can_send=self._armed, wall_clock=self.ctl.wall)
        try:
            self.winlink = WinopenHamShack(base / "winlink", on_received=self._winlink_received)
        except OSError:
            log.exception("the Winlink folder could not be made")
            self.winlink = None
        return self

    def stop(self) -> None:
        self.mail = None
        self.wl_session = None

    # -- packet mail ---------------------------------------------------------------
    def compose(self, address: str, subject: str, body: str, reply_to: int | None = None):
        """Queue a message; ValueError for an address that is not CALL or
        CALL@BBS."""
        if self.mail is None:
            raise RuntimeError("not started")
        return self.mail.compose(address, subject, body, reply_to=reply_to)

    def collect_now(self) -> str:
        """"" when asked for, else why not: "no-home" or "not-armed"."""
        if self.mail is None or not self.settings.mail.home_bbs:
            return "no-home"
        self.mail.collect_now()
        return "" if self._armed() else "not-armed"

    def send_now(self) -> str:
        if self.mail is None:
            return "no-home"
        self.mail.send_now()
        return "" if self._armed() else "not-armed"

    def outgoing(self) -> list:
        return sorted(self.outbox.items, key=lambda m: m.created, reverse=True) if self.outbox else []

    def retry(self, ident: int) -> None:
        if self.outbox is not None and self.ctl.engine is not None:
            with self.ctl.engine.lock:
                self.outbox.retry(ident)
            self.ctl.engine._wake()

    def delete_outgoing(self, ident: int) -> bool:
        if self.outbox is None or self.ctl.engine is None:
            return False
        with self.ctl.engine.lock:
            return self.outbox.delete(ident)

    def event_detail(self, reason: str, detail: dict) -> None:
        """The library's mail events as plain values for the window."""
        m = detail.get("mail")
        if m is not None:
            detail.update(id=m.id, to=m.address, subject=m.subject, route=m.route)
        if "why" in detail:
            detail["error"] = detail.pop("why")

    # -- Winlink ---------------------------------------------------------------
    def winlink_call(self) -> str:
        return (self.settings.winlink.account or (self.ctl.settings.station.callsign or "")).strip().upper()

    def winlink_compose(self, to: list[str], subject: str, body: str, files: list | None = None,
                        cc: list[str] | None = None) -> b2f.WinlinkMessage:
        """Into the Winlink outbox; ValueError for no addressee, a missing
        subject or attachments over the limit."""
        if self.winlink is None:
            raise RuntimeError("no-folder")
        to = [t.strip() for t in to if t.strip()]
        if not to:
            raise ValueError("no-addressee")
        if not subject.strip():
            raise ValueError("no-subject")
        files = list(files or [])
        if sum(len(f.data) for f in files) > max(0, int(self.settings.winlink.max_attachments_kb)) * 1024:
            raise ValueError("too-big")
        call = self.winlink_call()
        msg = b2f.WinlinkMessage(b2f.new_mid(call), call, [b2f.address(t) for t in to], subject.strip(), body,
                                 date=self.ctl.wall(), cc=[b2f.address(c) for c in (cc or []) if c.strip()],
                                 files=files, mbo=call)
        self.winlink.add_outgoing(msg)
        return msg

    def winlink_connect(self, gateway: str = "") -> str:
        """Connect to an RMS gateway and exchange mail. "" when started,
        else a key: "not-armed", "no-gateway", "no-password", "busy",
        "no-callsign"."""
        engine = self.ctl.engine
        if engine is None or self.winlink is None or self.ctl.station is None or self.ctl.station.callsign is None:
            return "no-callsign"
        if self.wl_session is not None and self.wl_session.state not in ("done", "failed"):
            return "busy"
        text = gateway or self.settings.winlink.gateway
        if not text:
            return "no-gateway"
        if not self.ctl.secrets.get(WINLINK_SECRET):
            return "no-password"
        if not self._armed():
            return "not-armed"
        try:
            call, via = parse_gateway(text)
        except ValueError:
            return "no-gateway"
        secrets = self.ctl.secrets
        self.wl_last = {"gateway": call, "state": "calling", "error": "", "sent": 0, "received": 0,
                        "at": self.ctl.wall()}
        self.ctl._emit("mail", "winlink-session", dict(self.wl_last))
        try:
            self.wl_session = b2f.call(engine, call, self.winlink, mycall=self.winlink_call(),
                                       password=lambda: secrets.get(WINLINK_SECRET, ""), via=via,
                                       locator=self.settings.station.locator, on_done=self._winlink_done)
        except Exception as e:                      # noqa: BLE001 - a refused connect (return path down)
            log.info("winlink: could not call %s: %s", call, e)
            self.wl_last.update(state="failed", error="no-connect")
            self.ctl._emit("mail", "winlink-session", dict(self.wl_last))
            return "no-connect"
        log.info("winlink: calling %s", call)
        return ""

    def _winlink_done(self, sess: b2f.Session) -> None:
        self.wl_last.update(state=sess.state, error=sess.error, sent=sess.result.sent,
                            received=sess.result.received, remote=sess.remote_error, at=self.ctl.wall())
        log.info("winlink session %s %s sent %d received %d %s", sess.state, sess.error, sess.result.sent,
                 sess.result.received, sess.remote_error)
        self.ctl._emit("mail", "winlink-session", dict(self.wl_last))

    def _winlink_received(self, msg: b2f.WinlinkMessage) -> None:
        self.ctl._emit("mail", "winlink-received", {"sender": b2f.plain(msg.sender), "subject": msg.subject,
                                                    "body": msg.body, "mid": msg.mid, "for_me": True})

    def winlink_progress(self) -> tuple | None:
        s = self.wl_session
        return s.progress if s is not None and s.state not in ("done", "failed") else None
