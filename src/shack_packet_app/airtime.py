"""How long a file takes on the air, at every mode (0.3.1). Brian asked on
20/10/2026 for the Send dialog to say how long a transfer will take "at
the various baud rates"; this is the arithmetic, without Qt, so tests
drive it and the window only draws (ui/filesend.py EstimateTable).

**Not raw baud.** 0.3.0 divided the file's bits by the bit rate times a
flat efficiency (0.66 / 0.68), which is close at 1200 baud and badly
wrong at 153 600, where the turnaround, not the bits, is most of the time
(ohs-libs shack_packet README, "Fast-mode throughput, and why it was
39 %"). The model here follows one transfer as the engine runs it:

- **What goes on the air**: the file after compression where it will
  apply (`wire_size`: the OHS protocol's DEFLATE with a sync flush per
  1024-byte chunk, exactly as the sender runs it, skipped where filexfer's
  own `worth_compressing` skips it, and never for YAPP), then the
  protocol's framing (an OHS DATA frame's 20 bytes a chunk; YAPP's 3 a
  256-byte block), cut into I frames of PACLEN, each frame's bits as the
  library itself builds them (`afsk.transmission_bits`: address, control,
  PID, FCS, flags, bit stuffing, FX.25 when it is on).
- **Overs**: k frames each (the window), fewer where the channel's 10 s
  cap on one transmission stops it first, each after TXDELAY - never less
  than the radio's turnaround plus the mode's lock bits, as the channel
  itself sends it.
- **The turn round between overs**: the receiver hears the over (receive
  lag), waits out its busy detector, keys up and sends its RR with its own
  TXDELAY; the sender hears that and keys again. `Timing` holds those
  delays per kind of radio. On a link test the acknowledgements go by
  internet instead (`acks="internet"`), which is how the 1200 baud
  calibration point was measured.
- **The protocol's own exchanges**: OFFER/ACCEPT before the data and
  DONE/RESULT after it, an over each way each.
- **Retries** (`loss`): the "with retries" figure loses one frame in
  `RETRY_LOSS`. With REJ a loss resends the rest of the over; with SREJ
  (two OHS stations on the fast modes) only that frame. A lost
  acknowledgement, SREJ or poll costs a T1 wait, which at one frame in
  ten is most windows of 63 (the library's simulator says so).

**Calibration** (tests/test_airtime.py holds each one):

- 1200 AFSK on the air, GM4BRI (IC-9700 over LAN) to GM0OTB, 19/10/2026:
  5142 bytes in 42.8 s, k 4, PACLEN 256, acknowledgements by internet.
- Pluto to nano on the air, 18/10/2026, "as keyed" (k 16 at 38 400, k 7
  below, PACLEN 256, TXDELAY 200 ms, REJ): 10 KB at 38 400 in 5.5 s and
  at 19 200 in 11.0 s; at 9600 33.5 s with 28 of 40 frames sent again,
  which the retries model reaches at a loss of one in five.
- The library's simulated channel with a Pluto's timing: gfsk153600
  moves more than 40 kbit/s of file data (ohs-libs test_filexfer), and the
  app's own settings at every fast mode (tools/airtime-sim.py prints the
  simulator's time beside this model's).
- 23 cm DD is not measured at all: the figures are this model's reading
  of what the engine would do (the channel paces a DD link as gfsk76800,
  ohs-libs radios/dd.py) and the table says "estimate, unmeasured".

**Measured beats modelled.** A finished transfer or a link test with a
station is kept (`Measurements`, `<data>/throughput.json`): its time
against this model's time for the same bytes says how much slower (or
faster) that path really is, and the estimate for the next file to that
station is scaled by it. One measurement per station and mode, the latest.
"""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
import zlib
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

#: The table's rows, slowest first: every fast mode a Pluto offers
#: (fastmodes.MODES) and 23 cm DD.
DD = "dd128k"
ROWS = ("afsk1200", "g3ruh9600", "g3ruh19200", "g3ruh38400", "gfsk76800", "gfsk153600", "4fsk38400", "4fsk76800",
        DD)
#: The DD radio's own bit rate (Icom's Advanced Manual, section 13).
DD_BITRATE = 128_000

