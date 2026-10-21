#!/usr/bin/env python3
"""Photograph the Send dialog's time table (0.3.1) offscreen, for a 200 KB
JPEG sent to the simulated station G4SIMF:

    QT_QPA_PLATFORM=offscreen python tools/send-estimate-shot.py OUT.png [RADIO_KIND] [MODE]

The station runs on the simulator; RADIO_KIND (default "pluto") and MODE
(a Pluto's fast mode, default "g3ruh9600") are given to the estimate only,
in memory, so the picture shows which rows that radio greys out without
any radio being opened. No radio, sound device or network is used.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHACK_LOCALE", "en")
os.environ.setdefault("SHACK_PACKET_NO_PAD", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def photo(path: Path, target: int = 200_000) -> int:
    """A JPEG of about `target` bytes: a gradient with some grain, so it
    is a photograph's size rather than a flat colour's."""
    from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter
    rng = random.Random(4)
    image = QImage(1600, 1200, QImage.Format.Format_RGB32)
    p = QPainter(image)
    g = QLinearGradient(0, 0, 1600, 1200)
    g.setColorAt(0, QColor(40, 90, 150))
    g.setColorAt(1, QColor(210, 190, 120))
    p.fillRect(image.rect(), g)
    for _ in range(6000):
        p.fillRect(rng.randrange(1600), rng.randrange(1200), 6, 6, QColor(rng.randrange(256), rng.randrange(256),
                                                                          rng.randrange(256)))
    p.end()
    for q in range(95, 10, -5):
        image.save(str(path), "JPEG", q)
        if path.stat().st_size <= target * 1.1:
            break
    return path.stat().st_size


def main(argv) -> int:
    out = Path(argv[1])
    kind = argv[2] if len(argv) > 2 else "pluto"
    mode = argv[3] if len(argv) > 3 else "g3ruh9600"
    tmp = tempfile.mkdtemp(prefix="shack-packet-shot-", dir=Path.home() / "build")
    os.environ["SHACK_PACKET_CONF"] = os.path.join(tmp, "conf")
    os.environ["SHACK_PACKET_DATA"] = os.path.join(tmp, "data")

    from shack_packet_app import simfiles
    from shack_packet_app.controller import Controller
    from shack_packet_app.settings import Settings
    from shack_packet_app.simband import SimBand
    from shack_packet_app.ui import qtapp, snapshot
    from shack_packet_app.ui.window import MainWindow

    app = qtapp.make_app([argv[0]])
    s = Settings()
    s.station.callsign, s.station.locator, s.station.licence_class = "GM4BRI", "IO75RL", "full"
    s.tx.set_up = s.setup_done = True
    ctl = Controller(s, band_factory=lambda: SimBand(55.94, -3.21, first_beacon_s=(400.0, 500.0),
                                                          beacon_every_s=600))
    win = MainWindow(s, {}, controller=ctl, persist=False)
    win.resize(1280, 800)
    win.show()
    win.start(first_run_check=False)

    def pump(seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return until() if until else True

    pump(1)
    ctl.arm(5)
    sess = ctl.connect(simfiles.CALL)
    if not pump(30, lambda: sess.connected):
        print("not connected to the simulated station", file=sys.stderr)
        return 1
    ctl.disarm()
    jpeg = Path(tmp) / "shack-photo.jpg"
    size = photo(jpeg)
    ctl.settings.radio.kind = kind                   # the estimate's only: nothing is reopened
    ctl.settings.fast.mode = mode
    from shack_packet_app.ui.filesend import SendFileDialog
    dlg = SendFileDialog(win, sess.id, str(jpeg))
    dlg.preset.setCurrentIndex(dlg.preset.findData("original"))
    dlg.resize(820, dlg.sizeHint().height())
    dlg.show()
    pump(0.8)
    out.parent.mkdir(parents=True, exist_ok=True)
    snapshot.save_widget(dlg, str(out))
    print(f"{out}: {size} byte JPEG, {kind}, {mode}")
    for r in dlg.estimates.rows:
        print(f"  {r.mode:12} {'now' if r.current else '   '} {'ok ' if r.available else r.why:12} "
              f"{r.good_s:8.1f} s {r.retries_s:8.1f} s")
    dlg.reject()
    win.close()
    ctl.stop()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
