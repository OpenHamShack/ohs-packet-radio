"""A link test by callsign, both directions, run by itself (0.3.0).

Asked for by Brian on 20/10/2026 after the 0.2.1 tests with GM0OTB: those
needed a relay on the tailnet (Ray could not reach it), a session name
both operators typed, a role each, and a second run by hand with the roles
swapped. Now each operator types the other's callsign and presses Start;
the two stations find each other through openhamshack.com
(ohs-libs shack_packet.weblink) and run:

1. **A to B, then B to A.** The station whose callsign sorts first sends
   first, so both agree without a word. Each half is linkctl's test as it
   was: the RF receiver never transmits (disarmed, arming refused, every
   frame for the sender by internet), the RF sender's operator arms as
   usual - this never arms anything.
2. **Antennas.** A radio with one antenna socket (any CAT radio, a HackRF)
   needs nothing between halves. A Pluto has separate TX and RX ports and
   one antenna, so its operator is asked to put the antenna on the right
   port before each half and confirm; the other station waits for that
   confirmation and says so.
3. **Results side by side**, live, from both ends: what the sender had
   acknowledged and what the receiver received and checked, per direction,
   then a summary and Save report.

**How the two coordinate.** Each station sends its whole state as one
telemetry message (`{"t": "tele", "kind": "ctl", ...}`) every second and
on every change, and every step waits on the other's latest one. Nothing
depends on one message arriving: the relay may drop a message while a
station rejoins (the TCP relay between halves), and the next copy carries
the same facts. The fields: `call`, `move` (a Pluto: needs the antenna
moved), `radio`, `half`, `rx_ready` (the half it is listening for),
`antenna` (the half its antenna is set for), `done` ({half: result}),
`stopped`.

**Advanced: the 0.2.1 relay.** With `LinkTestSettings.via == "relay"` the
same run goes through the TCP relay on a controller instead; the session
is made from the two calls, and each half is a fresh join in its role
(that relay pairs roles, not callsigns).

**Changes are noted.** `note()` (the control strip calls it when the
operator changes power, gain or squelch mid-run) adds a timestamped line
to the run, and the report carries them, so a result can be read against
what the radio was doing.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from shack_packet.ax25 import Address
from shack_packet.errors import PacketError

from . import __version__, host
from .linkctl import MAX_KB

log = logging.getLogger(__name__)

#: Status to the other station at least this often (module docstring).
STATUS_EVERY_S = 1.0
#: The other station's word that it is listening, before the sender gives up.
RX_READY_TIMEOUT_S = 90.0
#: After the sender is done, how long its receiver may take to say what it got.
RESULT_TIMEOUT_S = 30.0
#: Joining the TCP relay in the new role while the other station still
#: holds it ("role-taken") is retried for this long.
TCP_REJOIN_S = 20.0
PHASES = ("pairing", "antenna", "half", "done", "stopped", "failed")


class Stop(Exception):
    def __init__(self, why: str, detail: str = ""):
        super().__init__(why)
        self.why, self.detail = why, detail


def transfer_timeout_s(kb: int) -> float:
    """Generous: 5 KB at 1200 baud with retries is about a minute."""
    return 120.0 + 40.0 * kb


def needs_move(settings) -> bool:
    """A radio with separate TX and RX ports and one antenna (module
    docstring, step 2)."""
    return settings.radio.kind == "pluto"


def first_sender(a: str, b: str) -> str:
    """The call that sends first: the first of the two, sorted (the web
    relay says the same in its hello answer)."""
    return min(_norm(a), _norm(b))


def _norm(call: str) -> str:
    a = Address.from_text(call.strip().upper(), flag=False)
    return a.call if a.ssid == 0 else f"{a.call}-{a.ssid}"


class LinkRun:
    """One run for linkctl.LinkTest (`lt`), on a thread of its own."""

    def __init__(self, lt, *, peer: str, kb: int, via: str = "web", url: str = "", relay: str = ""):
        self.lt = lt
        self.ctl = lt.ctl
        self.peer = _norm(peer)
        self.kb = max(1, min(MAX_KB, int(kb)))
        self.via, self.url, self.relay = via, url, relay
        self.call = _norm(str(self.ctl.station.address()))
        self.first = first_sender(self.call, self.peer)
        self.move = needs_move(self.ctl.settings)
        self.phase = "pairing"
        self.half = 0
        self.role = ""
        self.why = self.detail = ""
        self.started = time.time()
        self.ended: float | None = None
        self.me: dict = {"call": self.call, "move": self.move, "radio": self.ctl.settings.radio.kind,
                         "kb": self.kb, "half": 0, "rx_ready": 0, "antenna": 0, "done": {}, "stopped": ""}
        self.them: dict = {}
        self.notes: list[dict] = []
        self.antenna_wanted = 0          # the half the operator is asked to set the antenna for
        self.client = None
        self.tele = None
        self.path = None
        self.log_path = None
        self.rtts: list[float] = []
        self._stop = threading.Event()
        self._confirm = threading.Event()
        self._sent_at = 0.0
        self._lock = threading.RLock()
        self._thread = threading.Thread(target=self._main, name="linkrun", daemon=True)

    # -- the operator ---------------------------------------------------------------------

    def start(self) -> LinkRun:
        self._thread.start()
        return self

    def stop(self, why: str = "stopped") -> None:
        self.me["stopped"] = why
        self._send_status(force=True)
        self._stop.set()

    def confirm_antenna(self) -> None:
        self._confirm.set()

    def note(self, text: str) -> None:
        with self._lock:
            self.notes.append({"wall": round(time.time(), 3), "half": self.half, "text": str(text)})
        log.info("link run %s: %s", self.peer, text)

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self.phase in ("pairing", "antenna", "half")

    # -- the run -----------------------------------------------------------------------------

    def _main(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_path = host.data_dir() / "linktest" / f"{stamp}-{self.peer}-run.jsonl"
        try:
            if self.via == "web":
                self._open_web()
            self._set_phase("pairing")
            self._wait(lambda: bool(self.them) or self.via == "relay", None, "pairing")
            for half in (1, 2):
                self._antenna(half)
                self._half(half)
            self._set_phase("done")
            self.ctl._emit("linktest", "run-done", {"peer": self.peer})
        except Stop as e:
            self.why, self.detail = e.why, e.detail
            self._set_phase("stopped" if e.why in ("stopped", "station-stopped") else "failed")
            if self.phase == "failed":
                log.warning("link run with %s failed: %s %s", self.peer, e.why, e.detail)
                self.ctl._emit("linktest", "run-failed", {"why": e.why, "detail": e.detail})
        except Exception as e:                      # noqa: BLE001 - never leave a half attached
            log.exception("link run with %s", self.peer)
            self.why, self.detail = "error", str(e)
            self._set_phase("failed")
        finally:
            self._end_half()
            self._close_client("run-ended")
            with self.lt._lock:
                self.lt.state, self.lt.role = "idle", ""
            self.ended = time.time()            # last: "ended" means cleaned up

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self._send_status(force=True)

    def _antenna(self, half: int) -> None:
        """Before each half, a Pluto's operator confirms the antenna is on
        the right port; everybody waits for both stations' word."""
        self._set_phase("antenna")
        if self.move:
            self._confirm.clear()
            self.antenna_wanted = half
            self._wait(self._confirm.is_set, None, "antenna")
            self.note(f"antenna confirmed on the {'TX' if self._role_for(half) == 'rf-sender' else 'RX'} port "
                      f"for half {half}")
        self.antenna_wanted = 0
        self.me["antenna"] = half
        self._send_status(force=True)
        # Both stations' word, over whatever connection is open: the web
        # pairing, or (the TCP relay) the half that just ended - before the
        # first half there is none, and the receiver's rx_ready is the gate.
        if self.client is not None:
            self._wait(lambda: self.them.get("antenna", 0) >= half, None, "antenna")

    def _role_for(self, half: int) -> str:
        i_first = self.call == self.first
        return "rf-sender" if (half == 1) == i_first else "rf-receiver"

    def _half(self, half: int) -> None:
        role = self._role_for(half)
        self.half, self.role = half, role
        self.me["half"] = half
        self._set_phase("half")
        if role == "rf-receiver":
            # Before anything else, as linkctl's join does: no TX antenna,
            # and arming stays refused for the whole half.
            self.ctl.disarm()
        if self.via == "relay":
            self._open_tcp(role)
        else:
            self.client.set_role(role)
        self._attach(role)
        if role == "rf-receiver":
            self.lt._listen(self.ctl.engine)
            self.me["rx_ready"] = half
            self._send_status(force=True)
            # The sender's word that it is done (its result), then ours.
            self._wait(lambda: str(half) in (self.them.get("done") or {}), transfer_timeout_s(self.kb) + 60, "half")
            deadline = time.monotonic() + 10
            self._wait(lambda: (self.lt.received or {}).get("ok") is not None or time.monotonic() > deadline,
                       15, "half")
            r = self.lt.received or {}
            self.me["done"][str(half)] = {"bytes": r.get("bytes", 0), "expect": r.get("expect"), "ok": r.get("ok"),
                                          "bps": round(r["bps"]) if r.get("bps") else None,
                                          "heard": self.ctl.engine.stats.get("heard", 0) if self.ctl.engine else 0}
            self._send_status(force=True)
        else:
            self._wait(lambda: self.them.get("rx_ready") == half, RX_READY_TIMEOUT_S, "half")
            # The operator arms, as for any transmission; this never does.
            self._wait(lambda: self.ctl.station is not None and self.ctl.station.armed, None, "arm")
            ok, why = self.lt.send_test(self.kb)
            if not ok:
                raise Stop("send-refused", why)
            t = self.lt.transfer
            self._wait(lambda: self.lt.view().get("transfer", {}).get("done") or t.get("ended"),
                       transfer_timeout_s(self.kb), "half")
            v = self.lt.view()
            tr, link = v.get("transfer") or {}, v.get("link") or {}
            result = {**{k: tr.get(k) for k in ("size", "acked", "done", "seconds", "bps", "ended")},
                      **{k: link.get(k) for k in ("srt_s", "t1v_s", "t1_expired", "retransmitted", "rej", "tx_mode")}}
            conn = t["conn"]
            if conn.state.value != "disconnected":
                conn.close()
                end = time.monotonic() + 10
                self._wait(lambda: conn.state.value == "disconnected" or time.monotonic() > end, 15, "half")
            self.me["done"][str(half)] = result
            self._measured(result)
            self._send_status(force=True)
            self._wait(lambda: str(half) in (self.them.get("done") or {}), RESULT_TIMEOUT_S, "half")
        self._end_half()

    def _measured(self, result: dict) -> None:
        """Our half as sender, finished: kept as a measurement of the path
        to this station (airtime.Measurements), so the Send dialog's
        estimate for a file to it is the path's own. Acknowledgements went
        by internet, which the comparison allows for."""
        if not result.get("done") or not result.get("seconds"):
            return
        from . import airtime
        files = getattr(self.ctl, "files", None)
        if files is None:
            return
        try:
            files.measured.add(airtime.Measurement(
                peer=self.peer, mode=result.get("tx_mode") or airtime.current_mode(self.ctl.settings),
                nbytes=int(result.get("acked") or result.get("size") or 0), seconds=float(result["seconds"]),
                kind="linktest", protocol="raw"))
        except Exception:                               # noqa: BLE001 - a measurement is never worth the run
            log.exception("link test speed not kept")

    # -- transport --------------------------------------------------------------------------

    def _env(self) -> dict:
        from shack_packet.linktest import environment
        return environment(self.ctl.link, app=__version__, extra={"radio_kind": self.ctl.settings.radio.kind,
                                                                  "frequency_hz": self.ctl.settings.channel.frequency_hz})

    def _open_web(self) -> None:
        from shack_packet.linktest import LinkTestError, Telemetry
        from shack_packet.weblink import DEFAULT_URL, WebRelayClient
        client = WebRelayClient(self.url or DEFAULT_URL, call=self.call, peer=self.peer,
                                role=self._role_for(1), env=self._env(), log_path=self.log_path,
                                version=__version__)
        self._hook(client)
        try:
            client.start()
        except LinkTestError as e:
            raise Stop(e.reason, e.detail) from None
        self.client = client
        self.first = client.first or self.first
        with self.lt._lock:
            self.lt.client = client
            self.lt.relay = "openhamshack.com"
            self.lt.session = client.pair[:8]
            self.lt.peer = self.peer
            self.lt.log_path = self.log_path
            self.lt.state = "ready" if client.ready else "waiting"
        self.tele = Telemetry(self.ctl.engine, client, self.ctl.link).start()
        self.lt.tele = self.tele

    def _open_tcp(self, role: str) -> None:
        """A fresh join in `role` (module docstring, Advanced)."""
        from shack_packet.linktest import LinkTestError, RelayClient, Telemetry, parse_address
        self._close_client("next-half")
        try:
            host_, port = parse_address(self.relay)
        except LinkTestError as e:
            raise Stop("linktest-relay", e.detail) from None
        session = "-".join(sorted((self.call, self.peer)))[:40]
        deadline = time.monotonic() + TCP_REJOIN_S
        while True:
            client = RelayClient(host_, port, session=session, role=role, call=self.call, peer=self.peer,
                                 env=self._env(), log_path=self.log_path)
            self._hook(client)
            try:
                client.start()
                break
            except LinkTestError as e:
                if "role-taken" not in e.detail or time.monotonic() > deadline or self._stop.is_set():
                    raise Stop(e.reason, e.detail) from None
                time.sleep(0.5)
        self.client = client
        with self.lt._lock:
            self.lt.client = client
            self.lt.relay, self.lt.session, self.lt.peer = f"{host_}:{port}", session, self.peer
            self.lt.log_path = self.log_path
            self.lt.state = "ready" if client.ready else "waiting"
        self.tele = Telemetry(self.ctl.engine, client, self.ctl.link).start()
        self.lt.tele = self.tele

    def _hook(self, client) -> None:
        prev_tele = client.on_tele

        def on_tele(msg):
            if prev_tele is not None:
                prev_tele(msg)
            if msg.get("kind") == "ctl":
                self.them = msg
        client.on_tele = on_tele
        client.on_state = self._client_state

    def _client_state(self, state: str, why: str) -> None:
        if not self.running:
            return                  # the run has ended: its client's last words change nothing
        with self.lt._lock:
            if state in ("waiting", "ready"):
                self.lt.state = state
                self.lt.why = why if state == "waiting" and why in ("peer-left", "peer-silent") else ""
            elif state == "lost":
                self.lt.state, self.lt.why = "lost", why
        if state == "lost":
            # As linkctl: the engine dropped the link without transmitting;
            # the sender is disarmed too.
            if self.role == "rf-sender" and self.ctl.station is not None:
                self.ctl.station.disarm("linktest-lost")
            self.ctl._emit("linktest", "lost", {"why": why})
        elif state == "ready":
            self.ctl._emit("linktest", "ready", {"peer": self.peer})

    def _attach(self, role: str) -> None:
        from shack_packet.linktest import RelayReturnPath
        path = RelayReturnPath(self.client, self.peer, outbound=role == "rf-receiver")
        try:
            self.ctl.engine.attach_return_path(path)
        except PacketError as e:
            raise Stop("linktest-already", str(e)) from None
        self.path = path
        with self.lt._lock:
            self.lt.path, self.lt.role = path, role
            self.lt.transfer = self.lt.received = None

    def _end_half(self) -> None:
        engine = self.ctl.engine
        accept, self.lt._accept = self.lt._accept, None
        path, self.path = self.path, None
        if engine is not None:
            if accept is not None:
                engine.unlisten(accept)
            if path is not None:
                try:
                    engine.detach_return_path(self.peer)
                except PacketError:
                    pass
        if path is not None:
            # Its callbacks stay chained on the client: make them no-ops.
            path.sink = None
        self.lt.path = None

    def _close_client(self, why: str) -> None:
        tele, client = self.tele, self.client
        self.tele = self.client = None
        self.lt.tele = None
        if tele is not None:
            tele.stop()
        if client is not None:
            self.rtts.extend(getattr(client, "rtts", []) or [])
            client.close(why)
        self.lt.client = None

    # -- waiting and telling ------------------------------------------------------------------

    def _wait(self, cond, timeout: float | None, step: str) -> None:
        """Until `cond()`, sending our status as we go; Stop on the
        operator's Stop, the other station's, the return path lost, the
        station gone, or the timeout."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._stop.is_set():
                raise Stop(self.me["stopped"] or "stopped")
            # Before the other station's leaving: its last word and its bye
            # can arrive in one poll, and the word is what was waited for.
            if cond():
                return
            if self.ctl.engine is None:
                raise Stop("station-stopped")
            client = self.client
            if client is not None and client.state == "lost":
                raise Stop(client.lost_why or "lost")
            if self.them.get("stopped"):
                raise Stop("peer-stopped", self.them["stopped"])
            if self.phase != "pairing" and self.lt.state == "waiting" and self.lt.why in ("peer-left", "peer-silent"):
                raise Stop(self.lt.why)
            if end is not None and time.monotonic() > end:
                raise Stop("timeout", step)
            self._send_status()
            time.sleep(0.05)

    def _send_status(self, force: bool = False) -> None:
        client = self.client
        if client is None:
            return
        now = time.monotonic()
        if not force and now - self._sent_at < STATUS_EVERY_S:
            return
        self._sent_at = now
        client.send({"t": "tele", "kind": "ctl", **json.loads(json.dumps(self.me))})

    # -- what the page and the report show -----------------------------------------------------

    def results(self) -> list[dict]:
        """Both directions, each with the sender's and the receiver's word."""
        out = []
        for half in (1, 2):
            sender = self.call if self._role_for(half) == "rf-sender" else self.peer
            receiver = self.peer if sender == self.call else self.call
            mine = (self.me.get("done") or {}).get(str(half))
            theirs = (self.them.get("done") or {}).get(str(half))
            sent, got = (mine, theirs) if sender == self.call else (theirs, mine)
            out.append({"half": half, "from": sender, "to": receiver, "sent": sent, "received": got})
        return out

    def view(self) -> dict:
        return {"phase": self.phase, "half": self.half, "role": self.role, "peer": self.peer, "call": self.call,
                "first": self.first, "why": self.why, "detail": self.detail, "kb": self.kb, "via": self.via,
                "antenna_wanted": self.antenna_wanted, "move": self.move,
                "peer_move": bool(self.them.get("move")), "peer_antenna": self.them.get("antenna", 0),
                "peer_radio": self.them.get("radio", ""), "paired": bool(self.them),
                "armed": bool(self.ctl.station is not None and self.ctl.station.armed),
                "results": self.results(), "notes": list(self.notes),
                "log": str(self.log_path) if self.log_path else ""}

    def report(self) -> dict:
        """Everything worth keeping about the run (Save report)."""
        client_env = None
        if self.lt.client is not None:
            client_env = (self.lt.client.peer_hello or {}).get("env")
        rtts = sorted(self.rtts + list(getattr(self.client, "rtts", []) or []))
        return {"app": __version__, "call": self.call, "peer": self.peer, "via": self.via,
                "relay": self.url if self.via == "web" else self.relay, "kb": self.kb,
                "started": self.started, "ended": self.ended, "phase": self.phase, "why": self.why,
                "detail": self.detail, "first": self.first, "radio": self.ctl.settings.radio.kind,
                "frequency_hz": self.ctl.settings.channel.frequency_hz, "peer_radio": self.them.get("radio"),
                "peer_env": client_env, "results": self.results(), "notes": list(self.notes),
                "relay_rtt_ms": ({"median": round(rtts[len(rtts) // 2] * 1000), "max": round(rtts[-1] * 1000),
                                  "samples": len(rtts)} if rtts else None),
                "log": str(self.log_path) if self.log_path else ""}


def report_text(rep: dict) -> str:
    """The report as an operator reads it, with the whole record as JSON
    underneath for whoever analyses it."""
    def when(t):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"
    lines = [f"OpenHamShack Packet Radio {rep['app']} - link test {rep['call']} <-> {rep['peer']}",
             f"Started {when(rep['started'])}, ended {when(rep['ended'])}: {rep['phase']}"
             + (f" ({rep['why']} {rep['detail']})".rstrip() if rep.get("why") else ""),
             f"{rep['kb']} KB each way at {rep['frequency_hz'] / 1e6:.4f} MHz; this radio {rep['radio']}, "
             f"theirs {rep.get('peer_radio') or '?'}; via {rep['via']} {rep['relay'] or ''}".rstrip()]
    if rep.get("relay_rtt_ms"):
        r = rep["relay_rtt_ms"]
        lines.append(f"Relay round trip median {r['median']} ms, max {r['max']} ms ({r['samples']} samples)")
    for h in rep["results"]:
        s, g = h.get("sent") or {}, h.get("received") or {}
        lines.append("")
        lines.append(f"{h['from']} -> {h['to']}")
        if not s and not g:
            lines.append("  not run")
            continue
        lines.append(f"  sent: {s.get('acked', '-')} of {s.get('size', '-')} bytes acknowledged in "
                     f"{s.get('seconds', '-')} s, {s.get('bps', '-')} bit/s"
                     + (f", ended: {s['ended']}" if s.get("ended") else "")
                     + f"; SRT {s.get('srt_s', '-')} s, T1 expiries {s.get('t1_expired', '-')}, "
                     f"retransmitted {s.get('retransmitted', '-')}, REJ {s.get('rej', '-')}")
        ok = {True: "CRC OK", False: "CRC WRONG", None: "incomplete"}.get(g.get("ok"), "-")
        lines.append(f"  received: {g.get('bytes', '-')} bytes, {ok}, {g.get('bps') or '-'} bit/s")
    if rep["notes"]:
        lines.append("")
        lines.append("Changes during the run:")
        for n in rep["notes"]:
            lines.append(f"  {when(n['wall'])} (half {n['half']}): {n['text']}")
    lines.append("")
    lines.append("---- record (JSON) ----")
    lines.append(json.dumps(rep, indent=1, default=str))
    return "\n".join(lines) + "\n"
