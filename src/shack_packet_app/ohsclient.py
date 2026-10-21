"""This app as a client of an OpenHamShack controller: find controllers,
pair with one, and list the radios it manages - so the desktop never opens
a radio the controller has reserved or is using.

Named ohsclient, not controller: controller.py is this app's station core
(PLAN-0.2 §1), and the two must never be confused.

Brian's decision D1 (18/10/2026): the desktop program runs the packet
modem. A controller will later run a BBS of its own and RESERVE the radio
it uses; until then it lists the radios its broker manages and who holds
each. So, in 0.2:

- Discovery finds controllers by `_openhamshack._tcp` and the legacy
  `_openhamshack._tcp`, and `/.well-known/openhamshack` (shack_client.discovery,
  CONTROLLER-CONTRACT §2-3). A Classic 0.9.x box answers `_openhamshack` too; it
  is not a controller and is left out.
- Pairing is the contract's password exchange (§4.2) with deviceKind
  `desktop`. The password is used once and never kept; the device token
  goes in secrets.json beside the LAN password, never in settings.json, so
  a diagnostics bundle cannot carry it (diagnostics.secrets_of).
- The radio list is `GET api/shack/v1/broker/devices` with the device
  token, which the broker allows at `view` level (dock360
  src/dock360/web/routes/shack_broker.py, device_list). The desktop only
  looks: it never claims through the broker in 0.2 (D1), and the broker
  refuses a device token a claim anyway ("a phone never holds a radio").
- A radio the controller lists as reserved, held by an app, queued for, in
  a hand-over or on the air is never opened here (radio_conflict). A radio
  on this computer's own USB, or a LAN radio the controller does not list,
  is driven directly as in 0.1.

"Reserved" is not in contract v1 yet: the BBS step adds it. This reads a
`reserved` field on a device row (true, or an object naming who reserved
it) so the desktop already honours it the day the broker sends it; the
contract change belongs to the platform's owner (HANDOVER).

**A Pluto on the controller** (0.2.1): the controller box can share a
Pluto plugged into it over the network, iiod and the Pluto's SSH on ports
of their own (`pluto://host?iiod=30431&ssh=30422`, shack_pluto.endpoint).
`controller_pluto_uri` builds that address from the paired controller's;
a station Pluto at the controller's own host is checked against the
controller's Pluto rows (`pluto_conflict`): reserved, held, queued, in a
hand-over or on the air, it is not opened. The rows do not yet say which
of several Plutos is shared (identity.address is the Pluto's own, not the
port the controller forwards), so with more than one, any of them
blocking refuses: the safe side of not knowing.

When the controller cannot be reached, the last list fetched in this run
decides (a radio it held a minute ago is still treated as held); with no
list at all the radio is opened, since nothing says otherwise - a LAN
Icom also refuses a second LAN session itself, so nothing can be keyed
twice that way.
"""
from __future__ import annotations

