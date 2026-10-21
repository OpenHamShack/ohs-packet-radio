"""Following the radio's own VFO (0.3.2): the station's channel is where
the radio is, not where Settings last put it.

Brian, 21/10/2026, on 0.3.1 with his IC-9700 over the LAN, armed on
144.925 MHz: "if I change the frequency on the radio it doesn't change on
the program". The rig library always knew - an IC-9700 volunteers every
turn of the dial as a CI-V transceive frame, and every rig's poll reads the
frequency and mode several times a second into its cache - but the app
showed, judged and recorded `settings.channel` and never looked. Worse,
anything that rebuilt the station would have tuned the radio back, and
closing it would have put back the frequency it was found on.

**What follows the radio.** `TuneWatch` reads the link's cache
(shack_packet RigLink.tuned: never a question to the radio) every time the
engine polls. A frequency that has held for SETTLE_S - a knob being turned
passes through many - becomes the station's channel: `settings.channel`
(the matching preset, or "custom"), which the status bar, the control
strip, the transfer estimates, the scope's markers, the log and the link
test reports all read; and the link's confirmed channel, so a reconnect or
rebuild tunes there instead of snapping back. Settings are saved by the
window when it hears "retuned". Any CAT radio: Icom over LAN or USB, the
FTX-1, the TM-V71. A Pluto, a sound card radio or the simulator has no VFO
of its own to follow, and a satellite pass that owns the VFOs for Doppler
is left alone (RigLink.vfo_owner).

**Transmit while it happens.** From the moment the radio leaves the
confirmed channel the link refuses to key ("off-channel", RigLink's guard,
also inside the lease the rig re-asks while keyed - a dial turned mid-over
unkeys). Nothing is queued up behind that: a frame refused at the key is
dropped, as every refused frame is. Once the new frequency has settled:

1. **What was meant for the old channel is cancelled, not re-sent.** Every
   frame still queued is discarded; every connected link is dropped
   without a frame (no DISC: it would go out on the new channel, to
   nobody); APRS messages still waiting for an ack are cancelled. A
   retry meant for 144.925 is never sent on 144.800 without the operator
   knowing - they are told how many of each went, and connect again on
   purpose. Re-sending after a confirmation was the other choice; it
   would put an old conversation on a new channel, where the station at
   the far end is not, and a file transfer's retries can run for minutes.
2. **The arming is judged again** (Controller.arm_refusal, with the new
   channel: the band plan for the licence, a satellite uplink's pass
   gate, everything Arm asks). Refused: disarmed, said in the status bar
   and the log. Allowed: still armed, and the status bar says where.
3. **Then the channel is confirmed** to the link, and keying is possible
   there - in that order, so there is no moment when an armed station on
   a channel it may not use could key.

A frequency that comes back to the confirmed channel before it settles was
a nudge: nothing is dropped, nothing changes.

**The mode.** Packet goes out in FM (an Icom's FM-D, the FTX-1's DATA-FM,
the TM-V71's FM or narrow FM). The mode knob turned to anything else
disarms at once - no settling, it is not a channel - and the link refuses
to key ("off-mode") until the radio is back in FM. A TM-V71 band switched
to a memory channel is treated the same way ("radio-memory"): it would
transmit on the memory's frequency, which its VFO read does not show.

**What the app itself tunes is the app's.** A channel chosen in the app
(the control strip, Settings, a scope click) still sets the radio, and
the data set-up puts the radio back where it was found when the station
closes. A frequency the operator turned to is theirs: it stays at close
(shack_rig.datasetup rule 6) - unless they turned back to the app's
channel before closing, when the app's change is undone as usual.
"""
from __future__ import annotations

import logging

from shack_packet.datalink import State

log = logging.getLogger(__name__)

#: How long the radio must sit on a new frequency before it is the channel.
#: A VFO knob turned by hand passes through a frequency every 10-50 ms; a
#: second is long enough to be a decision and short enough not to notice.
SETTLE_S = 1.0
#: The rig modes packet is sent in (shack_packet.radios.rig.PACKET_MODES).
PACKET_MODES = ("FM",)


