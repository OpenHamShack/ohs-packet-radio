"""Which channels the SDR monitor listens to: the operator's choice, from the
band plan, kept inside what the SDR can capture.

PLAN-0.2 §3.8 and Brian's decision of 18/10/2026: the operator chooses the
monitored channels; 2 m and 70 cm both have band plans; the channels must
fit the SDR's capture span. Built from data, never constants (BETA-PLAN
architecture change 5):

- the candidates are this station's packet presets (presets.py: the
  band-plan dataset's own APRS centre, and data/presets.json's channels in
  the dataset's presets shape, chosen by the station's country, then IARU
  region), grouped into bands by the dataset's own band edges
  (`shack_bandplan.band_of`), so "2m" and "70cm" mean what the dataset
  says for this station;
- the operator ticks which of them to watch (none ticked: all of them)
  and may type other frequencies in the same band;
- `plan()` refuses a choice wider than the SDR captures (the 80 % of the
  sample rate shack_packet.sdr.plan.ChannelPlan.centre_for allows, with DC
  kept clear) rather than quietly dropping channels: the operator should
  know which channel is not being heard.

A station whose country and region the dataset cannot tell (no callsign
yet) gets shack_packet's own default plan on 2 m, the UK one it always
had, rather than the ISS alone (the only preset valid everywhere), so the
monitor works before the setup check.
"""
from __future__ import annotations

from dataclasses import dataclass

import shack_bandplan as bp
from shack_packet import sdr

from . import presets

#: The bands the monitor offers. Both have packet channels in every plan
#: the presets carry; others can be added here once they do.
BANDS = ("2m", "70cm")
#: presets.Preset.role -> shack_packet.sdr.plan KINDS key.
KIND_OF_ROLE = {"aprs": "aprs", "node": "node", "satellite": "satellite"}


class PlanError(ValueError):
    """`reason` is a key the window translates (ui/texts.py)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def band_of(hz: float) -> str:
    return bp.band_of(bp.bundled(), float(hz))


def candidates(profile: dict, band: str) -> list[presets.Preset]:
    """This station's packet presets on `band`, in the presets' order.
    Listening is never refused, so a preset the licence does not allow for
    transmitting is still a channel the monitor may watch."""
    return [p for p in presets.presets(profile) if band_of(p.hz) == band]


def channels(s, profile: dict) -> list[sdr.Channel]:
    """The chosen channels for SdrSettings `s`, before any check of span."""
    cands = candidates(profile, s.band)
    chosen = [p for p in cands if not s.channels or p.id in s.channels]
    out = [sdr.Channel(p.id, float(p.hz), p.label, KIND_OF_ROLE.get(p.role, "other")) for p in chosen]
    have = {round(c.hz) for c in out}
    for hz in s.extra_hz or []:
        hz = int(round(float(hz)))
        if hz in have or band_of(hz) != s.band:
            continue
        have.add(hz)
        out.append(sdr.Channel(f"hz-{hz}", float(hz), "", "other"))
    if s.band == "2m" and not s.channels and not s.extra_hz and not _located(profile):
        return list(sdr.default_plan())
    return out


def _located(profile: dict) -> bool:
    """Whether the dataset knows this station's country or IARU region
    (else presets.py falls back to the few presets valid everywhere)."""
    ctx = presets.context(profile)
    return bool(ctx.country_iso or ctx.region)


@dataclass
class Fit:
    ok: bool
    span_hz: float          # lowest to highest channel edge
    usable_hz: float        # what the SDR captures usefully at its rate


def fit(chans: list, rate: float) -> Fit:
    usable = 0.8 * float(rate)
    if not chans:
        return Fit(False, 0.0, usable)
    p = sdr.ChannelPlan(tuple(chans))
    lo, hi = p.span()
    return Fit(p.centre_for(float(rate)) is not None, hi - lo, usable)


def plan(s, profile: dict) -> sdr.ChannelPlan:
    """The monitor's ChannelPlan. PlanError "no-channels" when nothing is
    chosen, "plan-too-wide" when the channels do not fit the capture."""
    chans = channels(s, profile)
    if not chans:
        raise PlanError("no-channels")
    f = fit(chans, s.rate)
    if not f.ok:
        raise PlanError("plan-too-wide", f"{f.span_hz:.0f} Hz of {f.usable_hz:.0f} Hz")
    return sdr.ChannelPlan(tuple(chans))
