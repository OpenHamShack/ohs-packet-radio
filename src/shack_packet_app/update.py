"""Self-update: the half with no window in it.

Lifted from openhamshack-nb ohscore/update.py at 9f2a3ed (18/10/2026), which was
written "so that ATV, Teletext and DATV can take this file as it is"; the
only changes are the logger's name, the translation import (none: these
sentences are for the log and Help › Check for updates…, as NB's are in
practice), busy_reason (what this app must not interrupt) and INNO_ARGS
(below). Change NB's and this together, or say
here why not.

Brian's decision (07/10/2026): every OpenHamShack app updates itself, checks
on start-up, and what it installs is signed as 360 Technology. For Packet
Radio (D7, 18/10/2026) that is the feed
https://openhamshack.com/desktop/packet/update.json, whose manifest names
`"app": "packet"`, signed with the same release key as NB and Radio
(ed25519:dad6e2ca90668fce). Both are baked into every build from 0.2.0 and
must never move, or every installed copy is stranded (GUIDE §6).

This module reads the signed feed, decides whether this installation has
something to install, downloads it, proves the download is the file the
feed names, and builds the command that installs it. ui/update.py is the
banner and the timing.

Why the feed itself is signed, rather than trusting HTTPS and the
installer's own Authenticode signature: the website is one shared hosting
account, and whoever controls it - or anything between it and the
operator - could otherwise point every client at an old signed installer
with a known fault, or at another app's. A detached Ed25519 signature
over the manifest's exact bytes, by a key that never leaves the release
host, closes that: nothing in the manifest is believed until the
signature checks against a public key compiled in here, and every file is
then held to the size and SHA-256 the signed manifest gives. On Windows the
installer's Authenticode signer is checked as well. Linux has no second
signature: the .deb's sha256 in the signed manifest is the whole proof.
macOS is offered its .dmg the same way, proved the same way, and then only
opened in Finder for the operator to drag across (install_command says
why).

Kept to the standard library plus `cryptography` (Ed25519), imported only
when a signature is checked: a run from source without it still starts,
and its checks say "cannot check signatures" rather than failing to import.

Every check that fails is quiet: it is logged and kept for Help › Check for
updates…, never put in front of the operator as a decision. An update that
cannot be proved is the same as no update.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

# The manifest's format. A client refuses any other number: a later format
# is for a later client, which the old one cannot be trusted to read right.
SCHEMA = 1
# A manifest is a few hundred bytes; anything far bigger is not one.
MAX_MANIFEST = 64 * 1024
SIG_LEN = 64                                  # a raw Ed25519 signature
SIGNER = "360 Technology"                     # the Authenticode signer on Windows installers

# The release keys this build trusts: (name, raw Ed25519 public key in hex).
# The private half of each lives only on the release host, at
# ~/.config/openhamshack-release/ed25519.key (tools/update-feed.py keygen makes
# it and prints the line to put here). More than one may be listed at once,
# which is how a key is rotated: a release that lists the next key beside
# the current one goes out signed by the current one; once installations
# have it, manifests are signed by the next, and a later release drops the
# old line (openhamshack-nb docs/update-feed.md, Key rotation). A manifest names the key it
# was signed with by key_id(), so an unlisted key is refused by name.
# Were it empty, every manifest would be refused, which
# is the safe way round - an app that trusts nothing installs nothing.
RELEASE_KEYS: tuple[tuple[str, str], ...] = (
    # Made 07/10/2026 on the release host, as "360 Technology OpenHamShack
    # release key 2026" (the name is only a label; the key is the same);
    # key id ed25519:dad6e2ca90668fce.
    ("360 Technology OpenHamShack release key 2026", "41fa5d6688cbad76bc2278394aa9283502e1f96435f472d59fbee86d95d03e9d"),
)

PLATFORMS = ("linux", "windows", "macos")
ARCHES = ("amd64", "arm64", "all")
KINDS = {"linux": "deb", "windows": "exe", "macos": "dmg"}
_VERSION = re.compile(r"^\d+(\.\d+){1,3}$")
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# A file the manifest names: a bare name in the manifest's own folder. Not a
# path and not a URL - the client both fetches it from beside the manifest
# and saves it under this name, so anything else is refused outright.
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~-]*$")
# The "What's new" link: a page of the same site, by relative reference.
_NOTES_URL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~-]*(#[A-Za-z0-9_-]+)?$")
NOTES_MAX = 300


class UpdateError(Exception):
    """A check, download or install that did not happen; str() is a
    sentence for the log and for Help › Check for updates…."""


class NotTrusted(UpdateError):
    """Something that failed to prove itself: a manifest whose signature or
    contents do not check, or a download that is not the file it names."""


# ------------------------------------------------------------------ keys
def key_id(public: bytes) -> str:
    """The name a manifest gives its signing key: derived from the key
    itself, so it can neither collide by accident nor be claimed for a
    different key."""
    return "ed25519:" + hashlib.sha256(public).hexdigest()[:16]


def trusted_keys(keys=None) -> dict[str, bytes]:
    """{key id: raw public key} from (name, hex) pairs; RELEASE_KEYS by
    default."""
    out = {}
    for _name, hexkey in (RELEASE_KEYS if keys is None else keys):
        raw = bytes.fromhex(hexkey)
        if len(raw) != 32:
            raise ValueError(f"release key {_name!r} is not a 32-byte Ed25519 public key")
        out[key_id(raw)] = raw
    return out


def verify_signature(data: bytes, sig: bytes, public: bytes) -> None:
    """Raises NotTrusted unless `sig` is `public`'s Ed25519 signature of
    exactly `data`."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:
        raise NotTrusted(f"cannot check signatures: the cryptography module is missing ({e})") from None
    if len(sig) != SIG_LEN:
        raise NotTrusted(f"the signature is {len(sig)} bytes, not an Ed25519 signature's {SIG_LEN}")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(sig, data)
    except InvalidSignature:
        raise NotTrusted("the manifest's signature does not match it: it was changed after signing, "
                         "or signed by another key") from None


