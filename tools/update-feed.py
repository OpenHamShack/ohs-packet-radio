#!/usr/bin/env python3
"""Write, sign and check Packet Radio's update feed.

    tools/update-feed.py pubkey  [--key PATH]
    tools/update-feed.py write   [--releases DIR] [--version V] [--notes TEXT] [--recommended] [--key PATH]
    tools/update-feed.py verify  [--releases DIR] [--pubkey HEX]

Packet Radio's copy of openhamshack-nb tools/update-feed.py (9f2a3ed), cut down
to one app. NB's knows only nb|ham and belongs to the NB session, so this
app has its own rather than a third entry in that one (PLAN-0.2 §3.4).
What it writes is the same format (openhamshack-nb docs/update-feed.md), read
by src/shack_packet_app/update.py, which is NB's ohscore/update.py.

The feed is https://openhamshack.com/desktop/packet/update.json and names
`"app": "packet"` (UPDATE_FEED, UPDATE_APP in shack_packet_app; Brian's D7,
18/10/2026), signed with the 360 Technology OpenHamShack release key that
signs NB's and Radio's (ed25519:dad6e2ca90668fce). Its private half lives
only on the release host (shack), at ~/.config/openhamshack-release/
ed25519.key or, while that is absent, ~/.config/openhamshack-release/ed25519.key
($OHS_RELEASE_KEY or --key name another place). There is no keygen here on
purpose: one key signs every OpenHamShack desktop feed, and NB's tool is
the one that makes and rotates it.

write makes <releases>/update.json from the installers in the release
folder (tools/release.sh puts them there) and signs it, as
update.json.sig beside it. Every entry is measured from the file itself
(size, SHA-256) and must agree with the .sha256 the build wrote beside
it. A platform this run has no file for keeps the previous manifest's
entry if that file is still in the folder unchanged, so a Windows
installer that follows the .deb a day later does not take the .deb out of
the feed. `published` only ever moves forward, because clients refuse a
manifest older than one they have accepted. The key must be one
update.py trusts, or every installed copy would refuse the result;
--untrusted-ok skips that check, for the tests alone.

verify checks the folder as a client would: the signature against the
keys update.py trusts (or --pubkey), then every file's size and SHA-256.

The manifest and its signature must reach the public site LAST, after
every file they name (tools/publish.sh does it in that order).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from shack_packet_app import UPDATE_APP, update  # noqa: E402

KEY_NAME = "360 Technology OpenHamShack release key"
DEFAULT_KEY = Path.home() / ".config" / "openhamshack-release" / "ed25519.key"
FORMER_KEY = Path.home() / ".config" / "openhamshack-release" / "ed25519.key"
DEFAULT_RELEASES = Path.home() / "build" / "shack-packet" / "release"
#: Each slot the feed can offer, and the installer's name in it (the
#: names tools/build-linux.sh, build-windows.ps1 and build-macos.sh give).
#: The .deb is Architecture: all; the AppImage has no slot, because an
#: AppImage cannot be updated in place by this client (update.KINDS).
SLOTS = (("linux", "all", "OpenHamShack-Packet-Radio-{v}-linux-all.deb"),
         ("windows", "amd64", "OpenHamShack-Packet-Radio-{v}-windows-x64.exe"),
         ("macos", "arm64", "OpenHamShack-Packet-Radio-{v}-macos-arm64.dmg"))


def die(msg: str) -> None:
    print(f"update-feed: {msg}", file=sys.stderr)
    sys.exit(1)


def key_path(a) -> Path:
    named = a.key or os.environ.get("OHS_RELEASE_KEY") or os.environ.get("OPENHAMSHACK_RELEASE_KEY")
    if named:
        return Path(named).expanduser()
    if not DEFAULT_KEY.exists() and FORMER_KEY.exists():
        return FORMER_KEY
    return DEFAULT_KEY


def load_key(path: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except FileNotFoundError:
        die(f"no release key at {path} (openhamshack-nb tools/update-feed.py keygen makes the one key)")
    except ValueError as e:
        die(f"{path} is not a private key ({e})")
    if not isinstance(key, Ed25519PrivateKey):
        die(f"{path} is not an Ed25519 key")
    return key


def raw_public(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def cmd_pubkey(a) -> None:
    pub = raw_public(load_key(key_path(a)))
    kid = update.key_id(pub)
    print(f"{kid} {pub.hex()}")
    if kid not in update.trusted_keys():
        # release.sh's gate: a key the app does not list signs nothing useful.
        die(f"{key_path(a)} is not in RELEASE_KEYS in src/shack_packet_app/update.py")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def measured(path: Path) -> tuple[int, str]:
    """(size, sha256), checked against the .sha256 sidecar when there is
    one: a copy damaged since the build wrote that stops here."""
    digest = sha256_of(path)
    side = path.with_name(path.name + ".sha256")
    if side.is_file():
        want = side.read_text(encoding="ascii").split()[0].lower()
        if want != digest:
            die(f"{path.name} does not match {side.name}")
    return path.stat().st_size, digest


def release_notes(version: str) -> str:
    """One line for the banner, for the people who will read it:
    docs/release-notes/packet-<version>.txt, cut to fit; empty without one."""
    try:
        line = " ".join((ROOT / "docs" / "release-notes" / f"packet-{version}.txt").read_text("utf-8").split())
    except OSError:
        return ""
    if len(line) > update.NOTES_MAX:
        line = line[:update.NOTES_MAX - 1].rsplit(" ", 1)[0] + "…"
    return line


def previous(folder: Path) -> dict:
    try:
        return json.loads((folder / "update.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def releases_dir(a) -> Path:
    return Path(a.releases or os.environ.get("RELEASES") or DEFAULT_RELEASES)


def cmd_write(a) -> None:
    folder = releases_dir(a)
    version = a.version or (ROOT / "VERSION").read_text().strip()
    try:
        update.parse_version(version)          # a feed names releases: never a "+label" build
    except ValueError as e:
        die(str(e))
    key = load_key(key_path(a))
    pub = raw_public(key)
    kid = update.key_id(pub)
    if kid not in update.trusted_keys() and not a.untrusted_ok:
        die(f"the key at {key_path(a)} ({kid}) is not in RELEASE_KEYS in src/shack_packet_app/update.py: "
            "every installed copy would refuse this feed")
    old = previous(folder)
    old_entries = {(e.get("platform"), e.get("arch")): e for e in old.get("files", []) if isinstance(e, dict)}
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    files, built = [], 0
    for plat, arch, pattern in SLOTS:
        name = pattern.format(v=version)
        if (folder / name).is_file():
            size, digest = measured(folder / name)
            was = old_entries.get((plat, arch), {})
            same = was.get("version") == version
            notes = a.notes if a.notes is not None else (was.get("notes") if same and "notes" in was
                                                         else release_notes(version))
            files.append({"platform": plat, "arch": arch, "kind": update.KINDS[plat], "version": version,
                          "url": name, "size": size, "sha256": digest,
                          "date": was.get("date") if same and was.get("date") else today, "notes": notes or ""})
            built += 1
            continue
        was = old_entries.get((plat, arch))
        if was and (folder / str(was.get("url", ""))).is_file():
            if measured(folder / was["url"]) != (was.get("size"), was.get("sha256")):
                die(f"{was['url']} is no longer the file the last manifest named")
            files.append(was)
            print(f"kept {plat} {arch}: {was['url']} ({was.get('version')}, from the last manifest)")
    if not built:
        die(f"no installer of {version} is in {folder}")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last = old.get("published", "")
    if last and stamp <= last:
        stamp = (dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ") + dt.timedelta(seconds=1)
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"schema": update.SCHEMA, "app": UPDATE_APP, "key": kid, "published": stamp}
    if a.recommended:
        doc["recommended"] = version
    doc["files"] = files
    data = (json.dumps(doc, indent=2) + "\n").encode("utf-8")
    sig = key.sign(data)
    # Proved as a client would before it is written, with the same code.
    m = update.read_manifest(data, sig, UPDATE_APP, keys=[(KEY_NAME, pub.hex())])
    tmp_json, tmp_sig = folder / "update.json.tmp", folder / "update.json.sig.tmp"
    tmp_json.write_bytes(data)
    tmp_sig.write_bytes(sig)
    os.replace(tmp_sig, folder / "update.json.sig")
    os.replace(tmp_json, folder / "update.json")
    for e in m.entries:
        print(f"  {e.platform:7s} {e.arch:5s} {e.version:8s} {e.url}  {e.size} bytes  sha256 {e.sha256[:12]}…")
    print(f"signed {folder / 'update.json'} with {kid}, published {stamp}"
          + (f", recommending {version}" if a.recommended else ""))


def cmd_verify(a) -> None:
    folder = releases_dir(a)
    try:
        data = (folder / "update.json").read_bytes()
        sig = (folder / "update.json.sig").read_bytes()
    except OSError as e:
        die(str(e))
    try:
        m = update.read_manifest(data, sig, UPDATE_APP, keys=[("given", a.pubkey)] if a.pubkey else None)
    except update.NotTrusted as e:
        die(str(e))
    bad = False
    for e in m.entries:
        p = folder / e.url
        if not p.is_file() or (p.stat().st_size, sha256_of(p)) != (e.size, e.sha256):
            print(f"{'MISSING' if not p.is_file() else 'DIFFERENT'} {e.url}")
            bad = True
            continue
        print(f"ok {e.platform} {e.arch} {e.version} {e.url}")
    if bad:
        die("the folder does not hold what its manifest names")
    print(f"{folder / 'update.json'}: signed by {m.key}, published {m.published}, every file present and matching")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="tools/update-feed.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pubkey", "write", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--key", help=f"the private key (default $OHS_RELEASE_KEY, {DEFAULT_KEY} or {FORMER_KEY})")
        if name in ("write", "verify"):
            p.add_argument("--releases", help=f"the release folder (default $RELEASES or {DEFAULT_RELEASES})")
        if name == "write":
            p.add_argument("--version", help="the release (default VERSION)")
            p.add_argument("--notes", help="the one-line notes (default docs/release-notes/packet-<v>.txt)")
            p.add_argument("--recommended", action="store_true",
                           help="name this version as recommended: clients on a newer one may go back to it")
            p.add_argument("--untrusted-ok", action="store_true", help="sign with an untrusted key (tests only)")
        if name == "verify":
            p.add_argument("--pubkey", help="check against this public key (hex) instead of the trusted ones")
    a = ap.parse_args(argv)
    {"pubkey": cmd_pubkey, "write": cmd_write, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    main()
