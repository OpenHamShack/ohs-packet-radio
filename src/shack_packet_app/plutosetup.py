"""The setup check's Pluto tools (PLAN-0.2 §3.6.2-3.6.4): what the "Check
this Pluto", "Enable 70 MHz-6 GHz" and crystal-correction controls do,
without the window, so the tests drive them and the window only draws.

Everything here is shack_pluto's (ohs-libs), product-neutral; this file
adds the app's choices:

- **Check** opens a second libiio context to the Pluto (iiod serves
  several, so the station's own link, if open, is left alone) and reads it,
  writing nothing: identity, firmware family, chip setting and range. It
  says whether the channel is in reach, and whether the AD9364 setting
  would bring it in on this firmware. A Pluto whose serial is no use is
  filed by its USB MAC, read over SSH.
- **Enable** is shack_pluto.ad9364.enable_ad9364, run on a thread by the
  window with the station's radio released first (a rebooting Pluto under
  an open link is a link failing mid-stream). The window asks for the two
  confirmations; this refuses without them as well.
- **The crystal correction** is kept per Pluto in the app's data folder
  (shack_pluto.xo) and found again at every open by links.xo_lookup.
- **A Pluto on the controller** (`pluto://host?iiod=..&ssh=..`): libiio is
  given `ip:host:port` (shack_pluto.endpoint) and SSH its own port. Port
  1883 is not asked there - the controller forwards only iiod and SSH,
  and anything answering 1883 is the controller's, not the Pluto's - so
  the firmware is told by the SSH probe (identity.probe_over_ssh). The
  identity's `uri` is the libiio address, which is what the reboot
  watchers open; the address as typed is kept in `ident.extra["address"]`
  for SSH (`Tools.runner`).
- **The USB network mode** (0.2.1): read with `fw_printenv` and from the
  USB gadget's functions over SSH, with the one recommended for the
  computer the Pluto is plugged into (`recommended_usb_mode`): ECM on
  macOS, which has no RNDIS; RNDIS on Linux and Windows - NCM stalls
  receive on Linux until the Pluto's watchdog reboots it (seen on shack,
  18/10/2026). The guided switch (`switch_usb_mode`) is the AD9364
  enable's method exactly (shack_pluto.ad9364): the operator's word that a
  dummy load or nothing is on TX, the environment backed up, `fw_setenv`
  read back, then the reboot detached under the TX-LO quiet watcher. It
  only ever switches to the recommended mode: a Pluto rebooted into a mode
  this computer cannot reach could not be quieted by the watcher.

Every SSH call here has its own timeout (SshRunner's connect, banner and
authentication 20 s, each command its own), and the check's libiio
context is given one (IIO_TIMEOUT_MS) before anything is read.

`Tools` is the seam: the window holds one, a test gives it another.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import host


@dataclass
class CheckResult:
    """What the window shows after a check. `ident` is shack_pluto's
    PlutoIdentity; `reach` True/False/None for the channel; `enable`
    "offer", "refused" (this firmware), or "" (not needed)."""
    ident: object
    channel_hz: int
    reach: bool | None
    enable: str
    xo: int | None
    #: The USB network mode (`usb_mode`), or None when SSH did not answer.
    usb: UsbMode | None = None
    #: The mode recommended for this Pluto's USB host, or "" when this
    #: computer is not its USB host and it is not on the controller.
    usb_recommended: str = ""


#: The USB network modes a Pluto's firmware offers (`usb_ethernet_mode`).
USB_MODES = ("rndis", "ncm", "ecm")
#: libiio's timeout for the check's context, ms: a Pluto on the far end of
#: a slow tailnet path answers well inside it; one that is gone fails fast.
IIO_TIMEOUT_MS = 5000
#: One read-only SSH command: the saved setting, then the functions the USB
#: gadget is running now (rndis.0, ncm.usb0, ecm.usb0...).
USB_MODE_COMMAND = ("fw_printenv usb_ethernet_mode 2>/dev/null; echo ---; "
                    "ls -d /sys/kernel/config/usb_gadget/*/functions/* 2>/dev/null")


@dataclass
class UsbMode:
    """`saved`: usb_ethernet_mode in the environment ("" when not set: the
    firmware's default); `running`: the mode the gadget runs now, "" when
    it could not be told."""
    saved: str
    running: str


def parse_usb_mode(text: str) -> UsbMode:
    env, _, funcs = (text or "").partition("---")
    saved = ""
    for line in env.splitlines():
        if line.strip().startswith("usb_ethernet_mode="):
            saved = line.split("=", 1)[1].strip().lower()
    running = ""
    for line in funcs.splitlines():
        name = line.strip().rsplit("/", 1)[-1].split(".")[0].lower()
        if name in USB_MODES:
            running = name
    return UsbMode(saved, running)


def usb_mode(runner) -> UsbMode | None:
    """The Pluto's USB network mode over SSH, None when SSH did not answer."""
    try:
        status, out = runner.run(USB_MODE_COMMAND, timeout=10.0)
    except Exception:                               # noqa: BLE001 - no answer: said by the window
        return None
    return parse_usb_mode(out)


def recommended_usb_mode(platform: str) -> str:
    """The mode for a Pluto on this computer's USB (module docstring)."""
    if platform == "darwin":
        return "ecm"
    return "rndis"


def is_usb_gadget_address(uri: str) -> bool:
    """Whether an address is a Pluto's own USB network (192.168.2.x)."""
    import ipaddress

    from shack_pluto.endpoint import EndpointError, parse
    try:
        return ipaddress.ip_address(parse(uri).host) in ipaddress.ip_network("192.168.2.0/24")
    except (EndpointError, ValueError):
        return False


def switch_usb_mode(ident, runner, iio, data_dir: Path, mode: str, *, operator_confirmed: bool,
                    watcher_factory=None, now=None):
    """Set the Pluto's USB network mode and reboot it under the quiet
    watcher, as shack_pluto.ad9364.enable_ad9364 does for the chip setting
    (module docstring). Raises ad9364.EnableError with one of its REASONS;
    a refusal changes nothing. Returns ad9364.EnableResult."""
    import time

    from shack_pluto.ad9364 import BACK_TIMEOUT_S, GONE_TIMEOUT_S, QUIET_HOLD_S, EnableError, QuietWatcher, \
        parse_env
    if mode not in USB_MODES:
        raise EnableError("enable-verify", f"no USB mode {mode!r}")
    if not operator_confirmed:
        raise EnableError("enable-unconfirmed")
    watcher_factory = watcher_factory or QuietWatcher
    now = now or time.time
    steps = []
    try:
        status, env_text = runner.run("fw_printenv", timeout=10.0)
    except Exception as e:                          # noqa: BLE001
        raise EnableError("enable-ssh", str(e)) from None
    if status != 0:
        raise EnableError("enable-ssh", env_text.strip()[:200])
    key = (ident.key or "unknown").replace(":", "-")
    folder = Path(data_dir) / "pluto-env"
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / f"{key}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now()))}.env"
    backup.write_text(env_text)
    steps.append(f"backed up to {backup}")
    try:
        status, out = runner.run(f"fw_setenv usb_ethernet_mode {mode}", timeout=10.0)
        if status != 0:
            raise EnableError("enable-ssh", f"fw_setenv usb_ethernet_mode: {out.strip()[:200]}")
        status, after = runner.run("fw_printenv", timeout=10.0)
    except EnableError:
        raise
    except Exception as e:                          # noqa: BLE001
        raise EnableError("enable-ssh", str(e)) from None
    if status != 0 or parse_env(after).get("usb_ethernet_mode") != mode:
        raise EnableError("enable-verify", f"usb_ethernet_mode={parse_env(after).get('usb_ethernet_mode')}")
    steps.append(f"fw_setenv usb_ethernet_mode {mode}, read back")
    watcher = watcher_factory(iio, ident.uri).start()
    try:
        runner.detach("sleep 1; reboot")
    except Exception as e:                          # noqa: BLE001
        raise EnableError("enable-ssh", f"reboot: {e}") from None
    watcher.armed()
    steps.append("reboot sent")
    watcher.join(GONE_TIMEOUT_S + BACK_TIMEOUT_S + QUIET_HOLD_S + 10)
    result = watcher.result
    result.backup, result.steps = backup, steps
    if watcher.error is not None:
        raise watcher.error
    return result


def _iio():
    importer = getattr(host, "import_iio", None)
    if importer is not None:
        return importer()
    from shack_packet.sdr.pluto import import_iio
    return import_iio()


class Tools:
    """The real Pluto, through libiio and SSH."""

    def __init__(self, data_dir: Path | None = None, platform: str | None = None):
        import sys
        self.data_dir = Path(data_dir) if data_dir else host.data_dir()
        self.platform = platform or sys.platform

    def runner(self, uri: str, password: str):
        from shack_pluto.guard import SshRunner
        return SshRunner.for_uri(uri, password or "analog")

    def _ssh(self, ident, password: str):
        return self.runner(ident.extra.get("address", ident.uri), password)

    def check(self, uri: str, password: str, channel_hz: int) -> CheckResult:
        from shack_pluto.ad9364 import needs_enable
        from shack_pluto.endpoint import EndpointError, SSH_PORT, parse
        from shack_pluto.identity import family_of, identify, mac_over_ssh, mqtt_answers, probe_over_ssh
        from shack_pluto.xo import XoStore
        iio = _iio()
        try:
            ep = parse(uri)
        except EndpointError:
            ep = None                               # a USB URI: libiio's own
        iio_uri = ep.iio_uri if ep is not None else uri
        ctx = iio.Context(iio_uri)
        if hasattr(ctx, "set_timeout"):
            ctx.set_timeout(IIO_TIMEOUT_MS)
        shared = ep is not None and ep.ssh_port != SSH_PORT     # forwarded: 1883 is not the Pluto's
        mqtt = ep is not None and not shared and mqtt_answers(ep.host)
        ident = identify(ctx, iio_uri, mqtt_answers=mqtt)
        ident.extra["address"] = uri
        runner = self.runner(uri, password) if ep is not None else None
        if runner is not None and (shared or ident.family in ("unknown", "adi")):
            # What SSH shows of the firmware: PlutoDVB2 behind the
            # controller, or one whose version string says nothing.
            probe = probe_over_ssh(runner)
            if probe is not None:
                ident.family = family_of(ident.fw_version, mqtt_answers=mqtt, probe=probe)
        if ident.serial is None and runner is not None:
            try:
                ident.mac = mac_over_ssh(runner)
            except Exception:                       # noqa: BLE001 - no MAC: no key, said by the window
                ident.mac = None
        reach = ident.covers(channel_hz)
        enable = ""
        if needs_enable(ident, channel_hz):
            enable = "offer" if ident.can_enable_ad9364 else "refused"
        xo = XoStore(self.data_dir).get(ident.key) if ident.key else None
        usb = usb_mode(runner) if runner is not None else None
        if shared:
            recommended = "rndis"                  # the controller is Linux
        elif is_usb_gadget_address(uri):
            recommended = recommended_usb_mode(self.platform)
        else:
            recommended = ""
        return CheckResult(ident, int(channel_hz), reach, enable, xo, usb, recommended)

    def enable(self, ident, password: str, *, operator_confirmed: bool):
        from shack_pluto.ad9364 import enable_ad9364
        return enable_ad9364(ident, self._ssh(ident, password), _iio(), self.data_dir,
                             operator_confirmed=operator_confirmed)

    def set_usb_mode(self, ident, password: str, mode: str, *, operator_confirmed: bool):
        return switch_usb_mode(ident, self._ssh(ident, password), _iio(), self.data_dir, mode,
                               operator_confirmed=operator_confirmed)

    def save_xo(self, key: str, ppm: float, how: str) -> int:
        from shack_pluto.xo import XoStore
        return XoStore(self.data_dir).put(key, ppm, how)

    def forget_xo(self, key: str) -> None:
        from shack_pluto.xo import XoStore
        XoStore(self.data_dir).forget(key)


def ppm_from(mode: str, expected_hz: float, measured_hz: float) -> float:
    """The crystal error from one measurement (shack_pluto.xo's two ways):
    "tx", this Pluto's own carrier sent at `expected_hz` and read at
    `measured_hz` on a calibrated receiver; "rx", a signal known to be at
    `expected_hz` that this Pluto showed at `measured_hz`."""
    from shack_pluto.xo import ppm_from_rx, ppm_from_tx
    if mode == "tx":
        return ppm_from_tx(measured_hz, expected_hz)
    return ppm_from_rx(measured_hz, expected_hz)