def describe_verifier() -> str:
    """For the start-up facts and --library-check: which library checks the
    feed's signatures, proved by checking RFC 8032's first test vector -
    so a frozen build that lost part of cryptography says so at start-up,
    and the Windows build's smoke test can look for it."""
    try:
        import cryptography
        pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        verify_signature(b"", sig, pub)
    except (ImportError, UpdateError) as e:
        return f"unavailable ({e})"
    return f"Ed25519 via cryptography {cryptography.__version__} (self-test passed)"


# ------------------------------------------------------------------ versions
def parse_version(v: str) -> tuple[int, ...]:
    if not isinstance(v, str) or not _VERSION.match(v):
        raise ValueError(f"{v!r} is not a version such as 0.1.9")
    return tuple(int(x) for x in v.split("."))


# A build that is not a release says so after a "+": the first macOS
# preview (07/10/2026) was built from main after 0.1.9 and is
# "0.1.9+mac-preview.1" in About. The label is build metadata, as in
# semantic versioning, and takes no part in the ordering: the preview
# counts as the release it was built after, so 0.1.10 is offered to it and
# 0.1.9 is not (it would be a step back to less code). Rejected: a fourth
# number (0.1.9.1), which would read as a release that never existed and
# could collide with one that later does; and a "-pre" suffix, which in
# semver orders BEFORE 0.1.9 and would have 0.1.9 offered as an "update".
# Only the installed version may carry one: the feed names releases, so
# its versions stay plain numbers (parse_version).
_LABEL = re.compile(r"^[0-9A-Za-z]+([.-][0-9A-Za-z]+)*$")


def installed_version(v: str) -> tuple[int, ...]:
    """The release an installed build orders as: its version without a
    "+label" (parse_version's rules for the rest)."""
    base, plus, label = v.partition("+") if isinstance(v, str) else (v, "", "")
    if plus and not _LABEL.match(label):
        raise ValueError(f"{v!r} is not a version such as 0.2.0 or 0.2.0+beta1")
    return parse_version(base)


