#!/usr/bin/env python3
"""The estimate table's model (src/shack_packet_app/airtime.py) beside the
library's simulated channel, mode by mode, with the settings the app gives
each mode: what airtime.py's constants are checked against when they
change. Simulated time, so it runs in seconds; nothing is keyed.

    tools/wt-test.sh's venv and PYTHONPATH, or:
    PYTHONPATH=src:<ohs-libs>/python/shack_packet/src python3 tools/airtime-sim.py [--kb 50] [--modes a,b]

`--loss 0.1` compares the "with retries" figure. Prints mode, simulated seconds, modelled seconds and their ratio. The
simulator knows the Pluto's timing (sim.PLUTO) but not a rig's, so AFSK
1200 here is a Pluto's; the rig's timing comes from the 19/10 on-air run.
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
import tempfile
from pathlib import Path

from shack_packet.channel import ChannelConfig
from shack_packet.datalink import LinkConfig
from shack_packet.engine import Engine
from shack_packet.filexfer import FileService, Inbox
from shack_packet.identity import Station
from shack_packet.sim import PLUTO, Medium, SimClock, Simulation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shack_packet_app import airtime                     # noqa: E402
from shack_packet_app.settings import FastRadioSettings  # noqa: E402


def simulate(mode: str, nbytes: int, seed: int = 1, loss: float = 0.0) -> float:
    clock = SimClock()
    if mode == airtime.DD:
        medium = Medium(clock, "frames", seed=seed, modem_mode="gfsk76800", loss=loss)
        channel, link, timing, turn = ChannelConfig(mode="gfsk76800"), LinkConfig(), None, 0.0
    elif mode == "afsk1200":
        medium = Medium(clock, "frames", seed=seed, baud=1200, loss=loss)
        channel, link, timing, turn = ChannelConfig(), LinkConfig(), PLUTO, 0.1
    else:
        medium = Medium(clock, "frames", seed=seed, modem_mode=mode, loss=loss)
        f = FastRadioSettings(mode=mode)
        fs = f.for_frequency(1_299_525_000)
        channel, link, timing, turn = fs.channel_config(), fs.link_config(), PLUTO, 0.1
    sim = Simulation(clock, medium)
    engines = []
    for call, s in (("G0ABC", 1), ("GM4BRI", 2)):
        radio = medium.radio(call, timing=timing, turnaround_s=turn) if timing else medium.radio(call)
        e = Engine(Station.of(call, tx_enabled=True), radio, channel=channel, link=link, clock=clock,
                   rng=random.Random(s))
        sim.add(e)
        engines.append(e)
    ea, eb = engines
    build = Path.home() / "build"
    tmp = Path(tempfile.mkdtemp(prefix="airtime-sim-", dir=build if build.is_dir() else None))
    events = []
    fa = FileService(ea, Inbox(tmp / "a"))
    fb = FileService(eb, Inbox(tmp / "b"), on_event=lambda t, k, d: events.append((k, t)))
    accepted = []
    eb.listen(eb.station.address(), lambda req: (lambda conn: accepted.append(conn)))
    conn = ea.connect("GM4BRI")
    assert sim.run(60, lambda: conn.connected and accepted)
    fa.attach(conn)
    fb.attach(accepted[0])
    data = b"\xff\xd8\xff\xe0" + random.Random(5).randbytes(nbytes - 4)
    start = clock.now
    t = fa.send_file(conn, tmp / "x.jpg", data=data)
    assert sim.run(120, lambda: any(k == "offer" for k, _ in events))
    fb.accept([x for k, x in events if k == "offer"][0])
    assert sim.run(36_000, lambda: t.state == "done"), t.state
    shutil.rmtree(tmp, ignore_errors=True)
    return clock.now - start


def model(mode: str, nbytes: int, loss: float = 0.0) -> float:
    if mode == airtime.DD:
        return airtime.transfer_s(nbytes, mode, airtime.DEFAULT_LINK, airtime.DD_TIMING, loss=loss)
    if mode == "afsk1200":
        return airtime.transfer_s(nbytes, mode, airtime.DEFAULT_LINK, airtime.PLUTO, loss=loss)
    f = FastRadioSettings(mode=mode)
    w = f.window_for()
    p = airtime.LinkParams(window=w, paclen=f.paclen, modulo=128 if w > 7 else 8, srej=f.srej, txdelay_s=0.0,
                           max_frames=w + 1)
    return airtime.transfer_s(nbytes, mode, p, airtime.PLUTO, loss=loss)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", type=int, default=50)
    ap.add_argument("--modes", default=",".join(airtime.ROWS))
    ap.add_argument("--loss", type=float, default=0.0, help="frames lost, 0-1 (the medium's and the model's)")
    a = ap.parse_args()
    n = a.kb * 1024
    print(f"{'mode':12} {'sim s':>8} {'model s':>8} {'model/sim':>9}")
    for mode in a.modes.split(","):
        s = simulate(mode, n, loss=a.loss)
        m = model(mode, n, loss=a.loss)
        print(f"{mode:12} {s:8.1f} {m:8.1f} {m / s:9.2f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
