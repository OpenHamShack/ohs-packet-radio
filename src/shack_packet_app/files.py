"""Files and pictures over a connected session: the station core's side
(0.3.0, workstream D). The protocols are ohs-libs' (shack_packet FILES.md):
OHS between OpenHamShack stations, YAPP for other software, 7PLUS for
posting through BBSs. This module wires them to the controller's chat
sessions, the settings and the window, without Qt.

**What the operator sees decided here**:

- Every chat session gets the OHS layer as it opens (Controller.
  _new_session). Text is untouched; nothing goes to the far end unless
  the operator sends a file, or the far end has said HELLO, or it said
  HELLO in an earlier session (`peers`, remembered in the data folder, so
  two OHS stations find each other again after a restart and a file left
  half-sent resumes on its own).
- An offer from another station waits in `offers` until the window asks
  the operator (accept / decline); nothing is written before that, and an
  offer bigger than the setting is declined without asking.
- Received files go to the folder in Settings (blank: "Received files" in
  the app's data folder) and are never opened, run or previewed by
  anything but Qt's own image decoder for a thumbnail.
- Frames only leave while transmit is armed, like every other frame: a
  transfer started disarmed waits in the engine's queue, the arming rule
  untouched.

**Threads.** The file service runs under the engine's lock on the engine's
runner thread; its events land here and are handed to the window as
controller events ("file", kind) and in `offers`. The window's calls
(`send`, `accept`, ...) take the engine's lock inside the service.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
from pathlib import Path

from shack_packet import sevenplus
from shack_packet.filexfer import FilePolicy, FileService, Inbox, Transfer
from shack_packet.yapp import YappHandler

from . import __version__, airtime, host

log = logging.getLogger(__name__)

#: Events the window hears: ("file", kind) with the transfer's id.
KINDS = ("offer", "started", "progress", "done", "failed", "declined", "cancelled", "interrupted", "hello")


def default_folder() -> Path:
    return host.data_dir() / "Received files"


class FilesManager:
    def __init__(self, ctl):
        self.ctl = ctl
        self.service: FileService | None = None
        self.yapp: YappHandler | None = None
        #: Transfers from earlier station runs (before a restart of the
        #: station core), so the Files page still lists them.
        self.history: list[Transfer] = []
        #: Incoming offers waiting for the window to ask the operator.
        self.offers: collections.deque[Transfer] = collections.deque()
        self.rev = 0
        self._lock = threading.Lock()
        self.peers: set[str] = self._load_peers()
        self.sim = None
        #: Timed transfers and link tests per station (airtime.Measurements),
        #: so the next estimate to that station is the path's, not the model's.
        self.measured = airtime.Measurements(host.data_dir() / "throughput.json")

    # -- settings ------------------------------------------------------------------

    @property
    def settings(self):
        return self.ctl.settings.files

    def folder(self) -> Path:
        f = self.settings.folder.strip()
        return Path(f).expanduser() if f else default_folder()

    def policy(self) -> FilePolicy:
        s = self.settings
        return FilePolicy(max_size=max(1, int(s.max_mb)) * 1024 * 1024, auto_resume=bool(s.auto_resume),
                          compress=bool(s.compress))

    def apply(self) -> None:
        """Settings changed on the Files page: the folder and policy take
        effect for the next file, without rebuilding the station."""
        if self.service is not None:
            self.service.policy = self.policy()
            self.service.inbox = Inbox(self.folder())

    # -- life ----------------------------------------------------------------------

    def start(self) -> None:
        """The station core has a new engine (Controller.start)."""
        if self.service is not None:
            for t in self.service.transfers:
                if t.active:
                    t.state, t.reason, t.resumable = "interrupted", "stopped", t.direction == "out"
            self.history = (self.history + list(self.service.transfers))[-200:]
        engine = self.ctl.engine
        self.service = FileService(engine, Inbox(self.folder()), policy=self.policy(),
                                   app=f"OpenHamShack Packet Radio {__version__}", on_event=self._event)
        self.service.greet = set(self.peers)
        self.yapp = YappHandler(self.service)
        self.offers.clear()
        if self.ctl.band is not None:
            from . import simfiles
            try:
                self.sim = simfiles.add_to(self.ctl.band, host.data_dir() / "simulator")
            except Exception:                           # noqa: BLE001 - the simulator's extra station is optional
                log.exception("simulated file station not added")
        self._changed()

    def attach(self, sess) -> None:
        """A chat session opened (Controller._new_session)."""
        if self.service is not None:
            self.service.attach(sess.conn)

    # -- commands -----------------------------------------------------------------

    def _session(self, session_id: int):
        sess = self.ctl.sessions.get(session_id)
        if sess is None or self.service is None:
            raise ValueError("no-session")
        return sess

    def send(self, session_id: int, path: Path | str, *, protocol: str | None = None, data: bytes | None = None,
             name: str | None = None) -> Transfer:
        """Offer a file on a chat session. Raises ValueError with a reason
        key ("no-session", "not-connected", "encrypted") or OSError."""
        sess = self._session(session_id)
        if sess.state != "connected":
            raise ValueError("not-connected")
        protocol = protocol or self.settings.protocol
        t = self.service.send_file(sess.conn, path, name=name, data=data, protocol=protocol)
        self._changed()
        return t

    def accept(self, t: Transfer) -> None:
        if self.service is not None:
            self.service.accept(t)
        self._changed()

    def decline(self, t: Transfer) -> None:
        if self.service is not None:
            self.service.decline(t)
        self._changed()

    def cancel(self, t: Transfer) -> None:
        if self.service is not None:
            self.service.cancel(t)
        self._changed()

    def resume(self, t: Transfer) -> bool:
        """Offer an interrupted outgoing file again, on a connected session
        to the same station. False when there is none."""
        ok = self.service is not None and t in self.service.transfers and self.service.resume(t)
        self._changed()
        return bool(ok)

    def transfers(self) -> list[Transfer]:
        now = list(self.service.transfers) if self.service is not None else []
        return self.history + now

    def session_of(self, t: Transfer):
        """The chat session a transfer runs on, or None."""
        conn = getattr(t.session, "conn", None)
        for sess in list(self.ctl.sessions.values()):
            if sess.conn is conn:
                return sess
        return None

    def estimates(self, nbytes: int, *, peer: str = "", protocol: str | None = None,
                  chunks: int | None = None) -> list:
        """The estimate table (airtime.table) for `nbytes` of on-air file
        data from this station, with what was measured with `peer`."""
        return airtime.table(self.ctl.settings, nbytes, protocol=protocol or self.settings.protocol, peer=peer,
                             measurements=self.measured, chunks=chunks)

    def wire_size(self, path: Path | str, *, protocol: str | None = None, data: bytes | None = None,
                  name: str | None = None) -> int:
        """The bytes of `path` (or `data`) that would go on the air:
        compressed as the OHS sender would, where it would."""
        protocol = protocol or self.settings.protocol
        compress = bool(self.settings.compress)
        if data is not None:
            return airtime.wire_size(data, name or Path(path).name, protocol=protocol, compress=compress)
        return airtime.file_wire_size(path, protocol=protocol, compress=compress)

    # -- 7PLUS ---------------------------------------------------------------------

    def sevenplus_encode(self, path: Path | str, out_dir: Path | str, lines: int | None = None) -> list[Path]:
        """Encode a file into 7PLUS parts in `out_dir`, for posting to a BBS
        as messages. Returns the part files."""
        path = Path(path)
        parts = sevenplus.encode(path.read_bytes(), path.name, lines_per_part=lines or self.settings.sevenplus_lines,
                                 mtime=path.stat().st_mtime)
        out = []
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        for fn, text in parts:
            dest = Path(out_dir) / fn
            dest.write_bytes(text)
            out.append(dest)
        return out

    def sevenplus_decode(self, sources) -> tuple[sevenplus.Result, Path | None]:
        """7PLUS parts from files (Path) or text (str/bytes: a capture,
        saved BBS messages, pasted text). A complete file is saved in the received folder under
        a safe name and never opened; (result, where it was saved)."""
        from shack_packet.filexfer import safe_name, unique_path
        c = sevenplus.Collector()
        for src in sources:
            # A Path is a file to read; bytes or str are text (pasted, or a
            # chat session's transcript).
            c.feed(Path(src).read_bytes() if isinstance(src, Path) else src)
        result = c.result()
        if not result.complete:
            return result, None
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        dest = unique_path(folder, safe_name(result.name or "7plus.bin"))
        dest.write_bytes(result.data)
        return result, dest

    # -- events --------------------------------------------------------------------

    def _event(self, t: Transfer | None, kind: str, detail: dict) -> None:
        if kind == "hello":
            sess = detail.get("session")
            if sess is not None:
                r = sess.conn.remote
                self._remember(f"{r.call}-{r.ssid}" if r.ssid else r.call)
            return
        if kind == "offer" and t is not None:
            self.offers.append(t)
        if kind == "done" and t is not None:
            self._measure(t)
        self._changed()
        if kind != "progress" and t is not None:
            self.ctl._emit("file", kind, {"id": t.id, "peer": t.peer, "name": t.name, "size": t.size,
                                          "direction": t.direction, "reason": t.reason})

    def _measure(self, t: Transfer) -> None:
        """A finished transfer is a measurement of the path to its station
        (airtime.Measurements): the file data on the air, from the ACCEPT
        to the result, at the mode the link was using."""
        if t.started is None or t.finished is None:
            return
        raw = max(0, t.size - t.start_at)
        chunks = max(1, -(-raw // airtime.OHS_CHUNK))
        if t.protocol == "ohs":
            nbytes = max(0, t.wire_bytes - chunks * airtime.OHS_CHUNK_OVERHEAD) or raw
        else:
            nbytes = raw
        sess = self.session_of(t)
        mode = getattr(getattr(getattr(sess, "conn", None), "link", None), "tx_mode", None)
        mode = mode or airtime.current_mode(self.ctl.settings)
        try:
            self.measured.add(airtime.Measurement(peer=t.peer, mode=mode, nbytes=nbytes,
                                                  seconds=t.finished - t.started, protocol=t.protocol,
                                                  chunks=chunks if t.compress else 0))
        except Exception:                               # noqa: BLE001 - a measurement is never worth a failure
            log.exception("transfer speed not kept")

    def _changed(self) -> None:
        with self._lock:
            self.rev += 1

    # -- the OHS stations we know ---------------------------------------------------

    def _peers_path(self) -> Path:
        return host.data_dir() / "ohs-stations.json"

    def _load_peers(self) -> set[str]:
        try:
            data = json.loads(self._peers_path().read_text("utf-8"))
            return {str(c) for c in data.get("stations", [])}
        except (OSError, ValueError, AttributeError):
            return set()

    def _remember(self, call: str) -> None:
        if call in self.peers:
            return
        self.peers.add(call)
        if self.service is not None:
            self.service.greet.add(call)
        try:
            p = self._peers_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"stations": sorted(self.peers)}), "utf-8")
            tmp.replace(p)
        except OSError:
            log.warning("could not save the list of OpenHamShack stations")