# ------------------------------------------------------------------ the manifest
@dataclass(frozen=True)
class Entry:
    """One installable file the manifest offers."""
    platform: str          # linux | windows | macos
    arch: str              # amd64 | arm64 | all
    kind: str              # deb | exe | dmg
    version: str
    url: str               # a file name beside the manifest
    size: int
    sha256: str
    date: str              # YYYY-MM-DD
    notes: str


@dataclass(frozen=True)
class Manifest:
    app: str
    key: str
    published: str
    entries: tuple[Entry, ...]
    recommended: str = ""
    notes_url: str = ""


def _need(cond: bool, what: str) -> None:
    if not cond:
        raise NotTrusted(f"the manifest is malformed: {what}")


def read_manifest(data: bytes, sig: bytes, app: str | tuple[str, ...], keys=None) -> Manifest:
    """The manifest in `data`, after proving it: signed by a trusted key
    over these exact bytes, for this app, in a format this client reads.
    Raises NotTrusted otherwise. `app` is the app's package name, or every
    name its feed may give it, the current one first: a package that was
    renamed keeps its feed under the old name for the copies that only
    know that one (openhamshack-nb docs/update-feed.md, "The app's name"). The JSON is parsed once before the
    signature is checked, only to learn which key it names - json.loads of
    untrusted bytes is safe, and nothing from it is used until the
    signature holds."""
    keys = trusted_keys(keys)
    _need(len(data) <= MAX_MANIFEST, f"{len(data)} bytes, more than a manifest can be")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise NotTrusted(f"the manifest is not JSON ({e})") from None
    _need(isinstance(doc, dict), "not a JSON object")
    kid = doc.get("key")
    if not keys:
        raise NotTrusted("this build trusts no release key yet, so it cannot accept any update")
    if kid not in keys:
        raise NotTrusted(f"the manifest is signed with key {kid!r}, which this version does not trust")
    verify_signature(data, sig, keys[kid])
    # Signed by us from here on; what follows guards against our own
    # mistakes and against a manifest meant for another app or format.
    _need(doc.get("schema") == SCHEMA, f"format {doc.get('schema')!r}, this version reads {SCHEMA}")
    names = (app,) if isinstance(app, str) else tuple(app)
    if doc.get("app") not in names:
        raise NotTrusted(f"the manifest is for {doc.get('app')!r}, not {names[0]}")
    published = doc.get("published")
    _need(isinstance(published, str) and bool(_STAMP.match(published)), "published is not a UTC time")
    rec = doc.get("recommended", "")
    if rec:
        try:
            parse_version(rec)
        except ValueError as e:
            raise NotTrusted(f"the manifest is malformed: recommended: {e}") from None
    notes_url = doc.get("notesUrl", "")
    _need(notes_url == "" or (isinstance(notes_url, str) and bool(_NOTES_URL.match(notes_url))),
          "notesUrl is not a page of this site")
    files = doc.get("files")
    _need(isinstance(files, list), "no files list")
    entries = []
    seen = set()
    for f in files:
        _need(isinstance(f, dict), "a file entry is not an object")
        plat, arch = f.get("platform"), f.get("arch")
        # A platform or architecture this client has no word for is passed
        # over, not refused: it is signed by us like the rest, and is for
        # some later client. Refusing it would mean the first feed to offer
        # a new system (macOS, 07/10/2026) stopped every older copy from
        # ever seeing another update. Everything about the entries it does
        # know is still checked in full.
        if plat not in PLATFORMS or arch not in ARCHES:
            log.info("update check: passing over a %r %r entry this version does not know", plat, arch)
            continue
        _need(f.get("kind") == KINDS[plat], f"a {plat} file of kind {f.get('kind')!r}")
        _need((plat, arch) not in seen, f"two entries for {plat} {arch}")
        seen.add((plat, arch))
        try:
            parse_version(f.get("version"))
        except ValueError as e:
            raise NotTrusted(f"the manifest is malformed: {e}") from None
        url = f.get("url")
        _need(isinstance(url, str) and bool(_FILE.match(url)) and ".." not in url, f"file name {url!r}")
        size = f.get("size")
        _need(isinstance(size, int) and not isinstance(size, bool) and size > 0, f"size {size!r}")
        _need(isinstance(f.get("sha256"), str) and bool(_SHA256.match(f["sha256"])), "sha256")
        _need(isinstance(f.get("date"), str) and bool(_DATE.match(f["date"])), "date")
        notes = f.get("notes", "")
        _need(isinstance(notes, str) and len(notes) <= NOTES_MAX, "notes")
        entries.append(Entry(plat, arch, f["kind"], f["version"], url, size, f["sha256"], f["date"], notes))
    return Manifest(doc["app"], kid, published, tuple(entries), rec, notes_url)


