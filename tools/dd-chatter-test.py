#!/usr/bin/env python3
"""The supervised IC-9700 DD chatter test: does a 9700 in DD send home-LAN
chatter to air, and does LAN remote control keep working in DD?

    tools/dd-chatter-test.py --host 198.51.100.150 --user <user> [--seconds 30]
    tools/dd-chatter-test.py --dry-run --yes          # against FakeIcom, no radio

Brian runs this himself, at the radio, with an antenna or dummy load he
chose on 23 cm (HANDOVER.md, 0.3.0 F). The two unknowns it settles decide
the DD transport's network set-up (docs/DD.md): (a) radio left on the home
LAN, or (b) the controller's filtered "Radio port" only.

What it does, in order, and what it never does:

1. Logs in over the LAN (CI-V only: no audio, no transmit stream), and sets
   the radio up through shack_rig.icom.satdd.dd_settings - 1299.525 MHz,
   DD, RPS off, RF POWER 0 (the 9700's minimum, about 0.5 W, not zero) -
   each value read, journalled to disk first and read back, exactly as the
   app's DD link does. A radio that will not take DD is put back and the
   test stops.
2. Asks you to press TRANSMIT on the radio (TX INHIBIT off). Nothing here
   can key the radio or change TX INHIBIT: in DD the radio transmits by
   itself whenever an Ethernet frame reaches its [LAN] port, and only after
   that key press.
3. For --seconds, polls 1C 00 (transmitting?) and 15 11 (Po) five times a
   second; from a third of the way in, sends 40 UDP broadcasts over ten
   seconds to --broadcast port 9 (discard) - ordinary LAN chatter. It
   counts the polls answered (does CI-V over the LAN survive DD?) and the
   time the radio reported transmitting, before and during the chatter.
4. Asks you to press TRANSMIT again (TX INHIBIT back on).
5. Puts everything back, last first, and prints what it found. Ctrl-C or
   any failure at any step still puts the radio back; if this process is
   killed outright, the journal (--journal) is recovered by the next run
   before it changes anything.

--dry-run runs the same steps against shack_rig's FakeIcom on 127.0.0.1:
the key presses are the fake's press_transmit(), the chatter is
FakeIcom.lan_traffic() (no packet leaves this computer), and --yes skips the
prompts. The tests run it that way (tests/test_dd_chatter_tool.py).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

CHANNEL_HZ = 1_299_525_000
POLL_S = 0.2
BROADCASTS = 40
BROADCAST_GAP_S = 0.25


def _say(text: str) -> None:
    print(text, flush=True)


class _Dry:
    """FakeIcom on a loop of its own, for --dry-run."""

    def __init__(self):
        from shack_rig.icom.fake import FakeIcom
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.radio = FakeIcom(username="op", password="secret", freq=145_825_000)
        self.radio.mode, self.radio.data, self.radio.filter = 0x05, 0x01, 0x01
        asyncio.run_coroutine_threadsafe(self.radio.start(), self.loop).result(5)

    def profile(self) -> dict:
        return {"model": "ic-9700", "host": "127.0.0.1", "port": self.radio.control_port, "username": "op"}

    def close(self) -> None:
        asyncio.run_coroutine_threadsafe(self.radio.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def _prompt(text: str, args, dry) -> None:
    _say("\n>>> " + text)
    if dry is not None:
        dry.radio.press_transmit()
        _say("    (dry run: the pretend operator pressed TRANSMIT)")
        return
    if args.yes:
        raise SystemExit("--yes is for --dry-run only: on a real radio somebody must press TRANSMIT")
    input("    Press Enter here when you have done it... ")


def run(args) -> dict:
    """The whole test; returns the result (also written to --out)."""
    from shack_packet.radios.dd import RigOnLoop
    from shack_rig.datasetup import DataSetup, Journal
    from shack_rig.icom.satdd import dd_settings, on_air

    dry = _Dry() if args.dry_run else None
    if dry is not None:
        profile, password = dry.profile(), "secret"
    else:
        profile = {"model": "ic-9700", "transport": "lan", "host": args.host, "port": args.port,
                   "username": args.user, "client_name": "shack-packet dd test"}
        password = os.environ.get("DD_RADIO_PASSWORD") or getpass.getpass("The radio's network password: ")
    result: dict = {"dry_run": bool(dry), "channel_hz": args.channel, "seconds": args.seconds}
    host = RigOnLoop(profile, password)
    setup = None
    try:
        _say(f"Connecting to the IC-9700 at {profile['host']}:{profile['port']} (CI-V only)...")
        host.start(20.0)
        journal = Journal(Path(args.journal), radio="IC-9700 DD chatter test")
        setup = DataSetup(dd_settings(host.rig, host.call, frequency=args.channel, power_level=0), journal,
                          radio="IC-9700 DD chatter test")
        recovered = setup.recover()
        if recovered:
            _say("Put back first, from a run that did not finish: "
                 + ", ".join(f"{ln.key} {ln.status}" for ln in recovered))
        report = setup.apply()
        result["setup"] = [ln.public() for ln in report]
        for ln in report:
            _say(f"  {ln.text()}")
        status = {ln.key: ln.status for ln in report}
        if any(status.get(k) not in ("set", "already") for k in ("frequency", "mode", "rf_power")):
            result["verdict"] = "the radio did not take DD at minimum power; nothing was transmitted"
            return result

        _prompt(f"The radio is in DD on {args.channel / 1e6:.3f} MHz at minimum RF power. Press TRANSMIT on "
                "the radio now, so TX INHIBIT goes off.", args, dry)

        polls = answered = 0
        on_air_before = on_air_during = 0.0
        max_po = 0
        chatter_from = args.seconds / 3
        chatter_started = threading.Event()

        def chatter():
            time.sleep(chatter_from)
            chatter_started.set()
            if dry is not None:
                for _ in range(BROADCASTS):
                    dry.radio.lan_traffic(1)
                    time.sleep(min(BROADCAST_GAP_S, args.seconds / 3 / BROADCASTS))
                return
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                for i in range(BROADCASTS):
                    s.sendto(b"OpenHamShack DD chatter test %d" % i, (args.broadcast, 9))
                    time.sleep(BROADCAST_GAP_S)
            finally:
                s.close()

        t = threading.Thread(target=chatter, name="chatter", daemon=True)
        t.start()
        t0 = time.monotonic()
        last = None
        while time.monotonic() - t0 < args.seconds:
            keyed, po = on_air(host.rig, host.call)
            polls += 1
            if keyed is not None:
                answered += 1
            if keyed:
                if chatter_started.is_set():
                    on_air_during += POLL_S
                else:
                    on_air_before += POLL_S
                max_po = max(max_po, po or 0)
            state = (keyed, po if keyed else None)
            if state != last:
                _say(f"  {time.monotonic() - t0:6.1f} s  transmitting={keyed}  Po={po}"
                     + ("  (chatter running)" if chatter_started.is_set() else ""))
                last = state
            time.sleep(POLL_S)
        t.join(15)
        result.update({"polls": polls, "answered": answered, "on_air_before_s": round(on_air_before, 1),
                       "on_air_during_chatter_s": round(on_air_during, 1), "max_po_raw": max_po})
        _prompt("Press TRANSMIT on the radio again, so TX INHIBIT is back on.", args, dry)
    finally:
        if setup is not None:
            back = setup.restore()
            result["restored"] = [ln.public() for ln in back]
            _say("Put back: " + ", ".join(f"{ln.key} {ln.status}" for ln in back))
        host.stop()
        if dry is not None:
            result["radio_after"] = {"freq": dry.radio.freq, "mode": dry.radio.mode,
                                     "frames_sent_by_dd": dry.radio.dd_frames_sent}
            dry.close()
    if "polls" in result:
        lan = (f"CI-V over the LAN answered {result['answered']} of {result['polls']} polls in DD"
               + (" - remote control works in DD" if result["answered"] == result["polls"] else
                  " - remote control is NOT reliable in DD"))
        air = ("LAN chatter WENT TO AIR: use the filtered Radio port (docs/DD.md, set-up b)"
               if result["on_air_during_chatter_s"] > 0 else
               "no transmission seen during the chatter: the home LAN may be safe (set-up a), "
               "repeat with the radio's DD Packet Output on All before relying on it")
        result["verdict"] = f"{lan}; {air}"
    _say("\nResult: " + result.get("verdict", "incomplete"))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))
    return result


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=50001)
    ap.add_argument("--user", default="")
    ap.add_argument("--channel", type=int, default=CHANNEL_HZ, help="Hz; a UK 23 cm high-speed data channel")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--broadcast", default="198.51.100.255", help="where the chatter goes (port 9, discard)")
    ap.add_argument("--journal", default=str(Path.home() / ".local/state/openhamshack/dd-chatter-journal.json"))
    ap.add_argument("--out", default="", help="write the result here as JSON")
    ap.add_argument("--dry-run", action="store_true", help="FakeIcom on 127.0.0.1; nothing leaves this computer")
    ap.add_argument("--yes", action="store_true", help="no prompts (dry run only)")
    args = ap.parse_args(argv)
    from shack_packet.radios.dd import UK_DD_CHANNELS_HZ
    if args.channel not in UK_DD_CHANNELS_HZ:
        ap.error("--channel must be one of " + ", ".join(str(c) for c in UK_DD_CHANNELS_HZ))
    if not args.dry_run and not (args.host and args.user):
        ap.error("--host and --user are needed (or --dry-run)")
    logging.basicConfig(level=logging.INFO if not args.dry_run else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s")
    return run(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
