"""Finding a Pluto's address: what the setup check's *Find* button runs.

Ported from OpenHamShack NB (`~/openhamshack-nb`, ohscore/devices/pluto.py
`discover()` and its helpers, 04/10/2026; GPL-3.0 like this program, same
authors). Why: a tester's Pluto on Windows was not at ip:192.168.2.1, the
address the setup check starts with, and NB found it where we could not.
NB's lessons are kept as it learnt them:

- **The combined scan is not trusted alone.** libiio 0.26's
  `iio.scan_contexts()` was seen (Debian 13, no avahi-daemon) to raise
  `OSError [Errno 26] Text file busy` and lose the USB half with the
  network half. A failed combined scan falls back to one backend at a time
  ("usb", then "ip"), each caught on its own so one broken backend cannot
  cost the other. Whether Avahi is the reason is told by its socket, not
  by the error text (libiio prints that only to stderr).
- **Likely addresses are probed directly** however the scan went: the
  factory ip:192.168.2.1, ip:pluto.local, and the .1 of any /24 on which
  this computer holds .10 - the host end of a Pluto's own USB network,
  which is where a Pluto set to another subnet is. NB looked only at
  Linux's enx*/usb* interfaces; this also asks the host name's addresses,
  which on Windows (the case that prompted this) lists every interface.
  Each probe is a thread given `timeout_s`, because a dead address on a
  live subnet otherwise costs the kernel's whole SYN-retry budget, and the
  probes run side by side rather than one after another.
- **One radio, one entry.** ip: and usb: for one Pluto share a serial;
  an mDNS name and a number can be one box; a probe repeats the scan.
  Entries are merged on any shared identity (serial, IPv4, IPv6, name),
  never across two different serials, and the merged entry keeps the
  address that keeps working: a number before a name before a link-local
  IPv6 before usb: (which needs udev permissions on Linux, and gives the
  transmitter guard no network to reach the Pluto by).

**Read-only.** A probe opens an IIO context, looks for the ad9361-phy
device and reads the context's own attributes (model, serial). It never
opens a buffer, never touches a channel and writes no attribute: the
transmitter is not opened and nothing on the Pluto changes.
`Context.set_timeout` is the client's own wait, not a write to the Pluto.

**Injectable.** libiio arrives as a module (`iio`), and the per-backend
scan, the list of direct addresses, the Avahi check and the name lookup
are parameters, so the tests run every path with fakes and no radio.

`find()` never raises; it returns what it found and `notes`, short codes
(NOTE_*) the window turns into sentences.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

#: libiio could not be loaded at all: nothing was searched.
NOTE_NO_LIBIIO = "no-libiio"
#: The combined scan failed and avahi-daemon is not running (Linux):
#: network discovery needs it; USB and the direct probes were still tried.
NOTE_NO_AVAHI = "scan-no-avahi"
#: The combined scan failed for another reason; the fallback ran.
NOTE_SCAN_FAILED = "scan-failed"

#: Description text libiio's scan gives a Pluto-family radio contains one
#: of these, e.g. "192.168.2.1 (Nano PlutoSDR (Z7010-AD9361)),
#: serial=UQRUR7HPFYWAMKGY". Anything else ("local:" with a laptop's own
#: sensors, an M2k) is dropped rather than offered as a radio.
_AD936X_MARKERS = ("pluto", "ad9361", "ad9363", "ad9364", "libresdr")


@dataclass
class Found:
    uri: str
    description: str


@dataclass
class FindResult:
    found: list[Found] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    uri: str
    description: str
    serial: str | None
    # Identities this candidate was known by before _prefer_ipv4 rewrote its
    # uri, so the rewrite never costs it a match in _dedupe.
    aliases: frozenset = frozenset()


def find(iio=None, *, timeout_s: float = 1.0, backend_scan=None, direct=None, avahi_running=None,
         resolve=None) -> FindResult:
    """Every Pluto-family radio this computer can see (module docstring).

    `iio`: the libiio module, or a callable returning it (raising when it
    cannot be loaded); `backend_scan(iio, b"usb"|b"ip") -> {uri: desc}`;
    `direct() -> [uri]`; `avahi_running() -> bool`; `resolve(name,
    timeout_s) -> IPv4 or None`. Each defaults to the real thing."""
    out = FindResult()
    backend_scan = backend_scan or _backend_scan
    direct = direct or direct_candidates
    avahi_running = avahi_running or _avahi_running
    resolve = resolve or _resolve_ipv4
    if iio is None or (callable(iio) and not hasattr(iio, "scan_contexts")):
        try:
            iio = (iio or _load_iio)()
        except Exception:                           # noqa: BLE001 - every load failure means "no libiio"
            out.notes.append(NOTE_NO_LIBIIO)
            return out

    cands: list[_Candidate] = []

    def absorb(scan) -> None:
        for uri, desc in dict(scan or {}).items():
            if _looks_like_ad936x(desc):
                cands.append(_Candidate(uri, desc, _serial_from_description(desc)))

    try:
        absorb(iio.scan_contexts())
    except Exception:                               # noqa: BLE001 - libiio says OSError today; anything is caught
        try:
            avahi = bool(avahi_running())
        except Exception:                           # noqa: BLE001
            avahi = True
        out.notes.append(NOTE_SCAN_FAILED if avahi else NOTE_NO_AVAHI)
        # Only "usb" truly sidesteps the Avahi failure ("ip" finds other
        # IIOD hosts by mDNS, Avahi's path too), so each is tried alone.
        for backend in (b"usb", b"ip"):
            try:
                absorb(backend_scan(iio, backend))
            except Exception:                       # noqa: BLE001 - this fallback is itself best-effort
                pass

    try:
        uris = list(direct())
    except Exception:                               # noqa: BLE001 - no extra addresses, the defaults stand
        uris = ["ip:192.168.2.1", "ip:pluto.local"]
    cands += _probe_all(iio, uris, timeout_s)

    cands = [_prefer_ipv4(c, timeout_s, resolve) for c in cands]
    out.found = [Found(c.uri, c.description) for c in _dedupe(cands)]
    return out


def _load_iio():
    from .plutosetup import _iio
    return _iio()


# -- what a description says --------------------------------------------------------------

def _looks_like_ad936x(description: str) -> bool:
    d = str(description).lower()
    return any(m in d for m in _AD936X_MARKERS)


def _serial_from_description(description: str) -> str | None:
    m = re.search(r"serial=(\S+)", description)
    return m.group(1) if m else None


def _ipv4(text: str) -> str | None:
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        return None


def _ipv6(text: str) -> str | None:
    # libiio's mDNS path writes link-local addresses with their scope
    # ("fe80::1%enp3s0"): the scope says which interface, not which radio.
    try:
        return str(ipaddress.IPv6Address(text.strip("[]").split("%", 1)[0]))
    except ValueError:
        return None


def _uri_host(uri: str) -> str | None:
    return uri[len("ip:"):] if uri.startswith("ip:") else None


def _described_host(description: str) -> str | None:
    """The address libiio puts before the model ("192.168.2.1 (...)", or
    "fe80::1%eth0 (...)" from mDNS). USB entries start with a
    vendor:product pair instead, which is neither."""
    m = re.match(r"(\S+) \(", description)
    return m.group(1) if m else None


def _preference(uri: str) -> int:
    """Lower sorts first (module docstring: the address that keeps working)."""
    host = _uri_host(uri)
    if host is not None:
        if _ipv4(host):
            return 0
        return 2 if _ipv6(host) else 1
    if uri.startswith("usb:"):
        return 3
    return 4


def _identity(text: str) -> str | None:
    if v4 := _ipv4(text):
        return f"v4:{v4}"
    if v6 := _ipv6(text):
        return f"v6:{v6}"
    # Anything else with a colon is a USB vendor:product pair, which every
    # Pluto of one model shares: matching on it would merge two radios.
    if ":" in text or "." not in text:
        return None
    return f"name:{text.lower().rstrip('.')}"


def _identities(c: _Candidate) -> set[str]:
    """Everything that says "this is that radio". An mDNS entry for a
    LibreSDR can carry no serial at all (NB, 04/10), so without the address
    keys it could never meet the numeric entry for the same box."""
    keys = set(c.aliases)
    if c.serial:
        keys.add(f"serial:{c.serial}")
    for text in (_uri_host(c.uri), _described_host(c.description)):
        if text and (k := _identity(text)):
            keys.add(k)
    return keys


def _resolve_ipv4(name: str, timeout_s: float) -> str | None:
    """`name`'s IPv4 address, or None. getaddrinfo cannot be cancelled and
    a .local name without nss-mdns can wait on unicast DNS, so it runs on
    its own thread and this stops waiting at the deadline."""
    box: list = []

    def run():
        try:
            infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
            box.append(infos[0][4][0] if infos else None)
        except (OSError, UnicodeError):
            box.append(None)

    t = threading.Thread(target=run, name="pluto-find-resolve", daemon=True)
    t.start()
    t.join(timeout_s)
    return box[0] if box else None


def _prefer_ipv4(c: _Candidate, timeout_s: float, resolve) -> _Candidate:
    """ip:<name> or ip:<IPv6> becomes ip:<IPv4> when the number can be
    known, because the number keeps working on a computer without mDNS.
    The name moves to the front of the description so the list still shows
    it, and the old identities ride along as aliases for merging."""
    host = _uri_host(c.uri)
    if host is None or _ipv4(host):
        return c
    named = _ipv6(host) is None
    described = _described_host(c.description)
    v4 = _ipv4(described) if described else None
    if v4 is None and named:
        try:
            v4 = _ipv4(resolve(host, timeout_s) or "")
        except Exception:                           # noqa: BLE001 - unresolved: the name stays
            v4 = None
    if v4 is None:
        return c
    desc = c.description
    if named:
        desc = host + desc[len(described):] if described else f"{host} {desc}"
    return _Candidate(f"ip:{v4}", desc, c.serial, frozenset(_identities(c)))


def _dedupe(cands: list[_Candidate]) -> list[_Candidate]:
    """Candidates grouped by any shared identity, two different serials
    never merged (two radios behind one address is unlikely, but listing
    both is the safe way to be wrong). Each group keeps its preferred uri,
    and a host name any member knew replaces a bare address at the front
    of the description so it stays visible."""
    groups: list[dict] = []
    for c in cands:
        keys, serial = _identities(c), c.serial
        hits = [g for g in groups
                if g["keys"] & keys and not (serial and g["serial"] and g["serial"] != serial)]
        if hits:
            g = hits[0]
            for other in hits[1:]:
                if g["serial"] and other["serial"] and g["serial"] != other["serial"]:
                    continue
                g["members"] += other["members"]
                g["keys"] |= other["keys"]
                g["serial"] = g["serial"] or other["serial"]
                groups.remove(other)
        else:
            g = {"keys": set(), "serial": None, "members": []}
            groups.append(g)
        g["members"].append(c)
        g["keys"] |= keys
        g["serial"] = g["serial"] or serial

    out = []
    for g in groups:
        best = min(g["members"], key=lambda m: _preference(m.uri))   # first-seen wins a tie
        desc = best.description
        described = _described_host(desc)
        names = [h for m in g["members"]
                 for h in (_uri_host(m.uri), _described_host(m.description))
                 if h and (_identity(h) or "").startswith("name:")]
        if names and described and (_ipv4(described) or _ipv6(described)):
            desc = names[0] + desc[len(described):]
        out.append(_Candidate(best.uri, desc, g["serial"]))
    return out


# -- the scans and probes ------------------------------------------------------------------

def _backend_scan(iio_mod, backend: bytes) -> dict[str, str]:
    """One libiio backend's scan (b"usb" or b"ip"). The Python binding has
    no public per-backend scan, only the private ctypes calls its own
    scan_contexts() makes, so each symbol is feature-detected and a binding
    without one yields nothing rather than an AttributeError."""
    names = ("_create_scan_context", "_get_context_info_list", "_context_info_get_uri",
             "_context_info_get_description", "_context_info_list_free", "_destroy_scan_context",
             "_POINTER", "_byref", "_ContextInfoPtr")
    if not all(hasattr(iio_mod, n) for n in names):
        return {}
    ptr = iio_mod._POINTER(iio_mod._ContextInfoPtr)()
    ctx = iio_mod._create_scan_context(backend, 0)
    try:
        n = iio_mod._get_context_info_list(ctx, iio_mod._byref(ptr))
        out = {}
        for i in range(n):
            uri = iio_mod._context_info_get_uri(ptr[i]).decode("ascii")
            out[uri] = iio_mod._context_info_get_description(ptr[i]).decode("ascii")
        iio_mod._context_info_list_free(ptr)
        return out
    finally:
        iio_mod._destroy_scan_context(ctx)


def _linux_usb_network_addresses() -> list[str]:
    """This computer's IPv4 addresses on enx*/usb* interfaces with a /24
    mask (NB's source: SIOCGIFADDR via fcntl, Linux only; any failure
    means nothing from here)."""
    try:
        import fcntl
        names = [n for _, n in socket.if_nameindex() if n.startswith(("enx", "usb"))]
    except (ImportError, AttributeError, OSError):
        return []
    SIOCGIFADDR, SIOCGIFNETMASK = 0x8915, 0x891B

    def ioctl_ip(sock, name: str, request: int) -> str | None:
        try:
            res = fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name.encode("ascii")[:15]))
        except OSError:
            return None
        return socket.inet_ntoa(res[20:24])

    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for name in names:
                addr, mask = ioctl_ip(s, name, SIOCGIFADDR), ioctl_ip(s, name, SIOCGIFNETMASK)
                if addr and mask == "255.255.255.0":
                    out.append(addr)
    except OSError:
        return []
    return out


def _host_addresses() -> list[str]:
    """This computer's IPv4 addresses as its host name resolves: on Windows
    every interface's, the Pluto's USB network among them. Only a numeric
    lookup of our own name, bounded like any other."""
    box: list = []

    def run():
        try:
            box.extend(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        except (OSError, UnicodeError):
            pass

    t = threading.Thread(target=run, name="pluto-find-self", daemon=True)
    t.start()
    t.join(1.0)
    return list(box)


def direct_candidates(addresses=None) -> list[str]:
    """The addresses worth probing whatever the scan said: the factory
    default, its mDNS name, and .1 on every /24 where this computer is .10
    (a Pluto's USB network gives its host .10 by DHCP; module docstring).
    `addresses` is this computer's IPv4 addresses (a test gives its own)."""
    if addresses is None:
        addresses = _linux_usb_network_addresses() + (_host_addresses() if sys.platform != "linux" else [])
    uris = ["ip:192.168.2.1", "ip:pluto.local"]
    for addr in addresses:
        if not (_ipv4(addr) and addr.endswith(".10")):
            continue
        uri = f"ip:{addr[:-len('10')]}1"
        if uri not in uris:
            uris.append(uri)
    return uris


def _probe(iio_mod, uri: str, timeout_s: float) -> _Candidate | None:
    """Read-only (module docstring): opened, asked for ad9361-phy, the
    context's attributes read, closed."""
    ctx = iio_mod.Context(uri)
    try:
        if hasattr(ctx, "set_timeout"):
            ctx.set_timeout(max(1, int(timeout_s * 1000)))
        if ctx.find_device("ad9361-phy") is None:
            return None                             # it answered, but it is not a Pluto-family radio
        attrs = dict(getattr(ctx, "attrs", {}) or {})
        model = attrs.get("hw_model") or attrs.get("hw_model_variant") or "AD936x"
        serial = attrs.get("hw_serial") or None
        host = uri.split(":", 1)[1] if ":" in uri else uri
        return _Candidate(uri, f"{host} ({model})" + (f", serial={serial}" if serial else ""), serial)
    finally:
        del ctx


def _probe_all(iio_mod, uris: list[str], timeout_s: float) -> list[_Candidate]:
    """Every probe on its own thread at once, waited for until one deadline.
    The binding cannot cancel an open in flight, so a probe that finishes
    after the deadline is simply not listened to."""
    results: list = [None] * len(uris)

    def run(i: int, uri: str):
        try:
            results[i] = _probe(iio_mod, uri, timeout_s)
        except Exception:                           # noqa: BLE001 - any failure means "not there"
            results[i] = None

    threads = [threading.Thread(target=run, args=(i, u), name="pluto-find-probe", daemon=True)
               for i, u in enumerate(uris)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout_s * 2       # the open, then the reads at the context's own timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return [r for r in list(results) if r is not None]


def _avahi_running() -> bool:
    # Avahi is Linux's: Windows and macOS libiio do their own mDNS, so a
    # failed scan there is not explained by it.
    return sys.platform != "linux" or Path("/var/run/avahi-daemon/socket").exists()