# ------------------------------------------------------------------ this machine
def machine_arch() -> str:
    """The Debian architecture of this machine: dpkg's own answer where
    there is a dpkg (a 32-bit userland on a 64-bit kernel is armhf, not
    arm64, and only dpkg knows), else the kernel's, in Debian's words."""
    dpkg = shutil.which("dpkg")
    if dpkg:
        try:
            out = subprocess.run([dpkg, "--print-architecture"], capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    m = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)


def install_kind(package: str, here: Path | None = None) -> str:
    """How this copy was installed, so what an update replaces: "exe" for
    a frozen Windows build (an Inno Setup installer replaces it), "dmg"
    for a frozen Mac .app (copied out of a .dmg), "deb" for the .deb
    (which puts the program under /usr/lib/<package>/), "" for anything
    else - a run from source or an unpacked copy, which has no installer
    to run and is updated however it was put there."""
    if WINDOWS:
        return "exe" if getattr(sys, "frozen", False) else ""
    if MACOS:
        return "dmg" if getattr(sys, "frozen", False) else ""
    here = (here or Path(__file__)).resolve()
    return "deb" if Path("/usr/lib", package) in here.parents else ""


def busy_reason(st: dict) -> str:
    """Why nothing may be installed, or the program restarted, right now:
    an update must never cut a transmission short, nor drop a connected
    AX.25 session the other station is still in (a restart ends the link
    without a disconnect, and the peer waits out its retries). '' when
    nothing stands in the way. `st` is the controller's status() plus
    `sessions`, the number of connected sessions."""
    if st.get("transmitting"):
        return "transmitting"
    if st.get("sessions"):
        return "connected"
    return ""


# ------------------------------------------------------------------ choosing
@dataclass(frozen=True)
class Offer:
    entry: Entry
    url: str               # absolute, resolved against the manifest's URL
    rollback: bool         # going back to the version the manifest recommends
    notes_url: str = ""


def choose(m: Manifest, plat: str, arch: str, installed: str, manifest_url: str = "") -> Offer | None:
    """What, if anything, this installation should install. On Linux the
    machine's own architecture first, then "all" (pure Python, runs
    anywhere, FT8/FT4 encoding through WSJT-X instead of ohsdigi); Windows
    builds are amd64; a Mac takes its own architecture's .dmg only (the
    Mac build is arm64, and an Intel Mac is offered nothing rather than an
    app it cannot run). Newer than what is installed: an update. Older: only
    when it is the manifest's `recommended` version - a release withdrawn
    is answered by recommending the one before it, and nothing else may
    move a client backwards (an old manifest replayed must not)."""
    if plat == "windows":
        wanted = ["amd64"]
    elif plat == "macos":
        wanted = [arch]
    else:
        wanted = [arch, "all"] if arch != "all" else ["all"]
    by_arch = {e.arch: e for e in m.entries if e.platform == plat}
    entry = next((by_arch[a] for a in wanted if a in by_arch), None)
    if entry is None:
        return None
    have, offered = installed_version(installed), parse_version(entry.version)
    if offered > have:
        rollback = False
    elif offered < have and m.recommended and parse_version(m.recommended) == offered:
        rollback = True
    else:
        return None
    url = urllib.parse.urljoin(manifest_url, entry.url) if manifest_url else entry.url
    notes = urllib.parse.urljoin(manifest_url, m.notes_url) if (manifest_url and m.notes_url) else ""
    return Offer(entry, url, rollback, notes)


