"""The receive-only SDR monitor: one SDR, every packet channel on the band
at once, beside (never instead of) the radio the station transmits on.

ohs-libs' shack_packet.sdr does the work (sources, channeliser, a receiver
a channel, the Monitor tables); this file chooses the source from the
settings, runs it, and feeds what it hears to the app's Monitor list and,
if the operator wants, to the APRS heard list and the map. Through
AprsStation.hear only: a message the SDR hears addressed to this station
is never answered, because the SDR may be listening to a channel the
station's radio is not on (aprs_station.py says so too).

| source | what | needs on the tester's machine |
|---|---|---|
| `synthetic` | a made-up 2 m band, paced to real time (the tests, a demo) | nothing |
| `rtl_tcp` | a remote RTL-SDR's rtl_tcp server, as a client | nothing |
| `rtlsdr` | an RTL-SDR dongle on USB | librtlsdr: in the Windows and macOS bundles from 0.2 (host.py), the distribution's librtlsdr0 on Linux; Zadig's WinUSB driver on Windows |
| `pluto` | an ADALM-Pluto, receive only, transmitter held quiet | libiio 0.26: in the Windows and macOS bundles from 0.2 (network only on macOS, as NB's: a Pluto there is reached by its USB network address), python3-libiio on Linux; ADI's USB driver on Windows (not yet in this installer) |

(0.1.x said both were bundled; they were not - `--library-check` on the
frozen build now proves what is.)

Nothing here has a way to transmit: the sdr package imports no radio link
and its Pluto backend refuses every transmitter write but "quiet".
"""
from __future__ import annotations

import logging
import threading
import time

from shack_packet import sdr
from shack_packet.aprs import TOCALL
from shack_packet.ax25 import Frame
from shack_packet.sdr.synth import Station as SynthStation

from . import host
from .settings import SdrSettings

log = logging.getLogger(__name__)

SOURCES = ("none", "synthetic", "rtl_tcp", "rtlsdr", "pluto")


class PacedSynthetic(sdr.SyntheticSource):
    """SyntheticSource at the speed of a real receiver, so a demo or a test
    sees frames arrive over time instead of all at once."""

    live = False

    def _open(self) -> None:
        super()._open()
        self._start = time.monotonic()

    def _read(self):
        z = super()._read()
        if z is not None:
            delay = self._start + self.pos / self.rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return z


def demo_source(centre: float, rate: float, repeats: int = 400) -> PacedSynthetic:
    """The synthetic band: a tracker with a position on 144.800, a node, a
    chat and the ISS, as ohs-libs' sdr.synth.demo_band but long enough for
    a session and endless after that (no end of the recording)."""
    def ui(src, text):
        return Frame.from_monitor(f"{src}>{TOCALL},WIDE1-1:{text}").encode()
    stations = [
        SynthStation(144_800_000, [ui("M0TST-9", "!5530.00N/00410.00W>synthetic tracker")] * repeats, -35, 0.5, 6.0),
        SynthStation(144_800_000, [ui("G0SDR", "=5552.20N/00320.10W-synthetic home")] * repeats, -42, 2.0, 9.5),
        SynthStation(144_925_000, [ui("GB7TST", ">synthetic node")] * repeats, -40, 1.5, 9.0, offset_hz=400),
        SynthStation(144_937_500, [ui("M0ABC", ">synthetic chat")] * repeats, -45, 2.5, 11.0, offset_hz=-300),
        SynthStation(145_825_000, [ui("RS0ISS", ">synthetic ISS")] * repeats, -50, 3.5, 13.0, offset_hz=2_000),
    ]
    return PacedSynthetic(centre, rate, stations, noise_dbfs=-35.0, seconds=None)


def make_source(s: SdrSettings, plan) -> sdr.IqSource:
    rate = float(s.rate)
    centre = plan.centre_for(rate)
    if centre is None:
        raise sdr.SdrError("rate-unsupported", "the channel plan does not fit the sample rate")
    if s.source == "synthetic":
        return demo_source(centre, rate)
    if s.source == "rtl_tcp":
        return sdr.RtlTcpSource(s.rtltcp_host, int(s.rtltcp_port), centre, rate, s.gain_db)
    if s.source == "rtlsdr":
        return sdr.RtlSdrSource(centre, rate, s.gain_db, index=int(s.device_index), search=host.rtlsdr_search())
    if s.source == "pluto":
        try:
            host.import_iio()            # the bundle's libiio, before the source's own import finds another
        except (ImportError, OSError, AttributeError, TypeError):
            pass                         # PlutoSource says "no-library" itself, in its own words
        return sdr.PlutoSource(s.pluto_uri, centre, rate, s.gain_db)
    raise sdr.SdrError("no-device", f"no source {s.source!r}")


class SdrMonitor:
    """One SDR's monitor, started and stopped by the controller. `on_frame`
    is told each sdr.Decoded (on the SDR's worker thread); `on_error` each
    SdrError reason key."""

    def __init__(self, settings: SdrSettings, *, plan=None, source_factory=make_source, on_frame=None,
                 on_error=None):
        self.settings = settings
        self.plan = plan or sdr.default_plan()
        self.source_factory = source_factory
        self.on_frame = on_frame
        self.on_error = on_error
        self.pipeline: sdr.Pipeline | None = None
        self.runner: sdr.Runner | None = None
        self.error = ""
        self.error_detail = ""
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.runner is not None and self.runner.running

    @property
    def monitor(self):
        return self.pipeline.monitor if self.pipeline is not None else None

    def start(self) -> None:
        with self._lock:
            if self.runner is not None:
                return
            self.error = self.error_detail = ""
            try:
                source = self.source_factory(self.settings, self.plan)
                self.pipeline = sdr.Pipeline(source.rate, source.centre, self.plan, ppm=float(self.settings.ppm))
                if self.on_frame is not None:
                    self.pipeline.monitor.listeners.append(self.on_frame)
                self.runner = sdr.Runner(source, self.pipeline, on_error=self._failed)
                self.runner.start()
            except sdr.SdrError as e:
                self.runner = None
                self._failed(e)
            except Exception as e:                  # noqa: BLE001 - a missing library, a bad setting
                log.exception("starting the SDR monitor")
                self.runner = None
                self._failed(sdr.SdrError("open-failed", str(e)))

    def stop(self) -> None:
        with self._lock:
            runner, self.runner = self.runner, None
        if runner is not None:
            try:
                runner.stop()
            except Exception:                       # noqa: BLE001 - stopping regardless
                log.exception("stopping the SDR monitor")

    def _failed(self, e) -> None:
        self.error = getattr(e, "reason", "") or "open-failed"
        self.error_detail = getattr(e, "detail", "") or str(e)
        log.warning("SDR monitor: %s (%s)", self.error, self.error_detail)
        if self.on_error is not None:
            self.on_error(self.error)
