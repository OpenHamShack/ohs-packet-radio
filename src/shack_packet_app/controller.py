"""The station behind the window: engine, radio link, services and SDR, run
off the GUI thread. The window only draws what this holds and calls its
commands; it never touches the engine, a radio or a socket itself.

    ctl = Controller(settings)
    ctl.start()                 # builds everything; the radio opens on a thread of its own
    ctl.status()                # what the status bar shows
    ctl.connect("GB7SIM")       # a chat session
    ctl.events.get_nowait()     # AppEvents for the window, in order
    ctl.stop()

**Threads.** The engine runs on its Runner; each radio link has its own
threads; the SDR monitor has a reader and a worker; opening a radio (up to
20 s on a LAN radio) runs on a short-lived thread. Every callback from
those lands here and only appends to a list or a queue under this
object's lock, so nothing the window reads is ever half-written and no
engine thread waits for the window. Commands take the engine's own lock
for as long as one call into it takes, and none blocks.

**No listener sockets.** Nothing here opens a server: the simulator is in
the process, an Icom is reached as a client, rtl_tcp as a client, and the
IP stack has no operating-system interface (ipnet.py).

**Transmit** is the ArmedStation's (txarm.py): `arm()` checks callsign,
setup, radio and band plan, and the station's refusal() - asked by the
channel and the radio link at the moment of keying - checks the arming and
its expiry every time. `stop()`, `apply()` and losing the radio disarm.
Nothing here transmits on its own: beacons only when the operator has
switched them on, and the mailbox and chat only answer stations that
connected (and only while armed, like everything else).
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from shack_packet import aprs, ax25
from shack_packet.aprs_station import AprsConfig, AprsStation, Beacon
from shack_packet.channel import ChannelConfig
from shack_packet.digi import DigiConfig, Digipeater
from shack_packet.engine import Engine, Runner
from shack_packet.errors import PacketError
from shack_packet.identity import IdentityError, is_callsign
from shack_packet.ip import IpConfig, IpInterface
from shack_packet.mailbox import FileStore, Mailbox, MailboxConfig, MemoryStore

from shack_packet.netrom import NodeTable

from . import diagupload, fastmodes, grid, host, ipnet, ohsclient, presets, scripts as scripts_mod, sdrplan
from .aprsextras import AprsExtras
from .bbs import SessionReader
from .files import FilesManager
from .logbook import Contact, LogBook
from .room import Room
from .linkctl import LinkTest
from .links import REAL, AudioTap, LinkError, make_link, power_status
from .mailclient import MailClient
from .pathrun import PathTest
from .rigfeatures import RigFeatures
from .sdrmon import SdrMonitor
from .settings import Settings
from .simband import SimBand
from .tracks import Tracks
from .tuning import TuneWatch
from .txarm import ArmedStation

log = logging.getLogger(__name__)

MONITOR_ROWS = 3000
#: The pad's BEACON key sends at most once a minute: APRS's own floor
#: between two beacons from one station (aprs_station.AprsConfig.min_gap_s
#: is shorter, but that is for a station's several beacons, not one pressed
#: again and again).
BEACON_NOW_GAP_S = 60.0
#: The drive-level helper's burst: three UI frames of about 140 bytes, a
#: second or so of carrier at 1200 baud and a fraction of one at the fast
#: modes - long enough to read a power meter's peak, short enough that an
#: amplifier driven into compression is not left there.
DRIVE_BURST_FRAMES = 3


@dataclass
class AppEvent:
    """What the window hears of: `kind` and `reason` are keys it translates
    (ui/texts.py), `detail` plain data."""
    kind: str
    reason: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class MonitorRow:
    seq: int
    time: float               # wall clock (UTC seconds)
    direction: str            # rx | tx | sdr
    channel: str              # "radio", or the SDR channel's id
    text: str                 # the TNC-style monitor line
    source: str = ""
    destination: str = ""
    type: str = ""            # the AX.25 frame type
    aprs_kind: str = ""
    fx25: bool = False
    level_db: float | None = None
    #: For a frame whose information field is not text (IP, ARP, NET/ROM):
    #: its PID and length; `text` is then the addresses alone.
    pid: int | None = None
    info_len: int = 0


@dataclass
class ChatLine:
    time: float
    direction: str            # rx | tx | note
    text: str                 # the text, or a note's key


@dataclass
class ChatSession:
    id: int
    remote: str
    via: list[str]
    incoming: bool
    conn: object
    lines: list[ChatLine] = field(default_factory=list)
    rev: int = 0
    _partial: str = ""
    #: 0.3.0: what the text means (bbs.py), a running connect script
    #: (scripts.py), the file the session is captured to, and when the
    #: link came up (for the log; None until it has).
    reader: SessionReader = field(default_factory=SessionReader)
    script: object = None
    capture: object = None
    connected_at: float | None = None
    logged: bool = False

    @property
    def kind(self) -> str:
        return self.reader.kind

    @property
    def state(self) -> str:
        return self.conn.state.value

    @property
    def connected(self) -> bool:
        return bool(self.conn.connected)


@dataclass
class MessageRow:
    time: float
    direction: str            # in | out
    peer: str
    text: str
    out: object = None        # the library's OutgoingMessage, whose state moves on

    @property
    def state(self) -> str:
        return self.out.state if self.out is not None else "received"


class Controller:
    def __init__(self, settings: Settings, *, secrets: dict | None = None, clock=time.monotonic, wall=time.time,
                 link_factory=make_link, band_factory=None, sdr_factory=None, sound_backend=None,
                 open_port=None, mailbox_store=None, ohs_transport=None):
        self.settings = settings
        self.secrets = dict(secrets or {})
        self.clock = clock
        self.wall = wall
        self.link_factory = link_factory
        self.band_factory = band_factory or self._default_band
        self.sdr_factory = sdr_factory
        self.sound_backend = sound_backend
        self.open_port = open_port
        self._mailbox_store = mailbox_store
        self._ohs_transport = ohs_transport
        self.ohs = ohsclient.ControllerClient(settings.controller, self.secrets, transport=ohs_transport)
        self.events: queue.Queue[AppEvent] = queue.Queue()
        self._lock = threading.RLock()
        self._seq = 0
        self.rows: collections.deque[MonitorRow] = collections.deque(maxlen=MONITOR_ROWS)
        self.rows_rev = 0
        self.sessions: dict[int, ChatSession] = {}
        self._next_session = 1
        self.messages: list[MessageRow] = []
        self.messages_rev = 0
        #: Files and pictures over chat sessions (files.py, 0.3.0).
        self.files = FilesManager(self)
        self.guard_log: collections.deque = collections.deque(maxlen=200)
        self.link_events: collections.deque = collections.deque(maxlen=500)
        #: What the radio link set on the radio and put back, from its
        #: setup-* events (shack_packet.radios.rig): "report", "restored" and
        #: "recovered" are lists of report lines (dicts: key, status, before,
        #: after, note); "warnings" is [(key, sentence)]. Kept across a
        #: reconnect until the next report, so the window can still show
        #: what was put back after the link has gone.
        self.radio_setup: dict = {"report": [], "restored": [], "recovered": [], "warnings": []}
        self.station: ArmedStation | None = None
        self.engine: Engine | None = None
        self.runner: Runner | None = None
        self.link = None
        self.tap: AudioTap | None = None
        self.band: SimBand | None = None
        self.aprs: AprsStation | None = None
        self.mailbox: Mailbox | None = None
        self.ip: IpInterface | None = None
        self.pinger: ipnet.Pinger | None = None
        self.os_if: ipnet.OsInterface = ipnet.os_interface()
        self.digi: Digipeater | None = None
        self.sdr: SdrMonitor | None = None
        self.link_state = "none"           # none | opening | open | failed | closed
        self.link_error = ""
        self.link_error_detail = ""
        self.last_tx_wall: float | None = None
        self.last_tx_frames = 0
        self.tx_refusals = collections.Counter()
        self.started = False
        self._opener: threading.Thread | None = None
        self._beacon_now_at: float | None = None
        #: Every position heard, for the map's tracks (tracks.py).
        self.tracks = Tracks(settings.map.track_minutes * 60.0)
        #: The Link test page's run with a friend (linkctl.py); idle unless joined.
        self.linktest = LinkTest(self)
        #: 0.3.0 workstream F: the IC-9700's scope, adaptive power,
        #: satellite tracking and its transmit gate (rigfeatures.py).
        self.rigx = RigFeatures(self)
        #: Which way the frame the monitor is about to see came (_tap):
        #: set here as well as at start, because the services and tests
        #: hand frames to _heard_frame without an engine running.
        self._heard_via = "radio"
        #: The Path test page's runs (pathrun.py); its last report outlives
        #: a restart, for the diagnostics bundle.
        self.pathtest = PathTest(self)
        #: 0.3.0. Every NET/ROM node heard or listed (the Nodes page); the
        #: log (logbook.py); a room hosted here (room.py); HamQTH lookups
        #: (callbook.py), made when first asked for.
        self.nodes = NodeTable()
        self.logbook = LogBook(_data_file("logbook.json"))
        self.room = Room(self)
        self._callbook = None
        #: 0.3.0: packet and Winlink mail (mailclient.py), and APRS beyond
        #: the fixed beacon (aprsextras.py); each idle until switched on.
        self.mailclient = MailClient(self)
        self.aprsx = AprsExtras(self)
        #: 0.3.2: the radio's own VFO and mode knob, followed (tuning.py).
        self.tuning = TuneWatch(self)

    # -- life ------------------------------------------------------------------

    def _default_band(self) -> SimBand:
        lat, lon = self.position() or (55.95, -3.19)
        return SimBand(lat, lon)

    def position(self) -> tuple[float, float] | None:
        """The GPS's or a typed-in position when there is one, else the
        locator's square centre."""
        pos = self.aprsx.position()
        if pos is not None:
            return pos
        loc = self.settings.station.locator
        if loc and grid.valid(loc):
            return grid.to_latlon(loc)
        return None

    def start(self) -> None:
        if self.started:
            return
        s = self.settings
        self.station = ArmedStation(clock=self.clock, on_change=self._arming_changed)
        try:
            self.station.set_callsign(s.station.address() or None)
        except (IdentityError, PacketError):
            self.station.callsign = None
        if s.radio.kind == "simulator":
            self.band = self.band_factory()
            self.band.start()
        self.link = None
        self.link_state, self.link_error, self.link_error_detail = "none", "", ""
        try:
            self.link = self.link_factory(s.radio, station=self.station, frequency_hz=int(s.channel.frequency_hz),
                                          transmit=bool(s.tx.set_up), password=self.secrets.get("icom_password", ""),
                                          band=self.band, sound_backend=self.sound_backend, open_port=self.open_port,
                                          sdr=s.sdr_radio, secrets=self.secrets, fast=s.fast,
                                          **({"dd": s.dd} if s.radio.kind == "ic-9700-dd" else {}))
        except LinkError as e:
            self.link_state, self.link_error, self.link_error_detail = "failed", e.reason, e.detail
            self._emit("radio", "link-failed", {"reason": e.reason, "detail": e.detail})
        fx25 = s.channel.fx25 if s.channel.fx25 in ("auto", 16, 32, 64) else None
        # A Pluto on a fast mode or adapting (fastmodes.station_fast): the
        # channel's mode, TXDELAY and window and the data link's k, PACLEN,
        # SREJ and ladder from its fast settings. Everything else, and a
        # Pluto on AFSK 1200, keeps the defaults 0.2.0 went on the air with.
        channel, link_config = ChannelConfig(fx25=fx25), None
        fs = fastmodes.station_fast(s)
        if fs is not None and self.link is not None:
            try:
                channel, link_config = fs.channel_config(fx25=fx25), fs.link_config()
            except Exception as e:                  # noqa: BLE001 - the link refused these settings already
                log.warning("fast-mode settings not used: %s", e)
        self.engine = Engine(self.station, self.link, channel=channel, link=link_config, clock=self.clock)
        if self.link is not None and not getattr(self.link, "carries_frames", False):
            self.tap = AudioTap(self.engine)
            self.link.attach(self.tap)
        self.engine.on_event = self._engine_event
        self.engine.monitors.append(self._heard_frame)
        self.engine.taps.append(self._tap)
        self._heard_via = "radio"
        prev_tx = self.engine.channel.on_transmit

        def on_transmit(start, end, frames, prev=prev_tx):
            if prev is not None:
                prev(start, end, frames)
            self._sent_frames(frames)
        self.engine.channel.on_transmit = on_transmit
        self._services()
        self.files.start()
        self.runner = Runner(self.engine).start()
        self.started = True
        if self.link is not None:
            self._open_link()
        if s.sdr.source != "none":
            self.start_sdr()
        self._emit("state", "started")
        self.rigx.start()

    def _services(self) -> None:
        s, engine = self.settings, self.engine
        has_call = self.station.callsign is not None
        beacons = []
        pos = self.position()
        if s.aprs.beacon and pos is not None:
            try:
                text = aprs.format_position(pos[0], pos[1], s.aprs.symbol or "/-", s.aprs.comment)
                beacons.append(Beacon(text, every_s=max(10, int(s.aprs.every_min)) * 60.0, via=_path(s.aprs.path)))
            except PacketError:
                log.warning("APRS beacon not made: bad symbol or position")
        beacons = self.aprsx.beacons(beacons, _path(s.aprs.path))
        self.aprs = AprsStation(engine, AprsConfig(beacons=beacons, message_via=_path(s.aprs.path)))
        self.aprs.on_message = self._aprs_message
        if has_call:
            self.aprsx.start(engine, _path(s.aprs.path))
        if s.digi.enabled and has_call:
            aliases = [a.strip().upper() for a in s.digi.aliases.split(",") if a.strip()]
            self.digi = Digipeater(engine, DigiConfig(enabled=True, aliases=aliases))
        if has_call and s.ip.enabled and s.ip.address:
            try:
                self.ip = IpInterface(engine, IpConfig(address=s.ip.address, network=s.ip.network or None,
                                                       mode=s.ip.mode, answer_ping=True))
                self.pinger = ipnet.Pinger(self.ip, self.clock)
                self.ip.on_packet = self._ip_packet
                self.os_if.attach(self.ip)
            except (ValueError, PacketError) as e:
                self.ip = self.pinger = None
                self._emit("ip", "bad-address", {"detail": str(e)})
        if has_call:
            store = self._mailbox_store
            if store is None:
                try:
                    store = FileStore(host.data_dir() / "mailbox.json")
                except (OSError, ValueError):
                    store = MemoryStore()
            ssid = s.mailbox.ssid if 0 <= int(s.mailbox.ssid) <= 15 else 1
            self.mailbox = Mailbox(engine, store, MailboxConfig(enabled=bool(s.mailbox.enabled),
                                                               ssid=ssid or None,
                                                               allow_third_party=bool(s.mailbox.third_party)),
                                   wall_clock=self.wall)
            engine.listen(self.station.address(), self._incoming_chat)
            self.mailclient.start(engine, store)
        engine.services.append(_ScriptClock(self))
        self.tuning.pending = self.tuning.bad_mode = None
        engine.services.append(self.tuning)

    def stop(self) -> None:
        if not self.started:
            return
        self.linktest.leave("stopped")
        self.pathtest.stop("stopped")
        self.room.stop()
        for sess in list(self.sessions.values()):
            self._session_over(sess)
        if self._callbook is not None:
            # Its thread stops here; the next lookup makes a new one with
            # whatever account the settings then hold.
            self._callbook.stop()
            self._callbook = None
        if self.station is not None:
            self.station.disarm("stopped")
        self.stop_sdr()
        self.aprsx.stop()
        self.mailclient.stop()
        if self.runner is not None:
            self.runner.stop()
        # A satellite pass's VFOs and adaptive power's RF POWER go back
        # before the link's own restore (rigfeatures.py).
        self.rigx.stop()
        link = self.link
        if link is not None and hasattr(link, "close"):
            try:
                link.close()
            except Exception:                       # noqa: BLE001 - closing regardless
                log.exception("closing the radio link")
        if self.band is not None:
            self.band.stop()
        try:
            self.os_if.detach()
        except Exception:                           # noqa: BLE001
            log.exception("detaching the IP interface")
        self.link_state = "closed"
        self.started = False
        self.engine = self.runner = self.aprs = self.mailbox = self.ip = self.pinger = self.digi = None
        self.band = None
        with self._lock:
            self.sessions.clear()
        self._emit("state", "stopped")

    def emergency_unkey(self) -> None:
        """Unkey the radio now, without waiting on anything the link may be
        stuck in: for a crash (applog.on_crash). close() does it properly
        on the way out; this is for when that may never run."""
        link = self.link
        if link is not None and hasattr(link, "emergency_unkey"):
            link.emergency_unkey()

    def apply(self, settings: Settings, secrets: dict | None = None) -> None:
        """New settings: everything is rebuilt, and starts disarmed."""
        if self._callbook is not None and secrets is not None:
            self._callbook.set_credentials(secrets.get("hamqth_user", ""), secrets.get("hamqth_password", ""))
        self.stop()
        self.tracks.keep_s = settings.map.track_minutes * 60.0
        self.settings = settings
        if secrets is not None:
            self.secrets = dict(secrets)
        self.ohs = ohsclient.ControllerClient(settings.controller, self.secrets, transport=self._ohs_transport)
        self.start()

    # -- the radio link -----------------------------------------------------------

    def _open_link(self) -> None:
        link = self.link
        self.link_state = "opening"
        self._emit("radio", "opening")

        def go():
            # D1 (18/10/2026): a radio the paired controller has reserved or
            # is using is never opened from here (ohsclient.py). Asked on
            # this thread, as the radio itself is: it may wait on the network.
            held = [r for what, r in self.ohs.conflicts(self.settings.radio, self.settings.sdr, self.settings.sdr_radio)
                    if what == "radio"]
            if held:
                self._controller_refused(held[0], "radio")
                return
            try:
                link.open()
            except Exception as e:                  # noqa: BLE001 - LinkRefused, or anything a radio does
                self.link_state = "failed"
                self.link_error = getattr(e, "reason", "") or "radio-error"
                self.link_error_detail = getattr(e, "detail", "") or str(e)
                log.warning("radio link did not open: %s (%s)", self.link_error, self.link_error_detail)
                self._emit("radio", "link-failed", {"reason": self.link_error, "detail": self.link_error_detail})
                return
            if self.link is link:
                self.link_state = "open"
                self._emit("radio", "link-open", {"setup": dict(getattr(link, "setup", {}) or {})})
        self._opener = threading.Thread(target=go, name="radio-open", daemon=True)
        self._opener.start()

    def _controller_refused(self, r, what: str) -> None:
        reason = "controller-reserved" if r.reserved else "controller-in-use"
        detail = f"{r.label} ({r.reserved_by or r.holder or r.state})"
        log.warning("%s not opened: the controller %s lists %s as %s", what, self.ohs.settings.name, r.label,
                    r.state)
        if what == "radio":
            self.link_state, self.link_error, self.link_error_detail = "failed", reason, detail
            self._emit("radio", "link-failed", {"reason": reason, "detail": detail})
        else:
            self._emit("sdr", "failed", {"reason": reason, "detail": detail})

    def reconnect(self) -> None:
        if self.link is None or self.link_state == "opening":
            return
        try:
            self.link.close()
        except Exception:                           # noqa: BLE001
            log.exception("closing before reconnecting")
        self.station.disarm("disarmed")
        self._open_link()

    def can_transmit(self) -> bool:
        """Whether this link has a transmit path at all (not whether it may now)."""
        r = self.settings.radio
        if self.link is None or r.kind == "none":
            return False
        if r.kind == "soundcard" and r.ptt == "none":
            return False
        if r.kind == "sdr-rx":
            return False
        return True

    # -- arming ---------------------------------------------------------------------

    def arm_refusal(self) -> tuple[str, str]:
        """("", "") if Arm may be pressed now, else (ARM_REFUSALS key, detail)."""
        if self.station is None or self.station.callsign is None or not is_callsign(self.station.callsign.call):
            return "no-callsign", ""
        if not self.settings.tx.set_up:
            return "not-set-up", ""
        if not self.can_transmit():
            return "receive-only", ""
        if self.link_state != "open":
            return "no-radio", self.link_error
        why, detail = self._sdr_arm_refusal()
        if why:
            return why, detail
        if self.linktest.blocks_arming():
            return "linktest-receiver", ""
        why, detail = self.tuning.arm_refusal()
        if why:
            return why, detail
        # The licence and the band, for packet as such. A fast mode's width
        # is judged once, by fastsettings' UK high-speed data segments in
        # _sdr_arm_refusal above: the dataset's plan has gaps where a wide
        # signal would pass (144.8 MHz, 1299 MHz), so it is not asked twice.
        why, detail = self.rigx.arm_refusal()
        if why:
            return why, detail
        verdict = presets.judge(self.settings.station.profile(), self.settings.channel.frequency_hz)
        if not verdict["allowed"] and not self.rigx.band_plan_excused(verdict, self.settings.channel.frequency_hz):
            return "band-plan", verdict["refusal"]
        return "", ""

    def _sdr_arm_refusal(self) -> tuple[str, str]:
        """An SDR that transmits (0.2): never without the operator's word
        that a band filter is fitted (D9's warning: an SDR's harmonics need
        one before any antenna), never a Pluto whose guard is not running or
        whose fast-mode settings are refused (a mode wider than 25 kHz
        outside the band plan's high-speed data segments), never a HackRF
        whose firmware cannot stop on an underrun. The links
        refuse each key the same way; this says so at Arm instead."""
        from .settings import SDR_TX_KINDS
        kind = self.settings.radio.kind
        if kind not in SDR_TX_KINDS:
            return "", ""
        if not self.settings.sdr_radio.filter_confirmed:
            return "sdr-filter", ""
        # The fast settings, and the UK band plan for a mode wider than
        # 25 kHz (fastsettings.wide_data_refusal): receiving there is fine,
        # so the link opened; sending is not.
        why, detail = fastmodes.arm_refusal(self.settings)
        if why:
            return why, detail
        link = self.link
        if kind == "pluto" and getattr(link, "guard_state", "running") in ("none", "failed"):
            return "tx-unguarded", getattr(link, "unguarded_why", "") or ""
        if kind == "hackrf" and getattr(link, "tx_refused_why", None):
            return "tx-firmware", link.tx_refused_why
        return "", ""

    def arm(self, minutes: float | None = None) -> tuple[bool, str, str]:
        why, detail = self.arm_refusal()
        if why:
            self._emit("tx", "arm-refused", {"reason": why, "detail": detail})
            return False, why, detail
        self.station.arm(minutes if minutes is not None else self.settings.tx.arm_minutes)
        log.warning("transmit ARMED for %.0f s as %s", self.station.remaining_s(), self.station.callsign)
        return True, "", ""

    def disarm(self) -> None:
        if self.station is not None:
            self.station.disarm("disarmed")
            link = self.link
            abort = getattr(link, "abort", None)
            if abort is not None and getattr(link, "keyed", False):
                abort()

    def halt(self) -> None:
        """Stop transmitting now (the pad's HALT): disarmed, which drops
        what is queued at the channel's next decision (shack_packet's
        channel refuses and counts it), and a transmission in progress cut.
        The same as Disarm but said as "halted", so the log and the status
        line say which key it was."""
        if self.station is not None:
            self.station.disarm("halted")
            link = self.link
            abort = getattr(link, "abort", None)
            if abort is not None and getattr(link, "keyed", False):
                abort()

    def beacon_now(self) -> str:
        """Send this station's position beacon now (the pad's BEACON key).
        "" when it was queued, else why not, as a reason key: "not-armed",
        "no-beacon" (beaconing is off or there is no locator), or
        "too-soon" within BEACON_NOW_GAP_S of the last one sent this way,
        so a key pressed again and again cannot fill the channel. Queued
        through the engine like any beacon: nothing keys unless the
        station is still armed when the channel decides."""
        if self.station is None or self.engine is None or self.aprs is None or not self.station.armed:
            return "not-armed"
        s, pos = self.settings, self.position()
        if not s.aprs.beacon or pos is None:
            return "no-beacon"
        now = self.clock()
        if self._beacon_now_at is not None and now - self._beacon_now_at < BEACON_NOW_GAP_S:
            return "too-soon"
        try:
            text = aprs.format_position(pos[0], pos[1], s.aprs.symbol or "/-", s.aprs.comment)
        except PacketError:
            return "no-beacon"
        self.engine.send_ui(self.aprs.config.tocall, text, via=_path(s.aprs.path))
        self._beacon_now_at = now
        log.info("beacon sent now, from the pad")
        return ""

    def drive_burst(self, gain_db: float) -> str:
        """The drive-level helper's burst (fastmodes.DriveSession): a few
        UI frames to TEST, "" when queued or a reason key. Only while armed,
        and like every frame it is refused again at the channel and the link
        if the arming, the guard or the band plan says no: the helper has no
        transmit path of its own. A Pluto only, and only one whose link was
        made at `gain_db` - the step the operator is looking at."""
        if self.station is None or self.engine is None or not self.station.armed:
            return "not-armed"
        if self.settings.radio.kind != "pluto" or self.link_state != "open":
            return "no-radio"
        if getattr(self.link, "tx_gain_db", gain_db) != gain_db:
            return "drive-step"
        for i in range(DRIVE_BURST_FRAMES):
            self.engine.send_ui("TEST", f"OHS drive level {gain_db:g} dB {i + 1}/{DRIVE_BURST_FRAMES} " + "x" * 100)
        log.warning("drive-level burst queued at %.2f dB", gain_db)
        return ""

    def _arming_changed(self, what: str) -> None:
        if what != "armed":
            log.info("transmit disarmed: %s", what)
        self._emit("tx", what)

    # -- what the engine tells us --------------------------------------------------------

    def _emit(self, kind: str, reason: str = "", detail: dict | None = None) -> None:
        self.events.put(AppEvent(kind, reason, dict(detail or {})))

    def _radio_setup_event(self, reason: str, detail: dict) -> None:
        lines = list(detail.get("lines") or [])
        if reason == "setup-recovered":
            # The first of a new open's events, when there is one at all.
            self.radio_setup = {"report": [], "restored": [], "recovered": lines, "warnings": []}
        elif reason == "setup-report":
            if self.radio_setup["report"] or self.radio_setup["restored"]:
                self.radio_setup = {"report": [], "restored": [], "recovered": [], "warnings": []}
            self.radio_setup["report"] = lines
        elif reason == "setup-restored":
            self.radio_setup["restored"] = lines
        elif reason == "setup-warning":
            self.radio_setup["warnings"].append((detail.get("what", ""), detail.get("note", "")))

    def _engine_event(self, event) -> None:
        detail = dict(event.detail or {})
        kind = event.kind
        if kind == "tx-refused":
            self.tx_refusals[event.reason] += 1
        elif kind == "ip-refused":
            self.guard_log.append((self.wall(), event.reason, detail.get("length", 0)))
        elif kind in ("radio", "unkeyed"):
            self.link_events.append((self.wall(), kind, event.reason, detail))
            self._radio_setup_event(event.reason, detail)
            if event.reason == "disconnected":
                self.station.disarm("radio-lost")
                diagupload.notable("radio-lost", str(detail.get("error", "")))
        elif kind == "mail" and event.reason == "mail-received":
            msg = detail.get("message")
            if msg is not None:
                # for_me: left for the operator, not third-party mail
                # waiting for another station to collect.
                owner = self.mailbox.owner if self.mailbox is not None else ""
                detail.update(sender=msg.sender, to=msg.to, subject=msg.subject, body=msg.body, id=msg.id,
                              for_me=msg.to == owner)
        elif kind == "mail":
            self.mailclient.event_detail(event.reason, detail)
        if event.connection is not None:
            sess = self._session_of(event.connection)
            if sess is not None:
                self._note(sess, f"{kind}:{event.reason}")
                detail["session"] = sess.id
                self._session_event(sess, kind)
        # Objects (a Connection, an OutgoingMessage) stay here; the window
        # gets plain keys and reads the rest from this object.
        detail = {k: v for k, v in detail.items() if isinstance(v, (str, int, float, bool, type(None)))}
        self._emit(kind, event.reason, detail)

    def _row(self, direction: str, frame: ax25.Frame | None, channel: str = "radio", text: str = "", **kw) -> None:
        with self._lock:
            self._seq += 1
            if frame is not None:
                kind = ""
                if frame.is_ui and frame.pid == 0xF0:
                    try:
                        pkt = aprs.parse(frame)
                        kind = pkt.kind
                    except Exception:               # noqa: BLE001 - a frame we cannot read is still shown
                        pkt, kind = None, ""
                    if pkt is not None and pkt.position is not None and direction in ("rx", "sdr"):
                        self.tracks.update(str(frame.source), self.wall(), pkt.position.lat, pkt.position.lon,
                                           pkt.course_deg, pkt.speed_kmh)
                text, pid, n = frame.monitor(), None, 0
                info = frame.info or b""
                if info and not _printable(info):
                    text = text.split(":", 1)[0] + ":"
                    pid, n = frame.pid, len(info)
                row = MonitorRow(self._seq, self.wall(), direction, channel, text, str(frame.source),
                                 str(frame.destination), frame.type.value, kind, pid=pid, info_len=n, **kw)
            else:
                row = MonitorRow(self._seq, self.wall(), direction, channel, text, **kw)
            self.rows.append(row)
            self.rows_rev += 1

    def _tap(self, kind: str, d: dict) -> None:
        """The engine's taps: which way a frame came (the monitor marks
        those that came by a link test's relay, linkctl.py), and the frames
        that went by relay rather than radio."""
        if kind == "heard":
            self._heard_via = d.get("via", "radio")
        elif kind == "return-path" and d.get("direction") == "out":
            f = d.get("frame")
            self._row("tx", f, channel="internet", text=d["raw"].hex() if f is None else "")

    def _heard_frame(self, frame) -> None:
        via, self._heard_via = self._heard_via, "radio"
        self._nodes_heard(frame)
        self._row("rx", frame, channel="internet" if via != "radio" else "radio")

    def _sent_frames(self, frames) -> None:
        self.last_tx_wall = self.wall()
        self.last_tx_frames = len(frames)
        self.aprsx.sent(len(frames))
        for raw in frames:
            f = ax25.parse(raw)
            self._row("tx", f, text=raw.hex() if f is None else "")

    def _sdr_frame(self, d) -> None:
        if d.frame is None:
            return
        self._nodes_heard(d.frame)
        self._row("sdr", d.frame, channel=d.channel, fx25=d.fx25 is not None, level_db=d.level_db)

    # -- chat ------------------------------------------------------------------------------

    def _session_of(self, conn) -> ChatSession | None:
        with self._lock:
            return next((s for s in self.sessions.values() if s.conn is conn), None)

    def _new_session(self, conn, incoming: bool) -> ChatSession:
        with self._lock:
            sess = ChatSession(self._next_session, str(conn.remote), [str(v) for v in conn.via], incoming, conn,
                               reader=SessionReader(str(self.station.address()) if self.station.callsign else ""))
            self._next_session += 1
            self.sessions[sess.id] = sess
        conn.on_data = lambda data, sess=sess: self._chat_data(sess, data)
        self.files.attach(sess)
        return sess

    def _incoming_chat(self, call):
        if not self.settings.chat.accept_incoming:
            return None

        def setup(conn):
            sess = self._new_session(conn, True)
            self._emit("chat", "incoming", {"session": sess.id, "remote": sess.remote})
        return setup

    def connect(self, remote: str, via: str = "") -> ChatSession:
        """A chat session to `remote` through `via` (comma or space separated).
        Raises ValueError for a call AX.25 cannot carry (the window checks
        with `valid_address` first)."""
        if self.engine is None:
            raise RuntimeError("not started")
        target = ax25.Address.from_text(remote.strip(), flag=False)
        path = [ax25.Address.from_text(v, flag=False) for v in _path(via)]
        if self.station.callsign is None:
            raise IdentityError("the station has no callsign")
        conn = self.engine.connect(target, via=path)
        existing = self._session_of(conn)
        if existing is not None:
            return existing
        sess = self._new_session(conn, False)
        self._note(sess, "note:connecting")
        return sess

    def _chat_data(self, sess: ChatSession, data: bytes) -> None:
        text = sess._partial + _decode(data)
        lines = text.replace("\r\n", "\r").replace("\n", "\r").split("\r")
        sess._partial = lines.pop()
        with self._lock:
            for line in lines:
                sess.lines.append(ChatLine(self.wall(), "rx", line))
            if sess._partial and len(sess._partial) > 200:
                lines.append(sess._partial)
                sess.lines.append(ChatLine(self.wall(), "rx", sess._partial))
                sess._partial = ""
            sess.rev += 1
            self._read_along(sess, lines, data)
        # The first new line that has words in it, for a notification
        # (ui/notifier.py); "lines" 0 is a prompt still being typed.
        said = [line for line in lines if line.strip()]
        self._emit("chat", "data", {"session": sess.id, "remote": sess.remote, "lines": len(said),
                                    "text": said[0] if said else ""})

    def chat_flush(self, sess: ChatSession) -> str:
        """A prompt the peer sent without a line end, shown as it is."""
        with self._lock:
            return sess._partial

    def _read_along(self, sess: ChatSession, lines: list[str], data: bytes) -> None:
        """What arrived, for the helpers (bbs.py), the node table, a
        running script and a capture file. Under self._lock."""
        reader = sess.reader
        for line in lines:
            got = reader.line(line)
            if got:
                nodes = [e for e in got if hasattr(e, "alias")]
                routes = [e for e in got if not hasattr(e, "alias")]
                self.nodes.listed(reader.listing.source, nodes, routes, self.wall())
            self._capture(sess, "rx", line)
        reader.partial(sess._partial)
        if sess.script is not None:
            sess.script.heard(_decode(data), self.clock())

    def _capture(self, sess: ChatSession, direction: str, text: str) -> None:
        if sess.capture is None:
            return
        try:
            stamp = time.strftime("%H:%M:%S", time.gmtime(self.wall()))
            sess.capture.write(f"{stamp} {'> ' if direction == 'tx' else ''}{text}\n")
            sess.capture.flush()
        except (OSError, ValueError):
            log.warning("capture to file stopped: the file could not be written")
            sess.capture = None
            self._note(sess, "note:capture-failed")

    def send(self, session_id: int, text: str) -> bool:
        """One line to the peer, ending in CR. An empty or whitespace-only
        line is refused (False) here, not only in the chat page: on 18/10
        Ray's Enter on an empty line went out as a frame holding a bare
        CR, a whole over for nothing, and a pad, a remote client or a
        script reaches this method without passing the page."""
        sess = self.sessions.get(session_id)
        if sess is None or not text.strip():
            return False
        self._send_line(sess, text)
        return True

    def _send_line(self, sess: ChatSession, text: str) -> None:
        """A line, CR-ended, as it is - empty included. Only for what the
        operator chose to send: `send` (which refuses a blank line), a
        paging answer, a helper's command and the steps of their script."""
        sess.conn.send(_encode(text) + b"\r")
        with self._lock:
            sess.lines.append(ChatLine(self.wall(), "tx", text))
            sess.rev += 1
            self._capture(sess, "tx", text)

    def disconnect(self, session_id: int) -> None:
        sess = self.sessions.get(session_id)
        if sess is not None:
            sess.conn.close()

    def forget(self, session_id: int) -> None:
        with self._lock:
            sess = self.sessions.pop(session_id, None)
        if sess is not None and sess.state != "disconnected":
            sess.conn.abort()
        if sess is not None:
            self._session_over(sess)

    # -- nodes, BBSs, scripts, conferences (0.3.0) -------------------------------------------

    def _nodes_heard(self, frame) -> None:
        try:
            with self._lock:
                self.nodes.heard_frame(frame, self.wall())
        except Exception:                           # noqa: BLE001 - a stranger's frame must not stop the monitor
            log.exception("NODES broadcast not read")

    def _session_event(self, sess: ChatSession, kind: str) -> None:
        """A session's link came up or went (engine thread, under its lock)."""
        if kind == "connected" and sess.connected_at is None:
            sess.connected_at = self.wall()
            if sess.script is not None:
                sess.script.start(self.clock())
        elif kind == "disconnected":
            self._session_over(sess)

    def _session_over(self, sess: ChatSession) -> None:
        if sess.script is not None:
            sess.script.stop("script-link-gone")
        self.capture_stop(sess.id, sess)
        auto = self.settings.log
        wanted = {"chat": auto.auto_chat, "bbs": auto.auto_bbs, "node": auto.auto_bbs,
                  "conference": auto.auto_conference}.get(sess.kind, False)
        if wanted and not sess.logged and sess.connected_at is not None:
            self.log_session(sess.id, sess=sess)

    def run_script(self, script) -> ChatSession:
        """Start a connect script (scripts.Script): connect, then its steps
        once the link is up. Raises scripts.ScriptError for one that
        cannot run, and what connect() raises."""
        steps = script.steps()
        call, via = scripts_mod.target(steps)
        sess = self.connect(call, via)
        if sess.script is not None and sess.script.state in ("waiting-link", "running"):
            sess.script.stop("script-replaced")
        sess.script = scripts_mod.ScriptRun(script.name, steps, lambda line, s=sess: self._send_line(s, line))
        self._note(sess, "note:script-started")
        if sess.connected:
            sess.script.start(self.clock())
        return sess

    def stop_script(self, session_id: int) -> None:
        sess = self.sessions.get(session_id)
        if sess is not None and sess.script is not None:
            sess.script.stop()

    def page(self, session_id: int, go_on: bool = True) -> bool:
        """Answer the paging question the session is stopped at: carry on
        (a bare CR, or Y) or stop (A, or N). The operator's button, so a
        bare CR is allowed here; False when the session is not at one."""
        from shack_packet.terminal import paging_reply
        sess = self.sessions.get(session_id)
        if sess is None or not sess.connected or sess.reader.prompt_kind != "page":
            return False
        with self._lock:
            prompt = sess.reader.prompt
            if sess._partial:
                # Answered: the question becomes a line of its own, so what
                # comes next does not start glued to it.
                sess.lines.append(ChatLine(self.wall(), "rx", sess._partial))
                sess._partial = ""
            sess.reader.prompt, sess.reader.prompt_kind = "", ""
        self._send_line(sess, paging_reply(prompt, go_on))
        return True

    def helper(self, session_id: int, command: str, expect: str = "", number: int = 0) -> bool:
        """A helper button's command (L, LM, R 12, K 12, CHAT...), typed
        into the session like the operator would, its answer read as
        `expect` ("list", "read", "kill")."""
        sess = self.sessions.get(session_id)
        if sess is None or not sess.connected or not command.strip():
            return False
        with self._lock:
            if expect:
                sess.reader.expect(expect, number)
        self._send_line(sess, command)
        return True

    def join_conference(self, session_id: int) -> bool:
        sess = self.sessions.get(session_id)
        if sess is None:
            return False
        with self._lock:
            sess.reader.joining()
            sess.reader.set_kind("conference")
        return self.helper(session_id, self.settings.bbs.conference_command.strip() or "CHAT")

    def send_mail(self, session_id: int, to: str, subject: str, body: str, bulletin: bool = False) -> bool:
        """Leave a message on the BBS the session is at, by its own
        SP/SB dialogue, run as a script (scripts.send_mail_steps)."""
        sess = self.sessions.get(session_id)
        if sess is None or not sess.connected or not to.strip():
            return False
        steps = scripts_mod.send_mail_steps(to, subject, body, bulletin)
        sess.script = scripts_mod.ScriptRun("mail", steps, lambda line, s=sess: self._send_line(s, line))
        sess.script.start(self.clock())
        return True

    def capture_start(self, session_id: int, path) -> None:
        """Append everything the session shows to `path` (UTF-8 text)."""
        sess = self.sessions.get(session_id)
        if sess is None:
            return
        self.capture_stop(session_id)
        f = open(path, "a", encoding="utf-8")       # noqa: SIM115 - held open while capturing
        stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.wall()))
        f.write(f"--- {sess.remote}, {stamp}\n")
        sess.capture = f
        self._note(sess, "note:capture-started")

    def capture_stop(self, session_id: int, sess: ChatSession | None = None) -> None:
        sess = sess or self.sessions.get(session_id)
        if sess is None or sess.capture is None:
            return
        f, sess.capture = sess.capture, None
        try:
            f.close()
        except OSError:
            pass

    # -- the log (logbook.py) ------------------------------------------------------------------

    def log_contact(self, station: str, kind: str, time_on: float, time_off: float, *, via: str = "",
                    comment: str = "") -> Contact:
        from shack_packet.terminal import base_call
        engine = self.engine
        modem = engine.channel.mode.name if engine is not None else ""
        info = self.callbook.lookup(station) if self.settings.lookup.enabled else None
        c = Contact(call=base_call(station), station=str(station).upper(), time_on=time_on, time_off=time_off,
                    freq_hz=int(self.settings.channel.frequency_hz), modem=modem, kind=kind, via=via,
                    comment=comment, my_call=self.settings.station.callsign.strip().upper(),
                    my_grid=self.settings.station.locator.strip().upper())
        if info is not None and info.found:
            c.name, c.qth, c.grid = info.name, info.qth, info.grid
        c = self.logbook.add(c)
        self._emit("log", "added", {"id": c.id, "call": c.call})
        return c

    def log_session(self, session_id: int, comment: str = "", sess: ChatSession | None = None) -> Contact | None:
        """Log a session as a contact (the "Log this contact" button, or
        the automatic log at its end). None if it never connected."""
        sess = sess or self.sessions.get(session_id)
        if sess is None or sess.connected_at is None:
            return None
        sess.logged = True
        off = self.wall() if sess.connected or sess.state != "disconnected" else self.wall()
        return self.log_contact(sess.remote, sess.kind, sess.connected_at, off, via=",".join(sess.via),
                                comment=comment)

    # -- callsign lookups (callbook.py) --------------------------------------------------------------

    @property
    def callbook(self):
        if self._callbook is None:
            from .callbook import Callbook
            self._callbook = Callbook(_data_file("callbook.json"), self.secrets.get("hamqth_user", ""),
                                      self.secrets.get("hamqth_password", ""),
                                      on_result=lambda e: self._emit("lookup", "found", {"call": e.call}))
        return self._callbook

    @callbook.setter
    def callbook(self, value) -> None:
        self._callbook = value

    def describe_call(self, call: str) -> dict | None:
        """HamQTH's name, QTH, locator and our distance and bearing for
        `call`, from the cache (a fetch queued if missing); None if
        lookups are off or nothing is known yet."""
        if not self.settings.lookup.enabled:
            return None
        return self.callbook.describe(call, self.settings.station.locator)

    def _note(self, sess: ChatSession, key: str) -> None:
        with self._lock:
            sess.lines.append(ChatLine(self.wall(), "note", key))
            sess.rev += 1

    # -- APRS --------------------------------------------------------------------------------

    def _aprs_message(self, message) -> None:
        with self._lock:
            self.messages.append(MessageRow(self.wall(), "in", message.source, message.text))
            self.messages_rev += 1
        # Only messages to this station reach here, each once: the library
        # checks the addressee (call and SSID, or an also_me call) and
        # drops a repeat of a message id it has already taken
        # (aprs_station.py dedupe_s).
        self._emit("aprs", "message", {"peer": message.source, "text": message.text,
                                       "addressee": message.addressee})

    def send_message(self, to: str, text: str) -> list[MessageRow]:
        if self.aprs is None:
            raise RuntimeError("not started")
        if self.station.callsign is None:
            raise IdentityError("the station has no callsign")
        ax25.Address.from_text(to.strip(), flag=False)
        rows = []
        for out in self.aprs.send_message(to, text):
            rows.append(MessageRow(self.wall(), "out", out.addressee, out.text, out))
        with self._lock:
            self.messages += rows
            self.messages_rev += 1
        return rows

    def wall_of(self, t: float) -> float:
        """An engine time (its monotonic clock) as wall-clock seconds."""
        return self.wall() - (self.clock() - t)

    def heard(self) -> list:
        """The APRS station's heard list (and the SDR's, when it feeds it)."""
        if self.aprs is None or self.engine is None:
            return []
        with self.engine.lock:
            return list(self.aprs.heard.values())

    # -- mailbox -------------------------------------------------------------------------------

    def set_mailbox(self, enabled: bool) -> None:
        self.settings.mailbox.enabled = bool(enabled)
        if self.mailbox is not None:
            self.mailbox.config.enabled = bool(enabled)

    def mail(self) -> list:
        if self.mailbox is None:
            return []
        return sorted(self.mailbox.store.all(), key=lambda m: m.time, reverse=True)

    def simulated_mail(self, subject: str, body: str) -> bool:
        """On the simulator only: have G4SIM leave a message in this
        station's mailbox (simband.leave_mail). False when there is no
        simulator or no mailbox running."""
        if self.band is None or self.mailbox is None or not self.mailbox.config.enabled:
            return False
        self.band.leave_mail(str(self.mailbox.address), subject, body)
        return True

    # -- IP -------------------------------------------------------------------------------------

    def _ip_packet(self, packet: bytes) -> None:
        if self.pinger is not None and self.pinger.packet_in(packet):
            return

    def ping(self, target: str, count: int = 4):
        if self.pinger is None:
            raise RuntimeError("IP is not on")
        return self.pinger.ping(target, count)

    # -- the SDR monitor --------------------------------------------------------------------------

    def start_sdr(self) -> None:
        if self.sdr is not None and self.sdr.running:
            return
        if any(what == "sdr" for what, _a in ohsclient.own_radio_addresses(self.settings.radio, self.settings.sdr)) \
                and self.ohs.paired:
            # A networked SDR the controller may hold: asked off this
            # thread (the window's), then started or refused.
            def check():
                held = [r for what, r in self.ohs.conflicts(self.settings.radio, self.settings.sdr) if what == "sdr"]
                if held:
                    self._controller_refused(held[0], "sdr")
                elif self.started:
                    self._start_sdr_now()
            threading.Thread(target=check, name="sdr-controller-check", daemon=True).start()
            return
        self._start_sdr_now()

    def _start_sdr_now(self) -> None:
        kw = {}
        if self.sdr_factory is not None:
            kw["source_factory"] = self.sdr_factory
        # The operator's channels (sdrplan.py); a choice wider than the SDR
        # captures is refused here, said on the SDR page, and nothing opens.
        try:
            kw["plan"] = sdrplan.plan(self.settings.sdr, self.settings.station.profile())
        except sdrplan.PlanError as e:
            refused = e
        else:
            refused = None
        self.sdr = SdrMonitor(self.settings.sdr, on_frame=self._sdr_frame,
                              on_error=lambda reason: self._emit("sdr", "failed", {"reason": reason}), **kw)
        if refused is not None:
            self.sdr._failed(refused)
            return
        self.sdr.start()
        if self.sdr.running and self.settings.sdr.feed_heard and self.aprs is not None:
            self.sdr.monitor.feed_heard(self.aprs)
        if self.sdr.running:
            self._emit("sdr", "started")

    def stop_sdr(self) -> None:
        if self.sdr is not None:
            self.sdr.stop()
            self._emit("sdr", "stopped")

    # -- the turnaround measurement -----------------------------------------------------------------

    def measure_turnaround(self, confirm: str, done=None):
        """radios.turnaround.measure against the radio's own output meter,
        on a thread; `confirm` must be turnaround.CONFIRM, which the window
        passes only when the operator has ticked both "I am at the radio"
        and "a dummy load is fitted". Needs the station armed, like any key."""
        from shack_packet.radios import AudioRadioLink
        from shack_packet.radios import turnaround
        link = self.link
        if not isinstance(link, AudioRadioLink) or self.settings.radio.kind not in REAL:
            raise RuntimeError("the turnaround needs a real radio link")
        if self.settings.radio.kind == "tm-v71":
            # Measured against the radio's output meter, and no command reads
            # a TM-V71's (shack_rig.kenwood.cat): it would key for nothing.
            raise RuntimeError("the TM-V71 has no power meter to measure the turnaround against")
        if confirm != turnaround.CONFIRM:
            raise PermissionError("the operator has not confirmed a dummy load and their presence")

        def go():
            try:
                probe = turnaround.RigPowerProbe(link)
                m = turnaround.measure(link, probe, confirm=confirm)
                if not m.ok or m.turnaround_s is None:
                    # No key-up seen on the power meter (0.1.0 then raised a
                    # TypeError on float(None) here): said, and nothing saved.
                    log.warning("turnaround measurement: no result (%s)", m.public())
                    self._emit("tx", "turnaround-failed",
                               {"detail": "the radio's power meter never showed the carrier"})
                    if done is not None:
                        done()
                    return
                self.settings.radio.turnaround_s = float(m.turnaround_s)
                link.turnaround_s = float(m.turnaround_s)
                self._emit("tx", "turnaround", {"turnaround_s": m.turnaround_s, "txdelay_s": m.txdelay_s})
            except Exception as e:                  # noqa: BLE001 - shown, and the link is unkeyed by measure()
                log.exception("turnaround measurement")
                self._emit("tx", "turnaround-failed", {"detail": str(e)})
            if done is not None:
                done()
        t = threading.Thread(target=go, name="turnaround", daemon=True)
        t.start()
        return t

    # -- what the window draws --------------------------------------------------------------------------

    def status(self) -> dict:
        st, engine = self.station, self.engine
        ch = engine.channel if engine is not None else None
        armed = bool(st is not None and st.armed)
        return {
            "callsign": str(st.callsign) if st is not None and st.callsign is not None else "",
            "radio": self.settings.radio.kind,
            "link": self.link_state,
            "linkError": self.link_error,
            "armed": armed,
            "armedRemainingS": st.remaining_s() if armed else 0.0,
            "busy": bool(ch is not None and (ch.dcd or ch.radio_busy)),
            "dcd": bool(ch is not None and ch.dcd),
            "transmitting": bool(ch is not None and ch.transmitting),
            "lastTx": self.last_tx_wall,
            "frequencyHz": int(self.settings.channel.frequency_hz),
            # The radio's own dial and mode as the rig's cache has them
            # (tuning.py): "radioHz" differs from frequencyHz only while the
            # radio is between channels, transmit held.
            "radioHz": (self.tuning.pending[0] if self.tuning.pending is not None else None),
            "radioMode": self.tuning.bad_mode or (self.tuning.live or {}).get("label") or "",
            "radioModeBad": self.tuning.bad_mode is not None,
            "heard": int(engine.stats["heard"]) if engine is not None else 0,
            "txRefused": dict(self.tx_refusals),
            "sdr": "running" if self.sdr is not None and self.sdr.running else
                   ("failed" if self.sdr is not None and self.sdr.error else "off"),
            "rfPower": power_status(self.settings.radio, self.link) if self.link is not None else None,
            "linktest": self.linktest.banner(),
        }

    def timing(self) -> dict:
        """The numbers a tester's bundle carries about the channel."""
        engine, link = self.engine, self.link
        out = {}
        if engine is not None:
            c = engine.channel
            out.update({"txdelayS": c.txdelay_s, "txtailS": c.config.txtail_s, "persist": c.config.persist,
                        "slottimeS": c.config.slottime_s, "transmissions": c.stats.transmissions,
                        "framesSent": c.stats.frames, "airtimeS": round(c.stats.airtime_s, 3),
                        "refused": dict(c.stats.refused), "engine": dict(engine.stats)})
        if link is not None:
            out["turnaroundS"] = getattr(link, "turnaround_s", None)
            out["turnaroundMeasured"] = bool(getattr(link, "turnaround_measured", False))
            out["linkStats"] = dict(getattr(link, "stats", {}) or {})
            out["setup"] = {k: v for k, v in (getattr(link, "setup", {}) or {}).items()
                            if isinstance(v, (str, int, float, bool, type(None)))}
        return out


