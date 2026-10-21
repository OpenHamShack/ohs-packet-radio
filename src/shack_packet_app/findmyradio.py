"""Find my radio (0.3.0): everything this computer can see that could be
the station's radio, as cards to pick from, and a read-only test of the
one picked. ui/findradio.py draws it at the top of the setup assistant's
radio page and in Settings › Radio.

Asked for by Brian on 20/10/2026 ("detect, then confirm"), from the
GM0OTB set-up: Ray's IC-9700 was a CP210x on COM4 with his own renamed
"IC9700 Speakers (USB Audio CODEC)" beside other radios' bridges and a
second CODEC, and every field was typed by hand from a phone call.

Where it looks, each bounded and each only reading:

- **Serial ports**: ohs-libs shack_rig.findradio (CI-V "who are you" at
  115200/19200/9600 to each Icom address, Yaesu's ID;, then Kenwood's TY
  and only after a Kenwood answer its ID), skipping ports in use; then the
  radio's sound devices by name, else by USB hub, else ask. A TM-V71 has
  no sound device of its own: the interface on its DATA jack (a Digirig, a
  SignaLink) on the same hub is paired, else asked for.
- **Icom on the LAN**: discover.find_icoms - Icom's "are you there"
  (no login, so no password and the radio is not taken from anybody) to
  the broadcast address, the address typed or remembered, and when asked
  for, every address of this computer's /24.
- **Plutos**: plutofind (0.2.3's Find).
- **The paired controller's radios**: its broker list (ohsclient), so a
  Pluto there can be picked as pluto://; others are shown, not offered.

A pick fills every field (`apply`); what the operator still has to set
on the radio is `guidance`, shown only when it applies: the LAN user's
case-sensitive name and password, CI-V USB baud 115200 when the radio
answered slower, the 9700's unreliable USB transmit audio (use LAN to
transmit), and, when a LAN test is refused, Unlink from [REMOTE].

`test_connection` is the "Test the connection" step: it opens the picked
radio long enough to read its frequency and mode, and closes it. It never
keys and changes nothing on the radio.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from . import discover

log = logging.getLogger(__name__)

#: Radio kinds this app drives (settings.RADIO_KINDS' radios).
SUPPORTED = ("ic-9700", "ic-705", "ftx-1", "tm-v71")
#: The /24 sweep: one 16-byte packet to each address, answers gathered for this long.
SUBNET_WAIT_S = 2.0
TEST_TIMEOUT_S = 15.0


@dataclass
class Candidate:
    kind: str                      # settings.radio.kind, "pluto", or the model key when not supported
    title: str
    how: str                       # usb | lan | pluto | controller
    detail: str = ""
    #: Settings changes: {"radio": {...}, "sdr_radio": {...}}.
    fields: dict = field(default_factory=dict)
    guidance: list = field(default_factory=list)
    supported: bool = True
    #: The radio's sound devices are not known: the operator chooses.
    ask_audio: bool = False


@dataclass
class Search:
    candidates: list = field(default_factory=list)
    notes: list = field(default_factory=list)      # (where, text) for the "what was looked at" fold
    seconds: float = 0.0


def search(settings, *, hosts=(), subnet: bool = False, controller=None, sound_backend=None,
           scan=None, find_icoms=None, find_plutos=None, sound_devices=None,
           cancel: threading.Event | None = None) -> Search:
    """Look everywhere (module docstring); each part may be replaced (tests)."""
    from shack_rig import findradio
    started = time.monotonic()
    out = Search()
    scan = scan or findradio.scan
    find_icoms = find_icoms or discover.find_icoms
    sound_devices = sound_devices or (lambda: discover.sound_devices(sound_backend))
    results: dict = {}

    def serial():
        results["serial"] = scan(cancel=cancel)

    def lan():
        targets = [h for h in hosts if h]
        if settings.radio.host:
            targets.append(settings.radio.host)
        if subnet:
            targets += subnet_hosts()
        results["lan"] = find_icoms(sorted(set(targets)), timeout_s=SUBNET_WAIT_S if subnet else 1.5)

    def plutos():
        if find_plutos is not None:
            results["pluto"] = find_plutos()
            return
        from . import plutofind
        results["pluto"] = plutofind.find()

    def ctl():
        results["controller"] = controller.radios() if controller is not None else []
    jobs = [("serial", serial), ("lan", lan), ("pluto", plutos), ("controller", ctl)]
    threads = []
    for name, fn in jobs:
        def run(fn=fn, name=name):
            try:
                fn()
            except Exception as e:                  # noqa: BLE001 - one place failing never ends the search
                log.warning("find my radio: %s: %s", name, e)
                out.notes.append((name, f"failed: {e}"))
        t = threading.Thread(target=run, name=f"findmyradio-{name}", daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(30)
    try:
        devices = sound_devices()
    except Exception:                               # noqa: BLE001 - no PortAudio: nothing to match
        devices = []
    ins = [d["name"] for d in devices if d.get("inputs")]
    outs = [d["name"] for d in devices if d.get("outputs")]

    scanned = results.get("serial")
    usb_found = list(scanned.found) if scanned else []
    for f in usb_found:
        findradio.match_audio(f, ins, outs, others=usb_found)
        out.candidates.append(_usb(f))
    if scanned:
        for n in scanned.ports:
            out.notes.append((n.port, f"{n.description} - {n.outcome}" + (f": {n.detail}" if n.detail else "")))
    for r in results.get("lan") or []:
        out.candidates.append(_lan(r, settings))
        out.notes.append((r.host, "an Icom answered on the network"))
    pl = results.get("pluto")
    for p in (pl.found if pl else []):
        out.candidates.append(Candidate("pluto", f"Pluto at {p.uri}", "pluto", p.description,
                                        {"radio": {"kind": "pluto"}, "sdr_radio": {"pluto_uri": p.uri}}))
    for note in (pl.notes if pl else []):
        out.notes.append(("pluto", note))
    for r in results.get("controller") or []:
        out.candidates.append(_controller(r, controller))
    # A 9700 found both ways: the network first (it transmits reliably).
    order = {"lan": 0, "usb": 1, "pluto": 2, "controller": 3}
    out.candidates.sort(key=lambda c: (not c.supported, order.get(c.how, 9)))
    out.seconds = round(time.monotonic() - started, 1)
    return out


def _usb(f) -> Candidate:
    supported = f.kind in SUPPORTED
    if f.kind in ("ic-9700", "ic-705"):
        fields = {"radio": {"kind": f.kind, "transport": "usb", "civ_device": f.port, "civ_baud": f.baud,
                            "audio_in": f.audio_in, "audio_out": f.audio_out}}
    elif f.kind == "ftx-1":
        fields = {"radio": {"kind": "ftx-1", "cat_device": f.port, "audio_in": f.audio_in,
                            "audio_out": f.audio_out, "ptt": "cat"}}
    elif f.kind == "tm-v71":
        # Its PC port, at the speed it answered; the interface's audio as
        # matched. PTT is the interface's and nothing on the port says how it
        # is wired, so RTS (a Digirig's) is the starting choice and the port
        # is asked for: the guidance says so.
        fields = {"radio": {"kind": "tm-v71", "cat_device": f.port, "cat_baud": f.baud, "audio_in": f.audio_in,
                            "audio_out": f.audio_out, "ptt": "rts"}}
    else:
        fields = {}
    guidance = []
    if f.kind == "tm-v71":
        guidance += ["tmv71-ptt", "tmv71-band", "tmv71-9600"]
    if f.kind.startswith("ic-") and f.baud != 115200:
        guidance.append("civ-baud")
    if f.kind == "ic-9700":
        guidance.append("9700-usb-tx")
    if not supported:
        guidance.append("unsupported")
    return Candidate(f.kind, f"{f.name} on {f.port} (USB)", "usb", f.evidence, fields, guidance, supported,
                     ask_audio=supported and not (f.audio_in and f.audio_out))


def _lan(r, settings) -> Candidate:
    # "Are you there" does not say which model; the one already set, else
    # the 9700 (the Test reads what the radio calls itself).
    kind = settings.radio.kind if settings.radio.kind in ("ic-9700", "ic-705") else "ic-9700"
    fields = {"radio": {"kind": kind, "transport": "lan", "host": r.host, "port": r.port}}
    return Candidate(kind, f"Icom on the network at {r.host}", "lan",
                     f"answered Icom's \"are you there\" on UDP {r.port}", fields, ["lan-user"])


def _controller(r, client) -> Candidate:
    is_pluto = "pluto" in (r.cls or "").lower()
    title = f"{r.label} on the controller"
    if is_pluto and client is not None:
        from urllib.parse import urlparse
        host = urlparse(client.settings.url or "").hostname or "host"
        return Candidate("pluto", title, "controller", r.state if hasattr(r, "state") else "",
                         {"radio": {"kind": "pluto"},
                          "sdr_radio": {"pluto_uri": f"pluto://{host}?iiod=30431&ssh=30422"}},
                         ["controller-held"] if r.blocks else [], supported=not r.blocks)
    return Candidate(r.cls or "controller", title, "controller", "", {}, ["controller-only"], supported=False)


def apply(settings, cand: Candidate):
    """A copy of `settings` with the candidate's fields filled in."""
    new = settings.copy()
    for section, values in cand.fields.items():
        target = getattr(new, section)
        for k, v in values.items():
            setattr(target, k, v)
    return new


