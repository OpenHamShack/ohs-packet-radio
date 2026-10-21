"""tools/release.sh and tools/publish.sh, run for real against throw-away
folders: release.sh with a test key (never the release key), publish.sh
with ssh, rsync and curl replaced on PATH by stubs that copy into a local
"site" folder and log every call - so the order (the feed last), the
removals and the HTTPS verification are what is checked. Nothing reaches
openhamshack.com."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from test_update import DEB, EXE, key_file, new_key  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def built(dist: Path, name: str, data: bytes) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / name).write_bytes(data)
    (dist / f"{name}.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")


@pytest.fixture
def rel(tmp_path):
    k, keys = new_key()
    keyfile = key_file(k, tmp_path / "key.pem")
    env = dict(os.environ, OHS_RELEASE_KEY=str(keyfile), RELEASE_UNTRUSTED_KEY_OK="1",
               RELEASE_VERIFY_PUBKEY=keys[0][1], RELEASE_ALLOW_DIRTY="1", PYTHON=os.environ.get("PYTHON", "python3"))
    import sys
    env["PYTHON"] = sys.executable

    def run(version, *extra, dist=tmp_path / "dist"):
        return subprocess.run(["bash", str(REPO / "tools" / "release-feed.sh"), "--version", version, "--skip-tests",
                               "--dist", str(dist), "--releases", str(tmp_path / "release"), *extra],
                              capture_output=True, text=True, timeout=120, env=env)
    return {"run": run, "dist": tmp_path / "dist", "release": tmp_path / "release", "tmp": tmp_path}


def test_release_gathers_proves_signs_and_lists_what_to_remove(rel):
    built(rel["dist"], DEB.format(v="0.2.0"), b"deb 0.2.0 " * 100)
    built(rel["dist"], EXE.format(v="0.2.0"), b"exe 0.2.0 " * 100)
    r = rel["run"]("0.2.0", "--notes", "First with updates")
    assert r.returncode == 0, r.stderr
    feed = json.loads((rel["release"] / "update.json").read_text())
    assert feed["app"] == "packet" and {f["platform"] for f in feed["files"]} == {"linux", "windows"}
    assert "Cache-Control" in (rel["release"] / ".htaccess").read_text()
    assert "remove from the remote site" not in r.stdout
    # 0.2.1 for Linux only: Windows keeps 0.2.0, nothing to remove yet.
    built(rel["dist"], DEB.format(v="0.2.1"), b"deb 0.2.1 " * 100)
    r = rel["run"]("0.2.1")
    assert r.returncode == 0, r.stderr
    feed = json.loads((rel["release"] / "update.json").read_text())
    assert {(f["platform"], f["version"]) for f in feed["files"]} == {("linux", "0.2.1"), ("windows", "0.2.0")}
    assert f"remove from the remote site:\n  {DEB.format(v='0.2.0')}\n  {DEB.format(v='0.2.0')}.sha256\n" in r.stdout


def test_release_refuses_damaged_or_replaced_bytes_and_a_beta(rel):
    name = DEB.format(v="0.2.0")
    built(rel["dist"], name, b"good")
    (rel["dist"] / name).write_bytes(b"damaged")
    r = rel["run"]("0.2.0")
    assert r.returncode == 1 and "does not match its .sha256" in r.stderr
    built(rel["dist"], name, b"good")
    assert rel["run"]("0.2.0").returncode == 0
    built(rel["dist"], name, b"different bytes, same name")
    r = rel["run"]("0.2.0")
    assert r.returncode == 1 and "a fix is a new version" in r.stderr
    built(rel["dist"], DEB.format(v="0.2.0+beta1"), b"beta")
    assert rel["run"]("0.2.0+beta1").returncode == 1            # the feed names releases only


STUB = r"""#!/usr/bin/env bash
# A stand-in for {name}: logs its arguments, and for rsync/curl moves bytes
# between the release folder and $STUB_SITE as the real one would.
echo "{name} $*" >> "$STUB_LOG"
case "{name}" in
  rsync)
    last="${{@: -1}}"; src="${{@: -2:1}}"
    case "$last" in
      *:*/) exit 0 ;;                                   # the backup (remote -> local): nothing there yet
      *:*) cp "$src" "$STUB_SITE/${{last##*/}}" ;;
    esac ;;
  curl)
    url="${{@: -1}}"; f="${{url##*/}}"; f="${{f%%\?*}}"
    cat "$STUB_SITE/$f" ;;
