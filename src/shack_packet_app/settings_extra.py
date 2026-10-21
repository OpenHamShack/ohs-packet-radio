"""Settings for 0.3.0's mail and APRS extras, beside settings.py's (which
holds them as fields of Settings, so one settings.json carries all):

- `mail`: store-and-forward between stations (stationmail.py in ohs-libs);
- `winlink`: email through an RMS packet gateway (b2f.py) - the account
  password is never here, it is in secrets.json (WINLINK_SECRET);
- `gps`, `igate`, `weather`, `telemetry`, `objects`: the APRS extras.

Kept in their own module so the 0.3.0 workstreams' changes to settings.py
stay a handful of lines each. Every default is off: nothing new
transmits, listens or connects anywhere until the operator turns it on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The Winlink account password's key in secrets.json.
WINLINK_SECRET = "winlink_password"

GPS_SOURCES = ("none", "serial", "gpsd", "manual")
WEATHER_SOURCES = ("none", "peet", "davis", "file")


@dataclass
class MailSettings:
    #: Where this station's mail is kept for it and collected from: an
    #: OpenHamShack controller's BBS (its call-1) or a GB7 BBS; blank for none.
    home_bbs: str = ""
    home_via: str = ""
    #: Forwarding partners, one per entry: "GB7XYZ-1 via MB7NXX routes GB7XYZ,#79".
    partners: list = field(default_factory=list)
    #: Try the addressee's own station first for mail with no @BBS.
    direct: bool = True
    #: The SSID other OpenHamShack stations' mailboxes answer on.
    direct_ssid: int = 1
    #: Collect from the home BBS every so many minutes; 0 only when asked.
    collect_every_min: int = 0


@dataclass
class WinlinkSettings:
    #: The Winlink account's callsign; blank for the station's own.
    account: str = ""
    #: RMS packet gateways the operator has entered: "GB7XYZ-10 via MB7NXX".
    #: Winlink's gateway list needs an API key it issues to client authors,
    #: so there is no fetched list (RELEASE-NOTES 0.3.0).
    gateways: list = field(default_factory=list)
    gateway: str = ""
    #: Largest attachment total on one message, kB. Packet at 1200 baud
    #: moves about 100 bytes a second: 20 kB is three or four minutes.
    max_attachments_kb: int = 20


@dataclass
class GpsSettings:
    source: str = "none"               # GPS_SOURCES
    serial_port: str = ""
    serial_baud: int = 4800
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    #: A fixed position typed in, "55.9533,-3.1883" (manual source).
    manual: str = ""
    #: Beacon from the GPS with SmartBeaconing instead of the fixed beacon.
    smart: bool = True
    mobile_symbol: str = "/>"
    fast_speed_kmh: float = 90.0
    fast_rate_s: float = 180.0
    slow_speed_kmh: float = 5.0
    slow_rate_s: float = 1800.0
    turn_min_deg: float = 28.0
    turn_slope: float = 255.0
    turn_time_s: float = 30.0


@dataclass
class IGateSettings:
    enabled: bool = False
    server: str = "rotate.aprs2.net:14580"
    #: The APRS-IS server-side filter (its own syntax): what is sent to us.
    filter: str = "m/50"
    #: Messages for stations heard locally go out on RF (IS to RF).
    tx: bool = False
    tx_via: str = "WIDE1-1"


@dataclass
class WeatherSettings:
    source: str = "none"               # WEATHER_SOURCES
    serial_port: str = ""
    serial_baud: int = 2400
    file: str = ""
    every_min: int = 10
    comment: str = ""


@dataclass
class TelemetrySettings:
    enabled: bool = False
    every_min: int = 20


@dataclass
class ObjectsSettings:
    #: Placed objects and items: {name, lat, lon, symbol, comment, item}.
    items: list = field(default_factory=list)
    every_min: int = 30
