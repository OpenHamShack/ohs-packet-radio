"""From the operator's radio settings to the engine's radio link.

| kind | link | needs |
|---|---|---|
| `simulator` | simband.LiveRadio on the app's SimBand | nothing |
| `ic-9700`, `ic-705` | shack_packet.radios.IcomLink (shack_rig's IcomRig: LAN/Wi-Fi or USB) | the LAN address, user and password, or the USB port and codec |
| `ftx-1` | shack_packet.radios.YaesuLink (YaesuRig: CAT on the Enhanced COM port) | the port and the codec |
| `tm-v71` | shack_packet.radios.KenwoodLink (KenwoodRig: its PC port; packet through an interface on its DATA jack) | the PC port, the interface's sound device and PTT (RTS/DTR, CM108 or VOX), the DATA speed |
| `soundcard` | shack_packet.radios.SoundcardLink | the input and output devices; PTT by RTS/DTR, VOX or none |
| `none` | no link: receive through the SDR monitor only | - |
| `pluto` | shack_packet.radios.PlutoLink, with shack_pluto's guard on the Pluto when transmit is set up | its URI (`ip:...`, or `pluto://host?iiod=..&ssh=..` for one on the controller); the SSH password (secrets, "pluto_password", ADI's default "analog"); the fast-mode settings (settings.FastRadioSettings) |
| `hackrf` | shack_packet.radios.HackRFLink, half-duplex | libhackrf; the serial when there are several |
| `sdr-rx` | shack_packet.radios.SdrRxLink on an RTL-SDR, an rtl_tcp server or an SDRplay RSP: receive only | the source's library (SDRplay's API is the user's own install) |

**SDRs that transmit** (`pluto`, `hackrf`). A Pluto keys only under a
guard running on the Pluto itself (shack_pluto.guard), started over SSH as
the link opens; if it will not start, the link is receive-only and says
why ("tx-unguarded"), and the setup check shows it. Each Pluto's crystal
correction is looked up at open for the Pluto actually plugged in (by its
serial, shack_pluto.xo) and written for that session. A HackRF has nothing
to run a guard on; radios/hackrf.py says what a crash mid-over leaves.
Both are made with `transmit` as the setup check left it, so a station
that has not set transmit up opens them receive-only, with no guard.

**A Pluto's fast-mode settings** (0.2.1, shack_packet.fastsettings) are
taken for the channel's frequency, so the amplifier's drive cap is the one
saved for that band: the mode or adaptive ladder, the PTT output and relay
timing, the turnaround and the cap go to PlutoLink, and the guard is told
to drop a driven GPO and keep the cap. Settings the library finds wrong
refuse the link with their own reason key; the band-plan check
(`fast-out-of-plan`) does not, since receiving is never out of plan: it
refuses arming instead (controller.py). A transmit gain above the band's
drive cap is lowered to the cap, and the log says so, rather than refusing
the link: the cap is the most the amplifier takes, and a receiver that
would not open because of a transmit setting helps nobody.

Every real link keys only through its own safety core (radios/base.py:
the station asked at keying and every 10 ms while keyed, a 15 s watchdog,
unkey on any error) and the station handed to it here is the app's
ArmedStation, so arming and its expiry reach the link directly.

`transmit` is the setup check's answer (settings.TxSettings.set_up): a
link made before the operator has set transmit up is made receive-only,
so a rig is not even asked for its transmit audio stream (shack_rig's
make_rig: "the transmit stream is asked for only when the app may
transmit at all"). Arming still gates every key after that.

`AudioTap` sits between a link and the engine to measure the receive level
for the setup check's meters; it forwards everything unchanged.
"""
from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

from shack_packet.channel import LinkSink

from .settings import RadioSettings

log = logging.getLogger(__name__)

#: Radio kinds that make a real link (anything else is the simulator or none).
REAL = ("ic-9700", "ic-705", "ftx-1", "tm-v71", "soundcard")
#: SDRs as the radio (settings.SDR_RADIO_KINDS): real links too, kept apart
#: so the turnaround tool, which reads a rig's power meter, is not offered
#: for them.
SDR_REAL = ("pluto", "hackrf", "sdr-rx")


