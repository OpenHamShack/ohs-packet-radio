# -*- mode: python -*-
# PyInstaller spec for "OpenHamShack Packet Radio.app", the macOS build.
# Read packaging/qt-app.spec first (the Linux AppImage's): this is its
# twin - the same stage, the same file walk for datas/hiddenimports - with
# only what a signed .app needs added: BUNDLE(), Info.plist, and the
# microphone/local-network usage strings a sandboxed-by-default macOS asks
# for even though this app never sends a microphone (a real radio's sound
# card is PyAudio's host API, which macOS treats the same as a microphone;
# IP-over-AX.25 and a Flex/network radio are TCP, which needs no entitlement
# at all - unlike NB's and HAM's Pluto, which does).
#
#   SHACK_STAGE=<stage> pyinstaller --distpath DIST --workpath WORK shack-packet.spec
#
# tools/build-macos.sh stages this file as app.spec (packaging/README.md),
# the same stage a Windows build is copied from, so one `shackpkg.py
# stage` on Linux serves both.
import json
import os
import sys
from pathlib import Path

stage = Path(os.environ["SHACK_STAGE"]).resolve()
src = stage / "src"
sys.path.insert(0, str(src))
app = json.loads((stage / "app.json").read_text())
RELEASE = app["version"]

NOT_SHIPPED = (".py", ".pyc", ".ts", ".pot")

datas, hidden = [], []
for pkg in sorted(p.name for p in src.iterdir() if p.is_dir()):
    for p in sorted((src / pkg).rglob("*")):
        if p.is_file() and p.suffix not in NOT_SHIPPED and "__pycache__" not in p.parts \
                and "_en_XA" not in p.name:
            datas.append((str(p), str(p.parent.relative_to(src))))
    for p in sorted((src / pkg).rglob("*.py")):
        parts = list(p.relative_to(src).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if "tests" not in parts:
            hidden.append(".".join(parts))
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

# The C libraries the Mac build carries (host.py says where and why a
# folder each) are NOT in here: as openhamshack-nb's Mac spec does with libiio,
# tools/build-macos.sh copies libiio/ and rtlsdr/ into
# Contents/Frameworks after PyInstaller and signs them with everything
# else. Given to PyInstaller as data they would land in Contents/Resources,
# where a Mach-O breaks the bundle's signature. Only ADI's iio.py (vendor
# py/, from the libiio source it was built from) goes on the import path
# here, and the licences beside it.
vendor = Path(os.environ["SHACK_VENDOR"]).resolve() if os.environ.get("SHACK_VENDOR") else None
pathex = [str(src)] + ([str(vendor / "py")] if vendor and (vendor / "py").is_dir() else [])
if vendor is not None:
    datas += [(str(p), "licences") for p in sorted((vendor / "licences").glob("*")) if p.is_file()]

a = Analysis(  # noqa: F821
    [str(stage / "launcher.py")],
    pathex=pathex,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "scipy", "matplotlib", "IPython", "webview", "gi"],
    noarchive=False,
)
# "Qt6Pdf" is the Linux/Windows .so/.dll name; macOS's framework drops the
# version number (Contents/Frameworks/QtPdf.framework), so the plain name
# is checked too (found when tools/build-macos.sh's on-disk check still
# caught it after this filter, 18/10/2026).
NO_PDF = ("qt6pdf", "qtpdf", "qpdf")
a.binaries = [b for b in a.binaries if not any(n in b[0].lower() for n in NO_PDF)]
a.datas = [d for d in a.datas if not any(n in d[0].lower() for n in NO_PDF)]
pyz = PYZ(a.pure)  # noqa: F821

# One executable: run from a Terminal (or an SSH session, which is how
# this is built) a windowed macOS program still has its stdout, so
# --self-test needs no console twin as the Windows build does.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app["executable"], console=False,  # noqa: F821
          debug=False, strip=False, upx=False, argv_emulation=False, target_arch="arm64",
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=app["executable"])  # noqa: F821
bundle = BUNDLE(  # noqa: F821
    coll,
    name=app["name"] + ".app",
    icon=str(stage / "icons" / "app.icns"),
    bundle_identifier=app["macos"]["bundle_id"],
    version=RELEASE,
    info_plist={
        "CFBundleDisplayName": app["name"],
        "CFBundleShortVersionString": RELEASE,
        "CFBundleVersion": RELEASE,
        "LSMinimumSystemVersion": app["macos"]["minimum_system"],
        # Without this macOS gives PyAudio's input stream silence, with no
        # prompt and no error, when an operator picks a sound-card radio
        # (the setup check's receive test would then just show no signal).
        "NSMicrophoneUsageDescription": "OpenHamShack Packet Radio reads your microphone input as the "
                                        "receiver audio when a sound-card radio is selected.",
    },
)