# -- the /24 ----------------------------------------------------------------------------

def local_ipv4() -> str | None:
    """This computer's address on its default route. A UDP socket's
    connect() sends nothing; it only picks the route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def subnet_hosts(addr: str | None = None) -> list[str]:
    """Every other address of this computer's /24 (private networks only:
    never a sweep of somebody's public block)."""
    addr = addr or local_ipv4()
    if not addr:
        return []
    ip = ipaddress.ip_address(addr)
    if not ip.is_private or ip.is_loopback:
        return []
    net = ipaddress.ip_network(f"{addr}/24", strict=False)
    return [str(h) for h in net.hosts() if str(h) != addr]


# -- Test the connection ------------------------------------------------------------------

@dataclass
class TestResult:
    ok: bool
    frequency_hz: int | None = None
    mode: str = ""
    radio: str = ""
    error: str = ""
    guidance: list = field(default_factory=list)


def test_connection(settings, secrets: dict, *, opener=None, make_rig=None) -> TestResult:
    """Open the radio in `settings` just long enough to read its frequency
    and mode (module docstring). Never keys; changes nothing."""
    r = settings.radio
    try:
        if r.kind in ("ic-9700", "ic-705") and r.transport == "usb":
            return _test_civ(r.civ_device, r.civ_baud or 115200, r.kind, opener)
        if r.kind in ("ic-9700", "ic-705"):
            return _test_lan(r, secrets.get("icom_password", ""), make_rig)
        if r.kind == "ftx-1":
            return _test_cat(r.cat_device, opener)
        if r.kind == "tm-v71":
            return _test_kenwood(r.cat_device, int(r.cat_baud or 9600), opener)
    except Exception as e:                          # noqa: BLE001 - said, never raised into the window
        return TestResult(False, error=str(e))
    return TestResult(False, error="nothing to test for this radio here")


