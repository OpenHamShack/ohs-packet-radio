"""The Link test page's station side: a run with a friend over one RF
direction, the receiver's answers through the relay on an OpenHamShack
controller (ohs-libs shack_packet.returnpath and .linktest; the contract
is shack_packet's LINK-TEST.md).

Built for Brian (GM4BRI) and Ray (GM0OTB), 19/10/2026: two Plutos, one
antenna each, no T/R switch. One station is the **RF sender** (antenna on
its Pluto's TX port; sends the test data, armed as usual), the other the
**RF receiver** (antenna on the RX port; never transmits: its arming is
refused for as long as it is joined, and every frame its engine has for
the sender goes by relay). Swap roles for the other direction.

**Nothing happens until Join**, and Join is an outgoing TCP connection to
the relay: the app still listens on nothing. While joined, the window
shows a banner saying acknowledgements are going via the internet, and
every frame through the relay is marked "internet" in the monitor.

**Losing the relay fails safe** (returnpath.py): the engine drops the link
without transmitting and discards anything queued for the other station;
here the sender is disarmed too, so nothing else keys either, and the
window says why. Join again to carry on.

**What is kept.** Both stations stream telemetry through the relay, which
logs every message; this end also keeps its own copy of everything sent
and received in the data folder's `linktest/` (one JSONL file a session),
and Save diagnostics carries the newest.

The test transfer: a header line `OHSLINK <length> <crc32>\\r` and that
many bytes of seeded random data, so the receiver can say when it has all
of it and whether it is right without knowing the size beforehand.
"""
from __future__ import annotations

import collections
import logging
import random
import threading
import time
import zlib

from shack_packet.ax25 import Address
from shack_packet.errors import PacketError

from . import __version__, host

log = logging.getLogger(__name__)

ROLES = ("rf-sender", "rf-receiver")
HEADER = b"OHSLINK"
#: The largest test transfer the page offers, in kilobytes.
MAX_KB = 1024


def test_data(kb: int, seed: int = 19) -> bytes:
    rng = random.Random(seed)
    body = rng.randbytes(int(kb) * 1024)
    return b"%s %d %08x\r" % (HEADER, len(body), zlib.crc32(body)) + body


def _same(a: Address, b: Address) -> bool:
    return a.call == b.call and a.ssid == b.ssid


