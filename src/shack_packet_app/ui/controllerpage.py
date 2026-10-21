"""Settings › Controller: find an OpenHamShack controller, pair with it, and
see its radios - which of them it has reserved or is using, and so which
this computer will leave alone (ohsclient.py, Brian's D1).

Shaped on openhamshack-nb ohscore/ui/controller.py (find, address, user name,
password and code, Pair, Unpair), without NB's logbook sync: Packet Radio
pairs only to read the controller's radio list in 0.2.

Everything that talks to the network runs on a thread and reports back
through a signal; the page never blocks the window. The password is used
once, for the token exchange, and never stored; the device token goes to
the dialog's copy of the secrets, which the window saves to secrets.json
when the dialog is accepted.
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from .. import ohsclient


class _Worker(QObject):
    done = pyqtSignal(str, object)          # (what, result or exception)

    def run(self, what: str, fn) -> None:
        def go():
            try:
                res = fn()
            except Exception as e:          # noqa: BLE001 - shown on the page, as a sentence
                res = e
            self.done.emit(what, res)
        threading.Thread(target=go, name=f"controller-{what}", daemon=True).start()


def radio_line(r: ohsclient.RemoteRadio) -> str:
    """One radio of the controller's, for a list: its name, address and state."""
    from PyQt6.QtCore import QCoreApplication as Q
    where = f" ({r.address})" if r.address else ""
    if r.state == "reserved":
        state = Q.translate("ControllerPage", "reserved by %1: not used from this computer").replace(
            "%1", r.reserved_by or Q.translate("ControllerPage", "the controller"))
    elif r.state == "on-air":
        state = Q.translate("ControllerPage", "ON AIR with %1: not used from this computer").replace("%1", r.holder)
    elif r.state == "in-use":
        state = Q.translate("ControllerPage", "in use by %1: not used from this computer").replace(
            "%1", r.holder or Q.translate("ControllerPage", "an app waiting for it"))
    elif r.state == "absent":
        state = Q.translate("ControllerPage", "not connected to the controller")
    else:
        state = Q.translate("ControllerPage", "free")
    return f"{r.label}{where}: {state}"


