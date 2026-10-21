#!/usr/bin/env python3
"""Gather the ohs-libs packages the installers carry into build/ohs-libs.

    tools/ohslibs.py [--from OHS_LIBS] [--to DIR]

The app runs on seven ohs-libs packages - shack_packet, shack_rig,
shack_bandplan, shack_i18n, shack_client (finding and pairing with a
controller), shack_pluto (the Pluto's guard, identity, AD9364 enable and
crystal store) and shack_pad (the AKP03E). 0.1.x took them from ohs-libs
main (164c5fe or later). 0.2 builds take them from ohs-libs
**release/packet-0.2** until Brian's real-radio checks let that branch
land on main; give its checkout as $OHS_LIBS (on shack,
~/wt/libs-rel-0.2), and say which commit a build carried in its notes -
OHS-LIBS.txt below records it. There is still no wheel index to pin them
from (REPO-LAYOUT §3), so shack-app.toml's [python] paths takes them from
build/ohs-libs, which this fills from an ohs-libs checkout (default
~/wt/packet-modem, or $OHS_LIBS).

A copy of each package's src/ rather than a pip install from the local
folders: pip would need setuptools in a build environment it fetches from
the network, and the development venv has none. The one thing a wheel
build adds is shack_bandplan's dataset, which its setup.py copies in from
the repository's data/bandplan (that package reads it beside itself once
installed, and from the repository only in a checkout); this does the
same, so the installers carry exactly what a wheel would. The checkout's
commit is written to build/ohs-libs/OHS-LIBS.txt for the build log: it is
not on the import path's package list and nothing reads it at run time.

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# shack_pluto (0.2): the Pluto's on-device guard, its identity, the AD9364
# enable and the crystal-correction store. shack_pad (0.2): the AKP03E.
# shack_client (0.2): finding and pairing with a controller.
PACKAGES = ("shack_packet", "shack_rig", "shack_bandplan", "shack_i18n", "shack_pluto", "shack_pad", "shack_client")
DEFAULT_FROM = Path(os.environ.get("OHS_LIBS", Path.home() / "wt" / "packet-modem"))
DEFAULT_TO = ROOT / "build" / "ohs-libs"


def fill(src: Path, dest: Path) -> str:
    """Copy the packages from the ohs-libs checkout at `src` into `dest`,
    replacing what was there; returns the checkout's commit (or "unknown")."""
    src, dest = Path(src), Path(dest)
    for pkg in PACKAGES:
        if not (src / "python" / pkg / "src" / pkg / "__init__.py").is_file():
            raise SystemExit(f"ohslibs: {src} has no python/{pkg}: is it an ohs-libs checkout "
                             "at main 164c5fe or later?")
    data = src / "data" / "bandplan"
    if not (data / "manifest.json").is_file():
        raise SystemExit(f"ohslibs: {data} is missing; shack_bandplan cannot run without its dataset")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
    for pkg in PACKAGES:
        shutil.copytree(src / "python" / pkg / "src" / pkg, dest / pkg, ignore=ignore)
    # As shack_bandplan's setup.py does for a wheel.
    shutil.copytree(data, dest / "shack_bandplan" / "data" / "bandplan")
    try:
        commit = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(src), "status", "--porcelain", "--", "python", "data"],
                               capture_output=True, text=True, check=True).stdout.strip()
        commit += "-dirty" if dirty else ""
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    (dest / "OHS-LIBS.txt").write_text(f"ohs-libs {commit} from {src}\n")
    return commit


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", type=Path, default=DEFAULT_FROM)
    ap.add_argument("--to", dest="dest", type=Path, default=DEFAULT_TO)
    a = ap.parse_args(argv)
    commit = fill(a.src, a.dest)
    print(f"ohs-libs {commit}: {', '.join(PACKAGES)} into {a.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
