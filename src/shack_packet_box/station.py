"""The BBS station on the controller: the engine, the radio the broker
lends it, the BBS, the conference room and the ID beacon, and the rules
for transmitting with nobody at the radio (docs/BBS-DESIGN.md §4).

    box = BoxStation(settings, broker=BrokerClient(...))
    box.start()                    # claims the radio (reserved), opens it, runs the engine
    box.arm(who)                   # a sysop with Transmit lets it answer on the air
    box.tick()                     # once a second: broker heartbeats, re-claims, expiry
    box.stop()                     # unkeys, closes the radio, ends the claim

**The radio is held for as long as the BBS runs** (D1): one broker claim,
`requester: auto`, `reserve: true`, mode rx until armed. The desktop app
will not open a radio the controller lists as held. A person's hand-over
request is refused (a BBS cannot step aside for a minute); an
administrator's stop (`stop: true`) is obeyed at once.

**Transmit** goes through `BrokeredLink`, the engine's only radio: before
every over it asks the broker to go on the air, acting for the sysop who
armed it (their device or browser session), and after the over it says
it is off. The broker re-checks that person's level and the licence each
time, so a revoked device or a signed-out session stops the BBS
transmitting at its next over. The arm expires (ARM_HOURS), is lost on
any restart, and is never set by anything but a person on the sysop page.
The simulator needs no broker and still needs the arm.

**A lost claim** (a platform update restarts the broker) is re-claimed
with `reclaim: true` while receive goes on and nothing keys; refused for
good (another app has the radio now), the radio is closed and asked for
again every RETRY_S (GUIDE §5 "A platform update drops every hardware
claim").
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shack_packet.bbs import Bbs, BbsConfig
from shack_packet.bbsstore import BbsStore, Retention
from shack_packet.channel import RadioLink
from shack_packet.conference import Room, RoomService
from shack_packet.engine import Engine, Runner
from shack_packet.idbeacon import IdBeacon
from shack_packet.identity import Station
from shack_packet.radios.base import LinkRefused

from .i18n import _
from .settings import BoxSettings

log = logging.getLogger(__name__)

RETRY_S = 30.0
#: How long a lost claim is re-asked for before the radio is closed: the
#: broker's restart grace is 15 s.
RECLAIM_WINDOW_S = 30.0
#: Occupied bandwidth of 1200 baud AFSK through an FM radio, for the
#: broker's band-plan check (ohs-libs modem afsk1200 occupied_hz, measured).
AFSK_BW_HZ = 9200


@dataclass
class ArmableStation(Station):
    """The station's identity, armed only by a person and only until the
    arm expires. `refusal()` is asked by the channel and by the radio
    links' safety cores at keying and while keyed."""
    acting: str = "-"
    armed_by: str = ""
    armed_at: float = 0.0
    expires: float | None = None
    clock: Callable[[], float] = time.time

    def arm(self, acting: str, by: str, hours: float) -> None:
        now = self.clock()
        self.acting, self.armed_by, self.armed_at = acting or "-", by, now
        self.expires = now + hours * 3600.0 if hours > 0 else None
        self.tx_enabled = True

    def disarm(self) -> None:
        self.tx_enabled = False
        self.acting, self.armed_by, self.expires = "-", "", None

    @property
    def armed(self) -> bool:
        return self.tx_enabled and (self.expires is None or self.clock() < self.expires)

    def refusal(self) -> str | None:
        base = super().refusal()
        if base is not None:
            return base
        if not self.armed:
            return "transmit-disabled"
        return None