def power_status(radio: RadioSettings, link) -> tuple[str, str] | None:
    """What to tell an operator about transmit power right now: a
    texts.rf_power_status key ("minimum", "pending", "manual" or "failed"),
    and a detail (the library's own sentence) for "failed" only. None for a
    radio kind this never applies to (the simulator, none).

    For the real-radio test (GUIDE.md, HANDOVER.md "What's next"): the
    turnaround tool and the arming dialog both read this, so what the
    operator is told agrees whichever one asked. The Icom link itself is the
    one place this is decided (`_power_min_confirmed`/`_power_min_failed`);
    this only reads what it already knows.
    """
    if radio.kind in ("ic-9700", "ic-705"):
        if not radio.minimum_power_for_test:
            return None
        if getattr(link, "_power_min_confirmed", False):
            return "minimum", ""
        failed = getattr(link, "_power_min_failed", None)
        if failed:
            return "failed", failed
        return "pending", ""
    if radio.kind in ("ftx-1", "tm-v71", "soundcard"):
        return "manual", ""
    if radio.kind in ("pluto", "hackrf"):
        return "sdr", ""
    return None


class LinkError(Exception):
    """A link that cannot be made from these settings; `reason` is a key the
    window translates (ui/texts.py) and `detail` the library's own words."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def setup_dir():
    """Where each radio's saved settings wait until they are put back
    (shack_rig.datasetup's journal): the next start puts back what a crash
    left changed."""
    from . import host
    return host.data_dir() / "radio-setup"


def make_link(radio: RadioSettings, *, station, frequency_hz: int, transmit: bool, password: str = "",
              band=None, sound_backend=None, open_port=None, sdr=None, secrets: dict | None = None,
              data_dir=None, sdr_factory=None, fast=None, dd=None):
    """The link for these settings, not yet open. `band` is the SimBand for
    the simulator; `sound_backend` and `open_port` are for tests. `sdr` is
    settings.SdrRadioSettings for the SDR kinds; `sdr_factory`, a test's
    stand-in for make_sdr_link.

    Every real rig link sets the radio up for data and puts it back on
    close (shack_packet.radios.rig): its saved settings are kept in
    `setup_dir()`, and `radio.mod_level_percent` is the transmit audio
    level it sets, when one is given. `fast` is settings.FastRadioSettings,
    used by a Pluto only."""
    kind = radio.kind
    if kind == "ic-9700-dd":
        return make_dd_link(radio, dd, station=station, password=password)
    if kind in SDR_REAL:
        return (sdr_factory or make_sdr_link)(radio, sdr, station=station, frequency_hz=frequency_hz,
                                              transmit=transmit, secrets=secrets or {}, data_dir=data_dir,
                                              fast=fast)
    if kind == "none":
        return None
    if kind == "simulator":
        if band is None:
            raise LinkError("setup", "no simulated band")
        return band.radio()
    turnaround = radio.turnaround_s
    try:
        if kind in ("ic-9700", "ic-705"):
            from shack_packet.radios import IcomLink
            profile = {"model": kind, "transport": radio.transport}
            if radio.transport == "usb":
                profile.update({"civ_device": radio.civ_device, "civ_baud": radio.civ_baud,
                                "audio_device": radio.audio_in, "audio_backend": "portaudio",
                                "tx_audio": "portaudio", "tx_audio_device": radio.audio_out or radio.audio_in})
            else:
                if not str(radio.host or "").strip():
                    # Refused here, once. Handed on, an empty host became a
                    # connect to ":50001" that the rig retried with a
                    # traceback every second or two for as long as the app ran
                    # (Ray's log, 18/10/2026).
                    raise LinkError("setup", "the radio's network address is not set")
                profile.update({"host": radio.host, "port": radio.port, "username": radio.username,
                                "client_name": "shack-packet"})
            return IcomLink(profile, password=password, frequency=frequency_hz, transmit=transmit,
                            station=station, turnaround_s=turnaround,
                            minimum_power_for_test=radio.minimum_power_for_test,
                            setup_dir=setup_dir(), mod_level_percent=radio.mod_level_percent)
        if kind == "ftx-1":
            from shack_packet.radios import YaesuLink
            ptt = radio.ptt if radio.ptt in ("cat", "rts", "dtr") else "cat"
            return YaesuLink(radio.cat_device, audio=radio.audio_in, audio_out=radio.audio_out, ptt=ptt,
                             ptt_device=radio.ptt_device, frequency=frequency_hz, transmit=transmit,
                             station=station, turnaround_s=turnaround, sound_backend=sound_backend,
                             open_port=open_port, setup_dir=setup_dir(),
                             mod_level_percent=radio.mod_level_percent)
        if kind == "tm-v71":
            from shack_packet.radios import KenwoodLink
            ptt = radio.ptt if radio.ptt in ("rts", "dtr", "cm108", "vox", "none") else "rts"
            return KenwoodLink(radio.cat_device, audio=radio.audio_in, audio_out=radio.audio_out, ptt=ptt,
                               ptt_device=radio.ptt_device, cm108_gpio=int(radio.cm108_gpio or 3),
                               speed=9600 if int(radio.data_speed or 1200) == 9600 else 1200,
                               pc_baud=int(radio.cat_baud or 0), frequency=frequency_hz, transmit=transmit,
                               station=station, turnaround_s=turnaround, sound_backend=sound_backend,
                               open_port=open_port, setup_dir=setup_dir())
        if kind == "soundcard":
            from shack_packet.radios import SoundcardLink
            ptt = radio.ptt if radio.ptt in ("rts", "dtr", "vox", "none") else "none"
            if not transmit:
                ptt = "none"
            return SoundcardLink(radio.audio_in, radio.audio_out, ptt=ptt, ptt_device=radio.ptt_device,
                                 station=station, turnaround_s=turnaround, sound_backend=sound_backend,
                                 open_port=open_port)
    except ImportError as e:
        raise LinkError("missing-library", str(e)) from None
    except Exception as e:                          # noqa: BLE001 - the library's LinkRefused and its AudioError
        raise LinkError(getattr(e, "reason", "setup") or "setup", getattr(e, "detail", "") or str(e)) from None
    raise LinkError("setup", f"unknown radio kind {kind!r}")


def make_dd_link(radio: RadioSettings, dd, *, station, password: str = ""):
    """The 23 cm DD link (0.3.0, experimental; shack_packet.radios.dd):
    AX.25 over AXUDP between this PC's DD-side address and the other
    station's, with the IC-9700 reached as for packet (its LAN address, or
    its USB CI-V port) only to put it in DD on the channel and back. No
    audio stream is opened. LinkError as make_link."""
    from .adaptpower import level_of
    from .settings import DdSettings
    dd = dd or DdSettings()
    profile = {"model": "ic-9700", "transport": radio.transport}
    if radio.transport == "usb":
        profile.update({"civ_device": radio.civ_device, "civ_baud": radio.civ_baud})
    elif str(radio.host or "").strip():
        profile.update({"host": radio.host, "port": radio.port, "username": radio.username,
                        "client_name": "shack-packet"})
    else:
        raise LinkError("setup", "the radio's network address is not set")
    try:
        from shack_packet.radios.dd import DdLink
        return DdLink(local=dd.local, peer=dd.peer, station=station, profile=profile, password=password,
                      frequency=int(dd.channel_hz),
                      power_level=None if dd.power_percent is None else level_of(dd.power_percent),
                      setup_dir=setup_dir())
    except ImportError as e:
        raise LinkError("missing-library", str(e)) from None


def xo_lookup(data_dir):
    """PlutoLink's crystal correction for whichever Pluto it finds at open:
    looked up by that Pluto's serial in shack_pluto.xo's store. A Pluto
    with no usable serial (the nano once reported all f's) has no entry
    here; the setup check files it by its MAC, which only it can read."""
    from shack_pluto.identity import usable_serial
    from shack_pluto.xo import XoStore
    store = XoStore(data_dir)

    def lookup(attrs: dict):
        serial = usable_serial(attrs.get("hw_serial"))
        return store.get(f"serial:{serial}") if serial else None
    return lookup


def make_sdr_link(radio: RadioSettings, sdr, *, station, frequency_hz: int, transmit: bool, secrets: dict,
                  data_dir=None, fast=None):
    """An SDR kind's link (module docstring). LinkError as make_link."""
    from . import host
    from .settings import FastRadioSettings, SdrRadioSettings
    sdr = sdr or SdrRadioSettings()
    fast = fast or FastRadioSettings()
    data_dir = data_dir or host.data_dir()
    turnaround = radio.turnaround_s
    try:
        if radio.kind == "pluto":
            from shack_packet.fastsettings import PROBLEMS
            from shack_packet.radios import PlutoLink
            fs = fast.for_frequency(frequency_hz)
            wrong = fs.problems()                   # without the frequency: the band plan refuses arming only
            if wrong:
                raise LinkError(wrong[0], PROBLEMS.get(wrong[0], ""))
            tx_gain = float(sdr.pluto_tx_gain_db)
            if fs.max_tx_gain_db is not None and tx_gain > fs.max_tx_gain_db:
                log.warning("Pluto transmit gain %.2f dB is above this band's amplifier drive cap; using the cap, "
                            "%.2f dB", tx_gain, fs.max_tx_gain_db)
                tx_gain = float(fs.max_tx_gain_db)
            kw = fs.pluto_kwargs()
            kw.setdefault("turnaround_s", turnaround)
            guard = None
            if transmit:
                from shack_pluto.guard import Guard, GuardError, SshRunner
                try:
                    runner = SshRunner.for_uri(sdr.pluto_uri, secrets.get("pluto_password") or "analog")
                    guard = Guard(runner, 15.0, **fs.guard_kwargs())
                except GuardError as e:
                    # A USB URI: no network to reach a guard over. The link
                    # still opens, receive-only, and says why.
                    guard = _FailedGuard(e)
            importer = _bundle("import_iio")
            return PlutoLink(sdr.pluto_uri, frequency_hz, tx_gain_db=tx_gain, rx_gain=sdr.pluto_rx_gain_db,
                             guard=guard, transmit=transmit, xo_correction=xo_lookup(data_dir), station=station,
                             iio_module=importer() if importer is not None else None, **kw)
        if radio.kind == "hackrf":
            from shack_packet.radios import HackRFLink
            return HackRFLink(frequency_hz, serial=sdr.hackrf_serial or None, tx_vga_db=sdr.hackrf_tx_vga_db,
                              amp=sdr.hackrf_amp, lna_db=sdr.hackrf_lna_db, vga_db=sdr.hackrf_vga_db,
                              transmit=transmit, station=station, turnaround_s=turnaround,
                              lib_path=_hackrf_lib(), search=_search())
        if radio.kind == "sdr-rx":
            from shack_packet.radios import SdrRxLink
            return SdrRxLink(make_rx_source(sdr), frequency_hz, ppm=sdr.rx_ppm, kind=sdr.rx_source,
                             station=station)
    except ImportError as e:
        raise LinkError("missing-library", str(e)) from None
    except LinkError:
        raise
    except Exception as e:                          # noqa: BLE001 - LinkRefused, SdrError
        raise LinkError(getattr(e, "reason", "setup") or "setup", getattr(e, "detail", "") or str(e)) from None
    raise LinkError("setup", f"unknown radio kind {radio.kind!r}")


def make_rx_source(sdr):
    """The IqSource for a receive-only SDR, not yet open."""
    from shack_packet import sdr as sdrlib
    rate = 2_400_000
    if sdr.rx_source == "rtl_tcp":
        return sdrlib.RtlTcpSource(sdr.rx_host, int(sdr.rx_port), rate=rate, gain=sdr.rx_gain_db)
    if sdr.rx_source == "sdrplay":
        return sdrlib.SdrplaySource(rate=rate, gain=sdr.rx_gain_db, serial=sdr.rx_serial or None)
    return sdrlib.RtlSdrSource(rate=rate, gain=sdr.rx_gain_db, index=int(sdr.rx_index),
                               serial=sdr.rx_serial or None, search=_search())


def _bundle(name: str):
    """What the bundles' native-library helpers in host.py say (search
    folders, the bundled libiio), when this build has them (0.2's packaging
    adds them); nothing otherwise, and the libraries are looked for where
    the system keeps them."""
    from . import host
    return getattr(host, name, None)


def _search() -> list:
    fn = _bundle("rtlsdr_search")
    return list(fn()) if fn is not None else []


def _hackrf_lib():
    fn = _bundle("hackrf_lib")
    return fn() if fn is not None else None


class _FailedGuard:
    """A guard that could not even be made (a USB URI): start() raises what
    making it raised, so PlutoLink opens receive-only and says why."""

    def __init__(self, error):
        self.error = error
        self.confirmed = False

    def start(self):
        raise self.error

    def stop(self):
        pass


class AudioTap(LinkSink):
    """Forwards a link's callbacks to the engine and keeps the receive level
    (dBFS, the peak of the last 100 ms, and the RMS) for the meters."""

    def __init__(self, engine, window_s: float = 0.1):
        self.engine = engine
        self.station = engine.station
        self._lock = threading.Lock()
        self.peak_dbfs = -120.0
        self.rms_dbfs = -120.0
        self.updated = 0.0
        self.blocks = 0
        self.window_s = window_s

    def pcm_in(self, pcm) -> None:
        if isinstance(pcm, (bytes, bytearray, memoryview)):
            a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        else:
            a = np.asarray(pcm, dtype=np.float32)
        if a.size:
            peak = float(np.max(np.abs(a)))
            rms = float(np.sqrt(np.mean(a * a)))
            with self._lock:
                self.peak_dbfs = 20 * math.log10(max(peak, 1e-6))
                self.rms_dbfs = 20 * math.log10(max(rms, 1e-6))
                self.updated = time.monotonic()
                self.blocks += 1
        self.engine.pcm_in(pcm)

    def frame_in(self, frame: bytes) -> None:
        self.engine.frame_in(frame)

    def busy_in(self, busy: bool) -> None:
        self.engine.busy_in(busy)

    def link_event(self, kind: str, reason: str, detail: dict | None = None) -> None:
        self.engine.link_event(kind, reason, detail)

    def level(self) -> tuple[float, float] | None:
        """(peak, rms) dBFS, or None when no audio has arrived for a second."""
        with self._lock:
            if time.monotonic() - self.updated > 1.0:
                return None
            return self.peak_dbfs, self.rms_dbfs
