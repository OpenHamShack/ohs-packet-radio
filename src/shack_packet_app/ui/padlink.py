"""The AKP03E (or a USB keypad) on this window: shack_pad's DesktopPad, fed
by data/pad-actions.json and padactions.py, as NB's ohscore/ui/padlink.py
does for NB.

CONTROLLER-PAD.md §6.2: no service and no socket. The pad belongs to
whichever OpenHamShack app the operator is using, through a lock file in
the store folder every OpenHamShack app shares; this window gaining focus
asks for it and the owner hands it over within a second.

Threads. shack_pad calls `_perform` and `_faces` on its own thread. Disarm
and HALT act there and then (padactions.py says why); everything that
touches the window is sent across with a queued signal, so it runs on the
window's thread. `_faces` reads only what `tick()` copied on the window's
thread and the station's own thread-safe arming state.

No transmit lease is given to the library (InProcessApp lease=None): this
app declares no `ptt`/`transmit` action, so the pad has nothing to key.

Optional, as in NB: without shack_pad's HID transport, or with no pad
plugged in, nothing happens except that Settings › Control pad says so.
`SHACK_PACKET_NO_PAD=1` stops the window looking for one at all (the
tests set it, so a pad on the developer's desk is never taken over).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, pyqtSignal

from .. import padactions
from . import texts

log = logging.getLogger(__name__)

APP_ID = "shack-packet"
_failure = ""


def failure() -> str:
    """Why start() made no PadLink ("" if it did, or was never asked)."""
    return _failure


class PadLink(QObject):
    # From the pad's thread to the window's (Qt queues a signal across threads).
    _gui = pyqtSignal(str)

    def __init__(self, window, *, store_dir: Path | None = None, open_device=None, clock=time.monotonic):
        super().__init__(window)
        from shack_pad.focus import DesktopPad
        from shack_pad.router import InProcessApp, InputHost
        self.window = window
        self.actions = padactions.PadActions(self, clock=clock)
        self._gui.connect(self._on_gui, Qt.ConnectionType.QueuedConnection)
        # What the faces show of the window, copied by tick() on its thread.
        self._view = {"target": "", "chat": "", "transmitting": False}
        decl, layout = padactions.declaration()
        app = InProcessApp(padactions.KIND, self.tr("Packet"), decl, layout, perform=self._perform,
                           faces=self._faces)
        self.host = InputHost([app], maps=window.settings.pad.maps, log=self._log)
        self.pad = DesktopPad(self.host, app=APP_ID, store_dir=store_dir, open_device=open_device)
        window.installEventFilter(self)
        self.pad.start()
        self.pad.focus_gained()

    # -- the bridge padactions calls ------------------------------------------------------

    def armed(self) -> bool:
        st = self.window.ctl.station
        return bool(st is not None and st.armed)

    def disarm(self) -> None:
        self.window.ctl.disarm()
        self._gui.emit("refresh")

    def halt(self) -> None:
        self.window.ctl.halt()
        self._gui.emit("refresh")

    def arm(self) -> None:
        self._gui.emit("arm")

    def beacon_now(self) -> None:
        self._gui.emit("beacon")

    def connect(self) -> None:
        self._gui.emit("connect")

    def disconnect(self) -> None:
        self._gui.emit("disconnect")

    def page(self, step: int) -> None:
        self._gui.emit("page:next" if step > 0 else "page:prev")

    def adjust(self, which: str, step: int) -> None:
        self._gui.emit(f"dial:{which}:{step}")

    # -- shack_pad's callbacks (its thread) ------------------------------------------------

    def _perform(self, action) -> None:
        try:
            self.actions.perform(action.action, action.control, getattr(action, "step", 0) or 0)
            if action.action == "arm" and self.actions.arm_pending():
                self._gui.emit("arm-pending")
        except Exception:                           # noqa: BLE001 - a press must never take the window down
            log.exception("pad action %s", action.action)

    def _faces(self) -> dict:
        v = self._view
        armed = self.armed()
        faces = {}
        if self.actions.arm_pending():
            faces["arm"] = {"l1": self.tr("PRESS"), "l2": self.tr("AGAIN"), "tone": "warn"}
        elif armed:
            left = int(self.window.ctl.station.remaining_s())
            faces["arm"] = {"l1": self.tr("ARMED"), "l2": f"{left // 60}:{left % 60:02d}", "tone": "active"}
        # Disarm and BEACON dimmed while there is nothing to disarm and
        # nothing may be sent: a face only, the key still works (a disarm
        # never gets harder).
        faces["disarm"] = {"l1": self.tr("DISARM"), "l2": "", "tone": "warn" if armed else "disabled"}
        faces["beacon"] = {"l1": self.tr("BEACON"), "l2": self.tr("NOW") if armed else self.tr("not armed"),
                           "tone": "normal" if armed else "disabled"}
        faces["halt"] = {"l1": self.tr("HALT"), "l2": self.tr("TX")}
        faces["connect"] = {"l1": self.tr("CONNECT"), "l2": v["target"][:9],
                            "tone": "normal" if v["target"] else "disabled"}
        faces["disconnect"] = {"l1": self.tr("DISCONN"), "l2": v["chat"][:9],
                               "tone": "normal" if v["chat"] else "disabled"}
        return {"onAir": bool(v["transmitting"]), "faces": faces}

    def _log(self, message: str) -> None:
        log.info("pad: %s", message)

    # -- the window's thread ------------------------------------------------------------------

    def _on_gui(self, what: str) -> None:
        w = self.window
        try:
            if what == "arm":
                ok, why, detail = w.ctl.arm(w.settings.tx.arm_minutes)
                # The same checks as the bar's Arm (Controller.arm); a refusal
                # is said in the status line rather than a dialog, because the
                # operator is looking at the pad, not the screen.
                msg = texts.reason("armed" if ok else why)
                if not ok and detail and why == "band-plan":
                    msg += " " + detail
                w.statusBar().showMessage(msg, 10_000)
            elif what == "arm-pending":
                w.statusBar().showMessage(texts.reason("arm-press-again"), int(padactions.ARM_WINDOW_S * 1000))
            elif what == "beacon":
                why = w.ctl.beacon_now()
                w.statusBar().showMessage(texts.reason(why or "beacon-sent"), 10_000)
            elif what == "connect":
                self._connect()
            elif what == "disconnect":
                sess = self._chat()
                if sess is None:
                    w.statusBar().showMessage(texts.reason("no-chat"), 10_000)
                else:
                    w.ctl.disconnect(sess.id)
            elif what.startswith("dial:"):
                _d, which, step = what.split(":")
                strip = getattr(w, "control_strip", None)
                if strip is not None:
                    strip.dial(which, int(step))
            elif what in ("page:next", "page:prev"):
                n = w.nav.count()
                w.nav.setCurrentRow((w.nav.currentRow() + (1 if what == "page:next" else -1)) % n)
            w.bar.update_status(w.ctl.status())
        except Exception:                           # noqa: BLE001 - as _perform
            log.exception("pad action %s on the window", what)

    def target(self) -> str:
        """Who CONNECT calls: the station chosen on the APRS page while it
        is in view, else what is typed on the Connect page, else whoever
        the last chat was with."""
        w = self.window
        aprs = w.pages["aprs"]
        if w.current_page() is aprs:
            chosen = aprs.selected_call()
            if chosen:
                return chosen
        typed = w.pages["chat"].call.text().strip().upper()
        if typed:
            return typed
        with w.ctl._lock:
            sessions = list(w.ctl.sessions.values())
        return sessions[-1].remote if sessions else ""

    def _connect(self) -> None:
        w = self.window
        call = self.target()
        if not call:
            w.statusBar().showMessage(texts.reason("no-station-chosen"), 10_000)
            return
        chat = w.pages["chat"]
        chat.call.setText(call)
        w.nav.setCurrentRow(list(w.pages).index("chat"))
        chat.connect()

    def _chat(self):
        """The chat DISCONNECT ends: the one in view on the Connect page,
        else the newest that is not already over."""
        w = self.window
        chat = w.pages["chat"]
        view = chat.tabs.currentWidget()
        with w.ctl._lock:
            sessions = [s for s in w.ctl.sessions.values() if s.state != "disconnected"]
        if view is not None and view.sess in sessions:
            return view.sess
        return sessions[-1] if sessions else None

    def tick(self) -> None:
        """Called by the window's timer: what the faces show of the window."""
        chat = self._chat()
        self._view = {"target": self.target(), "chat": chat.remote if chat is not None else "",
                      "transmitting": bool(self.window.ctl.status().get("transmitting"))}

    # -- settings and life -----------------------------------------------------------------------

    def set_maps(self, maps: dict) -> None:
        self.host.set_maps(maps)

    def status(self) -> dict:
        """DesktopPad.status(): state, name, path, error."""
        return dict(self.pad.status())

    def start_test(self) -> None:
        self.host.start_test()

    def stop_test(self) -> None:
        self.host.stop_test()

    def testing(self) -> bool:
        return self.host.testing()

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.WindowActivate:
            self.pad.focus_gained()
        return False

    def close(self) -> None:
        self.pad.close()