class LinkTest:
    """One link test at a time, for one Controller."""

    def __init__(self, ctl):
        self.ctl = ctl
        self.client = None
        self.path = None
        self.tele = None
        self.state = "idle"          # idle | joining | waiting | ready | lost | failed
        self.why = ""
        self.detail = ""
        self.role = ""
        self.peer = ""
        self.session = ""
        self.relay = ""
        self.log_path = None
        self.transfer: dict | None = None
        self.received: dict | None = None
        self.notes: collections.deque = collections.deque(maxlen=200)
        self._accept = None
        self._joiner: threading.Thread | None = None
        self._lock = threading.RLock()
        #: The newest run by callsign (linkrun.py, 0.3.0), kept after it
        #: ends so the page can show its results and save its report.
        self.run = None

    # -- what the controller asks -----------------------------------------------------

    @property
    def joined(self) -> bool:
        return self.state in ("joining", "waiting", "ready", "lost")

    @property
    def running(self) -> bool:
        """A run by callsign under way (linkrun.py)."""
        return self.run is not None and self.run.running

    def blocks_arming(self) -> bool:
        """An RF receiver never transmits while joined (module docstring)."""
        return self.role == "rf-receiver" and self.state in ("joining", "waiting", "ready", "lost")

    def banner(self) -> dict | None:
        """What the window's banner says, or None when there is no test."""
        if not self.joined:
            return None
        return {"state": self.state, "role": self.role, "relay": self.relay, "session": self.session,
                "peer": self.peer, "why": self.why}

    # -- join and leave -----------------------------------------------------------------

    def join(self, ls, *, wait: bool = False) -> tuple[bool, str, str]:
        """Join the session in `ls` (settings.LinkTestSettings): checked
        here, connected on a thread of its own (up to ten seconds to an
        unreachable relay), the outcome as an AppEvent ("linktest",
        "joined"/"failed"). `wait` joins before returning (the tests)."""
        from shack_packet.linktest import LinkTestError, parse_address, valid_session
        from .controller import valid_address
        ctl = self.ctl
        if self.joined or self.running:
            return False, "linktest-already", ""
        if ctl.engine is None or ctl.station is None or ctl.station.callsign is None:
            return False, "no-callsign", ""
        role, peer, session = ls.role, (ls.peer or "").strip().upper(), (ls.session or "").strip()
        if role not in ROLES:
            return False, "linktest-role", role
        if not valid_address(peer):
            return False, "linktest-peer", peer
        if not valid_session(session):
            return False, "linktest-session", session
        try:
            relay_host, relay_port = parse_address(ls.relay)
        except LinkTestError as e:
            return False, "linktest-relay", e.detail
        mine = ctl.station.address()
        if _same(Address.from_text(peer, flag=False), mine):
            return False, "linktest-peer", peer
        with self._lock:
            self.role, self.peer, self.session = role, peer, session
            self.relay = f"{relay_host}:{relay_port}"
            self.state, self.why, self.detail = "joining", "", ""
            self.transfer = self.received = None
        if role == "rf-receiver":
            # Before anything else: an RF receiver has no antenna on its TX
            # port, and arming stays refused while it is joined.
            ctl.disarm()
        self._joiner = threading.Thread(target=self._join, args=(relay_host, relay_port, str(mine)),
                                        name="linktest-join", daemon=True)
        self._joiner.start()
        if wait:
            self._joiner.join(15)
        return True, "", ""

    def _join(self, relay_host: str, relay_port: int, call: str) -> None:
        from shack_packet.linktest import LinkTestError, RelayClient, RelayReturnPath, Telemetry, environment
        ctl = self.ctl
        engine, link = ctl.engine, ctl.link
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_path = host.data_dir() / "linktest" / f"{stamp}-{self.session}-{self.role}.jsonl"
        env = environment(link, app=__version__, extra={"radio_kind": ctl.settings.radio.kind,
                                                         "frequency_hz": ctl.settings.channel.frequency_hz})
        client = RelayClient(relay_host, relay_port, session=self.session, role=self.role, call=call,
                             peer=self.peer, env=env, log_path=self.log_path)
        client.on_state = self._client_state
        try:
            client.start()
        except LinkTestError as e:
            with self._lock:
                self.state, self.why, self.detail = "failed", e.reason, e.detail
            log.warning("link test: could not join %s at %s: %s", self.session, self.relay, e.detail)
            ctl._emit("linktest", "failed", {"why": e.reason, "detail": e.detail})
            return
        if engine is None or ctl.engine is not engine:
            client.close("station-stopped")
            with self._lock:
                self.state = "idle"
            return
        path = RelayReturnPath(client, self.peer, outbound=self.role == "rf-receiver")
        tele = Telemetry(engine, client, link).start()
        try:
            engine.attach_return_path(path)
        except PacketError as e:
            tele.stop()
            client.close("refused")
            with self._lock:
                self.state, self.why, self.detail = "failed", "linktest-already", str(e)
            ctl._emit("linktest", "failed", {"why": "linktest-already", "detail": str(e)})
            return
        with self._lock:
            self.client, self.path, self.tele = client, path, tele
            if self.state == "joining":
                self.state = "ready" if client.ready else "waiting"
        if self.role == "rf-receiver":
            self._listen(engine)
        log.warning("link test %s joined as %s with %s via %s: acknowledgements go by internet",
                    self.session, self.role, self.peer, self.relay)
        ctl._emit("linktest", "joined", {"role": self.role, "session": self.session, "relay": self.relay})

    def start_run(self, ls) -> tuple[bool, str, str]:
        """A run with the station in `ls.peer`, both directions, by itself
        (linkrun.py): through openhamshack.com, or the TCP relay in
        `ls.relay` when `ls.via` is "relay"."""
        from shack_packet.linktest import LinkTestError, parse_address
        from .controller import valid_address
        from .linkrun import LinkRun
        ctl = self.ctl
        if self.joined or self.running:
            return False, "linktest-already", ""
        if ctl.engine is None or ctl.station is None or ctl.station.callsign is None:
            return False, "no-callsign", ""
        peer = (ls.peer or "").strip().upper()
        if not valid_address(peer) or _same(Address.from_text(peer, flag=False), ctl.station.address()):
            return False, "linktest-peer", peer
        via = "relay" if ls.via == "relay" else "web"
        if via == "relay":
            try:
                parse_address(ls.relay)
            except LinkTestError as e:
                return False, "linktest-relay", e.detail
        with self._lock:
            self.state, self.why, self.detail, self.role = "joining", "", "", ""
            self.peer = peer
            self.transfer = self.received = None
            self.run = LinkRun(self, peer=peer, kb=ls.kb, via=via, url=getattr(ls, "url", ""), relay=ls.relay)
        self.run.start()
        return True, "", ""

    def stop_run(self, why: str = "stopped") -> None:
        run = self.run
        if run is not None and run.running:
            run.stop(why)

    def note(self, text: str) -> None:
        """A change worth reading a result against (the control strip's
        power, gain and squelch): into the run under way, if any."""
        run = self.run
        if run is not None and run.running:
            run.note(text)

    def leave(self, why: str = "left") -> None:
        if self.running:
            self.stop_run(why)
            self.run.join(15)
            return
        with self._lock:
            client, tele, path = self.client, self.tele, self.path
            self.client = self.tele = self.path = None
            was = self.state
            self.state, self.why = "idle", ""
            accept, self._accept = self._accept, None
        engine = self.ctl.engine
        if tele is not None:
            tele.stop()
        if engine is not None:
            if accept is not None:
                engine.unlisten(accept)
            if path is not None:
                engine.detach_return_path(self.peer)
        if client is not None:
            client.close(why)
        if was != "idle":
            self.ctl._emit("linktest", "left", {"why": why})

    # -- the relay's word --------------------------------------------------------------

    def _client_state(self, state: str, why: str) -> None:
        with self._lock:
            if self.state in ("idle", "failed"):
                return
            if state in ("waiting", "ready"):
                self.state = state
                self.why = why if state == "waiting" and why in ("peer-left", "peer-silent") else ""
            elif state == "lost":
                self.state, self.why = "lost", why
        if state == "ready":
            self.ctl._emit("linktest", "ready", {"peer": self.peer})
        elif state == "waiting" and why in ("peer-left", "peer-silent"):
            # From 0.2.3 the other station leaving is not the end of the
            # test (shack_packet.linktest): the engine has dropped the link
            # without transmitting and this station stays joined until it
            # rejoins. Not disarmed: nothing can be sent to that station
            # while its return path is down (engine.connect refuses), and
            # the operator is waiting to carry on.
            self.ctl._emit("linktest", "peer-left", {"why": why})
        elif state == "waiting" and why == "joined":
            pass
        elif state == "lost":
            # The engine has dropped the link without transmitting
            # (returnpath.py); the sender is disarmed as well, so nothing
            # else on this station keys while the test is broken.
            if self.role == "rf-sender" and self.ctl.station is not None:
                self.ctl.station.disarm("linktest-lost")
            self.ctl._emit("linktest", "lost", {"why": why})

    # -- the receiver ------------------------------------------------------------------

    def _listen(self, engine) -> None:
        peer = self.peer
        them = Address.from_text(peer, flag=False)

        def accept(call):
            if not _same(call.remote, them):
                return None

            def setup(conn):
                with self._lock:
                    self.received = {"remote": peer, "bytes": 0, "expect": None, "crc": None, "ok": None,
                                     "first": None, "last": None, "conn": conn}
                conn.on_data = self._rx_data
            return setup
        self._accept = accept
        # First in line: the chat's own listener would take the call as a
        # chat when incoming chats are allowed.
        engine.listeners.insert(0, (f"{engine.station.address().call}-{engine.station.address().ssid}", accept))

    def _rx_data(self, data: bytes) -> None:
        with self._lock:
            r = self.received
            if r is None:
                return
            now = time.monotonic()
            if r["ok"] is not None:
                # A further test on the same link: counted on its own, as
                # the sender counts it.
                r = self.received = {"remote": r["remote"], "bytes": 0, "expect": None, "crc": None, "ok": None,
                                     "first": None, "last": None, "conn": r["conn"]}
            r["first"] = r["first"] or now
            r["last"] = now
            buf = r.setdefault("buf", bytearray())
            buf += data
            r["bytes"] = len(buf)
            if r["expect"] is None and b"\r" in buf[:64]:
                head = bytes(buf[:buf.index(b"\r")]).split()
                if len(head) == 3 and head[0] == HEADER:
                    r["expect"], r["crc"] = int(head[1]), head[2].decode()
                    r["head"] = buf.index(b"\r") + 1
            if r["expect"] is not None and r["ok"] is None and len(buf) - r["head"] >= r["expect"]:
                body = bytes(buf[r["head"]:r["head"] + r["expect"]])
                r["ok"] = f"{zlib.crc32(body):08x}" == r["crc"]
                secs = max(1e-9, r["last"] - r["first"])
                r["bps"] = len(buf) * 8 / secs
                log.warning("link test: %d bytes from %s in %.1f s (%.0f bit/s), CRC %s", len(buf), self.peer,
                            secs, r["bps"], "OK" if r["ok"] else "WRONG")
                self.ctl._emit("linktest", "received", {"bytes": len(buf), "ok": r["ok"], "bps": round(r["bps"])})

    # -- the sender ------------------------------------------------------------------------

    def send_test(self, kb: int) -> tuple[bool, str]:
        """Connect to the other station and send `kb` KB of test data."""
        if self.role != "rf-sender":
            return False, "linktest-role"
        if self.state != "ready":
            return False, "linktest-not-ready"
        kb = max(1, min(MAX_KB, int(kb)))
        data = test_data(kb)
        engine = self.ctl.engine
        try:
            conn = engine.connect(self.peer)
        except PacketError as e:
            return False, str(e)
        # `base`: what this connection had already carried. engine.connect
        # hands back the link that is still up from the last test, and its
        # sent_bytes counts every test on it: the second 5 KB run of the
        # GM0OTB test on 19/10/2026 counted 10 284 bytes against its own
        # 42.8 s and showed 1921 bit/s, more than 1200 baud can carry.
        t = {"conn": conn, "size": len(data), "start": time.monotonic(), "acked_at": None, "kb": kb,
             "base": conn.sent_bytes}
        with self._lock:
            self.transfer = t

        def on_event(event):
            if event.kind == "connected":
                t["connected_at"] = time.monotonic()
            elif event.kind == "disconnected":
                t["ended"] = event.reason
        conn.on_event = on_event
        conn.send(data)
        return True, ""

    def stop_test(self) -> None:
        t = self.transfer
        if t is not None and t["conn"].state.value != "disconnected":
            t["conn"].close()

    # -- what the page draws --------------------------------------------------------------

    def view(self) -> dict:
        """A snapshot for the page: our state, our link, the other end."""
        with self._lock:
            out = {"state": self.state, "why": self.why, "detail": self.detail, "role": self.role,
                   "peer": self.peer, "session": self.session, "relay": self.relay,
                   "log": str(self.log_path) if self.log_path else ""}
            client, tele, t, r = self.client, self.tele, self.transfer, self.received
        if client is not None:
            out["clock"] = client.clock()
            out["counts"] = dict(client.counts)
            out["peer_env"] = (client.peer_hello or {}).get("env")
        engine = self.ctl.engine
        conn = None
        if t is not None:
            conn = t["conn"]
            sent = conn.sent_bytes - t.get("base", 0)
            # Unacknowledged frames are counted as full ones (the last is
            # usually short), so this never overstates; clamped to the test.
            acked = max(0, min(t["size"], sent - conn.pending * conn.link.n1 if conn.pending else sent))
            if conn.pending == 0 and t["acked_at"] is None and sent >= t["size"]:
                t["acked_at"] = time.monotonic()
            secs = (t["acked_at"] or time.monotonic()) - t["start"]
            out["transfer"] = {"size": t["size"], "acked": acked, "done": t["acked_at"] is not None,
                               "seconds": round(secs, 1), "bps": round(acked * 8 / max(secs, 1e-9)),
                               "ended": t.get("ended")}
        elif r is not None:
            conn = r.get("conn")
            out["received"] = {k: r.get(k) for k in ("bytes", "expect", "ok", "bps")}
        if conn is not None and engine is not None:
            link = conn.link
            out["link"] = {"state": link.state.value, "vs": link.vs, "va": link.va, "vr": link.vr, "rc": link.rc,
                           "srt_s": round(link.srt, 3), "t1v_s": round(link.t1v, 3),
                           "t1_expired": link.stats.get("t1_expired", 0),
                           "retransmitted": link.stats.get("retransmitted", 0),
                           "rej": link.stats.get("sent_REJ", 0), "srej": link.stats.get("sent_SREJ", 0),
                           "tx_mode": link.tx_mode}
        if engine is not None:
            out["engine"] = {k: engine.stats.get(k, 0) for k in ("rp_sent", "rp_heard", "rp_refused", "rp_dropped")}
        if tele is not None:
            out["peer_rx"] = tele.peer.get("rx")
            out["peer_frames"] = list(tele.peer_frames)[-30:]
            out["peer_adapt"] = tele.peer.get("adapt")
            out["peer_link"] = tele.peer.get("link-stats")
            out["peer_over"] = tele.peer.get("tx-over")
        return out