def _test_civ(device: str, baud: int, kind: str, opener=None) -> TestResult:
    from shack_rig.icom import civ
    from shack_rig.icom import models as icom_models
    from shack_rig.serialio import SerialPort
    addr = icom_models.MODELS[kind].civ_address
    port = (opener or SerialPort)(device, baud)
    port.open()
    try:
        hz = mode = None
        for cmd in (civ.read_freq(), civ.read_mode()):
            got = port.exchange(civ.frame(cmd, to=addr, frm=civ.DEFAULT_CTRL_ADDR), 0.3)
            frames, _ = civ.split_frames(got)
            for f in frames:
                try:
                    to, frm, payload = civ.parse(f)
                except civ.CivError:
                    continue
                if frm != addr:
                    continue
                if payload[:1] in (b"\x03", b"\x00") and len(payload) >= 6:
                    hz = civ.decode_freq(payload[1:6])
                elif payload[:1] in (b"\x04", b"\x01") and len(payload) >= 2:
                    mode = icom_models.ALL_MODES.get(payload[1], f"0x{payload[1]:02X}")
    finally:
        port.close()
    if hz is None:
        return TestResult(False, error=f"no answer on {device} at {baud} baud", guidance=["civ-baud"])
    return TestResult(True, frequency_hz=hz, mode=mode or "")