# ------------------------------------------------------------------ fetching
def user_agent(app: str, version: str) -> str:
    """The only thing a check says about itself: which program, which
    version - what any download from the site already tells its log."""
    return f"{app}/{version}"


def fetch(url: str, ua: str, limit: int, timeout: float = 20.0) -> bytes:
    """A plain GET, nothing added: no query string, no cookie, no
    identifier. Raises UpdateError (offline, refused, too big)."""
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"{url}: the server answered {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise UpdateError(f"{url}: could not be reached ({reason})") from None
    if len(data) > limit:
        raise UpdateError(f"{url}: more than {limit} bytes")
    return data


@dataclass(frozen=True)
class Result:
    """One check. status: "available" (offer set), "current", "offline"
    (could not fetch), "refused" (fetched, but did not prove itself),
    "unsupported" (nothing for this platform)."""
    status: str
    message: str
    offer: Offer | None = None
    when: float = 0.0


class Seen:
    """The newest manifest time this installation has accepted, kept in a
    small file beside its config. A manifest older than that is refused:
    an old, validly signed manifest served again (a stale CDN copy, or
    someone replaying one that recommended going back) must not undo a
    newer one."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def published(self) -> str:
        try:
            v = json.loads(self.path.read_text(encoding="utf-8")).get("published", "")
        except (OSError, ValueError, AttributeError):
            return ""
        return v if isinstance(v, str) and _STAMP.match(v) else ""

    def record(self, published: str) -> None:
        if published <= self.published():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"published": published}) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def check(feed_url: str, app: str | tuple[str, ...], version: str, *, plat: str, arch: str,
          seen: Seen | None = None, keys=None, fetcher: Callable[[str, str, int], bytes] = fetch,
          clock=time.time) -> Result:
    """Fetch the manifest and its signature, prove them, and say what this
    installation should do. Never raises: every outcome is a Result, and
    is logged. `app` as for read_manifest; the first name is the one the
    request and the log give."""
    now = clock()
    accepted, app = app, app if isinstance(app, str) else app[0]
    ua = user_agent(app, version)
    try:
        data = fetcher(feed_url, ua, MAX_MANIFEST)
        sig = fetcher(feed_url + ".sig", ua, SIG_LEN)
    except UpdateError as e:
        log.info("update check: %s", e)
        return Result("offline", str(e), when=now)
    try:
        m = read_manifest(data, sig, accepted, keys)
        if seen is not None and m.published < seen.published():
            raise NotTrusted(f"the manifest ({m.published}) is older than one already seen "
                             f"({seen.published()}); a stale or replayed copy")
        offer = choose(m, plat, arch, version, feed_url)
    except (NotTrusted, ValueError) as e:
        log.warning("update check: refused %s: %s", feed_url, e)
        return Result("refused", str(e), when=now)
    if seen is not None:
        seen.record(m.published)
    if offer is not None:
        what = "going back to" if offer.rollback else "new"
        log.info("update check: %s %s %s (%s %s)", app, what, offer.entry.version, plat, offer.entry.arch)
        return Result("available", f"{offer.entry.version} is available", offer, now)
    if not any(e.platform == plat for e in m.entries):
        log.info("update check: the feed has nothing for %s yet", plat)
        return Result("unsupported", f"no {plat} release is published yet", when=now)
    log.info("update check: %s %s is up to date", app, version)
    return Result("current", f"{version} is the newest", when=now)


# ------------------------------------------------------------------ downloading
def download(offer: Offer, folder: Path, ua: str, progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] | None = None, timeout: float = 30.0,
             opener=urllib.request.urlopen) -> Path:
    """The offered file into `folder`, under the manifest's name, proved
    against the signed manifest's size and SHA-256 before it is given its
    real name - so a file under that name has always been checked. Older
    downloads in the folder are removed: one update at a time."""
    e = offer.entry
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / e.url
    part = folder / (e.url + ".part")
    h = hashlib.sha256()
    got = 0
    req = urllib.request.Request(offer.url, headers={"User-Agent": ua})
    try:
        with opener(req, timeout=timeout) as r, open(part, "wb") as out:
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateError("download cancelled")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > e.size:
                    raise NotTrusted(f"{e.url} is bigger than the signed manifest says ({e.size} bytes)")
                h.update(chunk)
                out.write(chunk)
                if progress is not None:
                    progress(got, e.size)
    except urllib.error.HTTPError as err:
        part.unlink(missing_ok=True)
        raise UpdateError(f"{offer.url}: the server answered {err.code}") from None
    except (urllib.error.URLError, OSError) as err:
        part.unlink(missing_ok=True)
        raise UpdateError(f"{offer.url}: download failed ({getattr(err, 'reason', err)})") from None
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    if got != e.size or h.hexdigest() != e.sha256:
        part.unlink(missing_ok=True)
        raise NotTrusted(f"{e.url} does not match the signed manifest (size {got} of {e.size}, "
                         f"sha256 {h.hexdigest()[:12]}… not {e.sha256[:12]}…); not installing it")
    os.replace(part, final)
    for old in folder.iterdir():
        if old != final and old.is_file():
            old.unlink(missing_ok=True)
    log.info("update: downloaded and checked %s (%d bytes)", final, got)
    return final


# ------------------------------------------------------------------ Windows' own signature
def authenticode(path: Path) -> tuple[str, str]:
    """(status, signer subject) from Windows' own check of `path`'s
    Authenticode signature: PowerShell's Get-AuthenticodeSignature, which
    is WinVerifyTrust underneath, revocation and timestamp included -
    the same verdict Windows gives the installer when it runs. Chosen over
    calling WinVerifyTrust through ctypes because reading the signer's name
    that way takes four more crypt32 calls and their structures, where the
    cmdlet hands back both. The path travels in the environment, not in the
    command, so no quoting of it can go wrong."""
    ps = shutil.which("powershell.exe") or os.path.expandvars(
        r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:LB_UPDATE_FILE; "
              "[Console]::Out.WriteLine([string]$s.Status); "
              "if ($s.SignerCertificate) { [Console]::Out.WriteLine($s.SignerCertificate.Subject) }")
    env = dict(os.environ, LB_UPDATE_FILE=str(path))
    try:
        out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                             capture_output=True, text=True, timeout=60, env=env,
                             creationflags=0x08000000 if WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"cannot check the installer's signature ({e})") from None
    lines = out.stdout.strip().splitlines()
    return (lines[0].strip() if lines else "", lines[1].strip() if len(lines) > 1 else "")


def check_authenticode(path: Path, signer: str = SIGNER, query: Callable[[Path], tuple[str, str]] = authenticode) -> str:
    """Raises NotTrusted unless Windows calls `path`'s signature Valid and
    its certificate's organisation is `signer`. Returns the subject."""
    status, subject = query(Path(path))
    if status != "Valid":
        raise NotTrusted(f"{Path(path).name}: Windows says its signature is {status or 'missing'}; not running it")
    if not re.search(rf"(^|, )O={re.escape(signer)}(,|$)", subject):
        raise NotTrusted(f"{Path(path).name} is signed by {subject or 'nobody'}, not {signer}; not running it")
    return subject


