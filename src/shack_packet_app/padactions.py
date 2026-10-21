"""What the AKP03E pad's keys do in Packet Radio: the actions this app
declares in data/pad-actions.json, and the rules round them. No Qt, so the
rules can be tested on their own; ui/padlink.py wires them to shack_pad's
DesktopPad and to the window.

The actions (PLAN-0.2 §3.5, Brian's D4 of 18/10/2026):

| action | what it does |
|---|---|
| `arm` | arms transmit, but only on a **second press of the same key within 3 s**; the first press makes the key say "press again" |
| `disarm` | disarms, at once |
| `halt` | stops transmitting now: disarms, which drops the queue and cuts a transmission in progress, at once |
| `beacon` | sends this station's position beacon now, only while armed |
| `connect` / `disconnect` | the chat: connect to the station chosen in the window, or end the chat in view |
| `page:next` / `page:prev` | the window's next or previous page (Monitor, APRS...) |
| `power` / `squelch` (dials, 0.3.0) | RF power and squelch on a CAT radio, TX gain on a Pluto for `power`: the control strip's (radioctl.py), a detent a step |

**No PTT, ever.** Packet keys only through the engine, which keys only
while the station is armed and only to send what is queued (CONTROLLER-PAD
§7.1; NB's padactions.py says the same for its pad). None of these is a
shack_pad transmit action (`ptt`/`transmit`), so the library's lease never
comes into it: arming is the operator's permission for the engine, and
every check the station bar's Arm makes is made again here because both
end in the same `Controller.arm`.

Why two presses for `arm` and one for everything that stops: arming from a
pad is new to the family, and a key bumped while reaching for another must
not put a station on the air. Stopping must never get harder, so `disarm`,
`halt` and `disconnect` act on the first press. The first-revision AKP03E
sends no key releases, so nothing here depends on how long a key is held.

`perform()` is called on shack_pad's thread. `disarm` and `halt` run there,
straight away: `Controller.disarm` takes the station's own lock and is safe
from any thread, and a stop must not wait for a busy window. Everything
else goes to the window through `bridge`, which runs it on the window's
thread (ui/padlink.py).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from importlib import resources
from typing import Callable, Protocol

#: D4: the second press must come within this many seconds of the first.
ARM_WINDOW_S = 3.0
KIND = "packet"
MODEL = "ajazz-akp03e"


class Bridge(Protocol):
    """What the actions need from the window. `arm`, `beacon_now`,
    `connect`, `disconnect` and `page` may be called from any thread and
    must get themselves onto the window's; `disarm` and `halt` are called
    from the pad's thread and must be safe there."""

    def armed(self) -> bool: ...
    def arm(self) -> None: ...
    def disarm(self) -> None: ...
    def halt(self) -> None: ...
    def beacon_now(self) -> None: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def page(self, step: int) -> None: ...
    def adjust(self, which: str, step: int) -> None: ...


def declaration() -> tuple[dict, dict]:
    """(pad-actions.json, pad-layout.json) as this app ships them."""
    pkg = resources.files(__package__)
    return (json.loads(pkg.joinpath("data/pad-actions.json").read_text(encoding="utf-8")),
            json.loads(pkg.joinpath("data/pad-layout.json").read_text(encoding="utf-8")))


@dataclass
class _Pending:
    control: str
    at: float


class PadActions:
    def __init__(self, bridge: Bridge, clock: Callable[[], float] = time.monotonic):
        self.bridge = bridge
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: _Pending | None = None

    def arm_pending(self) -> bool:
        """The arm key has had its first press and is waiting for the
        second (what its face shows)."""
        with self._lock:
            if self._pending is not None and self.clock() - self._pending.at > ARM_WINDOW_S:
                self._pending = None
            return self._pending is not None

    def perform(self, action: str, control: str = "", step: int = 0) -> None:
        """One press, or a dial's turn (`step` detents). An action this
        version does not know is ignored: a layout from a newer version
        must not stop the pad."""
        if action in ("disarm", "halt"):
            self._cancel()
            (self.bridge.halt if action == "halt" else self.bridge.disarm)()
        elif action == "arm":
            self._arm_press(control)
        elif action == "beacon":
            self._cancel()
            # Said again by the window when it sends, from the station's own
            # state at that moment: this is only the pad not even asking.
            if self.bridge.armed():
                self.bridge.beacon_now()
        elif action == "connect":
            self._cancel()
            self.bridge.connect()
        elif action == "disconnect":
            self._cancel()
            self.bridge.disconnect()
        elif action in ("page:next", "page:prev"):
            self._cancel()
            self.bridge.page(1 if action == "page:next" else -1)
        elif action in ("power", "squelch") and step:
            # Never keys: the strip changes a level the radio already
            # transmits at when the station, armed, keys it.
            self._cancel()
            self.bridge.adjust(action, int(step))

    def _arm_press(self, control: str) -> None:
        now = self.clock()
        with self._lock:
            p = self._pending
            second = p is not None and p.control == control and now - p.at <= ARM_WINDOW_S
            self._pending = None if second else _Pending(control, now)
        if second:
            self.bridge.arm()

    def _cancel(self) -> None:
        """Any other key cancels a half-made arming: two presses of the arm
        key with something else between are not a deliberate double press."""
        with self._lock:
            self._pending = None
