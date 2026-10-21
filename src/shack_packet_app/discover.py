"""Finding the operator's radio: Icom radios on the LAN, serial ports, and
sound devices, for the setup check's lists. Nothing here keys or sets up
anything; each function only looks, and only when the operator presses the
button that asks for it (never at start-up).

**Icom on the LAN.** An IC-9700 or IC-705 answers Icom's network protocol
on UDP 50001, and the first packet of every session is "are you there?"
(16 bytes, type 0x03), to which the radio says "I am here" (0x04) -
shack_rig.icom.lan's handshake, from wfview. `find_icoms` sends that one
packet to the local broadcast address (and to any addresses given), waits
`timeout_s`, and lists who answered, then sends each a "disconnect"
(0x05) for the session it never opened, so the radio does not wait on us.
It never logs in, so it needs no password and cannot take the radio from
another program.

**Not proved on a real radio.** It has been tested against shack_rig's
FakeIcom only. Icom's documents do not say whether a radio answers a
broadcast; if Find shows nothing, the operator types the radio's address
(Menu > SET > Network on the radio shows it). The socket is a client's: an
ephemeral port, used for the one exchange and closed - no listener.
"""
from __future__ import annotations

import glob
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass

ICOM_PORT = 50001
_AREYOUTHERE, _IAMHERE, _DISCONNECT = 0x03, 0x04, 0x05


@dataclass
class FoundIcom:
    host: str
    port: int
    radio_id: int


def _packet(ptype: int, my_id: int, their_id: int = 0) -> bytes:
    return struct.pack("<IHHII", 0x10, ptype, 0, my_id, their_id)


def find_icoms(addresses=(), *, broadcast: bool = True, port: int = ICOM_PORT, timeout_s: float = 1.5) -> list[FoundIcom]:
    """Icom radios that answered "are you there" (module docstring)."""
    found: dict[tuple[str, int], FoundIcom] = {}
    targets = [(a, port) if isinstance(a, str) else tuple(a) for a in addresses]
    if broadcast:
        targets.append(("255.255.255.255", port))
    my_id = int.from_bytes(os.urandom(4), "little") or 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.1)
        for t in targets:
            try:
                s.sendto(_packet(_AREYOUTHERE, my_id), t)
            except OSError:
                continue                            # no route: that target is not there
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(64)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                break
            if len(data) != 0x10:
                continue
            _length, ptype, _seq, sentid, _rcvd = struct.unpack("<IHHII", data)
            if ptype == _IAMHERE and addr not in found:
                found[addr] = FoundIcom(addr[0], addr[1], sentid)
        for f in found.values():
            try:
                s.sendto(_packet(_DISCONNECT, my_id, f.radio_id), (f.host, f.port))
            except OSError:
                pass
    return sorted(found.values(), key=lambda f: (f.host, f.port))


def serial_ports() -> list[tuple[str, str]]:
    """(device, description) of every serial port, pyserial's list when it is
    installed, else the usual device names. An FTX-1 shows two (Enhanced and
    Standard COM); an IC-9700 one; a USB interface one."""
    try:
        from serial.tools import list_ports
        return sorted((p.device, p.description or "") for p in list_ports.comports())
    except ImportError:
        pass
    if sys.platform.startswith("win"):
        return []
    names = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*") + glob.glob("/dev/cu.*")
    return [(n, "") for n in sorted(names)]


def sound_devices(backend=None) -> list[dict]:
    """shack_rig.sound's list: name, inputs, outputs, rate; [] without
    PortAudio rather than an error."""
    from shack_rig import sound
    return sound.list_devices(backend)
