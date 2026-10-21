"""Where this app keeps things on each operating system, answered here and
nowhere else (the habit openhamshack-nb's ohscore/host.py set for NB and Radio).

| | settings | data (mailbox, logs, diagnostics) |
|---|---|---|
| Linux | `$XDG_CONFIG_HOME/openhamshack-packet` (~/.config/...) | `$XDG_DATA_HOME/openhamshack-packet` (~/.local/share/...) |
| macOS | `~/Library/Application Support/openhamshack-packet` | the same folder, `data/` inside |
| Windows | `%APPDATA%\\openhamshack-packet` | the same folder, `data\\` inside |

`SHACK_PACKET_CONF` and `SHACK_PACKET_DATA` override either: the tests give
every test its own folders, and a tester can run a second copy beside the
first without the two sharing a mailbox.

The folder is named after the app id rather than the product name, and the
product name is never used for a path: a rename of the product (the family
has had one already) must not move anybody's settings out from under them.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

APP_DIR = "openhamshack-packet"

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"


def config_dir() -> Path:
    override = os.environ.get("SHACK_PACKET_CONF", "").strip()
    if override:
        return Path(override)
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR
    if MACOS:
        return Path.home() / "Library" / "Application Support" / APP_DIR
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_DIR


def data_dir() -> Path:
    override = os.environ.get("SHACK_PACKET_DATA", "").strip()
    if override:
        return Path(override)
    if WINDOWS or MACOS:
        return config_dir() / "data"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_DIR


def log_dir() -> Path:
    return data_dir() / "logs"


def system_summary() -> dict:
    """What a diagnostics bundle says about the machine: enough to tell a
    Windows 10 laptop from a Mac without naming the person or the host."""
    import platform
    return {"os": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False))}


# ---------------------------------------------------------------- libraries a bundle carries
# The Windows and macOS builds carry two C libraries of their own, each in
# a folder named for it under the bundle's unpack folder (PyInstaller's
# sys._MEIPASS: _internal\ on Windows, Contents/Frameworks on a Mac .app):
#   libiio/  libiio 0.26 (ADI's Windows release: libiio.dll, libxml2.dll,
#            libusb-1.0.dll, libserialport-0.dll; on macOS libiio.dylib
#            built from source, network backend only, as NB's), for a Pluto
#   rtlsdr/  librtlsdr (rtl-sdr-blog's Windows release: rtlsdr.dll, which
#            has libusb linked in; on macOS librtlsdr.dylib and
#            libusb-1.0.dylib built from source), for an RTL-SDR dongle
#   hackrf/  libhackrf 2026.01.3 (from 0.2.0; on Windows conda-forge's
#            hackrf-0.dll with the libusb-1.0.dll and libwinpthread-1.dll
#            it imports, OpenHamShack ATV's pins; on macOS libhackrf.dylib
#            and its own libusb-1.0.dylib built from source), for a HackRF
# A folder each, not beside the .exe: nothing else that searches the
# program's folder can pick up our libusb or libxml2. Linux uses the
# distribution's (the .deb Recommends them). tools/build-windows.ps1 and
# build-macos.sh fill the folders; --library-check proves them.

def bundle_dir() -> Path | None:
    """PyInstaller's unpack folder in a frozen build, else None."""
    if not getattr(sys, "frozen", False):
        return None
    d = getattr(sys, "_MEIPASS", None)
    return Path(d) if d else Path(sys.executable).resolve().parent


def bundled_dir(name: str) -> Path | None:
    """The bundle's folder for one library (`libiio`, `rtlsdr`), or None
    (Linux, a run from source, or a bundle without it)."""
    b = bundle_dir()
    if b is None or not (WINDOWS or MACOS):
        return None
    d = b / name
    return d if d.is_dir() else None


@contextlib.contextmanager
def bundled_libiio():
    """`import iio` inside this block loads the bundled libiio.

    openhamshack-nb ohscore/host.py's bundled_libiio, for the same trap: ADI's
    iio.py finds its library with ctypes.util.find_library, which on
    Windows walks PATH (System32 may hold ADI's libiio 1.0, which these
    0.2x bindings cannot use) and on macOS reads DYLD_LIBRARY_PATH first
    (Homebrew's may be 1.0). So the bundle's folder goes first in that
    variable for the length of the import only - left there, our libusb
    and libxml2 would reach every child process too - and iio.py then
    loads ours by its full path. Windows resolves libiio.dll's own imports
    from the folder it was loaded from, so the whole set is ours.
    Yields the folder, or None (nothing changed: the system's is used)."""
    d = bundled_dir("libiio")
    if d is None:
        yield None
        return
    var = "DYLD_LIBRARY_PATH" if MACOS else "PATH"
    old = os.environ.get(var)
    os.environ[var] = str(d) + (os.pathsep + old if old else "")
    try:
        yield d
    finally:
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old


def import_iio():
    """The libiio bindings, loaded from the bundle when there is one. Once
    imported here, shack_packet.sdr.pluto's own `import iio` gets this
    module. Raises ImportError or OSError as a plain import would."""
    with bundled_libiio():
        import iio
    return iio


def rtlsdr_search() -> tuple:
    """Where shack_packet.sdr.rtlsdr looks first for librtlsdr: the
    bundle's folder, if this build carries one."""
    d = bundled_dir("rtlsdr")
    return (d,) if d is not None else ()


#: libhackrf's file in the bundle's hackrf/ folder: conda-forge names the
#: Windows DLL hackrf-0.dll, which shack_packet.radios.hackrf's own list of
#: names does not include, so the bundle's copy is given by its full path.
HACKRF_FILES = {"win32": "hackrf-0.dll", "darwin": "libhackrf.dylib"}


def hackrf_lib() -> Path | None:
    """The bundle's libhackrf, by full path, or None (Linux, a run from
    source, or a bundle without it): then the system's is looked for."""
    d = bundled_dir("hackrf")
    name = HACKRF_FILES.get("win32" if WINDOWS else sys.platform)
    if d is None or name is None or not (d / name).is_file():
        return None
    return d / name