class _ScriptClock:
    """An engine service (polled under its lock, as the mailbox is) that
    times out connect scripts' waits."""

    def __init__(self, ctl: Controller):
        self.ctl = ctl

    def _runs(self):
        return [s.script for s in list(self.ctl.sessions.values()) if s.script is not None]

    def poll(self, now: float) -> None:
        for sess in list(self.ctl.sessions.values()):
            run = sess.script
            if run is None or run.state != "running":
                continue
            run.poll(now)
            if run.state == "failed":
                self.ctl._note(sess, f"note:{run.reason}")
                self.ctl._emit("script", run.reason, {"session": sess.id, "waiting": run.steps[run.at].arg})

    def next_deadline(self) -> float | None:
        due = [d for d in (r.next_deadline() for r in self._runs()) if d is not None]
        return min(due) if due else None


def _data_file(name: str):
    try:
        return host.data_dir() / name
    except OSError:
        return None


def _path(text: str) -> list[str]:
    return [p.strip().upper() for p in str(text or "").replace(" ", ",").split(",") if p.strip()]


def _printable(info: bytes) -> bool:
    """Whether a frame's information field reads as text (a chat line, an
    APRS packet) rather than an IP datagram, ARP or NET/ROM."""
    bad = sum(1 for b in info if b < 32 and b not in (9, 10, 13))
    return bad <= len(info) // 20


def valid_address(text: str) -> bool:
    try:
        ax25.Address.from_text(text.strip(), flag=False)
        return True
    except (ValueError, PacketError):
        return False


def _decode(data: bytes) -> str:
    """Packet text is bytes: UTF-8 where it is, Latin-1 otherwise (old TNCs
    and BBSs send Latin-1 or code page 437; nothing in a chat is lost)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _encode(text: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError:
        return text.encode("utf-8")
