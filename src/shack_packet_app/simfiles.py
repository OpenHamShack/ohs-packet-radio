"""G4SIMF, the simulator's file station: somebody to send a file or a
picture to, and to have one sent back by, with no radio and no friend
(simband.py's other stations are an APRS tracker, an APRS station and a
node).

It is an OpenHamShack station as far as the protocol goes - ohs-libs'
FileService and YAPP on a real engine on the simulated band - so a file
sent to it goes through exactly the same OHS or YAPP exchange as on the
air, and so does one it sends. It takes every file it is offered,
checks it (the protocol's SHA-256), says so in the chat and throws it
away: nothing a tester sends is kept. Typed commands: PIC (it sends a
test-card picture), TXT (a text file by OHS), YAPP (the same by YAPP),
? (these), B (bye). It only sends when asked, like the rest of the
simulator; it only ever exists when the radio is the simulator.
"""
from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

from shack_packet.engine import Runner
from shack_packet.filexfer import FileService, Inbox
from shack_packet.yapp import YappHandler

#: Not an SSID of G4SIM: an engine answers on every SSID of its own call
#: (a mailbox lives on one), so G4SIM and G4SIM-9 sent DM to anybody
#: calling "G4SIM-2" before this station could answer.
CALL = "G4SIMF"


def test_card(width: int = 192, height: int = 128) -> bytes:
    """A small PNG of colour bars, made here (no image asset bundled)."""
    bars = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0),
            (0, 0, 192), (16, 16, 16)]
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            r, g, b = bars[x * len(bars) // width]
            if y > height * 3 // 4:
                v = x * 255 // width
                r = g = b = v
            rows += bytes((r, g, b))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


TEXT = ("This file came from G4SIMF, OpenHamShack Packet Radio's simulated file station.\n"
        "It went over the simulated channel by the same protocol as on the air.\n" * 20).encode()


class _Console:
    def __init__(self, station, conn):
        self.station = station
        self.conn = conn
        self.line = bytearray()

    def data(self, data: bytes) -> None:
        for b in data:
            if b in (13, 10):
                if self.line:
                    self.command(self.line.decode("latin-1").strip().upper())
                self.line.clear()
            else:
                self.line.append(b)

    def command(self, word: str) -> None:
        svc = self.station.service
        if word in ("?", "H", "HELP"):
            self.conn.send("Commands: PIC a picture, TXT a text file, YAPP the text file by YAPP, B bye. "
                           "Or send me a file.\r")
        elif word == "PIC":
            svc.send_file(self.conn, Path("g4sim-test-card.png"), data=test_card())
        elif word == "TXT":
            svc.send_file(self.conn, Path("from-g4sim.txt"), data=TEXT)
        elif word == "YAPP":
            svc.send_file(self.conn, Path("from-g4sim.txt"), data=TEXT, protocol="yapp")
        elif word in ("B", "BYE", "Q"):
            self.conn.send("73\r")
            self.conn.close()
        else:
            self.conn.send(f"G4SIMF is a file station. ? for help. (You said: {word[:40]})\r")


class FileStation:
    def __init__(self, band, inbox_dir: Path):
        self.inbox_dir = Path(inbox_dir)
        shutil.rmtree(self.inbox_dir, ignore_errors=True)
        self.engine = band._station(CALL)
        self.service = FileService(self.engine, Inbox(self.inbox_dir), app="OpenHamShack simulator",
                                   on_event=self._event)
        YappHandler(self.service)
        self.engine.listen(CALL, self._incoming)
        self.runner = Runner(self.engine).start()
        band.runners.append(self.runner)

    def _incoming(self, call):
        def setup(conn):
            console = _Console(self, conn)
            conn.on_data = console.data
            self.service.attach(conn)
            prev = conn.on_event

            def on_event(event, prev=prev):
                if prev is not None:
                    prev(event)
                # Greeted on a reset too, as GB7SIM is (simband._Node).
                # No HELLO of its own: like any OHS station meeting a
                # caller for the first time, it says HELLO in answer to
                # the caller's, so the first file is what introduces them.
                if event.kind in ("connected", "reset"):
                    conn.send("G4SIMF file station (simulated). Send me a file, or type ? for help.\r")
            conn.on_event = on_event
        return setup

    def _event(self, t, kind, detail) -> None:
        if t is None:
            return
        if kind == "offer":
            self.service.accept(t)
        elif kind == "done" and t.direction == "in":
            t.session.send_text(f"Received {t.name}, {t.size} bytes, checked. 73\r".encode("latin-1", "replace"))
            try:
                Path(t.path).unlink()
            except OSError:
                pass


def add_to(band, inbox_dir: Path) -> FileStation:
    return FileStation(band, inbox_dir)