def start(window, **kw) -> PadLink | None:
    """A PadLink, or None: when the operator turned the pad off, when the
    tests ask for none, or when shack_pad cannot run here (it is optional;
    Settings › Control pad says why)."""
    global _failure
    if not window.settings.pad.enabled or os.environ.get("SHACK_PACKET_NO_PAD") == "1" and not kw:
        _failure = ""
        return None
    try:
        link = PadLink(window, **kw)
        _failure = ""
        return link
    except Exception as e:                          # noqa: BLE001 - see above
        log.info("no pad support here", exc_info=True)
        _failure = f"{type(e).__name__}: {e}"
        return None


def describe(st: dict | None, enabled: bool = True) -> str:
    """Settings › Control pad's status line, for DesktopPad.status() (None:
    no PadLink; `enabled`: whether the settings ask for one)."""
    tr = QCoreApplication.translate
    if st is None:
        if failure():
            return tr("PadLink", "No control pad support here: %1").replace("%1", failure())
        if enabled:
            return tr("PadLink", "Not looking for a control pad.")
        return tr("PadLink", "The control pad is turned off.")
    state = st.get("state", "")
    if state == "serving":
        return tr("PadLink", "%1 is connected (%2).").replace("%1", st.get("name") or "AKP03E").replace(
            "%2", st.get("path") or "")
    if state == "error":
        return st.get("error") or tr("PadLink", "The control pad would not open.")
    if state == "waiting":
        return tr("PadLink", "Another OpenHamShack program is using the control pad.")
    return tr("PadLink", "No control pad is plugged in.")