class BrokeredLink(RadioLink):
    """The engine's radio: the real link inside (or none while the radio is
    away), keyed only after the box's transmit gate - the broker's on-air
    yes - and reported off the air after every over."""

    def __init__(self, box: BoxStation):
        self.box = box
        self.inner: RadioLink | None = None
        self.keyed = False
        self.sink = None

    def set_inner(self, link: RadioLink | None) -> None:
        self.inner = link
        if link is not None:
            for attr in ("rate", "carries_frames", "turnaround_s", "baud", "mode"):
                setattr(self, attr, getattr(link, attr))
            if self.sink is not None:
                link.attach(self.sink)

    def attach(self, sink) -> None:
        self.sink = sink
        if self.inner is not None:
            self.inner.attach(sink)

    def key(self) -> None:
        inner = self.inner
        if inner is None:
            raise LinkRefused("no-radio", "the BBS's radio is not open")
        why = self.box.tx_gate()
        if why:
            raise LinkRefused("broker-refused", why)
        self.keyed = True
        inner.key()

    def unkey(self) -> None:
        try:
            if self.inner is not None:
                self.inner.unkey()
        finally:
            if self.keyed:
                self.keyed = False
                self.box.off_air()

    def send_pcm(self, pcm: bytes) -> None:
        self.inner.send_pcm(pcm)

    def send_frames(self, frames, txdelay_s, airtime_s, modes=None) -> None:
        if modes is None:
            self.inner.send_frames(frames, txdelay_s, airtime_s)
        else:
            self.inner.send_frames(frames, txdelay_s, airtime_s, modes)

    def __getattr__(self, name):
        inner = self.__dict__.get("inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


def default_link_factory(settings: BoxSettings, station, access: dict | None, band):
    """The real link for these settings: a network radio at the address
    the broker lent, or the simulated band."""
    from shack_packet_app.links import make_link
    from shack_packet_app.settings import RadioSettings
    if settings.radio_kind == "simulator":
        return band.radio()
    host = str((access or {}).get("address") or "")
    radio = RadioSettings(kind=settings.radio_kind, host=host, username=settings.radio_user)
    if settings.radio_kind == "pluto":
        # A Pluto lent over the network is reached by its address.
        from shack_packet_app.settings import SdrRadioSettings
        sdr = SdrRadioSettings(pluto_uri=f"ip:{host}")
        return make_link(radio, station=station, frequency_hz=settings.frequency_hz, transmit=settings.tx_allowed,
                         sdr=sdr, secrets={"pluto_password": settings.radio_password})
    return make_link(radio, station=station, frequency_hz=settings.frequency_hz, transmit=settings.tx_allowed,
                     password=settings.radio_password)


class BoxStation:
    def __init__(self, settings: BoxSettings, *, broker=None, link_factory=None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 store: BbsStore | None = None):
        self.settings = settings
        self.broker = broker
        self.link_factory = link_factory or default_link_factory
        self.clock = clock
        self.wall = wall
        self.store = store
        self.log: collections.deque[dict] = collections.deque(maxlen=2000)
        self.state = "stopped"          # stopped | claiming | opening | running | radio-refused | failed
        self.error = ""
        self.claim_state = "none"       # none | held | lost
        self._lost_at: float | None = None
        self._next_try = 0.0
        self.on_air = False
        self.station: ArmableStation | None = None
        self.link = BrokeredLink(self)
        self.engine: Engine | None = None
        self.runner: Runner | None = None
        self.bbs: Bbs | None = None
        self.room: Room | None = None
        self.idbeacon: IdBeacon | None = None
        self.band = None
        self._lock = threading.RLock()
        self._ticker: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        self._closed = False

    # ---------------------------------------------------------------- log
    def note(self, kind: str, text: str, call: str = "") -> None:
        self.log.append({"t": self.wall(), "kind": kind, "call": call, "text": text})
        log.info("%s %s %s", kind, call, text)

    @property
    def device(self) -> str:
        return self.settings.radio_device if self.settings.radio_kind != "simulator" else ""

    def reason(self) -> str:
        s = self.settings
        return f"BBS {s.bbs_address} on {s.frequency_hz / 1e6:.4f} MHz"

    # ------------------------------------------------------------- build
    def build(self) -> None:
        """What needs no radio: the store, the station and the room. The
        engine and its services come with the first radio (`_engine_up`),
        because the link's kind decides the engine's receiver."""
        s = self.settings
        if not s.callsign:
            raise ValueError(_("The BBS needs a callsign (BBS_CALLSIGN)."))
        if self.store is None:
            self.store = BbsStore(Path(s.data_dir) / "bbs.db", home=s.callsign,
                                  retention=Retention(s.bulletin_days, s.personal_days, s.read_days))
        self.station = ArmableStation.of(s.callsign)
        self.station.clock = self.wall
        self.room = Room(s.room_address or f"{s.callsign}-{s.bbs_ssid}", clock=self.clock, topic=s.room_topic)
        self.room.observers.append(lambda kind, call, text: self.note("room-" + kind, text, call))
        for problem in s.problems:
            self.note("settings", problem)

    def _engine_up(self) -> None:
        s = self.settings
        self.engine = Engine(self.station, self.link, clock=self.clock)
        cfg = BbsConfig(ssid=s.bbs_ssid or None, info=s.info, qth=s.qth, hierarchy=s.hierarchy,
                        max_message_bytes=s.max_message_kb * 1024, max_per_day=s.max_per_day,
                        max_held=s.max_held, idle_timeout_s=s.idle_minutes * 60.0, max_sessions=s.max_sessions,
                        partners=list(s.partners), forward_interval_s=s.forward_minutes * 60.0)
        self.bbs = Bbs(self.engine, self.store, cfg, room=self.room, wall_clock=self.wall)
        if s.room_ssid:
            RoomService(self.engine, self.room, s.room_ssid, allow=self._room_allows)
        self.idbeacon = IdBeacon(self.engine, s.id_text or s.default_id())
        self.bbs.observers.append(lambda kind, call, text: self.note(kind, text, call))
        if self._started:
            self.runner = Runner(self.engine).start()

    def _room_allows(self, call: str) -> bool:
        user = self.store.user(call)
        return user is None or not user.barred

    def start(self) -> None:
        """Build, take and open the radio, and run: the engine on its
        Runner, the broker and timers on a ticker thread."""
        self.build()
        self._started = True
        self.radio_up()
        self._ticker = threading.Thread(target=self._tick_loop, name="box-tick", daemon=True)
        self._ticker.start()

    def _tick_loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:                       # noqa: BLE001 - the ticker must outlive a bad tick
                log.exception("box tick failed")

    # ------------------------------------------------------------- radio
    def radio_up(self) -> bool:
        """Claim the radio (reserved) and open it; False with `error` set if
        either was refused. The simulator needs no broker."""
        s = self.settings
        with self._lock:
            access = None
            if self.device:
                if self.broker is None:
                    self._down("failed", _("A real radio is used only through the controller's broker."))
                    return False
                self.state = "claiming"
                why = self.broker.claim(self.device, "rx", "auto", reason=self.reason(), reserve=True)
                if why:
                    self._down("radio-refused", why)
                    return False
                self.claim_state = "held"
                access = (self.broker.claims.get(self.device) or {}).get("access")
            elif s.radio_kind == "simulator" and self.band is None and self.link_factory is default_link_factory:
                from shack_packet_app.simband import SimBand
                self.band = SimBand().start()
            self.state = "opening"
            try:
                link = self.link_factory(s, self.station, access, self.band)
                if hasattr(link, "open"):
                    link.open()
            except Exception as e:                  # noqa: BLE001 - LinkError, LinkRefused, a radio's own failure
                self._down("failed", getattr(e, "detail", "") or str(e))
                return False
            self.link.set_inner(link)
            if self.engine is None:
                self._engine_up()
            self.state = "running"
            self.error = ""
            self.note("radio", _("{kind} open on {mhz} MHz, receiving").format(
                kind=s.radio_kind, mhz=f"{s.frequency_hz / 1e6:.4f}"))
            return True

    def _down(self, state: str, why: str) -> None:
        self.state, self.error = state, why
        self._next_try = self.clock() + RETRY_S
        self.note("radio", why)

    def radio_down(self, why: str, state: str = "stopped") -> None:
        """Unkey, close the radio and end the claim."""
        with self._lock:
            self.disarm(why)
            link, self.link.inner = self.link.inner, None
            if link is not None and hasattr(link, "close"):
                try:
                    link.close()
                except Exception:                   # noqa: BLE001 - closing regardless
                    log.exception("closing the radio")
            if self.device and self.broker is not None and self.claim_state == "held":
                self.broker.release(self.device)
            self.claim_state = "none"
            self.state = state
            self.error = why if state != "stopped" else ""
            self.note("radio", _("radio released: {why}").format(why=why))

    def stop(self) -> None:
        self._stop.set()
        self.radio_down(_("the BBS was stopped"))
        if self.runner is not None:
            self.runner.stop()
        if self.band is not None:
            self.band.stop()
            self.band = None
        if self.store is not None:
            self.store.close()
            self.state = "stopped"
            self._closed = True

    # ----------------------------------------------------------- transmit
    def arm(self, who: dict) -> str:
        """A person with Transmit lets the BBS answer on the air: '' or why
        not. Their device or browser session is the acting identity for
        every over until the arm ends."""
        s = self.settings
        if not s.tx_allowed:
            return _("Transmit is not allowed in this BBS's settings (TX_ALLOWED).")
        if self.state != "running":
            return _("The BBS's radio is not open.")
        if self.device:
            why = self.broker.claim(self.device, "tx", "auto", acting=who.get("device", "-"),
                                    reason=self.reason(), reserve=True)
            if why:
                return why
        self.station.arm(who.get("device", "-"), who.get("user", "-"), s.arm_hours)
        self.note("transmit", _("armed by {user} ({device})").format(user=who.get("user"), device=who.get("device")))
        return ""

    def disarm(self, why: str = "") -> None:
        station = self.station
        if station is None or not station.tx_enabled:
            return
        station.disarm()
        if self.device and self.broker is not None and self.claim_state == "held":
            self.broker.set_mode(self.device, "rx")
        self.note("transmit", _("disarmed: {why}").format(why=why or _("by the sysop")))

    def tx_gate(self) -> str:
        """Called by BrokeredLink before every key-up: '' to go ahead."""
        if not self.station.armed:
            return _("not armed")
        if not self.device:
            self.on_air = True
            return ""
        if self.broker is None or self.claim_state != "held":
            return _("the radio is not held from the broker")
        why = self.broker.on_air(self.device, {"freqHz": self.settings.frequency_hz, "bandwidthHz": AFSK_BW_HZ,
                                               "mode": "packet"}, self.station.acting)
        if why:
            self.note("transmit", _("the broker refused to go on the air: {why}").format(why=why))
            return why
        self.on_air = True
        return ""

    def off_air(self) -> None:
        self.on_air = False
        if self.device and self.broker is not None:
            self.broker.off_air(self.device)

    # ------------------------------------------------------- the platform
    def release_request(self, body: dict) -> tuple[int, dict]:
        """The broker asks for the radio (CONTROLLER-CONTRACT 7.5)."""
        if body.get("stop"):
            self.radio_down(_("the controller asked for the radio (stop)"), state="radio-refused")
            self._next_try = self.clock() + RETRY_S
            return 202, {"released": True}
        self.note("radio", _("refused to hand the radio to {app}: the BBS has it reserved").format(
            app=body.get("for") or "-"))
        return 409, {"error": _("The Packet Radio BBS has this radio reserved."), "code": "reserved",
                     "reserved": True, "onAir": self.on_air}

    def busy(self) -> dict:
        reasons = []
        if self.on_air:
            reasons.append("on-air")
        if self.bbs is not None and self.bbs.sessions:
            reasons.append(f"{len(self.bbs.sessions)} connected")
        return {"busy": bool(reasons), "reasons": reasons}

    def tick(self) -> None:
        """Once a second: heartbeats, a lost claim, the arm's expiry, a
        refused radio's retry."""
        now = self.clock()
        st = self.station
        if st is not None and st.tx_enabled and not st.armed:
            self.disarm(_("the arm expired"))
        if self.broker is None or not self.device:
            return
        lost, asked = self.broker.beat()
        for device, why in lost:
            if device == self.device:
                self.claim_state = "lost"
                self._lost_at = now
                self.disarm(why)
                self.note("radio", why)
        for device, req in asked:
            if device == self.device:
                if req.get("stop"):
                    self.release_request({"stop": True})
                else:
                    self.note("radio", _("the controller asked for the radio; it stays reserved for the BBS"))
        if self.claim_state == "lost":
            why = self.broker.claim(self.device, "rx", "auto", reason=self.reason(), reclaim=True, reserve=True)
            if not why:
                self.claim_state = "held"
                self.note("radio", _("the radio is held again (receive only until armed)"))
            else:
                code = (self.broker.refused or {}).get("code", "")
                if code not in ("broker_restarting", "service_unavailable") or now - self._lost_at > RECLAIM_WINDOW_S:
                    self.claim_state = "none"
                    self.radio_down(why, state="radio-refused")
                    self._next_try = now + RETRY_S
        elif self.state in ("radio-refused", "failed") and now >= self._next_try:
            self._next_try = now + RETRY_S
            self.radio_up()

    # -------------------------------------------------------------- views
    def status(self) -> dict:
        s, st = self.settings, self.station
        return {
            "state": self.state, "error": self.error,
            "callsign": s.callsign, "bbs": s.bbs_address, "room": s.room_address,
            "radio": {"kind": s.radio_kind, "device": self.device, "frequencyHz": s.frequency_hz,
                      "claim": self.claim_state, "reserved": bool(self.device) and self.claim_state == "held"},
            "transmit": {"allowed": s.tx_allowed, "armed": bool(st and st.armed),
                         "armedBy": st.armed_by if st else "", "expires": st.expires if st else None,
                         "onAir": self.on_air},
            "bbsState": self.bbs.public() if self.bbs and not self._closed else None,
            "roomMembers": self.room.calls() if self.room else [],
            "idSent": self.idbeacon.sent if self.idbeacon else 0,
            "problems": list(s.problems),
        }
