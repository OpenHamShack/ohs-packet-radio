"""A conference room hosted from the app over RF: ohs-libs'
shack_packet.conference (Room on a RoomService, the same room the
OpenHamShack controller runs always-on, so a station sees one behaviour
whichever hosts it), opened and closed by the operator, with the lines the
window shows and the stays the log records.

**When it answers.** Only while the operator has the room open (never
saved: every start begins closed, like arming) *and* transmit is armed:
the service's `allow` asks the station's arming at each incoming call. A
station calling a disarmed room hears nothing at all - not even the DM
an unanswered call would get, because that too would be a transmission -
and its next SABM after the operator arms is let in. Members already in
when the arming runs out stay connected, but nothing more goes out to
them until it is armed again: every frame passes the same transmit switch
and channel access as the rest of the app (the room adds no way to
transmit).

The operator is in the room without a connection: their lines go out with
their call in front (Room.say), and the members list shows them first.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from PyQt6.QtCore import QCoreApplication
from shack_packet.conference import Room as LibRoom
from shack_packet.conference import RoomConfig, RoomService


@dataclass
class RoomLine:
    time: float
    kind: str            # join | leave | idle | say | me | host | topic | note
    call: str
    text: str = ""


class Room:
    def __init__(self, ctl):
        self.ctl = ctl
        self.room: LibRoom | None = None
        self.service: RoomService | None = None
        self.lines: list[RoomLine] = []
        self.rev = 0
        #: Each member's arrival (wall clock), for the log.
        self.since: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def open(self) -> bool:
        return self.room is not None

    def host(self) -> str:
        st = self.ctl.station
        return str(st.address()) if st is not None and st.callsign is not None else ""

    def name(self, address: str = "") -> str:
        """The room's name as members are welcomed to it ("the NAME
        conference"): the operator's, or the address they called."""
        return self.ctl.settings.room.name.strip() or address or self.address()

    def address(self) -> str:
        return str(self.service.address) if self.service is not None else ""

    def start(self) -> str:
        """Open the room; "" or a reason key it could not be."""
        ctl = self.ctl
        if self.room is not None:
            return ""
        if ctl.engine is None or ctl.station is None or ctl.station.callsign is None:
            return "no-callsign"
        r = ctl.settings.room
        ssid = int(r.ssid) if 0 <= int(r.ssid) <= 15 else 4
        taken = {int(ctl.settings.station.ssid)}
        if ctl.settings.mailbox.enabled:
            taken.add(int(ctl.settings.mailbox.ssid))
        if ssid in taken:
            return "room-ssid-taken"
        cfg = RoomConfig(max_members=max(1, int(r.max_members)), idle_s=max(1, int(r.idle_minutes)) * 60.0)
        engine = ctl.engine
        with engine.lock:
            room = LibRoom(self.name(str(ctl.station.address(ssid))), clock=engine.clock, config=cfg, topic=r.topic.strip())
            room.observers.append(self._event)
            self.service = RoomService(engine, room, ssid,
                                       allow=lambda call: bool(ctl.station is not None and ctl.station.armed))
            self.room = room
        self._add("note", "", "room-opened")
        ctl._emit("room", "opened", {"address": self.address()})
        return ""

    def stop(self) -> None:
        room, service = self.room, self.service
        if room is None:
            return
        self.room = self.service = None
        engine = self.ctl.engine
        goodbye = QCoreApplication.translate("Room", "The room is closing. 73.")
        with engine.lock:
            # RoomService has no close of its own (the controller's room is
            # never closed); this is what one would do: stop answering,
            # stop the idle timer, say goodbye and let each member go.
            engine.unlisten(service._accept)
            if room in engine.services:
                engine.services.remove(room)
            if room.members:
                room.say(goodbye, call=self.host())
            for m in list(room.members):
                room.leave(m, "closed")
        engine._wake()
        now = self.ctl.wall()
        for call in list(self.since):
            self._stay_ended(call, now)
        self._add("note", "", "room-closed")
        self.ctl._emit("room", "closed")

    def say(self, text: str) -> bool:
        room = self.room
        text = str(text or "").strip()
        if room is None or not text:
            return False                # a blank line would key the radio for nothing
        with self.ctl.engine.lock:
            room.say(text, call=self.host())
        self.ctl.engine._wake()
        return True

    def set_topic(self, topic: str) -> None:
        self.ctl.settings.room.topic = topic.strip()
        if self.room is not None and topic.strip():
            with self.ctl.engine.lock:
                self.room.set_topic(topic, by=self.host())
            self.ctl.engine._wake()

    def members(self) -> list[str]:
        """The operator first, then the stations, in the order they came."""
        if self.room is None:
            return []
        with self.ctl.engine.lock:
            return [self.host()] + self.room.calls()

    def _add(self, kind: str, call: str, text: str = "") -> None:
        with self._lock:
            self.lines.append(RoomLine(self.ctl.wall(), kind, call, text))
            del self.lines[:-2000]
            self.rev += 1

    def _stay_ended(self, call: str, now: float) -> None:
        since = self.since.pop(call, None)
        if since is not None and self.ctl.settings.log.auto_room:
            self.ctl.log_contact(call, "room", since, now, comment=f"room {self.name()}")

    def _event(self, kind: str, call: str, text: str) -> None:
        """The room's observer (engine thread, under its lock): append and
        queue only. Room kinds: joined, left (text: why), line, topic."""
        now = self.ctl.wall()
        if kind == "joined":
            self.since[call] = now
            self._add("join", call)
            self.ctl._emit("room", "join", {"remote": call})
        elif kind == "left":
            self._stay_ended(call, now)
            self._add("idle" if text == "idle" else "leave", call, text)
            self.ctl._emit("room", "leave", {"remote": call, "why": text})
        elif kind == "line":
            if text.lower().startswith("/me "):
                self._add("me", call, text[4:].strip())
                if call != self.host():
                    self.ctl._emit("room", "me", {"remote": call, "text": text[4:].strip()})
            elif call == self.host():
                self._add("host", call, text)
            else:
                self._add("say", call, text)
                self.ctl._emit("room", "say", {"remote": call, "text": text})
        elif kind == "topic":
            self._add("topic", call, text)
