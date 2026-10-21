"""Help › Save diagnostics: one zip a tester can email.

| file | what |
|---|---|
| `summary.json` | app and library versions, the system (no host or user name), when, the station's status |
| `settings.json` | the settings as saved - no secrets live there (settings.py keeps the LAN password apart) |
| `log.txt` | the recent program log, run through applog.redact with the known secrets |
| `log-file.txt` | the end of shack-packet.log on disk, redacted the same way: it reaches back past this run, to the one that crashed |
| `fatal.txt` | the end of fatal.log, where faulthandler writes a hard crash's stacks |
| `frames.jsonl` | the last frames heard and sent (Monitor's rows) |
| `timing.json` | TXDELAY, turnaround (and whether it was measured), channel counters, the link's set-up |
| `radio-events.jsonl` | what the radio link reported: unkeys, watchdogs, set-up, losses |
| `linktest.jsonl` | the end (1 MB) of the newest link test's own log (linkctl.py), when there has been one |
| `path-test.txt` | the last path test's report (Path test page), when this station has received one |
| `window.png` | the window, if the caller passes a picture of it |

The password is checked for once more after everything is written: a
bundle that contains it is not saved at all (and the test proves it).
The user's home folder is written `~` wherever it appears (a traceback's
file paths name the account, which is often the person), and the system
summary carries no host name.

Saving sends nothing: the tester saves the zip and chooses whom to email
it to. The opt-in upload (diagupload.py, from 0.2) sends the same texts,
made by contents() here, without the window picture.
"""
from __future__ import annotations

import io
import json
import platform
import time
import zipfile
from pathlib import Path

from . import __version__, applog, host
from .settings import to_dict


class LeakError(Exception):
    pass


def versions() -> dict:
    out = {"shack-packet-app": __version__, "python": platform.python_version()}
    for mod in ("shack_packet", "shack_rig", "shack_bandplan", "shack_i18n", "shack_client", "numpy"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except ImportError:
            out[mod] = None
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        out["PyQt6"], out["Qt"] = PYQT_VERSION_STR, QT_VERSION_STR
    except ImportError:
        out["PyQt6"] = None
    return out


def _rows(ctl, n: int = 500) -> str:
    with ctl._lock:
        rows = list(ctl.rows)[-n:]
    return "".join(json.dumps({"time": r.time, "dir": r.direction, "channel": r.channel, "type": r.type,
                               "aprs": r.aprs_kind, "fx25": r.fx25, "level": r.level_db, "text": r.text},
                              ensure_ascii=False) + "\n" for r in rows)


def _no_home(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "~") if len(home) > 1 else text


def secrets_of(ctl) -> list[str]:
    """Every secret the station holds (the LAN password, a controller's
    device token): what no bundle may contain."""
    return [v for v in ctl.secrets.values() if isinstance(v, str) and v]


def _newest_linktest() -> Path | None:
    try:
        logs = sorted((host.data_dir() / "linktest").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return logs[-1] if logs else None


def contents(ctl, *, now: float | None = None) -> dict[str, str]:
    """Every text file of the bundle, scrubbed: the user's home folder as
    `~`, known secrets and secret-looking values redacted, and checked once
    more - LeakError names the file if a secret is still in one. The saved
    zip and the upload (diagupload.py) are both made from this, so what a
    tester saves is what would have been sent."""
    secrets = secrets_of(ctl)
    now = time.time() if now is None else now
    summary = {"made": now, "versions": versions(), "system": host.system_summary(), "status": ctl.status()}
    files = {
        "summary.json": json.dumps(summary, indent=1, default=str),
        "settings.json": json.dumps(to_dict(ctl.settings), indent=1, ensure_ascii=False),
        "log.txt": applog.redact(applog.ring.text(), secrets),
        # The tester saves a bundle after restarting a program that closed,
        # so the ring alone has only the run that worked. The file is read
        # while this process's RotatingFileHandler still has it open.
        "log-file.txt": applog.redact(applog.tail(host.log_dir() / applog.LOG_NAME), secrets),
        "fatal.txt": applog.redact(applog.tail(host.log_dir() / applog.FATAL_NAME, 200_000), secrets),
        "frames.jsonl": _rows(ctl),
        "timing.json": json.dumps(ctl.timing(), indent=1, default=str),
        "radio-events.jsonl": "".join(json.dumps({"time": t, "kind": k, "reason": r, "detail": d}, default=str) + "\n"
                                      for t, k, r, d in list(ctl.link_events)),
    }
    newest = _newest_linktest()
    if newest is not None:
        files["linktest.jsonl"] = applog.redact(applog.tail(newest, 1_000_000), secrets)
    # A tester asked "what did the path test say?" has usually already
    # closed the page; the report rides along so nobody has to copy it.
    pt = getattr(ctl, "pathtest", None)
    report = pt.report() if pt is not None else ""
    if report:
        files["path-test.txt"] = report + "\n"
    out = {}
    for name, text in files.items():
        text = _no_home(text)
        for s in secrets:
            if len(s) >= 3 and s in text:
                raise LeakError(name)
        out[name] = text
    return out


def build(ctl, *, screenshot_png: bytes | None = None, now: float | None = None) -> bytes:
    files = contents(ctl, now=now)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
        if screenshot_png:
            z.writestr("window.png", screenshot_png)
    return buf.getvalue()


def default_name(now: float | None = None) -> str:
    return time.strftime("shack-packet-diagnostics-%Y%m%d-%H%M%S.zip", time.gmtime(now or time.time()))


def save(ctl, path: Path, **kw) -> Path:
    data = build(ctl, **kw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path