class TuneWatch:
    """An engine service (Engine.services): polled under the engine's lock,
    on its thread, before the channel decides to send - so a queue it
    discards cannot be half-way into a transmission."""

    def __init__(self, ctl):
        self.ctl = ctl
        #: (Hz, since) while the radio is off the confirmed channel and not
        #: yet settled; None on channel.
        self.pending: tuple[int, float] | None = None
        #: The cache as last read (RigLink.tuned), for the status bar.
        self.live: dict | None = None
        #: The mode label the radio is in when it is not one packet uses
        #: ("MEMORY" for a TM-V71 band on a memory channel), and the
        #: reason key that goes with it.
        self.bad_mode: str | None = None
        self.bad_why = ""

    # -- the engine's service interface ------------------------------------------------

    def poll(self, now: float) -> None:
        ctl = self.ctl
        link = ctl.link
        tuned = getattr(link, "tuned", None)
        t = tuned() if tuned is not None and ctl.link_state == "open" else None
        self.live = t
        if t is None:
            self.pending = None
            return
        self._judge_mode(t)
        hz, confirmed = t.get("freq"), getattr(link, "frequency", None)
        if hz is None or confirmed is None:
            return
        hz = int(hz)
        if hz == int(confirmed):
            if self.pending is not None:
                log.info("the radio is back on %.4f MHz before settling: nothing changed", hz / 1e6)
            self.pending = None
            return
        if self.pending is None or self.pending[0] != hz:
            if self.pending is None:
                log.warning("the radio moved off %.4f MHz (now %.4f): transmit held until it settles",
                            int(confirmed) / 1e6, hz / 1e6)
            self.pending = (hz, now)
            return
        if now - self.pending[1] >= SETTLE_S:
            self.pending = None
            self._follow(hz, t)

    def next_deadline(self) -> float | None:
        return self.pending[1] + SETTLE_S if self.pending is not None else None

    # -- for the controller ----------------------------------------------------------------

    def arm_refusal(self) -> tuple[str, str]:
        """Arm's question: never while the radio is between channels or not
        in FM."""
        if self.bad_mode is not None:
            return self.bad_why, self.bad_mode
        if self.pending is not None:
            return "radio-retuning", ""
        return "", ""

    # -- inside --------------------------------------------------------------------------------

    def _judge_mode(self, t: dict) -> None:
        mode = t.get("mode")
        if (mode is None or mode in PACKET_MODES) and not t.get("memory"):
            if self.bad_mode is not None:
                log.info("the radio is back in %s", t.get("label") or mode)
                self.ctl._emit("radio", "mode-ok", {"mode": t.get("label") or mode or ""})
            self.bad_mode = None
            return
        why = "radio-memory" if t.get("memory") else "radio-mode"
        label = "MEMORY" if t.get("memory") else (t.get("label") or mode)
        if self.bad_mode == label:
            return
        self.bad_mode, self.bad_why = label, why
        station = self.ctl.station
        was = station is not None and station.armed
        if was:
            station.disarm(why)
        log.warning("the radio was put in %s (%s): %s", label, why,
                    "transmit disarmed" if was else "it is not keyed until it is back")
        self.ctl._emit("radio", "mode-changed", {"mode": label, "why": why, "disarmed": was})

    def _follow(self, hz: int, t: dict) -> None:
        ctl = self.ctl
        s, engine, link, station = ctl.settings, ctl.engine, ctl.link, ctl.station
        old = int(s.channel.frequency_hz)
        # 1. What was meant for the old channel (module docstring).
        links = 0
        if engine is not None:
            for conn in list(engine.connections.values()):
                if conn.state is not State.DISCONNECTED:
                    conn.link.drop("retuned")
                    links += 1
            frames = engine.channel.discard(lambda _f: True)
        else:
            frames = 0
        messages = 0
        if ctl.aprs is not None:
            for m in ctl.aprs.outgoing:
                if m.state in ("queued", "sent"):
                    m.state = "cancelled"
                    messages += 1
        # 2. The station's channel, and the arming judged on it.
        from . import presets
        match = next((p.id for p in presets.presets(s.station.profile()) if p.hz == hz), None)
        s.channel.preset, s.channel.frequency_hz = match or "custom", hz
        was = station is not None and station.armed
        why, detail = ctl.arm_refusal() if was else ("", "")
        if why:
            station.disarm("retuned")
        # 3. Only now may the link key there.
        link.confirm_channel(hz)
        log.warning("the radio was retuned %.4f -> %.4f MHz (%s): %d link(s) dropped, %d queued frame(s) and "
                    "%d APRS message(s) cancelled; %s", old / 1e6, hz / 1e6, t.get("label") or "?", links, frames,
                    messages, (f"transmit DISARMED ({why}: {detail})" if why else
                               "still armed" if was else "not armed"))
        ctl._emit("radio", "retuned", {"hz": hz, "from": old, "by": "radio", "preset": match or "",
                                       "links": links, "frames": frames, "messages": messages,
                                       "armed": bool(was and not why), "refusal": why, "detail": detail})
