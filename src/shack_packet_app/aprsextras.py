"""APRS beyond the fixed beacon (0.3.0), behind the window: a GPS and
SmartBeaconing, an iGate, a weather station, telemetry, and objects and
items placed on the map. The protocol and the etiquette are ohs-libs'
(nmea.py, aprsis.py, aprswx.py, aprs_services.py); this module only runs
them: reader threads for a serial GPS, gpsd, a weather station and an
APRS-IS server, each outbound or local, none listening.

Every piece is off until switched on in Settings. Transmissions go
through the engine like everything else, so nothing reaches the air
unless transmit is armed; the iGate's receive side (RF to APRS-IS) needs
no arming, since it only sends to the internet what was heard.

Threads: each reader keeps its latest value under this object's lock;
the engine's services read it when they want it. Nothing here calls the
window.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from shack_packet import aprsis, aprswx, nmea
from shack_packet.aprs_services import AprsSchedule, PlacedObject, ScheduleConfig
from shack_packet.aprs_station import Beacon, Fix, SmartBeaconing
from shack_packet.errors import PacketError

log = logging.getLogger(__name__)

#: A GPS fix older than this is not used for a beacon.
FIX_STALE_S = 30.0


def manual_position(text: str) -> tuple[float, float] | None:
    """"55.9533, -3.1883" (decimal degrees) or None."""
    try:
        a, b = [float(x) for x in str(text).replace(";", ",").split(",")[:2]]
    except ValueError:
        return None
    if -90 <= a <= 90 and -180 <= b <= 180:
        return a, b
    return None


class _Reader(threading.Thread):
    """A thread that opens something, reads lines (or bytes) from it, and
    tries again after a pause when it fails. `state`: idle | open | failed;
    `error` the last reason."""

    def __init__(self, name: str):
        super().__init__(name=name, daemon=True)
        self.stop_event = threading.Event()
        self.state = "idle"
        self.error = ""

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        pause = 5.0
        while not self.stop_event.is_set():
            try:
                self.session()
                pause = 5.0
            except Exception as e:                  # noqa: BLE001 - a device unplugged, a server gone
                self.state, self.error = "failed", str(e)[:200]
                log.info("%s: %s", self.name, e)
            if self.stop_event.wait(pause):
                break
            pause = min(pause * 2, 60.0)
        self.state = "idle"

    def session(self) -> None:
        raise NotImplementedError


def _open_serial(port: str, baud: int, timeout: float = 2.0):
    import serial                                   # pyserial: a real device only
    return serial.Serial(port, baud, timeout=timeout)


class GpsReader(_Reader):
    def __init__(self, settings, on_fix, *, open_serial=_open_serial, connect=socket.create_connection):
        super().__init__("gps")
        self.s = settings
        self.on_fix = on_fix
        self.open_serial = open_serial
        self.connect = connect

    def session(self) -> None:
        if self.s.source == "serial":
            port = self.open_serial(self.s.serial_port, int(self.s.serial_baud))
            reader = nmea.NmeaReader()
            self.state = "open"
            try:
                buf = b""
                while not self.stop_event.is_set():
                    buf += port.read(256) or b""
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        fix = reader.line(raw.decode("ascii", "replace"))
                        if fix is not None:
                            self.on_fix(fix)
                    buf = buf[-1024:]
            finally:
                port.close()
        elif self.s.source == "gpsd":
            sock = self.connect((self.s.gpsd_host, int(self.s.gpsd_port)), timeout=10)
            self.state = "open"
            try:
                sock.settimeout(30)
                sock.sendall(nmea.GPSD_WATCH)
                buf = b""
                while not self.stop_event.is_set():
                    data = sock.recv(4096)
                    if not data:
                        raise OSError("gpsd closed the connection")
                    buf += data
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        fix = nmea.from_gpsd(raw.decode("utf-8", "replace"))
                        if fix is not None:
                            self.on_fix(fix)
            finally:
                sock.close()
        else:
            self.stop_event.set()


class WeatherReader(_Reader):
    def __init__(self, settings, on_wx, *, open_serial=_open_serial):
        super().__init__("weather")
        self.s = settings
        self.on_wx = on_wx
        self.open_serial = open_serial

    def session(self) -> None:
        src = self.s.source
        if src == "file":
            self.state = "open"
            last = None
            while not self.stop_event.is_set():
                path = Path(self.s.file)
                try:
                    stamp = path.stat().st_mtime
                    if stamp != last:
                        last = stamp
                        got = aprswx.from_file(path.read_text(encoding="utf-8", errors="replace"))
                        if got:
                            self.on_wx(got)
                    self.error = ""
                except OSError as e:
                    self.error = str(e)[:200]
                self.stop_event.wait(30.0)
            return
        port = self.open_serial(self.s.serial_port, int(self.s.serial_baud))
        self.state = "open"
        try:
            if src == "peet":
                buf = b""
                while not self.stop_event.is_set():
                    buf += port.read(256) or b""
                    while b"\n" in buf or b"\r" in buf:
                        cut = min(i for i in (buf.find(b"\n"), buf.find(b"\r")) if i >= 0)
                        raw, buf = buf[:cut], buf[cut + 1:]
                        got = aprswx.peet_line(raw.decode("ascii", "replace"))
                        if got:
                            self.on_wx(got)
                    buf = buf[-512:]
            elif src == "davis":
                while not self.stop_event.is_set():
                    port.write(aprswx.DAVIS_WAKE)
                    port.read(2)
                    port.write(aprswx.DAVIS_LOOP)
                    ack = port.read(1)
                    if ack and ack[0] == aprswx.DAVIS_ACK:
                        got = aprswx.davis_loop(port.read(aprswx.LOOP_SIZE))
                        if got:
                            self.on_wx(got)
                    self.stop_event.wait(60.0)
        finally:
            port.close()


@dataclass
class _Counts:
    rx: int = 0
    tx: int = 0
    digi: int = 0
    igated: int = 0


class AprsExtras:
    def __init__(self, ctl, *, open_serial=_open_serial, connect=socket.create_connection, aprs_is=None):
        self.ctl = ctl
        self.open_serial = open_serial
        self.connect = connect
        self.aprs_is_factory = aprs_is or aprsis.AprsIsClient
        self._lock = threading.Lock()
        self.fix: nmea.Fix | None = None
        self.fix_at: float | None = None
        self.wx: dict | None = None
        self.wx_at: float | None = None
        self.gps: GpsReader | None = None
        self.weather: WeatherReader | None = None
        self.igate: aprsis.IGate | None = None
        self.client: aprsis.AprsIsClient | None = None
        self.schedule: AprsSchedule | None = None
        self._counts = _Counts()
        self._last_counts = _Counts()
        self.engine = None

    @property
    def settings(self):
        return self.ctl.settings

    # -- where we are ------------------------------------------------------------------
    def position(self) -> tuple[float, float] | None:
        """The GPS's (or the typed-in) position when there is one; None to
        use the locator."""
        g = self.settings.gps
        if g.source == "manual":
            return manual_position(g.manual)
        with self._lock:
            if self.fix is not None and self.fix_at is not None and time.monotonic() - self.fix_at < 300:
                return self.fix.lat, self.fix.lon
        return None

    def _on_fix(self, fix: nmea.Fix) -> None:
        with self._lock:
            self.fix, self.fix_at = fix, time.monotonic()

    def current_fix(self) -> Fix | None:
        """For SmartBeaconing (aprs_station.Beacon.source)."""
        g = self.settings.gps
        if g.source == "manual":
            pos = manual_position(g.manual)
            return Fix(pos[0], pos[1]) if pos else None
        with self._lock:
            if self.fix is None or self.fix_at is None or time.monotonic() - self.fix_at > FIX_STALE_S:
                return None
            f = self.fix
        return Fix(f.lat, f.lon, f.speed_kmh, f.course_deg)

    def beacons(self, beacons: list, path: list[str]) -> list:
        """The station's beacons with the GPS's put in place of the fixed
        one when a GPS is set to beacon (controller._services)."""
        g = self.settings.gps
        if g.source == "none" or not g.smart or not self.settings.aprs.beacon:
            return beacons
        smart = SmartBeaconing(fast_speed=g.fast_speed_kmh, fast_rate=max(60.0, g.fast_rate_s),
                               slow_speed=g.slow_speed_kmh, slow_rate=max(300.0, g.slow_rate_s),
                               turn_min=g.turn_min_deg, turn_slope=g.turn_slope, turn_time=max(15.0, g.turn_time_s))
        symbol = g.mobile_symbol if len(g.mobile_symbol) == 2 else "/>"
        if g.source == "manual":
            symbol = self.settings.aprs.symbol or "/-"
        return [Beacon(source=self.current_fix, smart=smart, symbol=symbol, comment=self.settings.aprs.comment,
                       via=list(path))]

    # -- life ------------------------------------------------------------------------------
    def start(self, engine, path: list[str]) -> AprsExtras:
        self.engine = engine
        s = self.settings
        if s.gps.source in ("serial", "gpsd"):
            self.gps = GpsReader(s.gps, self._on_fix, open_serial=self.open_serial, connect=self.connect)
            self.gps.start()
        if s.weather.source != "none":
            self.weather = WeatherReader(s.weather, self._on_wx, open_serial=self.open_serial)
            self.weather.start()
        cfg = ScheduleConfig(via=list(path), object_every_s=max(10, int(s.objects.every_min)) * 60.0,
                             weather_every_s=max(5, int(s.weather.every_min)) * 60.0,
                             weather_comment=s.weather.comment,
                             telemetry_every_s=max(10, int(s.telemetry.every_min)) * 60.0)
        self.schedule = AprsSchedule(engine, cfg, wall_clock=self.ctl.wall)
        for d in s.objects.items:
            try:
                self.schedule.place(_placed(d))
            except (PacketError, KeyError, TypeError, ValueError):
                log.warning("object %r not placed: it does not format", d)
        if s.weather.source != "none":
            self.schedule.weather_source = self._weather_now
        if s.telemetry.enabled:
            self.schedule.telemetry_source = self._telemetry_now
        engine.monitors.append(self._heard)
        if s.igate.enabled:
            self._start_igate(engine)
        return self

    def stop(self) -> None:
        for r in (self.gps, self.weather):
            if r is not None:
                r.stop()
        self.gps = self.weather = None
        if self.client is not None:
            self.client.stop()
        self.client = self.igate = self.schedule = self.engine = None

    # -- iGate ------------------------------------------------------------------------------
    def _start_igate(self, engine) -> None:
        g = self.settings.igate
        call = str(engine.station.address())
        via = [v.strip().upper() for v in g.tx_via.replace(",", " ").split() if v.strip()]
        self.igate = aprsis.IGate(call, aprsis.IGateConfig(tx=bool(g.tx), tx_via=via), clock=engine.clock)
        host, _, port = g.server.partition(":")
        try:
            server = (host.strip() or aprsis.SERVER[0], int(port or aprsis.SERVER[1]))
        except ValueError:
            server = aprsis.SERVER
        self.client = self.aprs_is_factory(call, aprsis.passcode(call), on_line=self._is_line, filter=g.filter,
                                           server=server, connect=self.connect)
        self.client.start()

    def _is_line(self, line: str) -> None:
        gate, engine = self.igate, self.engine
        if gate is None or engine is None:
            return
        with engine.lock:
            out = gate.is_to_rf(line)
        if out is not None:
            text, via = out
            engine.send_ui(engine_tocall(self.ctl), text, via=via)
            log.info("igate: IS to RF %s", text[:80])

    def _heard(self, frame) -> None:
        """Every frame heard on RF (the engine's monitors, under its lock)."""
        self._counts.rx += 1
        if frame.is_ui and frame.pid == 0xF0:
            gate, client = self.igate, self.client
            if gate is not None:
                gate.heard(frame)
                if client is not None:
                    line = gate.rf_to_is(frame)
                    if line is not None and client.send(line):
                        self._counts.igated += 1

    def sent(self, frames: int) -> None:
        self._counts.tx += frames

    # -- weather and telemetry -------------------------------------------------------
    def _on_wx(self, readings: dict) -> None:
        with self._lock:
            self.wx, self.wx_at = dict(readings), self.ctl.wall()

    def _weather_now(self):
        pos = self.position() or self.ctl.position()
        with self._lock:
            if pos is None or not self.wx:
                return None
            return pos[0], pos[1], dict(self.wx), self.wx_at

    def _telemetry_now(self) -> list:
        """What happened since the last report, each to 255: frames heard,
        sent, digipeated and gated (a count since start would pass 255 in
        an afternoon)."""
        digi = getattr(self.ctl.digi, "stats", {}) if self.ctl.digi is not None else {}
        self._counts.digi = int(digi.get("repeated", 0)) if hasattr(digi, "get") else 0
        c, last = self._counts, self._last_counts
        out = [c.rx - last.rx, c.tx - last.tx, c.digi - last.digi, c.igated - last.igated, 0]
        self._last_counts = _Counts(c.rx, c.tx, c.digi, c.igated)
        return out

    # -- objects -----------------------------------------------------------------------
    def place(self, d: dict) -> str:
        """Add or move an object or item (settings.objects.items keeps it);
        "" or a reason key ("bad-object")."""
        try:
            obj = _placed(d)
            obj.text()
        except (PacketError, KeyError, TypeError, ValueError):
            return "bad-object"
        items = [x for x in self.settings.objects.items if str(x.get("name", "")).upper() != obj.name.upper()]
        items.append({"name": obj.name, "lat": obj.lat, "lon": obj.lon, "symbol": obj.symbol,
                      "comment": obj.comment, "item": obj.item})
        self.settings.objects.items = items
        if self.schedule is not None:
            self.schedule.place(obj)
        return ""

    def kill(self, name: str) -> None:
        self.settings.objects.items = [x for x in self.settings.objects.items
                                       if str(x.get("name", "")).upper() != name.upper()]
        if self.schedule is not None:
            self.schedule.kill(name)

    def status(self) -> dict:
        with self._lock:
            fix, wx = self.fix, dict(self.wx or {})
        return {"gps": self.gps.state if self.gps else "off", "gps_error": self.gps.error if self.gps else "",
                "fix": (fix.lat, fix.lon, fix.speed_kmh) if fix else None,
                "weather": self.weather.state if self.weather else "off",
                "weather_error": self.weather.error if self.weather else "", "wx": wx,
                "igate": self.client.state if self.client else "off",
                "igate_error": self.client.error if self.client else "",
                "igate_stats": dict(self.igate.stats) if self.igate else {},
                "sent": dict(self.schedule.sent) if self.schedule else {}}


def _placed(d: dict) -> PlacedObject:
    return PlacedObject(str(d["name"]).strip(), float(d["lat"]), float(d["lon"]), str(d.get("symbol") or "/."),
                        str(d.get("comment") or ""), bool(d.get("item", False)))


def engine_tocall(ctl) -> str:
    return ctl.aprs.config.tocall if ctl.aprs is not None else "APZOHS"