def _test_cat(device: str, opener=None) -> TestResult:
    from shack_rig.serialio import SerialPort
    from shack_rig.yaesu import cat
    port = (opener or SerialPort)(device, 38400)
    port.open()
    try:
        fa = port.exchange(b"FA;", 0.3).decode("ascii", "replace").strip()
        md = port.exchange(b"MD0;", 0.3).decode("ascii", "replace").strip()
    finally:
        port.close()
    try:
        hz = cat.parse_freq(fa.rstrip(";"))
    except Exception:                               # noqa: BLE001 - said below
        return TestResult(False, error=f"no frequency from {device}")
    return TestResult(True, frequency_hz=hz, mode=md.rstrip(";"))


def _test_kenwood(device: str, baud: int, opener=None) -> TestResult:
    """A TM-V71's PC port: ID, the DATA jack's band from MU, and that
    band's FO. Reads only: never TX, never a write."""
    from shack_rig.kenwood import cat
    from shack_rig.serialio import SerialPort
    port = (opener or SerialPort)(device, baud)
    port.open()
    try:
        port.exchange(b"\r", 0.1)
        rid = port.exchange(cat.read_id().encode(), 0.4).decode("ascii", "replace")
        menu = port.exchange(cat.read_menu().encode(), 0.5).decode("ascii", "replace")
        answers = {}
        for text in (rid, menu):
            for line in cat.split_answers(text)[0]:
                answers[line.split(" ", 1)[0]] = line
        try:
            name = cat.parse_id(answers.get("ID", ""))
        except cat.CatError:
            return TestResult(False, error=f"no Kenwood answer on {device} at {baud} baud",
                              guidance=["tmv71-baud"])
        band = 0
        try:
            band = {0: 0, 1: 1, 2: 1, 3: 0}.get(cat.parse_menu(answers.get("MU", "")).data_band, 0)
        except cat.CatError:
            pass
        fo = port.exchange(cat.read_vfo(band).encode(), 0.5).decode("ascii", "replace")
    finally:
        port.close()
    for line in cat.split_answers(fo)[0]:
        try:
            vfo = cat.parse_vfo(line)
        except cat.CatError:
            continue
        return TestResult(True, frequency_hz=vfo.freq, mode=cat.MODES.get(vfo.mode, ""),
                          radio=f"{name}, band {'AB'[band]}")
    return TestResult(False, error=f"{name} answered but not its frequency", radio=name)


def _test_lan(r, password: str, make_rig=None) -> TestResult:
    if make_rig is None:
        from shack_rig.icom.build import make_rig
    rig = make_rig({"model": r.kind, "transport": "lan", "host": r.host, "port": r.port, "username": r.username,
                    "client_name": "OHS-Packet-test"}, password=password, tx_enabled=False)

    async def go():
        await rig.start()
        await rig.connect()
        end = time.monotonic() + TEST_TIMEOUT_S
        try:
            while time.monotonic() < end:
                st = rig.state
                if st.get("connected") and st.get("freq"):
                    return TestResult(True, frequency_hz=st["freq"], mode=st.get("mode_label") or st.get("mode") or "",
                                      radio=st.get("radio") or "")
                if st.get("link_state") == "failed":
                    break
                await asyncio.sleep(0.1)
            err = rig.state.get("error") or "the radio did not answer"
            return TestResult(False, error=err, guidance=["lan-user", "unlink-remote"])
        finally:
            await rig.stop()
    return asyncio.run(go())
