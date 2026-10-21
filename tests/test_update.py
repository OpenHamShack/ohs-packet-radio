"""Self-update without a window (shack_packet_app/update.py, NB's
ohscore/update.py lifted): the feed this build is tied to, the signed
manifest, what each system is offered, the download proved against it,
the install command - and, end to end, a release folder written and signed
by tools/update-feed.py with a throw-away key, served over a local HTTP
server, through to the command that would install it (never run).

The real release key is never used: every call that needs a key is given
a test one. A test may listen on loopback (the window may not:
test_ui.py's no-listening-socket test)."""
from __future__ import annotations

import hashlib
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from shack_packet_app import UPDATE_APP, UPDATE_FEED, update

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEB = "OpenHamShack-Packet-Radio-{v}-linux-all.deb"
EXE = "OpenHamShack-Packet-Radio-{v}-windows-x64.exe"
DMG = "OpenHamShack-Packet-Radio-{v}-macos-arm64.dmg"


def new_key():
    k = Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, [("test key", pub.hex())]


def key_file(k, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return path


def entry(platform="linux", arch="all", version="0.2.0", data=b"package bytes"):
    url = {"linux": DEB, "windows": EXE, "macos": DMG}[platform].format(v=version)
    return {"platform": platform, "arch": arch, "kind": update.KINDS[platform], "version": version, "url": url,
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "date": "2026-10-18", "notes": "Better"}


def manifest(k, keys, files, app=UPDATE_APP, published="2026-10-18T12:00:00Z", **extra):
    doc = {"schema": 1, "app": app, "key": update.key_id(bytes.fromhex(keys[0][1])), "published": published,
           **extra, "files": files}
    data = (json.dumps(doc, indent=2) + "\n").encode()
    return data, k.sign(data)


class Served:
    """A folder over HTTP on 127.0.0.1, remembering each request's path
    and headers: what a check tells the site about the machine."""

    def __init__(self, folder: Path):
        served = self
        self.requests: list[tuple[str, dict]] = []

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(folder), **kw)

            def do_GET(self):
                served.requests.append((self.path, dict(self.headers)))
                super().do_GET()

            def log_message(self, *_a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def write_release(folder: Path, keyfile: Path, version: str, files: dict[str, bytes]):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
        (folder / f"{name}.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    return subprocess.run([sys.executable, str(REPO / "tools" / "update-feed.py"), "write", "--key", str(keyfile),
                           "--releases", str(folder), "--version", version, "--untrusted-ok", "--notes", "Test notes"],
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def key():
    return new_key()


# ---------------------------------------------------------------- what this build is tied to
def test_the_feed_and_the_name_are_the_ones_brian_chose_and_the_release_key_is_trusted():
    # D7, 18/10/2026: baked in for good, so a change here strands every copy.
    assert UPDATE_FEED == "https://openhamshack.com/desktop/packet/update.json"
    assert UPDATE_APP == "packet"
    assert "ed25519:dad6e2ca90668fce" in update.trusted_keys()
    assert "self-test passed" in update.describe_verifier()


# ---------------------------------------------------------------- the manifest
def test_a_signed_manifest_for_packet_is_read(key):
    k, keys = key
    m = update.read_manifest(*manifest(k, keys, [entry()]), UPDATE_APP, keys)
    assert m.app == "packet" and [(e.platform, e.version) for e in m.entries] == [("linux", "0.2.0")]


def test_nb_or_radios_manifest_is_refused_even_signed_by_the_same_key(key):
    # One key signs every OpenHamShack feed: the app name is what keeps a
    # Packet Radio copy from being pointed at NB's installer.
    k, keys = key
    for other in ("openhamshack-nb", "openhamshack-nb", "openhamshack-radio", "openhamshack-ham"):
        with pytest.raises(update.NotTrusted, match=f"for '{other}', not packet"):
            update.read_manifest(*manifest(k, keys, [entry()], app=other), UPDATE_APP, keys)


def test_a_tampered_or_foreign_signed_manifest_is_refused(key):
    k, keys = key
    data, sig = manifest(k, keys, [entry()])
    with pytest.raises(update.NotTrusted, match="does not match"):
        update.read_manifest(data.replace(b"0.2.0", b"0.2.1"), sig, UPDATE_APP, keys)
    other, other_keys = new_key()
    with pytest.raises(update.NotTrusted, match="does not trust"):
        update.read_manifest(*manifest(other, other_keys, [entry()]), UPDATE_APP, keys)


# ---------------------------------------------------------------- what is offered
def test_each_system_gets_its_own_installer_and_only_a_newer_one(key):
    k, keys = key
    m = update.read_manifest(*manifest(k, keys, [entry(), entry("windows", "amd64"), entry("macos", "arm64")]),
                             UPDATE_APP, keys)
    assert update.choose(m, "linux", "amd64", "0.1.2").entry.kind == "deb"         # the "all" .deb
    assert update.choose(m, "windows", "amd64", "0.1.2").entry.kind == "exe"
    assert update.choose(m, "macos", "arm64", "0.1.2").entry.kind == "dmg"
    assert update.choose(m, "macos", "amd64", "0.1.2") is None                     # an Intel Mac: nothing
    assert update.choose(m, "linux", "amd64", "0.2.0") is None
    assert update.choose(m, "linux", "amd64", "0.3.0") is None


def test_a_beta_build_orders_as_the_release_it_comes_before_the_next_of(key):
    # Test builds are 0.2.0+betaN (D8: the diagnostics endpoint refuses
    # -beta.N). The label takes no part in ordering, so 0.2.0+beta3 is
    # offered 0.2.1, and not 0.2.0 itself.
    k, keys = key
    newer = update.read_manifest(*manifest(k, keys, [entry(version="0.2.1")]), UPDATE_APP, keys)
    assert update.choose(newer, "linux", "all", "0.2.0+beta3").entry.version == "0.2.1"
    same = update.read_manifest(*manifest(k, keys, [entry(version="0.2.0")]), UPDATE_APP, keys)
    assert update.choose(same, "linux", "all", "0.2.0+beta3") is None
    with pytest.raises(ValueError):
        update.installed_version("0.2.0-beta.1")


# ---------------------------------------------------------------- over HTTP, end to end
@pytest.fixture
def release(tmp_path, key):
    k, keys = key
    keyfile = key_file(k, tmp_path / "key" / "ed25519.key")
    rel = tmp_path / "rel"
    deb = b"the real package " * 1000
    res = write_release(rel, keyfile, "0.2.0", {DEB.format(v="0.2.0"): deb})
    assert res.returncode == 0, res.stderr
    server = Served(rel)
    yield {"keys": keys, "keyfile": keyfile, "rel": rel, "server": server, "feed": server.url + "update.json",
           "deb": deb}
    server.close()


def test_a_check_says_nothing_but_name_and_version_and_the_deb_is_proved(release, tmp_path):
    r = update.check(release["feed"], UPDATE_APP, "0.1.2", plat="linux", arch="amd64", keys=release["keys"],
                     seen=update.Seen(tmp_path / "seen.json"))
    assert r.status == "available", r.message
    assert r.offer.entry.notes == "Test notes"
    assert [p for p, _h in release["server"].requests] == ["/update.json", "/update.json.sig"]
    for _p, headers in release["server"].requests:
        assert headers["User-Agent"] == "packet/0.1.2" and "Cookie" not in headers
    path = update.download(r.offer, tmp_path / "dl", "packet/0.1.2")
    assert path.read_bytes() == release["deb"]
    assert update.install_command("deb", path, pkexec="/usr/bin/pkexec").argv == \
        ["/usr/bin/pkexec", "apt-get", "install", "-y", str(path.resolve())]


def test_a_swapped_file_on_the_site_is_never_kept(release, tmp_path):
    r = update.check(release["feed"], UPDATE_APP, "0.1.2", plat="linux", arch="amd64", keys=release["keys"])
    swapped = bytearray(release["deb"])
    swapped[10] ^= 1
    (release["rel"] / DEB.format(v="0.2.0")).write_bytes(bytes(swapped))
    with pytest.raises(update.NotTrusted, match="does not match the signed manifest"):
        update.download(r.offer, tmp_path / "dl", "ua")
    assert list((tmp_path / "dl").iterdir()) == []


def test_windows_gets_its_installer_once_added_and_the_deb_entry_stays(release, tmp_path):
    r = update.check(release["feed"], UPDATE_APP, "0.1.2", plat="windows", arch="amd64", keys=release["keys"])
    assert r.status == "unsupported"
    res = write_release(release["rel"], release["keyfile"], "0.2.0", {EXE.format(v="0.2.0"): b"MZ setup " * 300})
    assert res.returncode == 0, res.stderr
    r = update.check(release["feed"], UPDATE_APP, "0.1.2", plat="windows", arch="amd64", keys=release["keys"])
    assert r.status == "available", r.message
    path = update.download(r.offer, tmp_path / "dl", "ua")
    update.check_authenticode(path, query=lambda p: ("Valid", "CN=360 Technology, O=360 Technology, C=GB"))
    with pytest.raises(update.NotTrusted):
        update.check_authenticode(path, query=lambda p: ("Valid", "CN=Mallory, O=Mallory"))
    # This installer has no Pluto driver task, so no /MERGETASKS for one.
    assert update.install_command("exe", path).argv == [str(path.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                                                        "/NORESTART", "/RELAUNCH=1"]
    m = json.loads((release["rel"] / "update.json").read_text())
    assert m["app"] == "packet"
    assert sorted((f["platform"], f["arch"]) for f in m["files"]) == [("linux", "all"), ("windows", "amd64")]


def test_the_tool_verifies_its_folder_and_refuses_an_untrusted_key(release):
    tool = [sys.executable, str(REPO / "tools" / "update-feed.py")]
    ok = subprocess.run(tool + ["verify", "--releases", str(release["rel"]), "--pubkey", release["keys"][0][1]],
                        capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0 and "every file present and matching" in ok.stdout, ok.stderr
    # Without --untrusted-ok a test key signs nothing: no feed every copy would refuse.
    refused = subprocess.run(tool + ["write", "--key", str(release["keyfile"]), "--releases", str(release["rel"]),
                                     "--version", "0.2.0"], capture_output=True, text=True, timeout=60)
    assert refused.returncode == 1 and "not in RELEASE_KEYS" in refused.stderr
    # And a feed never names a labelled build.
    beta = subprocess.run(tool + ["write", "--key", str(release["keyfile"]), "--releases", str(release["rel"]),
                                  "--version", "0.2.0+beta1", "--untrusted-ok"], capture_output=True, text=True,
                          timeout=60)
    assert beta.returncode == 1


# ---------------------------------------------------------------- never in the radio's way
def test_never_while_transmitting_or_with_a_station_connected():
    assert update.busy_reason({"transmitting": True}) == "transmitting"
    assert update.busy_reason({"transmitting": False, "sessions": 1}) == "connected"
    assert update.busy_reason({"transmitting": False, "sessions": 0, "armed": True}) == ""