import ipaddress
import logging
import platform
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: The name a token is issued for (the device row's `app`, CONTROLLER-CONTRACT §4.1).
TOKEN_APP = "shack-packet-desktop"
SECRET_KEY = "controller_token"
#: Broker classes that are a radio this app could open (dock360 broker.py
#: CLASSES), and the app's radio kind each matches.
RADIO_CLASSES = {"ic9700": "ic-9700", "ic705": "ic-705", "ic7610": None, "pluto": "pluto", "libresdr": "pluto",
                 "hackrf": None, "rtlsdr": "rtlsdr", "sdrplay": None, "flex": None, "hermeslite": None}
#: Every Pluto answers on this address over its own USB gadget network,
#: so it names a different Pluto on every computer and matches nothing.
PLUTO_USB_NET = ipaddress.ip_network("192.168.2.0/24")


class ControllerError(Exception):
    """The controller refused (`code` its machine code: bad_credentials,
    token_revoked, level_too_low...) or answered something unexpected."""

    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class RemoteRadio:
    """One row of the controller's broker list, as this app reads it."""
    id: str
    cls: str
    label: str
    reach: str = ""
    address: str = ""
    present: bool = True
    tx_capable: bool = False
    holder: str = ""                 # the app holding it, "" if none
    mode: str = ""                   # rx | tx, the holder's
    on_air: bool = False
    queued: bool = False
    handover: bool = False
    reserved: bool = False
    reserved_by: str = ""

    @property
    def in_use(self) -> bool:
        return bool(self.holder or self.on_air or self.queued or self.handover)

    @property
    def blocks(self) -> bool:
        """Never opened from this computer while true (D1)."""
        return self.reserved or self.in_use

    @property
    def state(self) -> str:
        """reserved | on-air | in-use | absent | free: what the window shows."""
        if self.reserved:
            return "reserved"
        if self.on_air:
            return "on-air"
        if self.in_use:
            return "in-use"
        if not self.present:
            return "absent"
        return "free"


#: The store app that runs the controller's BBS (0.3.0, docs/BBS-DESIGN.md).
#: Its claim is a standing reservation. A broker with reservations (dock360
#: feature/ohs-edition-030-c) names it on the row as `reserved: {app, name,
#: reason, ...}` and that is what is shown; an older broker only names the
#: holder, so a radio the BBS holds is still shown as reserved by it rather
#: than merely in use. Either way it blocks.
BBS_APP = "shack-packet"
BBS_LABEL = "Packet Radio BBS"


def parse_device(row: dict) -> RemoteRadio:
    holder = row.get("holder") or {}
    holders = row.get("holders") or []
    reserved = row.get("reserved")
    if not reserved and holder.get("app") == BBS_APP:
        reserved = {"app": BBS_APP, "label": BBS_LABEL}
    ident = row.get("identity") or {}
    return RemoteRadio(
        id=str(row.get("id", "")), cls=str(row.get("class", "")), label=str(row.get("label") or row.get("id", "")),
        reach=str(row.get("reach", "")), address=str(ident.get("address", "") or ""),
        present=bool(row.get("present", True)), tx_capable=bool(row.get("txCapable")),
        holder=str(holder.get("app", "") or (holders[0] if holders else "")), mode=str(holder.get("mode", "")),
        on_air=bool(holder.get("onAir")), queued=bool(row.get("queue")), handover=bool(row.get("handover")),
        reserved=bool(reserved),
        reserved_by=str((reserved.get("name") or reserved.get("label") or reserved.get("app") or "")
                        if isinstance(reserved, dict) else ""))


def radios_of(body: dict) -> list[RemoteRadio]:
    """The radios in a broker device list (not the GPU, PTT outputs or
    LNB supplies)."""
    out = []
    for row in body.get("devices") or []:
        if isinstance(row, dict) and row.get("class") in RADIO_CLASSES:
            out.append(parse_device(row))
    return out


# ------------------------------------------------------------------ matching
def _addresses(host: str) -> set[str]:
    """`host` and what it resolves to (IPv4), so a radio typed as a name
    matches the controller's address for it."""
    host = (host or "").strip().strip("[]")
    if not host:
        return set()
    out = {host.lower()}
    try:
        ipaddress.ip_address(host)
        return out
    except ValueError:
        pass
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    return out


def own_radio_addresses(radio, sdr) -> list[tuple[str, set[str]]]:
    """(what, addresses) for each network radio this station would open:
    its LAN Icom, and its SDR monitor's Pluto or rtl_tcp server. Radios on
    this computer's own USB have no address and are this computer's."""
    out = []
    if radio.kind in ("ic-9700", "ic-705") and radio.transport == "lan" and radio.host:
        out.append(("radio", _addresses(radio.host)))
    if sdr.source == "pluto" and sdr.pluto_uri.startswith("ip:"):
        host = sdr.pluto_uri[3:].split(":")[0]
        try:
            usb_gadget = ipaddress.ip_address(host) in PLUTO_USB_NET
        except ValueError:
            usb_gadget = False
        if not usb_gadget:
            out.append(("sdr", _addresses(host)))
    if sdr.source == "rtl_tcp" and sdr.rtltcp_host and sdr.rtltcp_host not in ("127.0.0.1", "localhost"):
        out.append(("sdr", _addresses(sdr.rtltcp_host)))
    return out


#: The ports the controller box shares its Pluto on (shack_pluto.endpoint's
#: docstring: `tailscale serve` on ohs-box, 18/10/2026).
CONTROLLER_IIOD_PORT = 30431
CONTROLLER_SSH_PORT = 30422
PLUTO_CLASSES = ("pluto", "libresdr")


def controller_host(url: str) -> str:
    return (urlparse(url).hostname or "") if url else ""


def controller_pluto_uri(controller_url: str) -> str:
    """The address of the Pluto the controller at `controller_url` shares,
    or "" with no controller."""
    host = controller_host(controller_url)
    if not host:
        return ""
    return f"pluto://{host}?iiod={CONTROLLER_IIOD_PORT}&ssh={CONTROLLER_SSH_PORT}"


def pluto_host(uri: str) -> str:
    """The network host of a Pluto address, "" for a USB one."""
    from shack_pluto.endpoint import EndpointError, parse
    try:
        return parse(uri).host
    except EndpointError:
        return ""


def is_controller_pluto(uri: str, controller_url: str) -> bool:
    """Whether a Pluto address names the controller's own host."""
    host, ctl = pluto_host(uri), controller_host(controller_url)
    return bool(host and ctl) and bool(_addresses(host) & _addresses(ctl))


def pluto_conflict(radios: list[RemoteRadio]) -> RemoteRadio | None:
    """The controller's Pluto that may not be opened from here, or None
    (module docstring: with several, any one blocking)."""
    return next((r for r in radios if r.cls in PLUTO_CLASSES and r.blocks), None)


def radio_conflict(what: str, addresses: set[str], radios: list[RemoteRadio]) -> RemoteRadio | None:
    """The controller's radio at one of `addresses` that may not be opened
    from here, or None. `what` is "radio" or "sdr" (an SDR matches only an
    SDR class, a radio only a radio)."""
    sdr_classes = {"pluto", "libresdr", "rtlsdr", "hackrf", "sdrplay"}
    for r in radios:
        if not r.address or r.address.lower() not in addresses:
            continue
        if (r.cls in sdr_classes) != (what == "sdr"):
            continue
        if r.blocks:
            return r
    return None


# ------------------------------------------------------------------ the client
class ControllerClient:
    """Discovery, pairing and the radio list, over shack_client. Every call
    blocks (the network): the window calls them on a thread, the station
    core on its radio-opening thread. `transport` makes the HTTP transport
    (tests pass shack_client.testing's)."""

    def __init__(self, settings, secrets: dict, *, transport=None, discoverer=None):
        self.settings = settings          # settings.ControllerSettings
        self.secrets = secrets
        self._transport = transport
        self._discover = discoverer
        self._lock = threading.Lock()
        self.last_radios: list[RemoteRadio] | None = None
        self.last_at = 0.0
        self.last_error = ""

    @property
    def paired(self) -> bool:
        return bool(self.settings.url and self.secrets.get(SECRET_KEY))

    def _t(self, base_url: str, token: str | None = None, timeout: float = 5.0):
        if self._transport is not None:
            return self._transport(base_url, token)
        from shack_client.transport import HttpTransport
        return HttpTransport(base_url, token, timeout=timeout)

    def discover(self, timeout: float = 3.0) -> list:
        """Controllers on this network (raises shack_client's
        DiscoveryUnavailable without zeroconf)."""
        if self._discover is not None:
            found = self._discover(timeout)
        else:
            from shack_client.discovery import discover
            found = discover(timeout)
        return [c for c in found if not c.classic]

    def pair(self, url: str, username: str, password: str, totp: str | None = None) -> dict:
        """Exchange the password for a device token; returns the settings
        it wrote (name, ids). Raises shack_client's PairingError or Offline,
        or ValueError for something that is not a controller."""
        import secrets as _secrets
        from shack_client.discovery import well_known
        from shack_client.pairing import exchange_password
        url = url.strip()
        if "://" not in url:
            url = "http://" + url
        if not url.endswith("/"):
            url += "/"
        doc = well_known(url, transport=self._t(url))
        if not self.settings.client_id:
            self.settings.client_id = _secrets.token_hex(16)
        tok = exchange_password(self._t(url), username, password, totp=totp or None,
                                device_name=f"Packet Radio on {platform.node() or 'this computer'}",
                                device_kind="desktop", client_id=self.settings.client_id, app=TOKEN_APP)
        s = self.settings
        s.url, s.name, s.user = url, str(doc.get("name") or urlparse(url).hostname or url), tok.user
        s.controller_id, s.device_id = tok.controller_id or str(doc.get("controllerId", "")), tok.device_id
        self.secrets[SECRET_KEY] = tok.token
        with self._lock:
            self.last_radios, self.last_at, self.last_error = None, 0.0, ""
        log.info("paired with controller %s (%s) as %s, device %s", s.name, s.url, s.user, s.device_id)
        return {"name": s.name, "deviceId": s.device_id, "user": s.user}

    def unpair(self) -> None:
        """Forget the token here. The controller's device list still shows
        this computer until an administrator revokes it there: revoking
        from here (devices/self/revoke) is left out on purpose, since a
        mistaken revoke on a real box has cost a re-pair before (GUIDE §5)."""
        self.secrets.pop(SECRET_KEY, None)
        s = self.settings
        s.url = s.name = s.user = s.controller_id = s.device_id = ""
        with self._lock:
            self.last_radios, self.last_at, self.last_error = None, 0.0, ""

    def radios(self) -> list[RemoteRadio]:
        """The controller's radios now. Raises shack_client's Offline (no
        answer) or ControllerError (an answer that is a refusal)."""
        if not self.paired:
            raise ControllerError("not paired with a controller", "not_paired")
        from shack_client.transport import Offline
        try:
            status, body = self._t(self.settings.url, self.secrets[SECRET_KEY]).request(
                "GET", "api/shack/v1/broker/devices")
        except Offline as e:
            with self._lock:
                self.last_error = str(e) or "no answer"
            raise
        if status != 200:
            code = str(body.get("code") or f"http_{status}")
            with self._lock:
                self.last_error = code
            raise ControllerError(str(body.get("error") or f"the controller answered {status}"), code, status)
        radios = radios_of(body)
        with self._lock:
            self.last_radios, self.last_at, self.last_error = radios, time.time(), ""
        return radios

    def conflicts(self, radio, sdr, sdr_radio=None) -> list[tuple[str, RemoteRadio]]:
        """Each of this station's network radios the controller lists as
        reserved or in use: [(what, the controller's radio)]. Asks the
        controller; if it does not answer, the last list from this run
        decides, and with none, nothing conflicts (module docstring).
        `sdr_radio` is settings.SdrRadioSettings, for a Pluto as the
        station's radio."""
        if not self.paired:
            return []
        mine = own_radio_addresses(radio, sdr)
        shared = (radio.kind == "pluto" and sdr_radio is not None
                  and is_controller_pluto(sdr_radio.pluto_uri, self.settings.url))
        if not mine and not shared:
            return []
        try:
            radios = self.radios()
        except Exception as e:                      # noqa: BLE001 - Offline, a refusal: fall back below
            with self._lock:
                radios = self.last_radios
            log.info("controller %s did not list its radios (%s); %s", self.settings.name or self.settings.url, e,
                     "using the list from %s" % time.strftime("%H:%M:%S", time.localtime(self.last_at))
                     if radios is not None else "no earlier list, so nothing is held back")
            if radios is None:
                return []
        out = []
        if shared:
            r = pluto_conflict(radios)
            if r is not None:
                out.append(("radio", r))
        for what, addrs in mine:
            r = radio_conflict(what, addrs, radios)
            if r is not None:
                out.append((what, r))
        return out
