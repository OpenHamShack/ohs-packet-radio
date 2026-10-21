# -*- mode: python -*-
# PyInstaller spec for OpenHamShack Packet Radio, through ohs-libs' desktop
# packaging kit (shack-app.toml [pyinstaller] spec): the kit's own spec is
# for web-UI apps and leaves Qt out. Modelled on openhamshack-nb's
# packaging/appimage/qt-app.spec.
#
#   SHACK_STAGE=<stage> pyinstaller --distpath DIST --workpath WORK qt-app.spec
#
# Everything comes from the kit's stage: src/ holds shack_packet_app and
# the ohs-libs packages it runs on (shack_packet, shack_rig, shack_bandplan,
# shack_i18n), app.json names the program, launcher.py is the entry.
#
# One folder, never one file (the kit's spec says why). Every data file
# the packages carry goes in - the presets, the compiled catalog, the
# window icon, the VERSION the app reads beside itself, the SDR monitor's
# channel list - and every module is named, because
# several are imported only when the operator picks a radio (links.py:
# IcomLink, YaesuLink, SoundcardLink) or opens a dialog, and a module
# missing from a frozen build is otherwise found on a tester's PC.
# Running `--self-test` on the frozen program is what proves the bundle
# whole (HANDOVER.md has the command).
#
# On Windows (tools/build-windows.ps1 stages this file as app.spec too,
# the same way the AppImage builder does) the windowed .exe has no
# stdout at all, so --self-test's PASS/FAIL would be invisible. A second,
# console-mode .exe of the same program is built alongside it there, as
# openhamshack-nb's and openhamshack-datv's Windows builds do, and only there: a
# Linux console=False build still writes to the terminal that started it.
import json
import os
import sys
from pathlib import Path

stage = Path(os.environ["SHACK_STAGE"]).resolve()
src = stage / "src"
sys.path.insert(0, str(src))
app = json.loads((stage / "app.json").read_text())

# Translation sources and templates are a translator's, never read at run
# time; en_XA is the developer's pseudo-locale.
NOT_SHIPPED = (".py", ".pyc", ".ts", ".pot")

datas, hidden = [], []
for pkg in sorted(p.name for p in src.iterdir() if p.is_dir()):
    for p in sorted((src / pkg).rglob("*")):
        if p.is_file() and p.suffix not in NOT_SHIPPED and "__pycache__" not in p.parts \
                and "_en_XA" not in p.name:
            datas.append((str(p), str(p.parent.relative_to(src))))
    # Named from the files rather than by importing them (collect_submodules
    # does): a module that fails to import in the build container is
    # silently left out, which is how openhamshack-nb's first AppImage lost one.
    for p in sorted((src / pkg).rglob("*.py")):
        parts = list(p.relative_to(src).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if "tests" not in parts:
            hidden.append(".".join(parts))
# A real radio's sound card and serial port, imported inside functions.
hidden += ["pyaudio", "serial", "serial.tools.list_ports"]
# From 0.2, each imported only when used, so named here: the update feed's
# signature check, finding controllers, the control pad (hidapi as "hid",
# and Pillow, no longer excluded - shack_pad swallows a missing one by
# design, so the pad would just do nothing), and ADI's libiio bindings
# (iio.py, from the vendor folder below). `--library-check` on the frozen
# program proves every one loads (libcheck.py).
hidden += ["cryptography.hazmat.primitives.asymmetric.ed25519", "zeroconf", "hid", "PIL.Image", "PIL.ImageDraw",
           "PIL.ImageFont", "iio"]
# Imported only when a Pluto is the radio (links.make_sdr_link): the
# Pluto's guard and the AD9364 enable log in over SSH.
hidden += ["paramiko"]

# The C libraries a Windows or Mac build carries (host.py says where and
# why a folder each), which tools/build-windows.ps1 / build-macos.sh fetch
# or build into $SHACK_VENDOR: libiio/, rtlsdr/ and hackrf/ go into the bundle as
# folders of those names, py/iio.py (ADI's binding, unmodified) onto the
# import path, licences/ beside them. A Linux AppImage carries no C
# library - it loads the system's libiio.so.0 - but it does need the
# binding, which no wheel provides: tools/build-linux.sh puts py/iio.py
# and its licence in the stage's vendor/ folder, read here when
# $SHACK_VENDOR is not set (the kit's container sees only the stage). Until
# 0.2.0 the AppImage had no iio at all, and a Pluto failed there with
# missing-library while every other build had it.
vendor = Path(os.environ["SHACK_VENDOR"]).resolve() if os.environ.get("SHACK_VENDOR") else None
if vendor is None and (stage / "vendor").is_dir():
    vendor = (stage / "vendor").resolve()
pathex = [str(src)] + ([str(vendor / "py")] if vendor and (vendor / "py").is_dir() else [])
if vendor is not None:
    for sub in ("libiio", "rtlsdr", "hackrf", "licences"):
        datas += [(str(p), sub) for p in sorted((vendor / sub).glob("*")) if p.is_file()]

a = Analysis(  # noqa: F821
    [str(stage / "launcher.py")],
    pathex=pathex,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "scipy", "matplotlib", "IPython", "webview", "gi"],
    noarchive=False,
)
# No Qt PDF: 15 MB nothing imports (openhamshack-nb's bundle_prune.py found it).
a.binaries = [b for b in a.binaries if "Qt6Pdf" not in b[0] and "qpdf" not in b[0].lower()]
a.datas = [d for d in a.datas if "Qt6Pdf" not in d[0] and "qpdf" not in d[0].lower()]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app["executable"],  # noqa: F821
          console=False, upx=False)
if sys.platform == "win32":
    console_exe = EXE(pyz, a.scripts, [], exclude_binaries=True,  # noqa: F821
                       name=app["executable"] + "-console", console=True, upx=False)
    coll = COLLECT(exe, console_exe, a.binaries, a.datas, name=app["executable"], upx=False)  # noqa: F821
else:
    coll = COLLECT(exe, a.binaries, a.datas, name=app["executable"], upx=False)  # noqa: F821