#: The "with retries" figure's frame loss (module docstring).
RETRY_LOSS = 0.1
#: A lost acknowledgement waits for T1 before the sender polls
#: (datalink.FRACK).
T1_WAIT_S = 3.0
#: How long a station waits on a busy channel before talking anyway
#: (channel.MAX_BUSY_WAIT_S).
MAX_BUSY_S = 15.0
#: A file chunk and its OHS DATA framing (ohs-libs FILES.md: the 7-byte
#: frame header and DATA's 13 bytes of id, offset and flags).
OHS_CHUNK = 1024
OHS_CHUNK_OVERHEAD = 20
#: YAPP: a DT header of two bytes and YAPP-C's sum, per block of 256.
YAPP_BLOCK = 256
YAPP_BLOCK_OVERHEAD = 3
#: Larger files are compressed in part and the rest reckoned at its rate.
COMPRESS_SAMPLE = 2 * 1024 * 1024
#: Smaller transfers are mostly the offer and the result: not kept as a
#: measurement of the path. 1 KB, the link test's smallest, is kept: the
#: comparison is with the model's time for the same few frames.
MIN_MEASURED_BYTES = 1024


@dataclass(frozen=True)
class Timing:
    """What one kind of radio adds round an over, in seconds (sim.
    RadioTiming's quantities, plus the turnaround): from deciding to send
    to the first bit (`tx_lag`), the carrier left up after the last
    (`drain`), from a frame's last bit to the engine having it (`rx_lag`),
    the busy detector's hold after a carrier drops (`busy_tail`), the
    key-up time TXDELAY never goes below (`turnaround`), and how long an
    acknowledgement by internet takes (`internet`)."""
    tx_lag: float
    drain: float
    rx_lag: float
    busy_tail: float
    turnaround: float
    internet: float = 0.25


#: A Pluto: sim.PLUTO (taken from the keyed 38 400 run's log, 18/10/2026)
#: and the 100 ms turnaround the link assumes until one is measured.
PLUTO = Timing(tx_lag=0.03, drain=0.05, rx_lag=0.075, busy_tail=0.15, turnaround=0.10)
#: A rig on a sound card or Icom's LAN audio: calibrated on the 19/10 run
#: (an IC-9700 over LAN; its audio stream adds a few tenths each way).
RIG = Timing(tx_lag=0.10, drain=0.05, rx_lag=0.10, busy_tail=0.10, turnaround=0.0, internet=0.15)
#: 23 cm DD: an Ethernet bridge; nothing measured (module docstring).
#: Its "busy tail" is the channel's p-persistence instead (the library's
#: defaults, PERSIST 63 and 100 ms slots: 0.3 s on average before each
#: over), which the simulator shows costing a DD link about a third; the
#: Pluto's and the rig's figures were measured with it already in them.
DD_TIMING = Timing(tx_lag=0.02, drain=0.02, rx_lag=0.02, busy_tail=0.30, turnaround=0.0)


@dataclass(frozen=True)
class LinkParams:
    """The data link and channel settings a transfer runs with."""
    window: int = 4
    paclen: int = 256
    modulo: int = 8
    srej: bool = False
    txdelay_s: float = 0.30
    txtail_s: float = 0.03
    max_tx_s: float = 10.0
    max_frames: int = 8
    fx25: object = None


#: What every radio but a fast Pluto goes on the air with: the library's
#: ChannelConfig and LinkConfig defaults (controller.Controller.start).
DEFAULT_LINK = LinkParams()


@dataclass(frozen=True)
class ModeInfo:
    name: str
    bitrate: int            # data bits a second on the air
    pace_bitrate: int       # what the channel reckons airtime at
    lock_bits: int


@lru_cache(maxsize=None)
def mode_info(name: str) -> ModeInfo:
    if name == DD:
        # The channel paces a DD link as gfsk76800 (radios/dd.py DdLink.baud).
        return ModeInfo(DD, DD_BITRATE, 76_800, 96)
    from shack_packet import modem
    m = modem.mode(name)
    return ModeInfo(m.name, int(m.bitrate), int(m.bitrate), int(m.lock_bits))


