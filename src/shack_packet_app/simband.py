"""The Simulator radio: a pretend 2 m channel with a few pretend stations on
it, at wall-clock speed, so a tester (and the tests, and the screenshots)
can use every window with no radio at all.

It is ohs-libs' simulated channel (shack_packet.sim.Medium, frames mode:
airtime, collisions, carrier detect and half duplex are modelled; audio is
not) run against time.monotonic() instead of a simulated clock, with a
thread of its own that delivers transmissions when they end. The
operator's engine gets a radio on it like any other link, so everything
the window does goes through the same engine, channel access and
transmit checks as on the air: **nothing here transmits on a real radio,
and the operator's own station still has to be armed to send a single
frame into the simulation** - which is what the tests rely on to prove
that transmit stays off until armed.

The pretend stations are real engines too, each on its own radio on the
same medium, with ITU-shaped calls that exist only in here:

- `G4SIM-9`, a mobile APRS tracker, beaconing a position a few kilometres
  from the operator;
- `G4SIM`, a fixed APRS station that acknowledges every message (the APRS
  station does that itself) and answers it;
- `GB7SIM`, a node (alias SIM): ohs-libs' pretend BPQ node
  (shack_packet.simnode), so the node browser, BBS helpers, scripts and
  conferences have something to work against. Connect and it greets you;
  `?` lists its commands (NODES, ROUTES, BBS with paged mail, CHAT with
  two pretend members, `C GB7SJ` onward to a second pretend node); `B`
  disconnects; anything else is echoed, as GB7SIM always has. It sends
  a NET/ROM NODES broadcast now and then (first after `first_nodes_s`)
  and answers ARP and ping at 44.131.1.2 (IP over AX.25, datagram mode).

Since 0.3.0, for the Mailbox page's mail client (`_mail_stations`):
`M0SIM` is another OpenHamShack station whose mailbox answers on
`M0SIM-1` (so packet mail to M0SIM is delivered straight to it; not
G4SIM, because G4SIM-9 the tracker is the same call on the same channel
and its engine answers a connect to any G4SIM SSID it does not listen on
with DM, as one station's would), `GB7SBB-1` is a home BBS (ohs-libs' own
controller BBS: set it as Settings › Mail › Home BBS to deliver and
collect through it), and `GB7RMS-10` is a pretend Winlink gateway (b2f's
Gateway: any password, a welcome email waiting on the first connect,
nothing goes anywhere). All three answer only what the operator's
station sends, which needs transmit armed.

`leave_mail()` has G4SIM connect to the operator's mailbox and leave a
message, as a friend would, so the Mailbox page can be tried end to end.
It happens only when the operator asks (the Mailbox page's button), never
on a timer: a message that turns up on its own would look like a real
station to a tester who forgot they were on the simulator.

**Thread safety.** sim.Medium was written for one thread. Here the
engines' runner threads key their radios and hand them frames, while the
medium's own thread polls it, and the medium calls back into engines (which
take their own locks). So a radio does not touch the medium when it is
handed frames: it queues them, and the medium thread adds them before each
poll. The medium thread never holds a lock while it calls an engine, so no
lock is ever taken in two orders.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time

from shack_packet import aprs
from shack_packet.aprs_station import AprsConfig, AprsStation, Beacon
from shack_packet.engine import Engine, Runner
from shack_packet.identity import Station
from shack_packet.ip import IpConfig, IpInterface
from shack_packet.sim import Medium, SimRadio
from shack_packet.simnode import SimBpqNode, SimNodeConfig

log = logging.getLogger(__name__)

TRACKER, FIXED, NODE = "G4SIM-9", "G4SIM", "GB7SIM"
#: 0.3.0's mail stations: a home BBS and a Winlink gateway (_mail_stations).
SIM_BBS, SIM_RMS, SIM_FRIEND = "GB7SBB", "GB7RMS", "M0SIM"
#: GB7SIM's IP address: it answers ARP and ping there, so the IP page's
#: tools have someone to talk to (set your own in 44.131.0.0/16).
NODE_IP = "44.131.1.2"
#: What GB7SIM advertises and lists, and the node `C GB7SJ` reaches. Names
#: like the real GB7 nodes', which exist only in here.
NODE_ALIAS, ONWARD = "SIM", "GB7SJ"
NODE_TABLE = [("SJ", ONWARD, 200), ("ABDN", "GB7AB", 150), ("BRSTL", "GB7BS-7", 120)]


class WallClock:
    """sim.Medium reads `clock.now`; engines call the clock."""

    @property
    def now(self) -> float:
        return time.monotonic()

    def __call__(self) -> float:
        return time.monotonic()


class LiveRadio(SimRadio):
    """A SimRadio whose transmissions reach the medium through its thread."""

    def __init__(self, band: SimBand, name: str, **kw):
        super().__init__(band.medium, name, **kw)
        self.band = band
        self.open_count = 0

    def send_frames(self, frames, txdelay_s, airtime_s) -> None:
        if not self.keyed:
            raise RuntimeError("frames sent to an unkeyed radio")
        self.band._tx.put((self, list(frames), txdelay_s, airtime_s))
        self.band._wake.set()

    # the app's link interface (links.py): a simulator radio opens at once
    def open(self):
        self.open_count += 1
        return self

    def close(self) -> None:
        self.keyed = False

    @property
    def is_open(self) -> bool:
        return True


class SimBand:
    """The medium, its thread, and the pretend stations. `start()` brings
    them up; `radio()` is the operator's radio on the band."""

    def __init__(self, lat: float = 55.9, lon: float = -3.2, *, loss: float = 0.0, seed: int = 7,
                 neighbours: bool = True, beacon_every_s: float = 60.0, first_beacon_s: tuple = (2.0, 6.0),
                 stagger_s: float = 0.0, first_nodes_s: float = 20.0, nodes_every_s: float = 600.0):
        self.clock = WallClock()
        self.medium = Medium(self.clock, "frames", loss=loss, seed=seed)
        self._tx: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lat, self.lon = lat, lon
        self.with_neighbours = neighbours
        self.beacon_every_s = beacon_every_s
        self.first_beacon_s = first_beacon_s
        self.stagger_s = stagger_s
        self.engines: list[Engine] = []
        self.by_call: dict[str, Engine] = {}
        self.runners: list[Runner] = []
        self.aprs: dict[str, AprsStation] = {}
        self._operator: LiveRadio | None = None
        self.node: SimBpqNode | None = None
        self.nodes_every_s = nodes_every_s
        self._nodes_due = time.monotonic() + first_nodes_s

    def radio(self) -> LiveRadio:
        if self._operator is None:
            self._operator = LiveRadio(self, "operator")
            self.medium.radios.append(self._operator)
        return self._operator

    def radio_for(self, name: str) -> LiveRadio:
        """Another radio on the band, for a station a test brings along
        (a guest calling the operator's room, say)."""
        radio = LiveRadio(self, name)
        self.medium.radios.append(radio)
        return radio

    # -- the pretend stations -------------------------------------------------

    def _station(self, call: str) -> Engine:
        radio = LiveRadio(self, call)
        self.medium.radios.append(radio)
        engine = Engine(Station.of(call, tx_enabled=True), radio, clock=self.clock)
        self.engines.append(engine)
        self.by_call[call] = engine
        return engine

    def _offset(self, km_north: float, km_east: float) -> tuple[float, float]:
        lat = self.lat + km_north / 111.2
        lon = self.lon + km_east / (111.2 * max(0.1, math.cos(math.radians(self.lat))))
        return max(-89.9, min(89.9, lat)), ((lon + 180.0) % 360.0) - 180.0

    def _cfg(self, k: int) -> dict:
        """The k-th pretend station's beacon timing. `stagger_s` moves each
        station's first beacon that much later than the one before, so the
        three do not all reach for the channel in the same second.

        Why: the medium learns of a transmission only when its own thread
        next polls (LiveRadio, above), so a station's DCD is as stale as
        that thread is late. On a loaded machine two stations both found
        the channel clear and collided, losing both first beacons until the
        next round a minute later; the APRS-page test failed about one run
        in five that way (6 collisions counted in each failure, none in a
        pass). On the air that is a real hidden-terminal loss; here it is
        only the scheduler, so a test that needs every station heard
        promptly spaces them out."""
        lo, hi = self.first_beacon_s
        shift = k * self.stagger_s
        return dict(min_interval_s=self.beacon_every_s, min_gap_s=1.0, first_delay_s=(lo + shift, hi + shift))

    def _neighbours(self) -> None:
        cfg = self._cfg(0)
        tracker = self._station(TRACKER)
        lat, lon = self._offset(4.0, 6.0)
        self.aprs[TRACKER] = AprsStation(tracker, AprsConfig(beacons=[Beacon(
            aprs.format_position(lat, lon, "/>", "simulated mobile"), every_s=self.beacon_every_s)], **cfg))
        cfg = self._cfg(1)

        fixed = self._station(FIXED)
        lat, lon = self._offset(-7.0, -3.0)
        station = AprsStation(fixed, AprsConfig(beacons=[Beacon(
            aprs.format_position(lat, lon, "/-", "simulated home station"), every_s=self.beacon_every_s)], **cfg))
        self.aprs[FIXED] = station

        def answer(message, station=station):
            station.send_message(message.source, f"Got it: {message.text[:40]}")
        station.on_message = answer

        cfg = self._cfg(2)
        node = self._station(NODE)
        lat, lon = self._offset(10.0, -12.0)
        self.aprs[NODE] = AprsStation(node, AprsConfig(beacons=[Beacon(
            aprs.format_position(lat, lon, "/B", "GB7SIM node, connect for a chat"),
            every_s=self.beacon_every_s)], **cfg))
        self.node = SimBpqNode(node, SimNodeConfig(
            NODE_ALIAS, NODE, greeting="Welcome to GB7SIM, a simulated node. ? for help.", echo=True,
            nodes=NODE_TABLE, routes=[(1, ONWARD, 192, 3)]),
            onward=[SimNodeConfig("SJ", ONWARD, nodes=[(NODE_ALIAS, NODE, 192), ("ZZSIM", "GB7ZZ", 180)])])
        IpInterface(node, IpConfig(address=NODE_IP, network="44.131.0.0/16", answer_ping=True))

    def leave_mail(self, mailbox: str, subject: str, body: str, sender: str = FIXED):
        """`sender` connects to `mailbox` (the operator's, e.g. GM4BRI-1),
        sends the message with the mailbox's S command and disconnects. The
        operator's station answers only while armed, like any connection.
        Returns the sender's Connection."""
        engine = self.by_call[sender]
        conn = engine.connect(mailbox)
        conn.on_data = lambda data: None

        def on_event(event):
            # On every "connected": when our SABM retry crosses the UA the
            # link resets and comes up again, and the reset has thrown away
            # whatever was queued - the message with it. (The node's
            # greeting had the same trouble; see shack_packet.simnode.)
            if event.kind == "connected":
                text = body.replace("\r\n", "\r").replace("\n", "\r")
                conn.send(f"S\r{subject}\r{text}\r/EX\rB\r")
        conn.on_event = on_event
        return conn

    def _mail_stations(self) -> None:
        from shack_packet import b2f
        from shack_packet.bbs import Bbs, BbsConfig
        from shack_packet.bbsstore import BbsStore
        from shack_packet.mailbox import Mailbox, MailboxConfig, MemoryStore
        self.friend_mailbox = Mailbox(self._station(SIM_FRIEND), MemoryStore(), MailboxConfig(enabled=True, ssid=1))
        self.bbs_store = BbsStore(home=SIM_BBS)
        Bbs(self._station(SIM_BBS), self.bbs_store, BbsConfig(ssid=1, info="A simulated BBS. No RF."))

        def welcome(call: str) -> list:
            return [b2f.WinlinkMessage(b2f.new_mid(SIM_RMS), "SMTP:test@example.com", [call],
                                       "Welcome to the simulated gateway",
                                       "This came from GB7RMS-10, a pretend Winlink gateway inside the app.\n"
                                       "Nothing you send here leaves your computer.\n73",
                                       date=time.time())]
        self.rms = b2f.Gateway(self._station(SIM_RMS), ssid=10, accounts=None, welcome=welcome)

    # -- the medium's thread ---------------------------------------------------

    def start(self) -> SimBand:
        if self._thread is not None:
            return self
        if self.with_neighbours:
            self._neighbours()
            self._mail_stations()
        self._thread = threading.Thread(target=self._run, name="sim-band", daemon=True)
        self._thread.start()
        self.runners = [Runner(e).start() for e in self.engines]
        return self

    def stop(self) -> None:
        for r in self.runners:
            r.stop()
        self.runners = []
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.node is not None and time.monotonic() >= self._nodes_due:
                self._nodes_due = time.monotonic() + self.nodes_every_s
                try:
                    self.node.broadcast()
                except Exception:                   # noqa: BLE001 - a pretend node must not stop the band
                    log.exception("simulated NODES broadcast")
            try:
                while True:
                    radio, frames, txdelay, airtime = self._tx.get_nowait()
                    SimRadio.send_frames(radio, frames, txdelay, airtime)
            except queue.Empty:
                pass
            except Exception:                       # noqa: BLE001 - a bad transmission must not stop the band
                log.exception("simulated transmission")
            try:
                nxt = self.medium.poll(self.clock.now)
            except Exception:                       # noqa: BLE001
                log.exception("simulated medium")
                nxt = None
            wait = 0.05 if nxt is None else max(0.0, min(0.05, nxt - self.clock.now))
            self._wake.wait(wait)
            self._wake.clear()
