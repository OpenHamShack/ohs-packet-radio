"""OpenHamShack Packet Radio's store form: the controller's BBS, its
always-on conference room and its mail store, run headless in a container
on the OpenHamShack controller on a radio the controller's broker lends it
(docs/BBS-DESIGN.md).

    python3 -m shack_packet_box          # what the store image runs

The desktop app never imports this package, and this package never imports
the desktop window: one app codebase, two forms (REPO-LAYOUT §4.1), and no
server inside the desktop app.
"""
from __future__ import annotations

import os

# One OpenBLAS thread before numpy loads (GUIDE §5, as the desktop app does).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
