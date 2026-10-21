"""shack-packet: OpenHamShack Packet Radio.

    shack-packet                 the window
    shack-packet --self-test     no window: the engine, the simulator and the
                                 SDR monitor's synthetic band, end to end, then exit 0
    shack-packet --version
    shack-packet --library-check
                                 (hidden) load every optional library - cryptography,
                                 zeroconf, hidapi, Pillow, librtlsdr, libiio - and
                                 make each do one small thing; PASS/FAIL (libcheck.py)
    shack-packet --diagnostics-test ZIP
                                 (hidden) Help > Save diagnostics as a tester
                                 clicks it, on the simulator, to ZIP; PASS/FAIL
    shack-packet -v              DEBUG in the log

`--self-test` is what an installer build runs on the frozen program to
prove the bundle is whole (a missing module in a frozen build is otherwise
found on somebody's PC): it builds the station on the simulator, connects
to the simulated node and exchanges a line, sends an APRS message and waits
for its ack, and runs the synthetic SDR band until it decodes. Everything
stays inside the process: no radio, no sound device, no network.
"""
from __future__ import annotations

import argparse
import sys
import time


def self_test(out=sys.stdout) -> int:
    from .controller import Controller
    from .settings import Settings
    from .simband import FIXED, NODE, SimBand

    s = Settings()
    s.station.callsign, s.station.locator, s.station.licence_class = "G0TST", "IO85JW", "full"
    s.tx.set_up = True
    s.sdr.source = "synthetic"
    ctl = Controller(s, band_factory=lambda: SimBand(55.9, -3.2, first_beacon_s=(30.0, 40.0)))

    def wait(cond, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        return cond()
    steps = []
    try:
        ctl.start()
        steps.append(("radio", wait(lambda: ctl.link_state == "open", 5)))
        steps.append(("arm", ctl.arm(1)[0]))
        sess = ctl.connect(NODE)
        steps.append(("connect", wait(lambda: sess.connected, 20)))
        ctl.send(sess.id, "self test")
        steps.append(("chat", wait(lambda: any("self test" in line.text for line in sess.lines
                                               if line.direction == "rx"), 20)))
        msg = ctl.send_message(FIXED, "self test")[0]
        steps.append(("aprs", wait(lambda: msg.state == "acked", 20)))
        steps.append(("sdr", wait(lambda: any(r.direction == "sdr" for r in ctl.rows), 30)))
    finally:
        ctl.stop()
    steps.append(("pad", pad_check(out)))
    for name, ok in steps:
        print(f"{name}: {'ok' if ok else 'FAILED'}", file=out)
    ok = all(ok for _, ok in steps) and len(steps) == 7
    print("self-test: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def pad_check(out=sys.stdout) -> bool:
    """The control pad's part of the self-test: the actions load with
    nothing left out, and in a frozen build the two libraries the pad
    needs are in the bundle. shack_pad swallows a missing hidapi or Pillow
    by design, so without this a frozen build's pad would do nothing and
    say nothing (GUIDE §5, "three silent ways to a dead pad"). Outside a
    frozen build a missing one is only reported: a .deb recommends Pillow
    and needs no hidapi (Linux reads the pad through hidraw)."""
    from shack_pad import actions
    from shack_pad.faces import Face, have_pillow, render_jpeg

    from . import padactions
    decl, _layout = padactions.declaration()
    acts, notes = actions.load(decl)
    pictures = have_pillow() and bool(render_jpeg(Face("ARM", "TX")))
    try:
        import hid  # noqa: F401
        hidapi = True
    except ImportError:
        hidapi = False
    print(f"pad: {len(acts)} actions, key pictures {'yes' if pictures else 'no'}, "
          f"hidapi {'yes' if hidapi else 'no'}", file=out)
    ok = not notes and not actions.tx_actions(acts)
    if getattr(sys, "frozen", False):
        ok = ok and pictures and (hidapi or sys.platform.startswith("linux"))
    return ok


def diagnostics_test(path: str, out=sys.stdout) -> int:
    """What 0.1.0 shipped broken, proved on the frozen program: the window
    on the simulator, Help > Save diagnostics triggered as a click
    triggers it (the file dialog answered with `path`), with the program
    log open in its RotatingFileHandler as it always is. main() points
    the settings and data at a throwaway folder first unless the caller
    set them, so a tester's own are never touched; the radio is the
    simulator whatever is saved."""
    import zipfile
    from . import applog
    from .settings import Settings
    applog.install_crash_hooks()
    from PyQt6.QtWidgets import QFileDialog
    from .ui import qtapp
    from .ui.window import MainWindow
    app = qtapp.make_app([sys.argv[0]])
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (path, "Zip files (*.zip)"))
    s = Settings()
    s.radio.kind = "simulator"
    s.station.callsign, s.station.locator = "G0TST", "IO85JW"
    s.setup_done = True
    win = MainWindow(s, {}, persist=False)
    win.show()
    win.start(first_run_check=False)
    app.processEvents()
    try:
        win.act_diag.trigger()
        app.processEvents()
        with zipfile.ZipFile(path) as z:
            names = sorted(z.namelist())
        ok = {"summary.json", "log.txt", "log-file.txt", "window.png"} <= set(names)
        print("zip: " + ", ".join(names), file=out)
    except Exception as e:                          # noqa: BLE001 - reported as the FAIL
        print(f"no zip: {type(e).__name__}: {e}", file=out)
        ok = False
    finally:
        win.close()
    print("diagnostics-test: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def main(argv=None) -> int:
    from . import __version__, applog
    ap = argparse.ArgumentParser(prog="shack-packet", description="OpenHamShack Packet Radio")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--diagnostics-test", metavar="ZIP", help=argparse.SUPPRESS)
    ap.add_argument("--library-check", action="store_true", help=argparse.SUPPRESS)
    args, rest = ap.parse_known_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.library_check:
        from . import libcheck
        return libcheck.run(sys.stdout)
    if args.diagnostics_test:
        # Before install(): the throwaway folders must be set before the log
        # file is opened in them.
        import os
        import tempfile
        if not os.environ.get("SHACK_PACKET_CONF"):
            scratch = tempfile.mkdtemp(prefix="shack-packet-diag-")
            os.environ["SHACK_PACKET_CONF"] = os.path.join(scratch, "conf")
            os.environ["SHACK_PACKET_DATA"] = os.path.join(scratch, "data")
        applog.install(args.verbose, to_file=True)
        return diagnostics_test(args.diagnostics_test)
    applog.install(args.verbose, to_file=not args.self_test)
    if args.self_test:
        return self_test()
    applog.install_crash_hooks()
    from .ui import qtapp
    from .ui.window import MainWindow
    app = qtapp.make_app([sys.argv[0], *rest])
    quit_on = qtapp.QuitOnSignals(app)
    win = MainWindow()
    # A crash unkeys the radio before anything else (the excepthook runs
    # this), and so does any way out of the event loop - a closed window
    # stops the controller already, but a logoff, a signal or app.quit()
    # need not pass through closeEvent. stop() is safe twice.
    applog.on_crash(win.ctl.emergency_unkey)
    app.aboutToQuit.connect(win.ctl.stop)
    win.show()
    win.start()
    # The program's own connections to openhamshack.com start here and
    # nowhere else, so a window the tests, the screenshot tool or
    # --diagnostics-test make never fetches or sends anything.
    win.updates.start()
    win.diagsend.start()
    code = app.exec()
    quit_on.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
