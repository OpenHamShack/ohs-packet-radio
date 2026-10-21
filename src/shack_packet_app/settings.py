"""The operator's settings: the station profile, the radio and how to reach
it, the channel, and each service's switches. One JSON file per user
(host.config_dir()), written whole to a temporary file and renamed into
place, so a crash mid-save leaves the old settings rather than half of new
ones.

What is deliberately **not** here:

- **Transmit arming.** It is never saved. Every start, restart, reconnect
  and settings change begins disarmed (NB's first safety rule, and the
  Icom app's): arming is something a person does at the radio, each time.
- **Secrets.** The IC-9700/IC-705 LAN password lives in its own file
  (`secrets.json`, owner-only where the system allows), so diagnostics can
  include the whole of settings.json without scrubbing it, and a settings
  file mailed to a friend carries no password.

The radio's parameters cover every kind the app drives; the ones a kind
does not use are kept, so switching from the FTX-1 to the simulator and
back does not lose the FTX-1's port. Unknown keys in the file are kept as
they were (`extra`), so an older copy of the app opening a newer file does
not throw away what it does not understand.
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import host
from .settings_extra import (GpsSettings, IGateSettings, MailSettings, ObjectsSettings, TelemetrySettings,
                             WeatherSettings, WinlinkSettings)

SCHEMA = 1

#: Radio kinds, as stored. The window names them (ui/texts.py).
RADIO_KINDS = ("simulator", "ic-9700", "ic-705", "ftx-1", "tm-v71", "soundcard", "none")
#: SDRs as the station's radio (0.2, PLAN-0.2 §3.6, D9), offered after the
#: rest: a Pluto (transmit and receive, under its on-device guard), a
#: HackRF One (half-duplex), and receive-only SDRs (RTL-SDR, rtl_tcp,
#: SDRplay). A separate tuple so the 0.1 list keeps its order.
SDR_RADIO_KINDS = ("pluto", "hackrf", "sdr-rx")
#: Of those, the ones that can transmit: the band-filter confirmation and
#: the SDR transmit warnings apply to these.
SDR_TX_KINDS = ("pluto", "hackrf")

#: How long one arming lasts unless the operator chooses otherwise, and the
#: bounds of the choice (the Icom app's ARM_SECONDS is 15 minutes too).
ARM_MINUTES = 15
ARM_MIN_MINUTES, ARM_MAX_MINUTES = 1, 60


@dataclass
class StationProfile:
    callsign: str = ""
    ssid: int = 0
    locator: str = ""
    #: ISO country (GB, DE...): blank to take it from the callsign's prefix.
    country: str = ""
    #: The licence class as the band-plan dataset names it (full,
    #: intermediate, foundation...); blank when not chosen.
    licence_class: str = ""

    def address(self) -> str:
        call = self.callsign.strip().upper()
        return f"{call}-{self.ssid}" if call and self.ssid else call

    def profile(self) -> dict:
        """The shared station-profile shape shack_bandplan reads."""
        out = {"callsign": self.callsign.strip().upper(), "locator": self.locator.strip().upper()}
        if self.country:
            out["country"] = self.country.upper()
        if self.licence_class:
            out["licence"] = {"class": self.licence_class}
        return out


@dataclass
class RadioSettings:
    kind: str = "simulator"
    # Icom (IC-9700 / IC-705)
    transport: str = "lan"                  # lan | usb (Wi-Fi is "lan" for an IC-705)
    host: str = ""
    port: int = 50001
    username: str = ""
    civ_device: str = ""
    civ_baud: int = 0
    # Yaesu FTX-1 (its Enhanced COM port), Kenwood TM-V71 (its PC port)
    cat_device: str = ""
    #: The TM-V71's PC port speed (menu 519); 0 for its factory 9600.
    cat_baud: int = 0
    # the radio's sound device, both ways (USB codec or interface)
    audio_in: str = ""
    audio_out: str = ""
    # PTT: cat (FTX-1), rts | dtr (a serial line), cm108 (a CM108 sound
    # chip's GPIO pin: ptt_device is its HID device, "" for the only one),
    # vox, none
    ptt: str = "cat"
    ptt_device: str = ""
    #: The CM108 GPIO pin wired to PTT (Dire Wolf's default, 3).
    cm108_gpio: int = 3
    #: TM-V71 only: the DATA jack's speed, so the modem - 1200 AFSK or 9600
    #: G3RUH. The link sets the radio's EXT. DATA SPEED (menu 518) to match.
    data_speed: int = 1200
    #: Measured key-up time (radios.turnaround), or None until measured.
    turnaround_s: float | None = None
    #: Icom only: before the first key of an armed session, set RF POWER to
    #: its minimum and refuse to key unless the radio confirms it (shack_rig
    #: 14 0A; shack_packet.radios.icom._ensure_minimum_power). Default ON
    #: for this test build - this is the real-radio dummy-load test's own
    #: safety step, not a general setting to leave off once the test is
    #: done (HANDOVER.md "What's next"). Ignored by every other radio kind.
    minimum_power_for_test: bool = True
    #: The radio's transmit audio input level, in percent, that opening the
    #: link sets (the IC-9700's LAN or USB MOD Level, the FTX-1's USB MOD
    #: GAIN) and closing puts back; None reads and reports it only. None by
    #: default because no level has been measured on the air yet: the 9700's
    #: deviation limiter sits at about 4.3 kHz and the level that puts
    #: packet's peaks near 3 kHz is session R2's to find (PLAN-0.2 §7). Set
    #: in settings.json until then; there is no control for it.
    mod_level_percent: float | None = None


@dataclass
class ChannelSettings:
    #: The preset's id (presets.json), or "custom".
    preset: str = "aprs"
    frequency_hz: int = 144_800_000
    #: None (plain AX.25), "auto", 16, 32 or 64: FX.25 on transmit. Every
    #: station decodes plain AX.25, so it stays off unless chosen.
    fx25: object = None


@dataclass
class AprsSettings:
    beacon: bool = False
    every_min: int = 30
    symbol: str = "/-"
    comment: str = ""
    path: str = "WIDE1-1,WIDE2-1"


@dataclass
class DigiSettings:
    enabled: bool = False
    aliases: str = ""


@dataclass
class ChatSettings:
    #: Whether other stations may connect to this one for a chat.
    accept_incoming: bool = True


@dataclass
class BbsSettings:
    """Nodes and BBSs (0.3.0): the operator's connect scripts (scripts.py),
    each {"name": ..., "text": ...}, and what "Join conference" types at
    a node (BPQ's chat server is CHAT; some nodes call it CONF)."""
    scripts: list = field(default_factory=list)
    conference_command: str = "CHAT"


@dataclass
class RoomSettings:
    """A conference room hosted here (0.3.0, shack_packet.conference).
    Whether it is open is not saved, like arming: a room is opened by the
    operator each time, and answers only while transmit is armed. SSID 4
    because that is where GB7 nodes' chat servers usually sit, so a
    caller guesses it; blank name and topic take the callsign's."""
    ssid: int = 4
    name: str = ""
    topic: str = ""
    max_members: int = 8
    idle_minutes: int = 30


@dataclass
class LogSettings:
    """Which finished sessions go into the log by themselves (logbook.py);
    all off until the operator chooses, and "Log this contact" works
    whatever these say."""
    auto_chat: bool = False
    auto_bbs: bool = False             # BBS and node sessions
    auto_conference: bool = False      # a node's conference, joined from here
    auto_room: bool = False            # each station's stay in a room hosted here


@dataclass
class LookupSettings:
    """HamQTH callsign lookups (callbook.py). The account is in
    secrets.json (hamqth_user, hamqth_password), never here."""
    enabled: bool = True


@dataclass
class MailboxSettings:
    enabled: bool = False
    ssid: int = 1
    third_party: bool = False


@dataclass
class FilesSettings:
    """File and picture transfer (files.py, 0.3.0). The folder is where
    received files go, never opened by the app on its own; blank is
    "Received files" in the app's data folder."""
    folder: str = ""
    #: Offers bigger than this are declined without asking.
    max_mb: int = 50
    #: A file this station had accepted and holds part of is resumed
    #: without asking again when it is offered again.
    auto_resume: bool = True
    #: DEFLATE for file data and long text with OHS stations (skipped for
    #: files that are compressed already).
    compress: bool = True
    #: The protocol "Send file…" starts with: "ohs" or "yapp".
    protocol: str = "ohs"
    #: Pictures: the re-encode preset "Send file…" starts with
    #: (pictures.PRESETS), "original" to send the file as it is.
    picture_preset: str = "medium"
    #: 7PLUS: lines per part when encoding (7PLUS's own limit is 512).
    sevenplus_lines: int = 200


@dataclass
class IpSettings:
    enabled: bool = False
    address: str = ""
    network: str = ""
    mode: str = "datagram"


@dataclass
class SdrSettings:
    #: none | synthetic | rtl_tcp | rtlsdr | pluto
    source: str = "none"
    rtltcp_host: str = "127.0.0.1"
    rtltcp_port: int = 1234
    device_index: int = 0
    pluto_uri: str = "ip:192.168.2.1"
    rate: int = 2_400_000
    gain_db: float | None = None
    ppm: float = 0.0
    #: Stations the SDR hears go into the APRS heard list and the map.
    feed_heard: bool = True
    #: The band the monitor watches ("2m", "70cm": shack_bandplan's band
    #: names), and which of that band's packet presets it listens to, by
    #: preset id (presets.py). Empty means every preset the band has.
    #: `extra_hz`: frequencies the operator typed, beside the presets.
    #: sdrplan.py turns these into the channel plan and refuses one wider
    #: than the SDR can capture.
    band: str = "2m"
    channels: list = field(default_factory=list)
    extra_hz: list = field(default_factory=list)


@dataclass
class MapSettings:
    #: OpenStreetMap tiles under the built-in map. Off unless the operator
    #: turns it on: it is the one thing in the app that fetches from the
    #: internet (ui/tiles.py).
    tiles: bool = False
    #: The tile server; {z}/{x}/{y} are filled in. The OSM default, or the
    #: operator's own.
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    #: Tiles kept on disk, at most this many megabytes.
    cache_mb: int = 200
    #: A moving station's track is kept this long.
    track_minutes: int = 60


@dataclass
class PadSettings:
    #: Use an Ajazz AKP03E (or a USB keypad) when one is plugged in.
    enabled: bool = True
    #: The operator's remaps, in the family's controllerMaps shape
    #: ({kind: {model: entry}}, shack_pad.mapping). A reset is stored as
    #: an empty entry (None), never by removing it: the station profile a
    #: controller syncs never deletes a field, so a removed entry would
    #: bring the old map back (GUIDE §5).
    maps: dict = field(default_factory=dict)


@dataclass
class ScopeSettings:
    """The IC-9700's spectrum and waterfall (0.3.0, ui/pages/rig.py). The
    span and reference are the radio's own (27 15, 27 19) and are sent when
    the page opens; `busy_db` is how far above the scope's floor a packet
    channel's peak must be to be drawn busy."""
    span_hz: int = 100_000
    ref_db: float = 0.0
    speed: str = "mid"
    busy_db: float = 12.0


@dataclass
class PowerSettings:
    """Adaptive "lowest power that works" for a CAT radio (adaptpower.py):
    off until the operator switches it on. Percent of the radio's RF POWER
    range. `per_band` is the level that last worked on each band."""
    enabled: bool = False
    cap_percent: int = 50
    floor_percent: int = 5
    step_percent: int = 10
    per_band: dict = field(default_factory=dict)


@dataclass
class SatSettings:
    """Satellite tracking (sat/, 0.3.0). `tracking` is the NORAD number
    followed (0: none); `doppler` corrects an IC-9700's VFOs during its
    passes; `min_elevation_deg` is the transmit gate, never below 0.
    `extra_birds` are the operator's own satellites (sat/tle.py Bird keys).
    `saved` holds the channel and APRS path the ISS set-up replaced, to
    put back."""
    tracking: int = 0
    doppler: bool = True
    min_elevation_deg: float = 5.0
    step_hz: int = 0
    altitude_m: float = 0.0
    extra_birds: list = field(default_factory=list)
    saved: dict = field(default_factory=dict)


@dataclass
class DdSettings:
    """23 cm DD (experimental, 0.3.0; radio kind "ic-9700-dd", links.py
    and shack_packet.radios.dd). The radio's network settings are
    RadioSettings'; these are the DD side: the channel, this PC's address
    on the DD subnet and the other station's, the RF power to set (None:
    leave it) and the radio kind to go back to."""
    channel_hz: int = 1_299_525_000
    local: str = "10.99.0.1"
    peer: str = "10.99.0.2"
    power_percent: int | None = None
    previous_kind: str = ""


@dataclass
class SdrRadioSettings:
    """An SDR as the station's radio (radio.kind in SDR_RADIO_KINDS). Its
    own section, not RadioSettings, so the rig fields and these never mix,
    and a Pluto's SSH password stays in secrets.json ("pluto_password")."""
    # Pluto
    pluto_uri: str = "ip:192.168.2.1"
    #: -30 dB from 0.2.2; 0.2.1 started at -10 (ui/sdrpages.NEW_PLUTO_TX_GAIN_DB).
    pluto_tx_gain_db: float = -30.0
    #: None: the AD936x's slow AGC; a number: manual receive gain (dB).
    pluto_rx_gain_db: float | None = None
    # HackRF One
    hackrf_serial: str = ""
    hackrf_tx_vga_db: int = 20
    #: The HackRF's own 14 dB RF amplifier. Off: power comes from a PA after
    #: a band filter, never from a bare SDR into an antenna.
    hackrf_amp: bool = False
    hackrf_lna_db: int = 16
    hackrf_vga_db: int = 20
    # receive-only: rtlsdr | rtl_tcp | sdrplay
    rx_source: str = "rtlsdr"
    rx_host: str = "127.0.0.1"
    rx_port: int = 1234
    rx_index: int = 0
    rx_serial: str = ""
    rx_gain_db: float | None = None
    rx_ppm: float = 0.0
    #: The operator's word, in the setup check, that a band-pass filter is
    #: between this SDR and anything that radiates, and a dummy load until
    #: the filter and any amplifier are proven (GUIDE §1). Transmit cannot
    #: be set up for a Pluto or HackRF without it.
    filter_confirmed: bool = False
    #: The firmware family "Check this Pluto" last found at `pluto_uri`
    #: (shack_pluto.identity.FAMILIES), "" when it has not been checked
    #: there. Decides which PTT outputs are offered (fastmodes.ptt_outputs):
    #: a PlutoDVB2 or Tezuka Pluto drives its own GPOs.
    pluto_family: str = ""


@dataclass
class FastRadioSettings:
    """The fast and wide modes (0.2.1) for a radio that carries baseband -
    a Pluto, today - as the app stores them. shack_packet.fastsettings
    holds the meaning, the checks and the objects these become; this is
    only what is saved, plus what that module leaves to the app:

    - `mode` defaults to afsk1200 here, not the library's g3ruh9600, so a
      station that never opens these settings keeps sending what every
      other packet station hears (and APRS on 144.800 stays APRS).
    - The amplifier's drive cap is one per band (`drive_caps`, band name
      as shack_bandplan names it -> dB): an amplifier set up on 23 cm says
      nothing about the 70 cm one, and the cap given to the link is the
      one for the frequency in use (`for_frequency`). No entry: no cap.
    - `adapt_start` is what the last path test found for receiving
      (pathtest.Tally.best_mode); the Path test page sets it.
    """
    mode: str = "afsk1200"
    adapt: bool = False
    top_mode: str = "4fsk76800"
    floor_mode: str = "g3ruh9600"
    #: The window used when `window_auto` is off. Used only on a fast mode
    #: or adapting: a Pluto on AFSK 1200 keeps k 7 (fastmodes.station_fast).
    window: int = 63
    #: The window chosen per mode (fastmodes.default_window, 0.3.2): what
    #: one over carries. 0.3.0-0.3.1 used k 63 at every mode, which at 9600
    #: is two 10 s overs back to back that the far end talked over. A file
    #: saved by those versions has no `window_auto` and so gets this; its
    #: saved 63 was the default, not a choice.
    window_auto: bool = True
    paclen: int = 512
    srej: bool = True
    txdelay_ms: float = 0.0
    turnaround_ms: float | None = None
    ptt_output: str = "none"
    relay_lead_ms: float = 20.0
    relay_tail_ms: float = 10.0
    drive_caps: dict = field(default_factory=dict)
    adapt_start: str | None = None

    def drive_cap(self, frequency_hz: float) -> float | None:
        from .sdrplan import band_of
        cap = self.drive_caps.get(band_of(frequency_hz))
        return None if cap is None else float(cap)

    def window_for(self, mode: str | None = None) -> int:
        """The window a link at `mode` uses (default: this mode, or the
        ladder's slowest when adapting - the window is agreed once per
        link, and must suit the slowest mode it may fall to)."""
        if not self.window_auto:
            return int(self.window)
        from .fastmodes import default_window
        mode = mode or (self.floor_mode if self.adapt else self.mode)
        return default_window(mode, int(self.paclen))

    def for_frequency(self, frequency_hz: float):
        """shack_packet.fastsettings.FastSettings for this frequency."""
        from shack_packet.fastsettings import FastSettings
        d = {k: v for k, v in asdict(self).items() if k not in ("drive_caps", "window_auto")}
        d["window"] = self.window_for()
        return FastSettings.from_dict({**d, "max_tx_gain_db": self.drive_cap(frequency_hz)})


@dataclass
class TxSettings:
    #: The setup check's last step: the operator has said this computer may
    #: transmit at all. Arming is still needed for each session of use.
    set_up: bool = False
    arm_minutes: int = ARM_MINUTES


@dataclass
class ControllerSettings:
    """The OpenHamShack controller this app is paired with (ohsclient.py),
    or none. The device token is a secret and lives in secrets.json; what
    is here is shown in the window and safe in a diagnostics bundle."""
    url: str = ""
    name: str = ""
    user: str = ""
    controller_id: str = ""
    device_id: str = ""
    #: This installation's id for pairing (CONTROLLER-CONTRACT §4.2
    #: clientId): random, made at the first pairing, so pairing again
    #: replaces this computer's token rather than adding a second device.
    client_id: str = ""


@dataclass
class InternetSettings:
    """The app's own outgoing connections to openhamshack.com: neither is a
    server, and neither ever runs while transmitting."""
    #: Check https://openhamshack.com/desktop/packet/update.json at start-up
    #: and once a day (an installed copy only; ui/update.py).
    check_updates: bool = True
    #: Send diagnostics by themselves (diagupload.py). Off unless the
    #: operator turns it on in Settings with the consent text in front of
    #: them; sending needs this AND diag_consent naming the current
    #: CONSENT_VERSION, which only the Settings dialog writes - a hand-edited
    #: file, or a later version that sends more, means "ask again".
    diag_upload: bool = False
    diag_consent: str = ""


@dataclass
class LinkTestSettings:
    """The Link test page (linkctl.py): a run with a friend, one RF
    direction, the receiver's answers through the relay on an OpenHamShack
    controller. Nothing here does anything until the operator presses
    Join."""
    #: host:port of the relay (shack_packet tools/packet_relay.py, port
    #: 30480 on the controller box, tailnet only).
    relay: str = ""
    #: The session both stations type, e.g. "bri-ray-1".
    session: str = ""
    #: "rf-sender" (antenna on the TX port: transmits the data) or
    #: "rf-receiver" (antenna on the RX port: never transmits; answers by
    #: relay).
    role: str = "rf-receiver"
    #: The other station's callsign with its SSID.
    peer: str = ""
    #: The test transfer each way, in kilobytes (5 from 0.3.0: a
    #: minute at 1200 baud; 0.2.x offered 100).
    kb: int = 5
    #: How a run by callsign (linkrun.py, 0.3.0) reaches the other
    #: station: "web" (openhamshack.com) or "relay" (`relay` above, the
    #: 0.2.1 TCP relay on a controller; under Advanced).
    via: str = "web"
    #: The web relay's address when it is not openhamshack.com's (tests,
    #: a staging copy); empty for the real one.
    url: str = ""


@dataclass
class NotificationSettings:
    """A desktop notification, a sound and an unread count in the sidebar
    when something arrives for the operator (ui/notifier.py, 0.2.3, asked
    for by Brian after the GM0OTB test: a message sat unseen behind another
    page). Each kind can be switched off; a file from before 0.2.3 has none
    of these keys and gets the defaults, all on."""
    enabled: bool = True
    #: Play a sound with each notification.
    sound: bool = True
    #: Which sound: "beep" (the system's own), "spoken" (a two-note chime
    #: made in code, then "You've got mail!" and the like in the bundled
    #: voice; 0.2.3 used the system's speech here), "system-voice" (the
    #: chime, then the system's text-to-speech) or "custom" (`sound_file`);
    #: notifysound.py.
    sound_kind: str = "beep"
    #: A WAV file the operator chose for "custom".
    sound_file: str = ""
    #: APRS messages addressed to this station.
    messages: bool = True
    #: Lines from a station in a Connect/Chat session.
    chat: bool = True
    #: Messages left in the mailbox.
    mailbox: bool = True
    #: Mail this station sent was delivered, or could not be (0.3.0).
    mail_status: bool = True


@dataclass
class Settings:
    station: StationProfile = field(default_factory=StationProfile)
    radio: RadioSettings = field(default_factory=RadioSettings)
    channel: ChannelSettings = field(default_factory=ChannelSettings)
    aprs: AprsSettings = field(default_factory=AprsSettings)
    digi: DigiSettings = field(default_factory=DigiSettings)
    chat: ChatSettings = field(default_factory=ChatSettings)
    bbs: BbsSettings = field(default_factory=BbsSettings)
    room: RoomSettings = field(default_factory=RoomSettings)
    log: LogSettings = field(default_factory=LogSettings)
    lookup: LookupSettings = field(default_factory=LookupSettings)
    mailbox: MailboxSettings = field(default_factory=MailboxSettings)
    files: FilesSettings = field(default_factory=FilesSettings)
    ip: IpSettings = field(default_factory=IpSettings)
    sdr: SdrSettings = field(default_factory=SdrSettings)
    sdr_radio: SdrRadioSettings = field(default_factory=SdrRadioSettings)
    fast: FastRadioSettings = field(default_factory=FastRadioSettings)
    tx: TxSettings = field(default_factory=TxSettings)
    map: MapSettings = field(default_factory=MapSettings)
    pad: PadSettings = field(default_factory=PadSettings)
    scope: ScopeSettings = field(default_factory=ScopeSettings)
    power: PowerSettings = field(default_factory=PowerSettings)
    sat: SatSettings = field(default_factory=SatSettings)
    dd: DdSettings = field(default_factory=DdSettings)
    internet: InternetSettings = field(default_factory=InternetSettings)
    controller: ControllerSettings = field(default_factory=ControllerSettings)
    linktest: LinkTestSettings = field(default_factory=LinkTestSettings)
    notify: NotificationSettings = field(default_factory=NotificationSettings)
    # 0.3.0's mail and APRS extras (settings_extra.py).
    mail: MailSettings = field(default_factory=MailSettings)
    winlink: WinlinkSettings = field(default_factory=WinlinkSettings)
    gps: GpsSettings = field(default_factory=GpsSettings)
    igate: IGateSettings = field(default_factory=IGateSettings)
    weather: WeatherSettings = field(default_factory=WeatherSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    objects: ObjectsSettings = field(default_factory=ObjectsSettings)
    #: The setup check has been through to the end at least once.
    setup_done: bool = False
    ui: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def copy(self) -> Settings:
        return from_dict(to_dict(self))


def _build(cls, data: dict):
    """A dataclass from a dict, ignoring keys it does not have and keeping
    defaults for keys the dict lacks (an older file)."""
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        sub =f.default_factory if f.default_factory is not dataclasses.MISSING else None
        if isinstance(sub, type) and dataclasses.is_dataclass(sub) and isinstance(value, dict):
            value = _build(sub, value)
        kwargs[f.name] = value
    return cls(**kwargs)


def _migrate(data: dict) -> dict:
    """An older file's settings in today's places. The link-test build
    before 0.2.1 kept the Pluto's modem as sdr_radio.pluto_modes ("",
    a mode name or "adaptive"); 0.2.1's fast settings replace it. A file
    that already has `fast` keeps its own."""
    modes = str((data.get("sdr_radio") or {}).get("pluto_modes") or "").strip()
    if modes and "fast" not in data:
        data = {**data, "fast": {"adapt": True} if modes == "adaptive" else {"mode": modes}}
    return data


def from_dict(data: dict) -> Settings:
    known = {f.name for f in dataclasses.fields(Settings)}
    data = _migrate(data)
    s = _build(Settings, data)
    s.extra = {k: v for k, v in data.items() if k not in known and k != "schema"}
    return s


def to_dict(s: Settings) -> dict:
    out = asdict(s)
    extra = out.pop("extra", {})
    return {"schema": SCHEMA, **extra, **out}


def settings_path() -> Path:
    return host.config_dir() / "settings.json"


def secrets_path() -> Path:
    return host.config_dir() / "secrets.json"


def _write(path: Path, text: str, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass                                    # Windows: the folder is the user's own
    os.replace(tmp, path)


def load() -> Settings:
    path = settings_path()
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return Settings()
    except (ValueError, TypeError, OSError):
        # A damaged file is set aside rather than overwritten, so whatever
        # was in it can still be recovered by hand.
        try:
            os.replace(path, path.with_suffix(".json.damaged"))
        except OSError:
            pass
        return Settings()


def save(s: Settings) -> None:
    _write(settings_path(), json.dumps(to_dict(s), indent=1, ensure_ascii=False))


def load_secrets() -> dict:
    try:
        data = json.loads(secrets_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_secrets(data: dict) -> None:
    _write(secrets_path(), json.dumps(data, indent=1), private=True)