class ControllerPage(QWidget):
    """`client` is an ohsclient.ControllerClient over the dialog's copies of
    the controller settings and the secrets."""

    def __init__(self, client: ohsclient.ControllerClient, parent=None):
        super().__init__(parent)
        self.client = client
        self.worker = _Worker(self)
        self.worker.done.connect(self._done)
        lay = QVBoxLayout(self)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setObjectName("heading")
        lay.addWidget(self.state)
        about = QLabel(self.tr("Pairing lets this program see which radios your controller manages. A radio the "
                               "controller has reserved or is using is never opened from this computer; radios on "
                               "this computer's own USB, and network radios the controller does not list, are used "
                               "directly as before."))
        about.setWordWrap(True)
        about.setObjectName("dim")
        lay.addWidget(about)
        row = QHBoxLayout()
        self.find_btn = QPushButton(self.tr("Find controllers"))
        self.find_btn.clicked.connect(self.find)
        row.addWidget(self.find_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.found = QListWidget()
        self.found.setMaximumHeight(90)
        self.found.itemClicked.connect(lambda it: self.address.setText(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.found)
        self.address = QLineEdit(client.settings.url)
        self.address.setPlaceholderText(self.tr("Controller address, for example http://openhamshack.local/"))
        lay.addWidget(self.address)
        creds = QHBoxLayout()
        self.user = QLineEdit(client.settings.user)
        self.user.setPlaceholderText(self.tr("User name"))
        self.password = QLineEdit()
        self.password.setPlaceholderText(self.tr("Password"))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.totp = QLineEdit()
        self.totp.setPlaceholderText(self.tr("Code (if asked)"))
        self.totp.setMaximumWidth(130)
        for w in (self.user, self.password, self.totp):
            creds.addWidget(w)
        lay.addLayout(creds)
        acts = QHBoxLayout()
        self.pair_btn = QPushButton(self.tr("Pair"))
        self.pair_btn.setObjectName("primary")
        self.pair_btn.clicked.connect(self.pair)
        self.list_btn = QPushButton(self.tr("List its radios"))
        self.list_btn.clicked.connect(self.list_radios)
        self.unpair_btn = QPushButton(self.tr("Unpair"))
        self.unpair_btn.clicked.connect(self.unpair)
        for b in (self.pair_btn, self.list_btn, self.unpair_btn):
            acts.addWidget(b)
        acts.addStretch(1)
        lay.addLayout(acts)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        self.radios = QListWidget()
        lay.addWidget(self.radios, 1)
        self.refresh()
        if client.paired:
            self.list_radios()

    def refresh(self) -> None:
        s = self.client.settings
        if self.client.paired:
            self.state.setText(self.tr("Paired with %1 as %2.").replace("%1", s.name or s.url).replace("%2", s.user))
        else:
            self.state.setText(self.tr("Not paired with a controller."))
        self.unpair_btn.setEnabled(self.client.paired)
        self.list_btn.setEnabled(self.client.paired)

    def _say(self, text: str, ok: bool = True) -> None:
        self.result.setText(text)
        self.result.setObjectName("good" if ok else "bad")
        self.result.setStyleSheet("")

    # ---------------------------------------------------------- actions
    def find(self) -> None:
        self.found.clear()
        self.find_btn.setEnabled(False)
        self._say(self.tr("Looking for controllers on this network…"))
        self.worker.run("find", lambda: self.client.discover(timeout=3.0))

    def pair(self) -> None:
        url, user, pw = self.address.text().strip(), self.user.text().strip(), self.password.text()
        if not url:
            self._say(self.tr("Type the controller's address, or find one first."), False)
            return
        if not user or not pw:
            self._say(self.tr("Pairing needs your controller user name and password."), False)
            return
        self.pair_btn.setEnabled(False)
        self._say(self.tr("Pairing…"))
        code = self.totp.text().strip() or None
        self.worker.run("pair", lambda: self.client.pair(url, user, pw, code))

    def list_radios(self) -> None:
        self.list_btn.setEnabled(False)
        self.worker.run("radios", self.client.radios)

    def unpair(self) -> None:
        self.client.unpair()
        self.radios.clear()
        self._say(self.tr("Unpaired. Radios are opened from this computer as if there were no controller."))
        self.refresh()

    # ---------------------------------------------------------- results
    def _done(self, what: str, res) -> None:
        from shack_client.discovery import DiscoveryUnavailable
        from shack_client.pairing import PairingError
        from shack_client.transport import Offline
        self.password.clear()
        self.find_btn.setEnabled(True)
        self.pair_btn.setEnabled(True)
        if what == "find":
            if isinstance(res, DiscoveryUnavailable):
                self._say(self.tr("Finding controllers needs the zeroconf library. Type the address instead."),
                          False)
            elif isinstance(res, Exception):
                self._say(self.tr("Could not look for controllers: %1").replace("%1", str(res)), False)
            elif not res:
                self._say(self.tr("No controller answered. Type its address instead."), False)
            else:
                for c in res:
                    it = QListWidgetItem(f"{c.name}  {c.url}")
                    it.setData(Qt.ItemDataRole.UserRole, c.url)
                    self.found.addItem(it)
                self._say(self.tr("%n controller(s) found: pick one.", "", len(res)))
            return
        if what == "radios":
            self.refresh()
            if isinstance(res, Offline):
                self._say(self.tr("The controller did not answer."), False)
            elif isinstance(res, ohsclient.ControllerError) and res.code in ("token_revoked", "bad_credentials"):
                self._say(self.tr("The controller no longer knows this computer: pair again."), False)
            elif isinstance(res, Exception):
                self._say(self.tr("The controller did not list its radios: %1").replace("%1", str(res)), False)
            else:
                self.radios.clear()
                for r in res:
                    self.radios.addItem(radio_line(r))
                if not res:
                    self._say(self.tr("The controller manages no radios."))
                else:
                    self._say(self.tr("%n radio(s) on the controller.", "", len(res)))
            return
        if isinstance(res, Offline):
            self._say(self.tr("The controller did not answer at that address."), False)
        elif isinstance(res, PairingError):
            if res.code == "totp_required":
                self._say(self.tr("This account has a second factor: type the code from your app and pair "
                                  "again."), False)
            else:
                self._say(res.message, False)
        elif isinstance(res, Exception):
            self._say(self.tr("Pairing failed: %1").replace("%1", str(res)), False)
        else:
            self._say(self.tr("Paired with %1.").replace("%1", res["name"]))
            self.list_radios()
        self.refresh()
