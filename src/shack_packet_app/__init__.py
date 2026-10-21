"""OpenHamShack Packet Radio: the desktop app (Linux, Windows, macOS).

APRS (heard list, map, messages, beacons, an optional digipeater), AX.25
connected-mode chat to stations and nodes, a personal mailbox, IP over
AX.25 with in-app tools, and a receive-only SDR monitor - one native PyQt6
window over ohs-libs' packet engine (shack_packet) and rig library
(shack_rig). No servers: the app is a client of everything it talks to.

The import package is `shack_packet_app` because the library owns
`shack_packet` (REPO-LAYOUT §5's `shack_<name>` with the app's own name).
"""
from __future__ import annotations

import os
from pathlib import Path

# One OpenBLAS thread, set before anything imports numpy (it reads this
# once, at load). The receivers' FX.25 tag search is a small matrix product
# per slicer per block; with OpenBLAS's default threads, its idle threads
# spin after every one, and at 38 400 baud the process used 329 ms of CPU a
# second of IQ instead of about 116 (shack, 18/10/2026; ohs-libs
# shack_packet README "The fast modes"). The work is far too small to gain
# from threads, and a laptop receiving several modes at once (link
# adaptation, the path test) needs that CPU. setdefault: an operator who
# sets it for a reason keeps theirs.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def _version() -> str:
    """The packaged builds carry VERSION inside the package (the stage puts
    it there); a source checkout has it at the repository's root."""
    here = Path(__file__).resolve().parent
    for p in (here / "VERSION", here.parent.parent / "VERSION"):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return "0.0.0"


__version__ = _version()

APP_ID = "com.openhamshack.packet"
EXECUTABLE = "shack-packet"

#: The self-update feed and the app name its manifest gives (Brian's D7,
#: 18/10/2026). Baked into every build from 0.2.0 and never moved: a copy
#: polls exactly this URL and accepts exactly this name for the rest of
#: its life, so a move strands every installed copy (GUIDE §6, as NB's
#: brian-henderson.co.uk feeds did). `SHACK_PACKET_UPDATE_URL` points a
#: test build elsewhere; nothing else changes them.
UPDATE_FEED = "https://openhamshack.com/desktop/packet/update.json"
UPDATE_APP = "packet"
#: The .deb's package name (the kit's [app] executable): the .deb installs
#: under /usr/lib/<package>, which is how an update knows it was installed
#: from one, and the name its User-Agent gives.
PACKAGE = "shack-packet"
