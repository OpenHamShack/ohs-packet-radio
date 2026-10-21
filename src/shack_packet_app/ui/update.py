"""The window's half of self-update: when to check, the banner, Help ›
Check for updates…, and installing (update.py does the proving).

Adapted from openhamshack-nb ohscore/ui/update.py at 9f2a3ed: the same phases,
timing and rules, with this window's widgets and translation style
(`tr("… %1").replace("%1", …)`) in place of NB's bridge and controls.

- **Checked at start-up, never delaying it.** The first check is a few
  seconds after the window shows, on a thread, then the schedule looks
  every ten minutes and checks once a day (update.Schedule). Only an
  installed copy checks by itself (the .deb, the Windows installer, the
  Mac .app): a run from source or an AppImage has nothing this can
  install, and the tests and the screenshot tool never fetch anything.
  Help › Check for updates… checks on demand whatever the copy and the
  setting.
- **Quiet when it fails.** Offline (or 20i StackProtect's 401 page behind
  a VPN: GUIDE §5), refused or broken checks are logged and shown in Help ›
  Check for updates…, never as a pop-up.
- **A banner, not a dialog**, above the pages. Later hides it until the
  next start or a check by hand.
- **Never in the radio's way.** Install and restart is refused while
  transmitting or while a station is connected (update.busy_reason), the
  automatic check waits until then, and a download that finishes in the
  middle waits for another click. The restart is a normal close
  (closeEvent disarms, unkeys and saves) and the new copy starts only
  after this process has exited (atexit), so the two never hold the radio
  at once.
- **Linux:** pkexec apt-get install, then Restart now; without pkexec the
  banner gives the command. **Windows:** the signer is checked as well,
  and the installer runs unattended after exit and starts the program
  again (packaging/windows/shack-packet.iss, /RELAUNCH=1). **macOS:** the
  .dmg is proved and opened in Finder for the operator to drag across.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout)

from .. import PACKAGE, UPDATE_APP, UPDATE_FEED, __version__, host, update

log = logging.getLogger(__name__)


def _run(argv: list[str]) -> tuple[int, str]:
    import subprocess
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    return res.returncode, (res.stdout + res.stderr)[-2000:]


def _at_exit(argv: list[str]) -> None:
    atexit.register(update.spawn_detached, argv)


class UpdateBanner(QFrame):
    """The strip above the pages. Draws whatever UpdateController.banner_state() says."""

    primary = pyqtSignal()
    later = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(160)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(8)
        lay.addWidget(self.bar)
        self.btn_primary = QPushButton()
        self.btn_primary.setObjectName("primary")
        self.btn_later = QPushButton(self.tr("Later"))
        lay.addWidget(self.btn_primary)
        lay.addWidget(self.btn_later)
        self.btn_primary.clicked.connect(self.primary)
        self.btn_later.clicked.connect(self.later)
        self.hide()

    def show_state(self, s: dict | None) -> None:
        if not s:
            self.hide()
            return
        self.label.setText(s["text"])
        self.label.setToolTip(s.get("tip", ""))
        self.bar.setVisible(s.get("progress") is not None)
        if s.get("progress") is not None:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * s["progress"]))
        self.btn_primary.setVisible(bool(s.get("primary")))
        self.btn_primary.setText(s.get("primary") or "")
        self.btn_primary.setEnabled(not s.get("blocked"))
        self.btn_primary.setToolTip(s.get("blocked", ""))
        self.btn_later.setVisible(s.get("later", True))
        self.btn_later.setText(s.get("later_text") or self.tr("Later"))
        self.show()


class UpdateDialog(QDialog):
    """Help › Check for updates…: what this copy is, what the last check
    found (including why it failed), and the same choice as the banner."""

    def __init__(self, ctl: "UpdateController"):
        super().__init__(ctl.win)
        self.ctl = ctl
        self.setWindowTitle(self.tr("Check for updates"))
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        head = QLabel(self.tr("OpenHamShack Packet Radio %1").replace("%1", ctl.version))
        head.setObjectName("heading")
        lay.addWidget(head)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.about = QLabel("")
        self.about.setWordWrap(True)
        self.about.setObjectName("dim")
        lay.addWidget(self.about)
        row = QHBoxLayout()
        self.btn_install = QPushButton(self.tr("Download") if ctl.kind == "dmg" else self.tr("Install and restart"))
        self.btn_install.setObjectName("primary")
        self.btn_install.clicked.connect(self._install)
        self.btn_again = QPushButton(self.tr("Check again"))
        self.btn_again.clicked.connect(lambda: ctl.check(manual=True))
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.close)
        row.addWidget(self.btn_install)
        row.addStretch(1)
        row.addWidget(self.btn_again)
        row.addWidget(close)
        lay.addLayout(row)
        self.refresh()

    def _install(self) -> None:
        self.close()
        self.ctl.install()

    def refresh(self) -> None:
        c = self.ctl
        self.status.setText(c.status_text())
        if c.auto_enabled():
            about = self.tr("Automatic checks are on (Settings › Internet). A check fetches %1 and its signature, "
                            "and sends nothing but the program's name and version.")
        else:
            about = self.tr("Automatic checks are off (Settings › Internet). A check fetches %1 and its signature, "
                            "and sends nothing but the program's name and version.")
        self.about.setText(about.replace("%1", c.feed_url))
        offer = c.result.offer if c.result is not None else None
        self.btn_install.setVisible(offer is not None and bool(c.kind))
        self.btn_install.setEnabled(not c.busy())
        self.btn_install.setToolTip(c.busy_text())
        self.btn_again.setEnabled(not c.checking)


class UpdateController(QObject):
    """One per window. Everything that could block - the check, the
    download, Windows' signature check, apt - runs on a thread and comes
    back through a signal; the rest happens on the GUI thread."""

    _checked = pyqtSignal(object)
    _progress = pyqtSignal(int, int)
    _fetched = pyqtSignal(object)
    _applied = pyqtSignal(object)

    FIRST_DELAY_MS = 8_000
    TICK_MS = 10 * 60_000

    def __init__(self, win, *, feed_url: str | None = None, keys=None, plat: str | None = None,
                 arch: str | None = None, kind: str | None = None, version: str | None = None,
                 runner=_run, at_exit=_at_exit, authenticode=update.authenticode, pkexec: str | None = None,
                 clock=time.time, fetcher=update.fetch, opener=None):
        super().__init__(win)
        self.win = win
        self.feed_url = feed_url or os.environ.get("SHACK_PACKET_UPDATE_URL", "").strip() or UPDATE_FEED
        self.version = version if version is not None else __version__
        self.keys = keys
        self.plat = plat or ("windows" if update.WINDOWS else "macos" if update.MACOS else "linux")
        self._arch = arch
        self.kind = update.install_kind(PACKAGE) if kind is None else kind
        self.runner, self.at_exit, self.authenticode = runner, at_exit, authenticode
        self.pkexec = pkexec
        self.clock, self.fetcher, self.opener = clock, fetcher, opener
        self.seen = update.Seen(host.config_dir() / "update-seen.json")
        self.folder = host.data_dir() / "updates"
        self.schedule = update.Schedule()
        self.result: update.Result | None = None
        self.checking = False
        self.phase = ""              # "", offer, downloading, ready, installing, installed, opened, failed, later
        self.message = ""
        self.done = 0.0
        self.file: Path | None = None
        self.dismissed = ""
        self._manual = ""
        self._busy_seen = ""
        self.banner: UpdateBanner | None = None
        self.dialog: UpdateDialog | None = None
        self.timer: QTimer | None = None
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._fetched.connect(self._on_fetched)
        self._applied.connect(self._on_applied)

    # ------------------------------------------------------------ wiring
    @property
    def arch(self) -> str:
        if self._arch is None:
            self._arch = update.machine_arch() if self.plat in ("linux", "macos") else "amd64"
        return self._arch

    def attach(self, banner: UpdateBanner) -> None:
        self.banner = banner
        banner.primary.connect(self._primary)
        banner.later.connect(self.later)
        self._render()

    def start(self) -> None:
        """Called once the window is showing: automatic checks, for an
        installed copy only."""
        if self.timer is not None or not self.kind:
            return
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(self.FIRST_DELAY_MS, self.tick)

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.stop()

    def auto_enabled(self) -> bool:
        return bool(self.win.settings.internet.check_updates)

    def busy(self) -> str:
        st = dict(self.win.ctl.status())
        st["sessions"] = sum(1 for s in list(self.win.ctl.sessions.values()) if s.connected)
        return update.busy_reason(st)

    def busy_text(self) -> str:
        b = self.busy()
        if b == "transmitting":
            return self.tr("Not while transmitting: an update never interrupts an over.")
        if b == "connected":
            return self.tr("Not while a station is connected: disconnect first.")
        return ""

    def poll(self) -> None:
        """The window's tick: redraw when transmitting or a connection
        starts or ends, so the button's state is never stale."""
        b = self.busy()
        if b != self._busy_seen:
            self._busy_seen = b
            if self.phase:
                self._render()
            self._refresh_dialog()

    # ------------------------------------------------------------ checking
    def tick(self) -> None:
        if self.schedule.due(self.clock(), self.auto_enabled(), self.busy()):
            self.check(manual=False)

    def check(self, manual: bool = False) -> bool:
        """Start a check on a thread; False if one is already running."""
        if self.checking:
            return False
        self.checking = True
        if manual:
            self.dismissed = ""
        self.schedule.mark(self.clock())
        args = dict(plat=self.plat, arch=self.arch, seen=self.seen, keys=self.keys, fetcher=self.fetcher,
                    clock=self.clock)
        threading.Thread(target=lambda: self._checked.emit(
            update.check(self.feed_url, UPDATE_APP, self.version, **args)), daemon=True,
            name="update-check").start()
        self._refresh_dialog()
        return True

    def _on_checked(self, res: update.Result) -> None:
        self.checking = False
        self.result = res
        if res.status == "offline":
            self.schedule.retry_soon(res.when)
            if "answered 401" in res.message:
                # GUIDE §5: openhamshack.com's StackProtect answers VPN and
                # datacentre addresses with a 401 page, which is not "no update".
                log.info("update check: the site refused this address (401): a VPN or datacentre address "
                         "is challenged by the host's protection, so updates cannot be checked from here")
        if res.offer is not None and self.phase in ("", "failed") and res.offer.entry.version != self.dismissed:
            self.phase, self.message = "offer", ""
        elif res.offer is None and self.phase == "offer":
            self.phase = ""
        self._render()
        self._refresh_dialog()

    def open_dialog(self) -> UpdateDialog:
        if self.dialog is None:
            self.dialog = UpdateDialog(self)
            self.dialog.finished.connect(lambda _r: setattr(self, "dialog", None))
        self.dialog.show()
        self.dialog.raise_()
        self.check(manual=True)
        return self.dialog

    def status_text(self) -> str:
        if self.checking:
            return self.tr("Checking…")
        r = self.result
        if r is None:
            return self.tr("Not checked yet.")
        when = time.strftime("%H:%M", time.localtime(r.when))
        if r.status == "available":
            e = r.offer.entry
            text = (self.tr("Version %1 is recommended instead of this one (released %2).") if r.offer.rollback
                    else self.tr("Version %1 is available (released %2).")).replace("%1", e.version) \
                .replace("%2", e.date)
            if e.notes:
                text += " " + self.tr("What's new: %1").replace("%1", e.notes)
            if not self.kind:
                text += " " + self.tr("This copy was not installed from an installer, so update it the way it "
                                      "was put there.")
            return text
        if r.status == "current":
            return self.tr("Up to date: %1 is the newest (checked %2).").replace("%1", self.version) \
                .replace("%2", when)
        if r.status == "offline":
            return self.tr("Could not check at %1: %2. It is tried again later.").replace("%1", when) \
                .replace("%2", r.message)
        if r.status == "unsupported":
            return self.tr("Checked at %1: %2.").replace("%1", when).replace("%2", r.message)
        return self.tr("An update was found at %1 but refused, because it could not be proved genuine: %2.") \
            .replace("%1", when).replace("%2", r.message)

    # ------------------------------------------------------------ the banner's buttons
    def _primary(self) -> None:
        if self.phase == "installed":
            self.restart()
        elif self.phase == "opened":
            self._apply()
        else:
            self.install()

    def later(self) -> None:
        offer = self.result.offer if self.result is not None else None
        if offer is not None:
            self.dismissed = offer.entry.version
        self.phase = "later" if self.phase == "installed" else ""
        self.message = ""
        self._render()

    def install(self) -> None:
        """Download and prove the file, then install it - or, if the
        station became busy meanwhile, wait for another click."""
        offer = self.result.offer if self.result is not None else None
        if offer is None or self.phase in ("downloading", "installing"):
            return
        if not self.kind:
            self._fail(self.tr("This copy was not installed from an installer, so it cannot install an update "
                               "itself."))
            return
        if self.busy():
            self._render()
            return
        if self.phase == "ready" and self.file is not None and self.file.exists():
            self._apply()
            return
        self.phase, self.done, self.message = "downloading", 0.0, ""
        self._render()
        ua = update.user_agent(UPDATE_APP, self.version)

        def work():
            try:
                kw = {"opener": self.opener} if self.opener is not None else {}
                path = update.download(offer, self.folder, ua, progress=lambda a, b: self._progress.emit(a, b), **kw)
                if self.kind == "exe":
                    update.check_authenticode(path, query=self.authenticode)
                self._fetched.emit(path)
            except update.UpdateError as e:
                self._fetched.emit(e)
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _on_progress(self, got: int, size: int) -> None:
        self.done = got / size if size else 0.0
        self._render()

    def _on_fetched(self, res) -> None:
        if isinstance(res, Exception):
            log.warning("update: %s", res)
            self._fail(self.tr("The update was not installed: %1").replace("%1", str(res)))
            return
        self.file = res
        self.phase = "ready"
        if self.busy():
            self._render()                  # finished mid-over: waits for the operator
            return
        self._apply()

    def _apply(self) -> None:
        if self.busy():
            self._render()
            return
        try:
            cmd = update.install_command(self.kind, self.file, pkexec=self.pkexec)
        except update.UpdateError as e:
            self._fail(str(e))
            return
        v = self.result.offer.entry.version
        if self.kind == "exe":
            log.info("update: running %s after exit", cmd.argv)
            self.phase = "installing"
            self.message = self.tr("Installing %1; the program starts again by itself.").replace("%1", v)
            self._render()
            self.at_exit(cmd.argv)
            self.win.close()
            return
        if self.kind == "dmg":
            rc, out = self.runner(cmd.argv)
            if rc != 0:
                self._fail(self.tr("%1 is downloaded and checked, but could not be opened: open %2 in Finder.")
                           .replace("%1", v).replace("%2", str(self.file)))
                return
            log.info("update: opened %s for the operator to install", self.file)
            self.phase = "opened"
            self._render()
            return
        if cmd.argv is None:
            self._fail(self.tr("%1 is downloaded and checked. Install it from a terminal with: %2")
                       .replace("%1", v).replace("%2", cmd.manual))
            return
        self.phase = "installing"
        self.message = self.tr("Installing %1: your system asks for your password…").replace("%1", v)
        self._render()
        self._manual = cmd.manual
        threading.Thread(target=lambda: self._applied.emit(self.runner(cmd.argv)), daemon=True,
                         name="update-install").start()

    def _on_applied(self, res: tuple[int, str]) -> None:
        rc, out = res
        v = self.result.offer.entry.version
        if rc == 0:
            log.info("update: installed %s %s", PACKAGE, v)
            self.phase, self.message = "installed", ""
        elif rc in (126, 127):
            log.info("update: install not authorised (pkexec %d)", rc)
            self.phase = "ready"
            self.message = self.tr("The password prompt was closed, so nothing was installed.")
        else:
            log.warning("update: install failed (%d): %s", rc, out.strip()[-500:])
            self._fail(self.tr("Installing %1 failed. From a terminal: %2").replace("%1", v)
                       .replace("%2", self._manual))
            return
        self._render()

    def restart(self) -> None:
        if self.busy():
            self._render()
            return
        cmd = update.relaunch_command(PACKAGE)
        if cmd is None:
            self._fail(self.tr("Close the program and start it again to use the new version."))
            return
        self.at_exit(cmd)
        self.win.close()

    def _fail(self, text: str) -> None:
        self.phase, self.message = "failed", text
        self._render()

    # ------------------------------------------------------------ showing it
    def _refresh_dialog(self) -> None:
        if self.dialog is not None:
            self.dialog.refresh()

    def banner_state(self) -> dict | None:
        """The banner's whole content (None: hidden), so tests read it and
        the banner only draws it."""
        offer = self.result.offer if self.result is not None else None
        if not self.phase or self.phase == "later" or offer is None:
            return None
        v = offer.entry.version
        blocked = self.busy_text()
        s = {"text": "", "tip": offer.entry.notes, "blocked": blocked}
        install = self.tr("Download") if self.kind == "dmg" else self.tr("Install and restart")
        if self.phase == "offer":
            s["text"] = (self.tr("Packet Radio %1 is recommended instead of %2").replace("%1", v)
                         .replace("%2", self.version) if offer.rollback
                         else self.tr("Packet Radio %1 is available").replace("%1", v))
            s["primary"] = install
        elif self.phase == "downloading":
            s.update(text=self.tr("Downloading Packet Radio %1… %2 %").replace("%1", v)
                     .replace("%2", str(int(100 * self.done))), progress=self.done, later=False)
        elif self.phase == "ready":
            s["text"] = self.message or self.tr("Packet Radio %1 is downloaded and checked").replace("%1", v)
            if blocked:
                s["text"] += " - " + blocked
            s["primary"] = install
        elif self.phase == "installing":
            s.update(text=self.message, later=False)
        elif self.phase == "opened":
            s.update(text=self.tr("Packet Radio %1 is open in Finder: quit this one, drag the new one onto "
                                  "Applications, then start it again.").replace("%1", v),
                     primary=self.tr("Show it again"), blocked="")
        elif self.phase == "installed":
            s.update(text=self.tr("Packet Radio %1 is installed. Restart to use it.").replace("%1", v),
                     primary=self.tr("Restart now"))
        elif self.phase == "failed":
            s.update(text=self.message, later_text=self.tr("Close"), blocked="")
        return s

    def _render(self) -> None:
        if self.banner is not None:
            self.banner.show_state(self.banner_state())
