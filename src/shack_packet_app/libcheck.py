"""`shack-packet --library-check`: every optional library the program can
use, loaded and made to do one small thing, so a build proves its bundle
carries them (GUIDE §5: "a build check that names a tool must actually run
it"; 0.1.x's sdrmon table claimed libiio and librtlsdr were bundled, and
neither was).

| name | what uses it | the proof |
|---|---|---|
| cryptography | the update feed's Ed25519 signature | RFC 8032's first test vector verifies |
| zeroconf | finding controllers | imported, version read (no socket is opened) |
| hidapi | the AJAZZ AKP03E control pad | the C library's device list (read only) |
| Pillow | the pad's key pictures | a PNG drawn in memory |
| librtlsdr | an RTL-SDR dongle | loaded with every function typed; the dongle count read |
| libiio | a Pluto | the bindings' version, and where the library was loaded from |
| libhackrf | a HackRF One | loaded with every function typed; its version, and libusb started and stopped |

The tools' build scripts run it on the frozen program (Windows: the
console twin; macOS: the .app's executable) and stop the build unless
every line says ok. Nothing here opens a radio, a socket or a device.
"""
from __future__ import annotations

import io


def _cryptography() -> str:
    from . import update
    said = update.describe_verifier()
    if "self-test passed" not in said:
        raise RuntimeError(said)
    return said


def _zeroconf() -> str:
    import zeroconf
    from zeroconf import ServiceBrowser, Zeroconf  # noqa: F401 - what shack_client.discovery imports
    return f"zeroconf {zeroconf.__version__}"


def _hidapi() -> str:
    import hid
    n = len(hid.enumerate())
    return f"hidapi {getattr(hid, '__version__', '')}".strip() + f", {n} HID device(s) listed"


def _pillow() -> str:
    import PIL
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (8, 8), "black")
    ImageDraw.Draw(img).rectangle((2, 2, 5, 5), fill="red")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return f"Pillow {PIL.__version__}, a {len(buf.getvalue())}-byte PNG drawn"


def _librtlsdr() -> str:
    from shack_packet.sdr import rtlsdr
    from . import host
    lib = rtlsdr.load(search=host.rtlsdr_search())
    return f"{lib._name}, {lib.rtlsdr_get_device_count()} dongle(s) present"


def _libiio() -> str:
    import os
    from . import host
    iio = host.import_iio()
    where = os.path.dirname(getattr(getattr(iio, "_lib", None), "_name", "") or "") or "?"
    return f"libiio {'.'.join(map(str, iio.version[:2]))} from {where}"


def _libhackrf() -> str:
    # Never hackrf_device_list: it opens each HackRF to read its serial,
    # and this check must not touch a radio. hackrf_init only starts libusb.
    import ctypes
    from shack_packet.radios import hackrf
    from . import host
    lib = hackrf.load(lib_path=host.hackrf_lib())
    fn = getattr(lib, "hackrf_library_version", None)
    if fn is not None:
        fn.restype = ctypes.c_char_p
    version = fn().decode("ascii", "replace") if fn is not None else "?"
    if lib.hackrf_init() != 0:
        raise RuntimeError(f"{lib._name}: hackrf_init failed")
    lib.hackrf_exit()
    return f"{lib._name}, libhackrf {version}"


CHECKS = (("cryptography", _cryptography), ("zeroconf", _zeroconf), ("hidapi", _hidapi), ("Pillow", _pillow),
          ("librtlsdr", _librtlsdr), ("libiio", _libiio), ("libhackrf", _libhackrf))


def check() -> list[tuple[str, bool, str]]:
    out = []
    for name, fn in CHECKS:
        try:
            out.append((name, True, fn()))
        except Exception as e:                      # noqa: BLE001 - every failure is a line, not a crash
            out.append((name, False, f"{type(e).__name__}: {e}"))
    return out


def run(out) -> int:
    results = check()
    for name, ok, detail in results:
        print(f"{name}: {'ok' if ok else 'MISSING'} ({detail})", file=out)
    good = all(ok for _n, ok, _d in results)
    print("library-check: " + ("PASS" if good else "FAIL"), file=out)
    return 0 if good else 1
