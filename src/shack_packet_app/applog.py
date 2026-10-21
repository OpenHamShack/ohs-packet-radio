"""The program's own log: a rotating file in the data folder (2 MB x 5, as
NB's), and the last few thousand lines in memory for a diagnostics bundle
that has to be made even when the file cannot be written.

Log lines are for a developer and stay English (I18N.md rule 8); nothing
here reaches the window. Secrets never reach the log: the LAN password is
handed to the radio link and to nothing that logs, and `redact()` is run
over the log again when a bundle is made, in case a library ever prints one.
"""
from __future__ import annotations

import collections
import faulthandler
import logging
import logging.handlers
import re
import sys
import threading
import time

from . import host

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_NAME = "shack-packet.log"
FATAL_NAME = "fatal.log"


class RingHandler(logging.Handler):
    def __init__(self, capacity: int = 5000):
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=capacity)
        self._ring_lock = threading.Lock()
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:                           # noqa: BLE001 - a bad log call must not raise
            return
        with self._ring_lock:
            self.lines.append(line)

    def text(self) -> str:
        with self._ring_lock:
            return "\n".join(self.lines) + "\n"


ring = RingHandler()
_installed = False


def install(verbose: bool = False, to_file: bool = True) -> None:
    global _installed
    root = logging.getLogger()
    if not _installed:
        root.addHandler(ring)
        if to_file:
            try:
                host.log_dir().mkdir(parents=True, exist_ok=True)
                fh = logging.handlers.RotatingFileHandler(host.log_dir() / LOG_NAME, maxBytes=2_000_000,
                                                          backupCount=5, encoding="utf-8")
                fh.setFormatter(logging.Formatter(FORMAT))
                root.addHandler(fh)
            except OSError:
                pass                                # the ring still has it
        _installed = True
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


_SECRETISH = re.compile(r"(?i)(password|passwd|secret|token)(\W{1,3})([^\s,;\"']+)")


def redact(text: str, secrets=()) -> str:
    """Known secret values replaced, and anything that looks like
    `password=...` blanked."""
    # The pattern first: run after the known values, it took the "<" of
    # their "<redacted>" as part of the separator and wrote
    # "password=<<redacted>".
    text = _SECRETISH.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", text)
    for s in secrets:
        if s and len(s) >= 3:
            text = text.replace(s, "<redacted>")
    return text


_fatal_file = None
_on_crash: list = []


def on_crash(fn) -> None:
    """Something to run when an exception escapes: the window registers
    the radio link's unkey here, so a crash never leaves a radio keyed."""
    _on_crash.append(fn)


def _run_on_crash() -> None:
    for fn in list(_on_crash):
        try:
            fn()
        except Exception:                           # noqa: BLE001 - the next one must still run
            logging.getLogger(__name__).exception("crash handler %r failed", fn)


def install_crash_hooks() -> None:
    """Every crash leaves a traceback where a diagnostics bundle finds it.

    An exception that escapes (the main thread, a Qt slot, a worker
    thread) is logged at CRITICAL with its traceback, through the normal
    handlers, so it lands in shack-packet.log and the ring. Replacing
    sys.excepthook also stops PyQt6 turning an exception in a slot into a
    qFatal abort - the app logs it and carries on, which is what 0.1.0's
    Save diagnostics needed. The windowed Windows .exe has sys.stderr None,
    so without this a crash said nothing anywhere.

    A hard crash (segfault, abort) cannot run Python, so faulthandler
    writes its stacks straight to fatal.log beside the program log. That is
    its own file, not shack-packet.log: a second handle on the rotating log
    stops RotatingFileHandler renaming it on Windows. diagnostics puts it
    in the bundle.
    """
    global _fatal_file
    log = logging.getLogger("crash")

    def hook(kind, value, tb):
        log.critical("unhandled exception", exc_info=(kind, value, tb))
        _run_on_crash()

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.critical("unhandled exception in thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook
    if _fatal_file is None:
        try:
            host.log_dir().mkdir(parents=True, exist_ok=True)
            # Kept open for the life of the process: faulthandler writes to
            # the descriptor from a signal handler, with no chance to open one.
            _fatal_file = open(host.log_dir() / FATAL_NAME, "a", encoding="utf-8")
            _fatal_file.write(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            _fatal_file.flush()
            faulthandler.enable(_fatal_file, all_threads=True)
        except (OSError, RuntimeError):
            log.warning("faulthandler not enabled", exc_info=True)


def tail(path, limit: int = 1_000_000) -> str:
    """The last `limit` bytes of a log file the program may still have open
    for writing (Python opens files share-read on Windows, so reading one
    beside its RotatingFileHandler works). A missing file is an empty
    string; anything else that stops the read is said in the text, since
    the bundle is still worth having without it."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except FileNotFoundError:
        return ""
    except OSError as e:
        return f"[could not read {getattr(path, 'name', path)}: {e}]\n"
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text = "[...]" + text[text.find("\n") + 1:]
    return text
