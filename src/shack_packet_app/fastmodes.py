"""The fast and wide modes (0.2.1) as the app uses them: which station
gets them, what the band plan says about the chosen mode and frequency,
and which PTT outputs a Pluto may be offered. No Qt, so the tests drive it
and the window only draws.

shack_packet.fastsettings owns the settings' meaning, their checks and the
objects they become; settings.FastRadioSettings is what is saved. This
module adds the app's decisions:

- **Only a Pluto** (radio.kind "pluto") carries baseband, so only a Pluto
  link is given these settings. Every other radio stays AFSK 1200 exactly as
  0.2.0 sent it.
- **AFSK 1200 on a Pluto keeps 0.2.0's channel and link timing.** The
  library's `channel_config()` asks for TXDELAY 0 (its floor, the turnaround
  plus the mode's lock bits) and `link_config()` a window of 32, modulo 128.
  Both are right between two OHS stations at 9600 baud and up, and wrong
  for a 1200 baud node or TNC that has never heard of SABME: the Pluto's
  AFSK was proved on the air against an IC-9700 with the engine's defaults
  (300 ms TXDELAY, k 7). So a Pluto whose mode is afsk1200, with adaptation
  off, gets the same ChannelConfig and LinkConfig as any other radio; its
  PTT output and drive cap still apply (`station_fast`).
- **The band plan for wide modes** is fastsettings' WIDE_DATA_SEGMENTS (the
  RSGB 2026 plan's high-speed data segments). `plan()` says the same thing
  as the library's `wide_data_refusal`/`wide_data_notes` as data, so the
  window can say it in the operator's language; the library's own sentence
  is still what the arming refusal carries as its detail.
- **The LO leakage and IQ image** sit `ifo` and `2 x ifo` below the channel.
  A Pluto's ifo is 100 kHz, or 200 kHz when the widest mode it carries
  occupies more than 100 kHz (radios/pluto.py WIDE_OCCUPIED_HZ and
  WIDE_IFO_HZ, the rule PlutoLink applies): `ifo_hz`.
- **PTT outputs by firmware** (radios/pluto.py "PTT output"): Tezuka's
  firmware drives all four GPOs itself (GPO0 for TX, GPO2/3 for LNB power),
  so the library refuses gpo0-3 there and only "firmware" (its GPO0) is
  offered; PlutoDVB2's watchconsoletx.sh drives GPO0 and the uses of its
  other GPOs are not known, so the same; ADI's firmware and its clones have
  no script, so gpo0-3 driven by the link. A Pluto never checked is offered
  none at all until "Check this Pluto" has said which firmware it runs:
  guessing gpo0 on an unchecked PlutoDVB2 would fight its own script.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Where the wide modes go (shack_packet README, "Where the wide modes may
#: go (UK)"): 23 cm DD channel 4, whose LO and image at a 200 kHz ifo
#: (1299.325, 1299.125) stay inside 1299.000-1299.750; and 13 cm high speed
#: data for gfsk153600, the one mode too wide for a 150 kHz channel.
SUGGESTED_HZ = 1_299_525_000
SUGGESTED_13CM_HZ = 2_312_000_000
#: PTT outputs offered for each firmware family (module docstring).
PTT_BY_FAMILY = {
    "adi": ("none", "gpo0", "gpo1", "gpo2", "gpo3"),
    "tezuka": ("none", "firmware"),
    "plutodvb2": ("none", "firmware"),
}
#: The modes a Pluto offers, slowest first (modem.MODES' names).
MODES = ("afsk1200", "g3ruh9600", "g3ruh19200", "g3ruh38400", "gfsk76800", "gfsk153600", "4fsk38400",
         "4fsk76800")


#: What one OHS file write is (a 1024-byte chunk and its 20 bytes of DATA
#: framing), which the link cuts into frames of PACLEN: the size the
#: window is reckoned in.
OHS_WRITE = 1044
#: The channel's cap on one transmission (channel.ChannelConfig.max_tx_s),
#: less a TXDELAY.
OVER_S = 10.0 - 0.3


def default_window(mode: str, paclen: int = 512) -> int:
    """The window that one over carries at `mode` (0.3.2): as many frames
    of a file transfer, at their average size for this PACLEN, as fit in
    one 10 s transmission, at most 63.

    Why: 0.3.0-0.3.1 used k 63 everywhere. At 9600 with PACLEN 512 that is
    two 10 s overs back to back, and the receiver (holding its RR) took the
    unbroken busy for a stuck squelch at 15 s and transmitted into the
    second over (Packet Radio 0.3.1's estimates; the library simulator,
    tools/airtime-sim.py). ohs-libs 0.3.2 makes a receiver wait out its
    link partner's over, but a station still on 0.3.1 does not, and on a
    lossy channel a window one over long recovers faster: in the
    simulator, 100 KB at 9600 took 178 s at k 63 against a 0.3.1 receiver
    and 113 s at k 31; with 5 % of frames lost, k 31 was the quickest of
    7-63 (137-146 s against 192-196 at k 63). With both ends on 0.3.2 and a
    clean channel k 63 is 9 % quicker (104 s), which is not worth the
    others. This gives 31 at 9600, 62 at 19 200 and 63 above."""
    if mode == "afsk1200":
        return 7                                    # a Pluto's 1200 keeps 0.2.0's k 7 (station_fast)
    from shack_packet import modem
    m = modem.mode(mode)
    paclen = max(1, int(paclen))
    frames = -(-OHS_WRITE // paclen)
    # Address, control, PID and FCS (20 bytes), and one bit in 62 stuffed.
    frame_s = (OHS_WRITE / frames + 20) * 8 * (1 + 1 / 62) / m.bitrate
    return max(1, min(63, int(OVER_S / frame_s)))


def applies(kind: str) -> bool:
    """Whether a radio kind carries the fast modes (module docstring)."""
    return kind == "pluto"


def ptt_outputs(family: str | None) -> tuple[str, ...]:
    return PTT_BY_FAMILY.get(family or "", ("none",))


def ifo_hz(fs) -> float:
    """The channel's offset from the Pluto's LO for these FastSettings."""
    from shack_packet.radios.pluto import WIDE_IFO_HZ, WIDE_OCCUPIED_HZ
    return WIDE_IFO_HZ if fs.widest().occupied_hz > WIDE_OCCUPIED_HZ else 100_000.0


def station_fast(settings):
    """The FastSettings the station core gives the engine for these
    settings, or None when the engine keeps 0.2.0's defaults: any radio but
    a Pluto, and a Pluto on plain AFSK 1200 (module docstring)."""
    if not applies(settings.radio.kind):
        return None
    fs = settings.fast.for_frequency(settings.channel.frequency_hz)
    if not fs.adapt and fs.mode == "afsk1200":
        return None
    return fs


def arm_refusal(settings) -> tuple[str, str]:
    """("", "") or (a fastsettings.PROBLEMS key, the library's detail) for
    a Pluto whose fast settings may not transmit at this frequency."""
    if not applies(settings.radio.kind):
        return "", ""
    hz = settings.channel.frequency_hz
    fs = settings.fast.for_frequency(hz)
    problems = fs.problems(hz)
    if not problems:
        return "", ""
    key = problems[0]
    if key == "fast-out-of-plan":
        from shack_packet.fastsettings import wide_data_refusal
        return key, wide_data_refusal(hz, fs.widest()) or ""
    from shack_packet.fastsettings import PROBLEMS
    return key, PROBLEMS.get(key, "")


@dataclass
class PlanCheck:
    """What the band plan says about a mode at a frequency (module
    docstring). `segment` is (low Hz, high Hz, widest Hz, what) or None;
    `fits` False only for a wide mode outside a segment or wider than it
    allows; `lo_out`/`image_out` whether the LO leakage and IQ image fall
    outside the segment the channel is in."""
    mode: str
    occupied_hz: float
    wide: bool
    segment: tuple | None
    fits: bool
    ifo_hz: float
    lo_hz: float
    image_hz: float
    lo_out: bool
    image_out: bool


def plan(fs, frequency_hz: float) -> PlanCheck | None:
    """The band-plan check for FastSettings `fs` at `frequency_hz`, or None
    when its mode cannot be named (problems() says why)."""
    from shack_packet.fastsettings import WIDE_DATA_SEGMENTS, WIDE_HZ, wide_data_refusal
    try:
        m = fs.widest()
    except Exception:                               # noqa: BLE001 - an unknown mode: problems() has it
        return None
    ifo = ifo_hz(fs)
    hz = float(frequency_hz)
    segment = next((seg for seg in WIDE_DATA_SEGMENTS if seg[0] <= hz <= seg[1]), None)
    lo, image = hz - ifo, hz - 2 * ifo
    inside = (lambda f: segment[0] <= f <= segment[1]) if segment else (lambda f: True)
    return PlanCheck(mode=m.name, occupied_hz=m.occupied_hz, wide=m.occupied_hz > WIDE_HZ, segment=segment,
                     fits=wide_data_refusal(hz, m) is None, ifo_hz=ifo, lo_hz=lo, image_hz=image,
                     lo_out=not inside(lo), image_out=not inside(image))


class DriveSession:
    """The drive-level helper (pathtest.drive_steps) on the station core
    `ctl`: an amplifier's drive cap found by hand, a step at a time.

    Each step rebuilds the station with the Pluto's transmit gain AND that
    band's cap at the step's gain, so the guard on the Pluto holds the
    amplifier to the step whatever happens in this program. A rebuild
    starts disarmed (controller.apply), so each burst needs the operator's
    own Arm, through `burst()`: Controller.arm with all its refusals - the
    callsign, the band filter, the guard, the band plan - then
    Controller.drive_burst, whose frames the channel and the link refuse
    again if anything changed. Nothing here keys a radio by any other way.

    The operator watches a power meter or a receiver and says `good()` for
    a step that was clean (the next one is then set up) or stops; `finish()`
    puts the station back as it was and returns the last good step, which
    the window saves as that band's cap. Stopping at the first step saves
    nothing.
    """

    def __init__(self, ctl, settings, *, step_db: float = 2.0, start_db: float = -30.0):
        from shack_packet.pathtest import drive_steps

        from .sdrplan import band_of
        self.ctl = ctl
        self.original = settings.copy()
        self.band = band_of(settings.channel.frequency_hz)
        self.steps = drive_steps(start_db, step_db, 0.0)
        self.idx = -1
        self.last_good: float | None = None

    @property
    def gain(self) -> float | None:
        return self.steps[self.idx] if 0 <= self.idx < len(self.steps) else None

    def next_step(self) -> float | None:
        """Set up the next step; None when there is none left."""
        if self.idx + 1 >= len(self.steps):
            return None
        self.idx += 1
        s = self.original.copy()
        s.sdr_radio.pluto_tx_gain_db = self.gain
        s.fast.drive_caps = {**s.fast.drive_caps, self.band: self.gain}
        self.ctl.apply(s)
        return self.gain

    def burst(self) -> tuple[str, str]:
        """Arm for a minute and send one burst at this step: ("", "") or a
        reason key and detail (the arming's, or drive_burst's)."""
        if self.gain is None:
            return "drive-step", ""
        if not self.ctl.station.armed:
            ok, why, detail = self.ctl.arm(1)
            if not ok:
                return why, detail
        return self.ctl.drive_burst(self.gain), ""

    def good(self) -> float | None:
        """This step was clean: remember it, and set up the next."""
        self.last_good = self.gain
        return self.next_step()

    def finish(self) -> float | None:
        """Back to the settings the helper started from (disarmed, as any
        rebuild); the last good step, or None."""
        self.ctl.apply(self.original)
        return self.last_good
