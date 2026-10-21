"""The store app's settings, from the module's environment (Dock360 gives a
module one variable per line, so no setting may hold a line break: the
information text takes `|` for a new line and the partners a comma
between them). Every value is checked here, once; a bad one falls back to
its default and says so in `problems`, so a typo never stops the BBS
starting, and never turns transmit on (docs/BBS-DESIGN.md §7)."""
from __future__ import annotations

from dataclasses import dataclass, field

from shack_packet.bbs import Partner
from shack_packet.identity import is_callsign

RADIO_KINDS = ("simulator", "ic-9700", "ic-705", "pluto")
#: The broker device class each radio kind is claimed as (dock360 broker CLASSES).
BROKER_CLASS = {"ic-9700": "ic9700", "ic-705": "ic705", "pluto": "pluto"}


@dataclass
class BoxSettings:
    callsign: str = ""
    bbs_ssid: int = 1
    room_ssid: int = 2
    radio_kind: str = "simulator"
    radio_device: str = ""
    radio_user: str = ""
    radio_password: str = ""
    frequency_hz: int = 144_950_000
    #: The sysop may arm transmit at all. Off by default: a fresh install
    #: never even sets a radio up for transmitting.
    tx_allowed: bool = False
    arm_hours: float = 24.0
    info: str = ""
    qth: str = ""
    hierarchy: str = ""
    max_message_kb: int = 8
    max_per_day: int = 20
    max_held: int = 100
    idle_minutes: int = 10
    max_sessions: int = 4
    bulletin_days: int = 30
    personal_days: int = 90
    read_days: int = 14
    partners: list[Partner] = field(default_factory=list)
    forward_minutes: int = 30
    id_text: str = ""
    room_topic: str = ""
    data_dir: str = "/data"
    problems: list[str] = field(default_factory=list)

    @property
    def broker_class(self) -> str:
        return BROKER_CLASS.get(self.radio_kind, "")

    @property
    def bbs_address(self) -> str:
        return f"{self.callsign}-{self.bbs_ssid}" if self.bbs_ssid else self.callsign

    @property
    def room_address(self) -> str:
        return f"{self.callsign}-{self.room_ssid}" if self.room_ssid else ""

    def default_id(self) -> str:
        mhz = f"{self.frequency_hz / 1e6:.4f}".rstrip("0").rstrip(".")
        parts = [f"{self.bbs_address} BBS"]
        if self.room_address:
            parts.append(f"{self.room_address} conference")
        return ", ".join(parts) + f", {mhz} MHz"

    @classmethod
    def from_env(cls, env) -> BoxSettings:
        s = cls()
        bad = s.problems

        def text(key, default="", limit=200):
            return str(env.get(key, default) or default).strip()[:limit]

        def number(key, default, lo, hi, kind=int):
            raw = str(env.get(key, "") or "").strip()
            if not raw:
                return default
            try:
                v = kind(raw)
            except ValueError:
                bad.append(f"{key}={raw!r} is not a number; {default} is used")
                return default
            if not lo <= v <= hi:
                bad.append(f"{key}={raw} is outside {lo}..{hi}; {default} is used")
                return default
            return v

        call = text("BBS_CALLSIGN").upper()
        if call and not is_callsign(call.split("-")[0]):
            bad.append(f"BBS_CALLSIGN={call!r} is not a callsign; the BBS cannot start without one")
            call = ""
        s.callsign = call.split("-")[0]
        s.bbs_ssid = number("BBS_SSID", 1, 0, 15)
        s.room_ssid = number("ROOM_SSID", 2, 0, 15)
        if s.room_ssid and s.room_ssid == s.bbs_ssid:
            bad.append("ROOM_SSID is the BBS's own SSID; the room is reached by C only")
            s.room_ssid = 0
        kind = text("RADIO_KIND", "simulator").lower()
        if kind not in RADIO_KINDS:
            bad.append(f"RADIO_KIND={kind!r} is not one of {', '.join(RADIO_KINDS)}; the simulator is used")
            kind = "simulator"
        s.radio_kind = kind
        s.radio_device = text("RADIO_DEVICE").lower()
        if kind != "simulator" and not s.radio_device:
            s.radio_device = f"{s.broker_class}-1"
        s.radio_user = text("RADIO_USER")
        s.radio_password = str(env.get("RADIO_PASSWORD", "") or "")
        s.frequency_hz = number("FREQUENCY_HZ", 144_950_000, 1_000_000, 6_000_000_000)
        s.tx_allowed = text("TX_ALLOWED", "false").lower() in ("1", "true", "yes", "on")
        s.arm_hours = number("ARM_HOURS", 24.0, 0.0, 24.0 * 30, float)
        s.info = text("BBS_INFO", "", 1000)
        s.qth = text("BBS_QTH", "", 40)
        s.hierarchy = text("BBS_HIERARCHY", "", 60).upper().lstrip(".")
        s.max_message_kb = number("MAX_MESSAGE_KB", 8, 1, 256)
        s.max_per_day = number("MAX_PER_DAY", 20, 1, 1000)
        s.max_held = number("MAX_HELD", 100, 1, 10000)
        s.idle_minutes = number("IDLE_MINUTES", 10, 1, 240)
        s.max_sessions = number("MAX_SESSIONS", 4, 1, 20)
        s.bulletin_days = number("BULLETIN_DAYS", 30, 1, 3650)
        s.personal_days = number("PERSONAL_DAYS", 90, 1, 3650)
        s.read_days = number("READ_DAYS", 14, 1, 3650)
        for item in text("FWD_PARTNERS", "", 1000).split(","):
            if item.strip():
                try:
                    p = Partner.parse(item)
                except ValueError as e:
                    bad.append(f"FWD_PARTNERS: {e}")
                    continue
                if not is_callsign(p.peer):
                    bad.append(f"FWD_PARTNERS: {p.call!r} is not a callsign")
                    continue
                s.partners.append(p)
        s.forward_minutes = number("FWD_INTERVAL_MIN", 30, 5, 24 * 60)
        s.id_text = text("ID_TEXT", "", 200)
        s.room_topic = text("ROOM_TOPIC", "", 200)
        s.data_dir = text("OHS_PACKET_BOX_DATA", "/data", 400)
        return s
