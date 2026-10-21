"""Transmit arming: the last of the gates, a deliberate act with an expiry.

The engine already refuses to key without a callsign or with its transmit
switch off, and asks at the moment of keying (shack_packet.identity). This
is the app's layer on top, after OpenHamShack Icom's digital-modes
transmitter (dock360-apps-icom-9700 icom/app/digi/transmit.py): transmit
is possible only when all of these hold, every one checked again when the
radio would key rather than when the operator asked:

1. a callsign the engine accepts (identity.is_callsign);
2. transmit set up on this computer (the setup check's last step,
   settings.TxSettings.set_up), off until the operator has been through it;
3. the band-plan dataset allows a packet signal on the channel's frequency
   for this station's country and licence (shack_bandplan);
4. **armed**: the operator pressed Arm, and the arming has not expired.

Arming lasts `minutes` (15 by default, 1 to 60) and is never saved: every
start begins disarmed. **Expiry is checked on reading**, in `refusal()`,
which is what the channel and the radio link ask at keying and while keyed
- so there is no timer that could be the thing that failed, and a frame
queued while armed and due a second after expiry is refused. Disarming,
expiry, and losing the radio all have the same effect: the next key is
refused, and a transmission in progress is cut within the link's tick.

`ArmedStation` is the engine's Station with this check in it, so the
engine, its channel and the radio link all read one answer.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from shack_packet.identity import Station

from .settings import ARM_MAX_MINUTES, ARM_MIN_MINUTES

#: Why arming was refused, as keys the window translates (ui/texts.py).
ARM_REFUSALS = ("no-callsign", "not-set-up", "no-radio", "band-plan", "receive-only",
                # an SDR as the radio (0.2): no band filter confirmed; the
                # Pluto's guard not running; a HackRF's firmware too old
                "sdr-filter", "tx-unguarded", "tx-firmware")


@dataclass
class ArmedStation(Station):
    """identity.Station whose transmit switch is the arming, with expiry."""
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    armed_until: float = 0.0
    #: Told whenever arming changes (armed, disarmed, expired), with the
    #: reason key ("armed", "disarmed", "expired", or a refusal).
    on_change: Callable[[str], None] | None = field(default=None, repr=False)

    def __post_init__(self):
        self._lock = threading.Lock()
        self._expired_told = False

    def arm(self, minutes: float) -> None:
        minutes = max(ARM_MIN_MINUTES, min(ARM_MAX_MINUTES, float(minutes)))
        with self._lock:
            self.armed_until = self.clock() + minutes * 60.0
            self.tx_enabled = True
            self._expired_told = False
        self._tell("armed")

    def disarm(self, why: str = "disarmed") -> None:
        with self._lock:
            was = self.tx_enabled
            self.tx_enabled = False
            self.armed_until = 0.0
        if was:
            self._tell(why)

    @property
    def armed(self) -> bool:
        """Armed and not expired (reading it notices an expiry, as keying does)."""
        self.refusal()
        return self.tx_enabled

    def remaining_s(self) -> float:
        if not self.tx_enabled:
            return 0.0
        return max(0.0, self.armed_until - self.clock())

    def refusal(self) -> str | None:
        expired = False
        with self._lock:
            if self.tx_enabled and self.clock() >= self.armed_until:
                self.tx_enabled = False
                self.armed_until = 0.0
                expired = not self._expired_told
                self._expired_told = True
        if expired:
            self._tell("expired")
        return super().refusal()

    def _tell(self, what: str) -> None:
        if self.on_change is not None:
            try:
                self.on_change(what)
            except Exception:                       # noqa: BLE001 - a listener must not stop a refusal
                pass
