#!/usr/bin/env python3
"""Put a Packet Radio release on openhamshack.com's downloads page - the
part of tools/release-site.sh that runs on ohs-dev, where the website's
repository (~/openhamshack-site) and its live build (~/build/site-dist)
are.

    site-downloads.py --version V --files DIR [--site REPO] [--live DIST] [--stage DIST] [--desktop DIR]

What 0.1.1 and 0.1.2 did by hand (HANDOVER "Published to openhamshack.com"),
written down so it cannot be done differently next time:

1. The four installers in --files (.deb, AppImage, Windows .exe, macOS
   .dmg, each with its .sha256, checked here) are copied into
   ~/build/beta-build/desktop/{linux,windows,macos}/packet/, the folders
   the site's collect-downloads.py reads, and the older Packet files there
   are deleted.
2. data/downloads.json: ONLY the Packet entries change - version, file
   name, size, SHA-256, url and source - matched by OS and kind. Running
   collect-downloads.py instead would move every product it finds a newer
   build of (13/10: six products to unreleased builds), so it is not run.
3. The stage is a copy of the live dist (--live): the old Packet files and
   their .sha256 out of downloads/, the new ones in, the Packet lines of
   downloads/SHA256SUMS replaced; then tools/build.py --dist <stage>.
4. The guard: everything that differs between the live dist and the stage
   must be one of apps/packet/index.html, downloads/index.html,
   downloads/SHA256SUMS, or an old or new Packet download. Anything else
   (another product's line, a page somebody changed and never published)
   stops here, before anything is uploaded.
5. tools/check-site.py --dist <stage> must pass.

Step 1 is done last, once 2-5 have passed. Prints the "remove:" lines
(downloads/<old file>) for publish-web.sh --remove. Changes nothing outside
the stage, the desktop folders and data/downloads.json; a failed step
leaves the stage for a look (delete it before trying again).
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
PREFIX = "OpenHamShack-Packet-Radio-"
# (os, kind, file name pattern) as data/downloads.json and the builds name them.
SLOTS = (("linux", "AppImage", "{p}{v}-linux-x86_64.AppImage"),
         ("linux", "deb", "{p}{v}-linux-all.deb"),
         ("windows", "installer", "{p}{v}-windows-x64.exe"),
         ("macos", "dmg", "{p}{v}-macos-arm64.dmg"))
FOLDER = {"linux": "linux", "windows": "windows", "macos": "macos"}
ALLOWED_PAGES = {"apps/packet/index.html", "downloads/index.html", "downloads/SHA256SUMS"}


def die(msg: str) -> None:
    print(f"site-downloads: {msg}", file=sys.stderr)
    sys.exit(1)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--files", required=True, type=Path)
    ap.add_argument("--site", type=Path, default=HOME / "openhamshack-site")
    ap.add_argument("--live", type=Path, default=HOME / "build" / "site-dist")
    ap.add_argument("--stage", type=Path)
    ap.add_argument("--desktop", type=Path, default=HOME / "build" / "beta-build" / "desktop")
    a = ap.parse_args()
    v = a.version
    stage = a.stage or HOME / "build" / f"site-dist-packet-{v}-stage"
    data_path = a.site / "data" / "downloads.json"

    # The four files, whole.
    new = {}
    for os_name, kind, pat in SLOTS:
        name = pat.format(p=PREFIX, v=v)
        f = a.files / name
        if not f.is_file():
            die(f"{name} is not in {a.files}")
        want = (a.files / f"{name}.sha256").read_text().split()[0]
        if sha256(f) != want:
            die(f"{name} does not match its .sha256")
        new[(os_name, kind)] = (name, f, want)
    if stage.exists():
        die(f"{stage} already exists (a previous attempt?); look at it, delete it, run again")

    # 2. data/downloads.json, Packet entries only.
    data = json.loads(data_path.read_text(encoding="utf-8"))
    old_names = []
    for e in data["artefacts"]:
        if e.get("product") != "packet":
            continue
        key = (e.get("os"), e.get("kind"))
        if key not in new:
            continue
        name, f, digest = new.pop(key)
        if e.get("filename") and e["filename"] != name:
            old_names.append(e["filename"])
        e.update(version=v, status="available", filename=name, size=f.stat().st_size, sha256=digest,
                 url=f"downloads/{name}", source=f"~/build/beta-build/desktop/{FOLDER[key[0]]}/packet/{name}")
    if new:
        die(f"downloads.json has no Packet entry for {sorted(new)}; add the slot by hand first")

    # 3. The stage.
    shutil.copytree(a.live, stage, symlinks=True)
    dl = stage / "downloads"
    for old in old_names:
        for x in (old, f"{old}.sha256"):
            (dl / x).unlink(missing_ok=True)
    lines = []
    sums = dl / "SHA256SUMS"
    for line in sums.read_text().splitlines():
        if line.split() and line.split()[-1] in old_names:
            continue
        lines.append(line)
    for os_name, kind, pat in SLOTS:
        name = pat.format(p=PREFIX, v=v)
        shutil.copy2(a.files / name, dl / name)
        digest = sha256(dl / name)
        (dl / f"{name}.sha256").write_text(f"{digest}  {name}\n")
        lines.append(f"{digest}  {name}")
    sums.write_text("\n".join(lines) + "\n")
    data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(a.site / "tools" / "build.py"), "--dist", str(stage)], check=True,
                   stdout=subprocess.DEVNULL)

    # 4. The guard.
    before, after = tree(a.live), tree(stage)
    changed = {p for p in before & after if not filecmp.cmp(a.live / p, stage / p, shallow=False)}
    gone, added = before - after, after - before
    moved = {f"downloads/{n}" for o in old_names for n in (o, f"{o}.sha256")}
    moved |= {f"downloads/{pat.format(p=PREFIX, v=v)}{s}" for _o, _k, pat in SLOTS for s in ("", ".sha256")}
    unexpected = sorted((changed - ALLOWED_PAGES) | (gone - moved) | (added - moved))
    for p in sorted(changed | gone | added):
        print(f"  {'changed' if p in changed else 'removed' if p in gone else 'added'}: {p}")
    if unexpected:
        die("the stage differs from the live site in more than this release:\n  " + "\n  ".join(unexpected))

    # 5. The site's own check.
    subprocess.run([sys.executable, str(a.site / "tools" / "check-site.py"), "--dist", str(stage)], check=True)
    # 1. The desktop folders collect-downloads.py reads.
    for os_name, kind, pat in SLOTS:
        name = pat.format(p=PREFIX, v=v)
        d = a.desktop / FOLDER[os_name] / "packet"
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob(f"{PREFIX}*"):
            if not old.name.startswith(f"{PREFIX}{v}-"):       # this release's other files stay
                old.unlink()
        for x in (name, f"{name}.sha256"):
            shutil.copy2(a.files / x, d / x)

    for old in old_names:
        print(f"remove: downloads/{old}")
        print(f"remove: downloads/{old}.sha256")
    print(f"stage: {stage}")


if __name__ == "__main__":
    main()
