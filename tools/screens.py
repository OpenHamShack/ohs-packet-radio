#!/usr/bin/env python3
"""Photograph every screen of the app offscreen, on the simulator and the
synthetic SDR band, for the tester guide and for review:

    QT_QPA_PLATFORM=offscreen python tools/screens.py [OUT_DIR]

OUT_DIR defaults to ~/build/shack-packet/screens. The station is a test
one (G0TST) in its own temporary folders; it chats to the simulated node,
messages the simulated station, has a mailbox message waiting and runs the
SDR monitor for long enough for the lists to fill (about 40 s). Each
picture is the window's own grab (ui/snapshot.py): nothing else on the
desktop can appear in it. No radio, sound device or network is used.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHACK_LOCALE", "en")
os.environ.setdefault("SHACK_PACKET_NO_PAD", "1")          # never take over a pad on the desk
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main(argv) -> int:
    out = Path(argv[1] if len(argv) > 1 else Path.home() / "build" / "shack-packet" / "screens")
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):                  # a renumbered page must not leave its old picture
        old.unlink()
    tmp = tempfile.mkdtemp(prefix="shack-packet-screens-", dir=Path.home() / "build")
    os.environ["SHACK_PACKET_CONF"] = os.path.join(tmp, "conf")
    os.environ["SHACK_PACKET_DATA"] = os.path.join(tmp, "data")

    from shack_packet.mailbox import MemoryStore
    from shack_packet_app.controller import Controller
    from shack_packet_app.settings import Settings
    from shack_packet_app.simband import FIXED, NODE, SimBand
    from shack_packet_app.ui import qtapp, snapshot
    from shack_packet_app.ui.window import PAGES, MainWindow

    app = qtapp.make_app([argv[0]])
    s = Settings()
    s.station.callsign, s.station.locator, s.station.licence_class = "G0TST", "IO85JW", "full"
    s.tx.set_up = s.setup_done = True
    s.sdr.source = "synthetic"
    s.ip.enabled, s.ip.address, s.ip.network = True, "44.131.1.1", "44.131.0.0/16"
    s.mailbox.enabled = True
    s.aprs.beacon = True
    store = MemoryStore()
    store.add("M0ABC", "G0TST", "Sunday's net", "Hello - are you on the 144.925 net on Sunday at 2000?\r73 Ann",
              time.time() - 3600)
    ctl = Controller(s, band_factory=lambda: SimBand(55.94, -3.21, first_beacon_s=(1.0, 2.0), stagger_s=2.0,
                                                          beacon_every_s=20),
                     mailbox_store=store)
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
    ctl.arm(15)
    sess = ctl.connect(NODE)
    ctl.send_message(FIXED, "Good evening, testing the new app")
    pump(20, lambda: sess.connected)
    ctl.send(sess.id, "?")
    pump(5)
    ctl.send(sess.id, "hello from the screenshot")
    ctl.ping("44.131.1.2", 1)
    ctl.simulated_mail("Test from the simulator", "Hello from G4SIM, a pretend station inside the app.\r73")
    pump(25)
    # A mobile station heard through GB7SIM, three positions apart, so the
    # map shows a track and a digipeated (dashed) path as well as the
    # simulator's direct stations. Through the engine's own entry points for
    # a heard frame (AprsStation.hear, as the SDR monitor feeds it, and the
    # controller's monitor hook), never as a transmission.
    from shack_packet.aprs import TOCALL
    from shack_packet.ax25 import Frame
    for i, (lat, lon) in enumerate([("5603.00N", "00318.00W"), ("5604.20N", "00315.40W"), ("5605.50N", "00312.10W")]):
        f = Frame.from_monitor(f"M0MOB-9>{TOCALL},GB7SIM*,WIDE2-1:!{lat}/{lon}>04{i}/035mobile via the node")
        with ctl.engine.lock:
            ctl.aprs.hear(f)
        ctl._heard_frame(f)
    for key in PAGES:
        win.nav.setCurrentRow(PAGES.index(key))
        pump(0.6)
        snapshot.save_widget(win, str(out / f"{PAGES.index(key) + 1:02d}-{key}.png"))
    # The map with a station chosen: its details under the list, its path
    # drawn bold.
    aprs_page = win.pages["aprs"]
    win.nav.setCurrentRow(PAGES.index("aprs"))
    aprs_page.map.span_km = 25
    aprs_page.select("M0MOB-9")
    pump(0.6)
    snapshot.save_widget(win, str(out / f"{PAGES.index('aprs') + 1:02d}-aprs-chosen.png"))
    # Settings: the SDR monitor's channels, the map, the control pad, and
    # 0.2's Internet (updates, the diagnostics opt-in) and Controller tabs.
    # The Controller tab looks for controllers only when its button is
    # pressed, so nothing here reaches the network.
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    for tab in ("sdr", "map", "pad", "internet", "controller"):
        dlg = SettingsDialog(win.settings, win, tab)
        dlg.resize(640, 720)
        dlg.show()
        pump(0.6)
        snapshot.save_widget(dlg, str(out / f"settings-{tab}.png"))
        dlg.reject()
    # The setup check, page by page, on the same station. "On the radio" is
    # shown for an IC-9700 on USB, the simulator having nothing to set; the
    # wizard goes back to the simulator before the next page would open a
    # radio, so no real radio is ever opened here.
    win.open_setup()
    wiz = win.wizard
    pump(0.5)

    def shot(n, name, wait=1.5):
        pump(wait)
        snapshot.save_widget(wiz, str(out / f"setup-{n}-{name}.png"))
    shot(1, "station")
    wiz.next()
    shot(2, "radio")
    radio = wiz.radio
    # The Pluto's panel and its "On the radio" page (the band filter, the
    # guard). Choosing the kind opens nothing: a radio is opened only on
    # the listening page, which the wizard reaches after going back to
    # the simulator below.
    radio.kind.setCurrentIndex(radio.kind.findData("pluto"))
    shot(2, "radio-pluto")
    wiz.next()
    shot(3, "radio-menu-pluto")
    wiz.back()
    radio.kind.setCurrentIndex(radio.kind.findData("ic-9700"))
    radio.transport.setCurrentIndex(radio.transport.findData("usb"))
    wiz.next()
    shot(3, "radio-menu")
    wiz.back()
    radio.kind.setCurrentIndex(radio.kind.findData("simulator"))
    wiz.next()
    wiz.next()
    shot(4, "audio")
    wiz.next()
    shot(5, "receive", 6)
    wiz.next()
    shot(6, "transmit")
    wiz.reject()
    pump(0.5)
    win.close()
    print(f"screens in {out}")
    for p in sorted(out.glob("*.png")):
        print(f"  {p.name}")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