@lru_cache(maxsize=None)
def _frame_bits(info_bytes: int, modulo: int, fx25) -> int:
    """One I frame's bits on the air carrying `info_bytes`, with the
    library's own stuffing and FX.25 (random data stands for a compressed
    file; text stuffs a little less)."""
    from shack_packet import afsk
    rng = random.Random(info_bytes)
    header = 14 + (2 if modulo == 128 else 1) + 1
    frame = rng.randbytes(header + info_bytes)
    return len(afsk.transmission_bits([frame], fx25_mode=fx25, txdelay_ms=0.0, tail_ms=0.0, baud=1200))


def _rr_bits(modulo: int, fx25) -> int:
    from shack_packet import afsk
    frame = bytes(14 + (2 if modulo == 128 else 1))
    return len(afsk.transmission_bits([frame], fx25_mode=fx25, txdelay_ms=0.0, tail_ms=0.0, baud=1200))


def _writes(nbytes: int, protocol: str, chunks: int | None) -> tuple[int, float]:
    """How the sender hands the file to the link: (writes, bytes each). An
    OHS DATA frame is one write of a chunk (compressed, when it is) and its
    20 bytes; YAPP one block and its 3. The link cuts each write into I
    frames of PACLEN without joining it to the next, so a 1044-byte DATA
    frame at PACLEN 512 is three frames, the last of 20 bytes."""
    if protocol == "raw":
        return 1, float(nbytes)
    if protocol == "yapp":
        n = max(1, math.ceil(nbytes / YAPP_BLOCK))
        return n, nbytes / n + YAPP_BLOCK_OVERHEAD
    n = max(1, chunks if chunks else math.ceil(nbytes / OHS_CHUNK))
    return n, nbytes / n + OHS_CHUNK_OVERHEAD


def _ohs_acks(nbytes: int) -> int:
    """The receiver's progress ACKs: every 5 % of the file, 1-8 KiB apart
    (ohs-libs FILES.md)."""
    every = min(8192, max(1024, nbytes * 0.05))
    return max(1, int(nbytes / every))


