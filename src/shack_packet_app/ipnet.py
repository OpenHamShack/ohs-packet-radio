"""IP over AX.25 in release 1: the engine's IP stack with tools inside the
app, and the seam where an operating-system interface will plug in later.

**What release 1 does.** ohs-libs' `shack_packet.ip.IpInterface` carries
IPv4 over AX.25 (RFC 1226, datagram or virtual circuit), ARP over AX.25 and
routes, and every packet passes the encryption guard before anything is
queued (UK licence clause 11(2): no encrypted traffic). The app shows its
address, ARP and route tables, the guard's refusals, and has its own
`Pinger` (ICMP echo, built and matched here), so a tester can prove an IP
path to another station with nothing installed.

**What it does not do: no TUN/utun/Wintun interface.** Giving the
operating system a network interface needs administrator rights on all
three systems (a TUN device and CAP_NET_ADMIN on Linux, utun on macOS
through a privileged helper, the Wintun driver on Windows). Brian decided
on 18/10/2026 that release 1 keeps IP inside the app and the system
interface comes in release 2, so none is created and nothing asks for
elevation.

**The seam.** `OsInterface` is what such an adapter will implement:
`attach(ip)` is given the running IpInterface and connects it to the
system (packets the system routes to the radio go to `ip.send`, and
`ip.on_packet` hands received ones to the system), `detach()` undoes it,
and `describe()` says what it did for the IP page. `NoOsInterface`, the
only one built, attaches nothing. The controller owns exactly one, made by
`os_interface()`, so adding a TUN adapter is that function and a class -
no change to the engine, the window or the guard, and every packet the
system sends still passes the guard inside `ip.send`.
"""
from __future__ import annotations

import ipaddress
import os
import struct
import threading
import time
from dataclasses import dataclass, field

from shack_packet.ip import ipv4_checksum


class OsInterface:
    """An operating-system network interface for the engine's IP stack."""

    name = "none"

    def attach(self, ip) -> None:
        raise NotImplementedError

    def detach(self) -> None:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "attached": False}


class NoOsInterface(OsInterface):
    """Release 1: no system interface; IP is used from inside the app."""

    def attach(self, ip) -> None:
        pass

    def detach(self) -> None:
        pass


def os_interface() -> OsInterface:
    return NoOsInterface()


def echo_request(src: str, dst: str, ident: int, seq: int, payload: bytes = b"", ttl: int = 64) -> bytes:
    """An IPv4 ICMP echo request."""
    icmp = bytearray(struct.pack(">BBHHH", 8, 0, 0, ident & 0xFFFF, seq & 0xFFFF) + payload)
    icmp[2:4] = struct.pack(">H", ipv4_checksum(bytes(icmp)))
    total = 20 + len(icmp)
    header = bytearray(struct.pack(">BBHHHBBH4s4s", 0x45, 0, total, ident & 0xFFFF, 0, ttl, 1, 0,
                                   ipaddress.IPv4Address(src).packed, ipaddress.IPv4Address(dst).packed))
    header[10:12] = struct.pack(">H", ipv4_checksum(bytes(header)))
    return bytes(header) + bytes(icmp)


@dataclass
class PingResult:
    seq: int
    sent_at: float
    rtt_s: float | None = None
    refused: str = ""             # the guard's or the IP layer's key, when it was never sent


@dataclass
class PingRun:
    target: str
    ident: int
    results: list[PingResult] = field(default_factory=list)

    @property
    def answered(self) -> int:
        return sum(1 for r in self.results if r.rtt_s is not None)


class Pinger:
    """ICMP echo over the engine's IP stack. `ping(target, count)` sends
    `count` requests `interval_s` apart from a thread of its own; replies
    arrive through `on_packet` (the controller chains it after the stack's
    other listeners) and are matched by ident and sequence. Results are
    read from `runs` by the window."""

    def __init__(self, ip, clock=time.monotonic):
        self.ip = ip
        self.clock = clock
        self.runs: list[PingRun] = []
        self._lock = threading.Lock()

    def ping(self, target: str, count: int = 4, interval_s: float = 5.0, payload: bytes = b"OpenHamShack") -> PingRun:
        ipaddress.IPv4Address(target)                    # ValueError for the caller to show
        run = PingRun(target, int.from_bytes(os.urandom(2), "big"))
        with self._lock:
            self.runs.append(run)
            self.runs = self.runs[-20:]

        def go():
            for seq in range(1, count + 1):
                packet = echo_request(str(self.ip.address), target, run.ident, seq, payload)
                result = PingResult(seq, self.clock())
                with self._lock:
                    run.results.append(result)
                before = dict(self.ip.stats)
                if not self.ip.send(packet):
                    after = self.ip.stats
                    refused = [k[len("refused_"):] for k in after if k.startswith("refused_")
                               and after[k] > before.get(k, 0)]
                    result.refused = refused[0] if refused else "refused"
                if seq < count:
                    time.sleep(interval_s)
        threading.Thread(target=go, name="ping", daemon=True).start()
        return run

    def packet_in(self, packet: bytes) -> bool:
        """A packet the stack delivered: True if it was one of our replies."""
        if len(packet) < 28 or packet[0] >> 4 != 4 or packet[9] != 1:
            return False
        ihl = (packet[0] & 0x0F) * 4
        if len(packet) < ihl + 8 or packet[ihl] != 0:
            return False
        ident, seq = struct.unpack(">HH", packet[ihl + 4:ihl + 8])
        now = self.clock()
        with self._lock:
            for run in self.runs:
                if run.ident != ident:
                    continue
                for r in run.results:
                    if r.seq == seq and r.rtt_s is None:
                        r.rtt_s = now - r.sent_at
                        return True
        return False