# ------------------------------------------------------------------ installing
# Inno Setup's switches for an unattended update: no pages, no message
# boxes, never reboot by itself. /RELAUNCH=1 is ours: the installer's [Run]
# entry starts the program again only when it is given
# (packaging/windows/shack-packet.iss), since an unattended install skips
# the "Launch" tick box. NB's also passes /MERGETASKS=!plutodrivers; this
# installer has no Pluto driver task yet, and whoever adds one (0.2's SDR
# work) adds that switch here in the same commit.
INNO_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1")


@dataclass(frozen=True)
class Install:
    argv: list[str] | None      # what to run; None when it cannot be run from here
    manual: str                 # the same, for the operator to type, when it cannot


# Opening a .dmg mounts it and shows it in Finder: the new app beside a
# link to Applications, for the operator to drag across.
MAC_OPEN = "/usr/bin/open"


def install_command(kind: str, path: Path, pkexec: str | None = None) -> Install:
    """How to install the downloaded file. A .deb through apt (so its
    Depends are pulled in, as a first install's were) as root by pkexec,
    which asks for the system password in the desktop's own prompt; with
    no pkexec, the command to type instead. An .exe is run as it is: the
    installer is per user and elevates itself only for an all-users
    install.

    A .dmg is opened, not installed. While the Mac build is unsigned
    (ad-hoc only; docs/macos.md), installing it behind the operator's back
    would be wrong three ways. There is no second signature to check, as
    Authenticode is on Windows - only the feed's sha256. A file this
    program downloads carries no quarantine flag, so Gatekeeper - the one
    check a Mac user expects an unsigned app to pass through - would never
    look at it. And macOS ties the microphone permission to the app's code
    signature, which for an ad-hoc signature is the hash of that very
    build: a silent swap would leave transmit audio silent until the
    operator noticed and granted it again. Dragging the new copy across
    keeps the operator in the loop for all three. Once the app is signed
    with a Developer ID and notarised, the same ".dmg" kind can grow a
    real install (check the signer as check_authenticode does, replace
    the .app, relaunch)."""
    path = Path(path).resolve()
    if kind == "deb":
        cmd = ["apt-get", "install", "-y", str(path)]
        manual = "sudo " + " ".join(shlex.quote(c) for c in cmd)
        pk = pkexec if pkexec is not None else shutil.which("pkexec")
        return Install([pk, *cmd] if pk else None, manual)
    if kind == "exe":
        return Install([str(path), *INNO_ARGS], "")
    if kind == "dmg":
        return Install([MAC_OPEN, str(path)], "")
    raise UpdateError("this copy was not installed by a package, so it cannot update itself")