esac
exit 0
"""


@pytest.fixture
def publish(rel):
    built(rel["dist"], DEB.format(v="0.2.0"), b"deb " * 50)
    assert rel["run"]("0.2.0").returncode == 0
    tmp = rel["tmp"]
    stubs, site, keys = tmp / "stubs", tmp / "site", tmp / "keys"
    for d in (stubs, site, keys):
        d.mkdir()
    for name in ("ssh", "rsync", "curl"):
        (stubs / name).write_text(STUB.format(name=name))
        (stubs / name).chmod(0o755)
    (keys / "id_ed25519").write_text("not a real key\n")
    (keys / "meta").write_text(f"handle=test\nexpires={int(time.time()) + 30 * 86400}\n")
    env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", STUB_LOG=str(tmp / "calls.log"),
               STUB_SITE=str(site), PUBLISH_KEY_DIR=str(keys), PUBLISH_BACKUP_ROOT=str(tmp / "backups"),
               PUBLISH_VERIFY_WAIT="0")

    def run(*args):
        r = subprocess.run(["bash", str(REPO / "tools" / "publish.sh"), "--releases", str(rel["release"]), *args],
                           capture_output=True, text=True, timeout=120, env=env)
        calls = (tmp / "calls.log").read_text().splitlines() if (tmp / "calls.log").exists() else []
        return r, calls
    return {"run": run, "site": site, "keys": keys}


def test_publish_uploads_what_the_feed_names_with_the_feed_last_and_verifies(publish):
    r, calls = publish["run"]("--remove", "OpenHamShack-Packet-Radio-0.1.2-linux-all.deb")
    assert r.returncode == 0, r.stdout + r.stderr
    uploads = [c.split()[-1].rsplit("/", 1)[-1] for c in calls if c.startswith("rsync") and ":public_html" in
               c.split()[-1] and not c.split()[-1].endswith("/")]
    assert uploads[-2:] == ["update.json.sig", "update.json"]
    assert set(uploads) == {DEB.format(v="0.2.0"), DEB.format(v="0.2.0") + ".sha256", ".htaccess",
                            "update.json.sig", "update.json"}
    assert any("rm -f -- public_html/desktop/packet/OpenHamShack-Packet-Radio-0.1.2-linux-all.deb" in c
               for c in calls)
    assert sum(c.startswith("curl") for c in calls) == 4                    # every served file, not .htaccess
    assert "every file verified over HTTPS" in r.stdout


def test_publish_dry_run_touches_nothing_and_no_key_means_no_upload(publish):
    r, calls = publish["run"]("--dry-run")
    assert r.returncode == 0 and "would upload: update.json" in r.stdout and calls == []
    (publish["keys"] / "meta").write_text(f"handle=test\nexpires={int(time.time()) + 600}\n")
    r, calls = publish["run"]()
    assert r.returncode == 1 and "no month upload key" in r.stdout and calls == []
    r, calls = publish["run"]("--remove", "../../index.html")
    assert r.returncode == 2 and calls == []


# -- tools/release.sh's own refusals, and tools/site-downloads.py ---------------------------------

def test_release_refuses_a_test_version_and_an_unknown_step(tmp_path):
    # Both before anything is built, changed or committed.
    for args in (["0.2.0+beta1"], ["0.2.0", "--only", "linux,ios"]):
        r = subprocess.run(["bash", str(REPO / "tools" / "release.sh"), *args], capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, HOME=str(tmp_path)))
        assert r.returncode != 0 and "release.sh:" in r.stderr, r.stderr
    assert not (tmp_path / "build").exists()


SITE_BUILD = '''import json, sys
from pathlib import Path
dist = Path(sys.argv[sys.argv.index("--dist") + 1])
data = json.loads((Path(__file__).resolve().parent.parent / "data" / "downloads.json").read_text())
for prod in ("packet", "nb"):
    names = [e["filename"] for e in data["artefacts"] if e["product"] == prod]
    (dist / "apps" / prod).mkdir(parents=True, exist_ok=True)
    (dist / "apps" / prod / "index.html").write_text(" ".join(names))
(dist / "downloads" / "index.html").write_text(" ".join(e["filename"] for e in data["artefacts"]))
(dist / "index.html").write_text(EXTRA)
'''


@pytest.fixture
def site(tmp_path):
    """A website in miniature: the live dist as the 0.1.2 release left it,
    and a site repository whose build.py writes pages from downloads.json."""
    old = {"linux-x86_64.AppImage": ("linux", "AppImage"), "linux-all.deb": ("linux", "deb"),
           "windows-x64.exe": ("windows", "installer"), "macos-arm64.dmg": ("macos", "dmg")}
    repo, live, files = tmp_path / "site", tmp_path / "site-dist", tmp_path / "files"
    (repo / "tools").mkdir(parents=True)
    (repo / "data").mkdir()
    arts = [{"product": "nb", "os": "linux", "kind": "deb", "filename": "nb-0.1.22.deb", "version": "0.1.22"}]
    (live / "downloads").mkdir(parents=True)
    sums = []
    for tail, (os_name, kind) in old.items():
        name = f"OpenHamShack-Packet-Radio-0.1.2-{tail}"
        built(live / "downloads", name, b"old " + tail.encode())
        sums.append(f"{hashlib.sha256(b'old ' + tail.encode()).hexdigest()}  {name}")
        arts.append({"product": "packet", "os": os_name, "kind": kind, "filename": name, "version": "0.1.2"})
        built(files, f"OpenHamShack-Packet-Radio-0.2.0-{tail}", b"new " + tail.encode())
    (live / "downloads" / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (repo / "data" / "downloads.json").write_text(json.dumps({"artefacts": arts}))
    (repo / "tools" / "check-site.py").write_text("import sys; sys.exit(0)\n")

    def run(stage_home="home"):
        # The live dist as the last publish built it, then the release's
        # build with whatever the site repository now says.
        (repo / "tools" / "build.py").write_text("EXTRA = 'home'\n" + SITE_BUILD)
        subprocess.run([__import__("sys").executable, str(repo / "tools" / "build.py"), "--dist", str(live)],
                       check=True)
        (repo / "tools" / "build.py").write_text(f"EXTRA = {stage_home!r}\n" + SITE_BUILD)
        return subprocess.run([__import__("sys").executable, str(REPO / "tools" / "site-downloads.py"),
                               "--version", "0.2.0", "--files", str(files), "--site", str(repo), "--live", str(live),
                               "--stage", str(tmp_path / "stage"), "--desktop", str(tmp_path / "desktop")],
                              capture_output=True, text=True, timeout=60)
    return {"run": run, "repo": repo, "stage": tmp_path / "stage", "desktop": tmp_path / "desktop"}


def test_the_site_step_changes_only_the_packet_entries_pages_and_files(site):
    r = site["run"]()
    assert r.returncode == 0, r.stderr
    removes = [ln.split(": ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("remove: ")]
    assert len(removes) == 8 and all("0.1.2" in p for p in removes)
    data = json.loads((site["repo"] / "data" / "downloads.json").read_text())
    assert {e["version"] for e in data["artefacts"] if e["product"] == "packet"} == {"0.2.0"}
    assert [e["version"] for e in data["artefacts"] if e["product"] == "nb"] == ["0.1.22"], "no other product moved"
    dl = site["stage"] / "downloads"
    assert not list(dl.glob("*0.1.2*")) and len(list(dl.glob("*0.2.0*"))) == 8
    sums = (dl / "SHA256SUMS").read_text()
    assert "0.1.2" not in sums and sums.count("0.2.0") == 4
    assert len(list(site["desktop"].rglob("*0.2.0*"))) == 8


def test_the_site_step_refuses_when_anything_else_on_the_site_would_change(site):
    # A page somebody changed and never published must stop the release,
    # not ride along with it - and nothing outside the stage is touched.
    r = site["run"]("home, edited and never published")
    assert r.returncode != 0 and "more than this release" in r.stderr and "\n  index.html" in r.stderr
    assert not site["desktop"].exists(), "the desktop folders are only filled once every check has passed"