def transfer_s(nbytes: int, mode: str, link: LinkParams = DEFAULT_LINK, timing: Timing = PLUTO, *,
               protocol: str = "ohs", acks: str = "air", loss: float = 0.0, handshake: bool = True,
               chunks: int | None = None) -> float:
    """Seconds from offering `nbytes` of file data (on the air, after any
    compression; `chunks` the original's 1024-byte chunks when compressed)
    to the result, at `mode` (module docstring)."""
    m = mode_info(mode)
    rate = m.pace_bitrate
    txdelay = max(link.txdelay_s, timing.turnaround + m.lock_bits / m.bitrate)
    paclen = max(1, int(link.paclen))
    writes, per_write = _writes(max(0, int(nbytes)), protocol, chunks)
    per_write_frames = max(1, math.ceil(per_write / paclen))
    rest = max(1, round(per_write - (per_write_frames - 1) * paclen))
    write_s = ((per_write_frames - 1) * _frame_bits(paclen, link.modulo, link.fx25)
               + _frame_bits(rest, link.modulo, link.fx25)) / rate
    frame_s = write_s / per_write_frames
    n_frames = writes * per_write_frames
    # Frames an over carries: the window, the channel's frame count and its
    # cap on one transmission's length.
    by_time = max(1, int((link.max_tx_s - txdelay - link.txtail_s) / frame_s))
    per_over = max(1, min(int(link.window), int(link.max_frames), by_time))
    over_fixed = timing.tx_lag + txdelay + link.txtail_s
    rr_s = _rr_bits(link.modulo, link.fx25) / rate
    small_s = _frame_bits(120, link.modulo, link.fx25) / rate
    if acks == "internet":
        turn = timing.drain + timing.rx_lag + timing.internet + timing.tx_lag
    else:
        # Their busy hold and key-up, their RR over, then ours.
        turn = (timing.drain + timing.rx_lag + timing.busy_tail + timing.tx_lag + txdelay + rr_s + link.txtail_s
                + timing.drain + timing.rx_lag + timing.busy_tail + timing.tx_lag)

    # Retries: the frames sent again, in expectation.
    resent_frac = 0.0
    p = min(0.9, max(0.0, float(loss)))
    if p > 0:
        if link.srej:
            resent_frac = p
        else:
            # Go-back-N from the first loss in an over.
            n = per_over
            resent_frac = sum(p * (1 - p) ** (i - 1) * (n - i + 1) for i in range(1, n + 1)) / n
        resent_frac /= 1 - min(resent_frac, 0.9) / 2         # and some of those are lost again

    # Window by window: the window's frames go in overs back to back (the
    # sender keys again as soon as it has dropped), then one acknowledgement.
    # A run of overs longer than the far end's patience with a busy channel
    # (channel.MAX_BUSY_WAIT_S) is talked over: it transmits into the over
    # in progress then, losing what is left of it, and the sender waits out
    # T1. The
    # library's simulator does exactly this at 9600 with k 63 and PACLEN 512
    # (tools/airtime-sim.py); it is the engine's behaviour, so it is here.
    gap = timing.drain + timing.tx_lag
    talk_over = MAX_BUSY_S + timing.busy_tail + timing.tx_lag

    def cycle(w: float) -> tuple[float, float]:
        """One window of `w` frames: (seconds, frames delivered)."""
        overs = math.ceil(w / per_over - 1e-9)
        sizes = [per_over] * (overs - 1) + [w - per_over * (overs - 1)]
        t, delivered = 0.0, 0.0
        for i, f in enumerate(sizes):
            start = t + (gap if i else 0.0) + over_fixed
            end = start + f * frame_s
            if acks == "air" and i and end > talk_over:
                # The frames that ended before it are heard; the rest are not.
                heard = max(0.0, min(f, math.floor((talk_over - start) / frame_s)))
                return end + T1_WAIT_S + 2 * (over_fixed + small_s) + turn, max(delivered + heard, 1.0)
            t, delivered = end, delivered + f
        if p > 0 and acks == "air":
            # Recovery that itself fails leaves the sender waiting out T1 and
            # polling. With REJ (k 4-7) that is a window with a loss whose
            # REJ or resend is lost too: the 9600 run of 18/10 sent 28 of 40
            # frames again with only four REJs and no long stalls. With SREJ
            # and windows of 63 there are SREJs, resends and polls for every
            # frame lost, and the simulator stalls most windows at one in ten
            # (tools/airtime-sim.py --loss 0.1), sometimes twice.
            if link.srej:
                stuck = 1 - (1 - p) ** (2 + 2 * p * w)
                stuck /= 1 - min(stuck, 0.5)
            else:
                stuck = (1 - (1 - p) ** w) * (1 - (1 - p) ** 2)
            t += stuck * (T1_WAIT_S + 2 * (over_fixed + small_s) + turn)
        return t + turn, max(delivered, 1.0)

    remaining = n_frames * (1 + resent_frac)
    window = float(max(1, int(link.window)))
    seconds, cycles = 0.0, 0
    if remaining > window:
        # Every whole window is the same: reckoned once (a big file at k 4
        # is thousands of them).
        t_full, d_full = cycle(window)
        n = int((remaining - window) // d_full)
        seconds, cycles, remaining = n * t_full, n, remaining - n * d_full
    while remaining > 1e-9:
        t, d = cycle(min(window, remaining))
        seconds, cycles, remaining = seconds + t, cycles + 1, remaining - d
    if protocol == "ohs" and acks == "air":
        seconds += _ohs_acks(nbytes) * small_s
    if handshake:
        # OFFER -> ACCEPT and DONE -> RESULT: a short over each way, twice.
        seconds += 2 * (2 * (over_fixed + small_s) + turn)
    return seconds


# -- what a station can do ------------------------------------------------------------------


@dataclass
class Row:
    """One line of the estimate table."""
    mode: str
    bitrate: int
    available: bool
    current: bool
    good_s: float
    retries_s: float
    #: Why not, when not available: a key ("receive-only", "afsk-only",
    #: "radio-modes", "dd-only", "dd-off", "no-radio").
    why: str = ""
    #: A measurement with this station at this mode: (seconds, Measurement).
    measured: tuple | None = None
    unmeasured: bool = False

    @property
    def best_s(self) -> float:
        return self.measured[0] if self.measured else self.good_s


#: A TM-V71's modes, by its DATA speed setting (settings.RadioSettings.data_speed).
TMV71_MODES = {1200: "afsk1200", 9600: "g3ruh9600"}


def current_mode(settings) -> str:
    """The mode this station's data goes at now (adapting: where the
    ladder starts, the last path test's best or the top)."""
    kind = settings.radio.kind
    if kind == "ic-9700-dd":
        return DD
    if kind == "tm-v71":
        return TMV71_MODES.get(int(settings.radio.data_speed or 1200), "afsk1200")
    if kind != "pluto":
        return "afsk1200"
    fs = settings.fast
    if fs.adapt:
        return fs.adapt_start or fs.top_mode
    return fs.mode


def availability(settings, mode: str) -> str:
    """"" when this station can send at `mode`, else why not (Row.why)."""
    kind = settings.radio.kind
    if kind == "sdr-rx":
        return "receive-only"
    if kind == "none":
        return "no-radio"
    if kind == "ic-9700-dd":
        return "" if mode == DD else "dd-only"
    if mode == DD:
        return "dd-off" if kind == "ic-9700" else "dd-ic9700"
    if kind == "pluto":
        return ""
    if kind == "tm-v71":
        # Its DATA jack runs at 1200 or 9600 (menu 518) and the PR9
        # discriminator output is not specified past 9600: two modes, either
        # of which the station can switch to (the control strip).
        return "" if mode in TMV71_MODES.values() else "radio-modes"
    return "" if mode == "afsk1200" else "afsk-only"


def link_params(settings, mode: str) -> tuple[LinkParams, Timing]:
    """The settings a transfer at `mode` would run with on this station:
    a Pluto's fast settings above 1200 baud (fastmodes.station_fast), the
    library's defaults otherwise, with the channel's FX.25."""
    fx = settings.channel.fx25 if settings.channel.fx25 in ("auto", 16, 32, 64) else None
    kind = settings.radio.kind
    if mode == DD:
        return replace(DEFAULT_LINK, fx25=fx), DD_TIMING
    if mode == "afsk1200" and not (kind == "pluto" and settings.fast.adapt):
        return replace(DEFAULT_LINK, fx25=fx), (PLUTO if kind in ("pluto", "hackrf") else RIG)
    if kind == "tm-v71":
        # A TM-V71 at 9600 runs what the controller builds for it: the
        # library's defaults (fastmodes.station_fast is for a Pluto only),
        # through a radio's turnaround, not a Pluto's.
        return replace(DEFAULT_LINK, fx25=fx), RIG
    f = settings.fast
    window = int(f.window_for(mode))
    turnaround = (f.turnaround_ms / 1000.0) if f.turnaround_ms is not None else PLUTO.turnaround
    params = LinkParams(window=window, paclen=int(f.paclen), modulo=128 if window > 7 else 8, srej=bool(f.srej),
                        txdelay_s=float(f.txdelay_ms) / 1000.0, max_frames=window + 1, fx25=fx)
    return params, replace(PLUTO, turnaround=turnaround)


def table(settings, nbytes: int, *, protocol: str = "ohs", peer: str = "", measurements=None,
          chunks: int | None = None) -> list[Row]:
    """Every row for a file of `nbytes` on-air bytes (`chunks`: the
    original's 1024-byte chunks, when those bytes are compressed) from
    this station."""
    now = current_mode(settings)
    rows = []
    for mode in ROWS:
        why = availability(settings, mode)
        params, timing = link_params(settings, mode)
        good = transfer_s(nbytes, mode, params, timing, protocol=protocol, chunks=chunks)
        bad = transfer_s(nbytes, mode, params, timing, protocol=protocol, loss=RETRY_LOSS, chunks=chunks)
        measured = None
        if measurements is not None and peer:
            measured = measurements.scaled(peer, mode, good, params, timing)
        rows.append(Row(mode=mode, bitrate=mode_info(mode).bitrate, available=not why, current=mode == now,
                        good_s=good, retries_s=bad, why=why, measured=measured, unmeasured=mode == DD))
    return rows


# -- the bytes that go ------------------------------------------------------------------------


def wire_size(data: bytes, name: str, *, protocol: str = "ohs", compress: bool = True) -> int:
    """The bytes of file data that go on the air: DEFLATE'd as the OHS
    sender runs it (a sync flush a chunk) where it applies, else as is."""
    if protocol != "ohs" or not compress or not data:
        return len(data)
    from shack_packet.filexfer import worth_compressing
    if not worth_compressing(name, data[:65536]):
        return len(data)
    sample = data[:COMPRESS_SAMPLE]
    z = zlib.compressobj(6, zlib.DEFLATED, -15)
    out = 0
    for at in range(0, len(sample), OHS_CHUNK):
        out += len(z.compress(sample[at:at + OHS_CHUNK])) + len(z.flush(zlib.Z_SYNC_FLUSH))
    if len(sample) < len(data):
        out = int(out * len(data) / len(sample))
    return min(out, len(data))


def file_wire_size(path: Path | str, *, protocol: str = "ohs", compress: bool = True) -> int:
    path = Path(path)
    size = path.stat().st_size
    if protocol != "ohs" or not compress:
        return size
    with open(path, "rb") as f:
        sample = f.read(COMPRESS_SAMPLE)
    n = wire_size(sample, path.name, protocol=protocol, compress=compress)
    return n if len(sample) >= size else min(size, int(n * size / max(1, len(sample))))


# -- what was measured ------------------------------------------------------------------------


def base_call(call: str) -> str:
    return str(call or "").strip().upper().split("-")[0]


@dataclass
class Measurement:
    """One timed transfer with a station: `nbytes` of on-air file data in
    `seconds` at `mode`, by file ("file") or link test ("linktest", whose
    acknowledgements went by internet), at `when` (UTC epoch)."""
    peer: str
    mode: str
    nbytes: int
    seconds: float
    kind: str = "file"
    when: float = 0.0
    protocol: str = "ohs"
    chunks: int = 0

    def ratio(self, params: LinkParams, timing: Timing) -> float:
        """Measured time against this model's for the same transfer."""
        acks = "internet" if self.kind == "linktest" else "air"
        model = transfer_s(self.nbytes, self.mode, params, timing, protocol=self.protocol, acks=acks,
                           handshake=self.kind == "file", chunks=self.chunks or None)
        return self.seconds / max(model, 1e-6)


class Measurements:
    """The latest measurement per station and mode, kept in a JSON file."""

    def __init__(self, path: Path | None):
        self.path = path
        self.items: dict[str, Measurement] = {}
        # Files finish on the engine's thread, link tests on their own.
        self._lock = threading.Lock()
        self._load()

    @staticmethod
    def _key(peer: str, mode: str) -> str:
        return f"{base_call(peer)} {mode}"

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(Path(self.path).read_text("utf-8"))
            for d in data.get("measurements", []):
                m = Measurement(**{k: d[k] for k in ("peer", "mode", "nbytes", "seconds", "kind", "when",
                                                     "protocol", "chunks") if k in d})
                self.items[self._key(m.peer, m.mode)] = m
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    def add(self, m: Measurement) -> bool:
        """Keep `m` if it says something about the path; True if kept."""
        if m.nbytes < MIN_MEASURED_BYTES or m.seconds <= 0 or not base_call(m.peer):
            return False
        try:
            mode_info(m.mode)
        except Exception:                           # noqa: BLE001 - a mode this table does not have
            return False
        m.peer = base_call(m.peer)
        m.when = m.when or time.time()
        with self._lock:
            self.items[self._key(m.peer, m.mode)] = m
            self._save()
        return True

    def get(self, peer: str, mode: str) -> Measurement | None:
        return self.items.get(self._key(peer, mode))

    def scaled(self, peer, mode, good_s, params, timing) -> tuple[float, Measurement] | None:
        """(seconds for this file, the measurement) or None: the model's
        good-link time scaled by how the measured transfer compared with
        the model's time for it."""
        m = self.get(peer, mode)
        if m is None:
            return None
        return good_s * m.ratio(params, timing), m

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            p = Path(self.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"measurements": [vars(m) for m in self.items.values()]}, indent=1), "utf-8")
            tmp.replace(p)
        except OSError:
            log.warning("could not save the measured transfer speeds")