def spawn_detached(argv: list[str]) -> None:
    """Start `argv` so that it outlives this process and is not its child
    in any sense the desktop cares about - the installer that replaces
    this program, or this program starting again after it."""
    if WINDOWS:
        flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(argv, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(argv, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def relaunch_command(package: str) -> list[str] | None:
    """How the installed program is started again: the .deb's launcher on
    PATH (/usr/bin/<package>), or the frozen .exe itself on Windows."""
    if WINDOWS and getattr(sys, "frozen", False):
        return [sys.executable]
    found = shutil.which(package)
    return [found] if found else None


# ------------------------------------------------------------------ when
class Schedule:
    """When an automatic check is due: the first soon after start-up, then
    one a day while the program runs. Never while transmitting or
    recording (put off, not dropped: the next look after the over finds it
    still due), never with the setting off. A check asked for by hand
    counts as the day's. Timed by the wall clock on each look rather than by
    one long timer, so a laptop that slept through the 24 hours checks when
    it wakes."""

    INTERVAL = 24 * 3600.0
    # After a check that could not reach the site: an hour, not a day. A
    # laptop started before it found a network (a field day, a hotel)
    # should not wait until tomorrow; an hour of silence costs nothing.
    RETRY = 3600.0

    def __init__(self, interval: float = INTERVAL, retry: float = RETRY):
        self.interval = interval
        self.retry = retry
        self.last: float | None = None

    def due(self, now: float, enabled: bool, busy: str = "") -> bool:
        if not enabled or busy:
            return False
        return self.last is None or now - self.last >= self.interval

    def mark(self, now: float) -> None:
        self.last = now

    def retry_soon(self, now: float) -> None:
        """The check at `now` could not reach the site: due again after RETRY."""
        self.last = now - self.interval + self.retry
