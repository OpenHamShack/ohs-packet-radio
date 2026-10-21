"""A callsign's HamQTH details as a tooltip (Monitor, chats, nodes, the
room's members), and the dialog where the operator enters the free
HamQTH account (Station › Callsign lookup). callbook.py does the work;
this only words it.

The tooltip never waits: it shows what the cache knows and asks for the
rest in the background, so hovering a call the first time says
"looking up" and the next hover has the answer.
"""
from __future__ import annotations

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout)

from shack_packet.terminal import base_call

from . import fmt


def tooltip(ctl, call: str) -> str:
    """One or two lines about `call`, or "" when lookups are off."""
    call = base_call(call)
    if not call or not ctl.settings.lookup.enabled:
        return ""
    if not ctl.callbook.configured and not ctl.callbook.entries.get(call):
        return QCoreApplication.translate("CallInfo", "No callsign lookup account (Station › Callsign lookup).")
    d = ctl.describe_call(call)
    if d is None:
        return QCoreApplication.translate("CallInfo", "%1: looking up…").replace("%1", call)
    if not d["found"]:
        return QCoreApplication.translate("CallInfo", "%1: not found on HamQTH").replace("%1", call)
    who = ", ".join(p for p in (d["name"], d["qth"], d["country"]) if p)
    text = f"{call}: {who}" if who else call
    if d["grid"]:
        text += "\n" + QCoreApplication.translate("CallInfo", "Locator %1").replace("%1", d["grid"])
    if d["km"] is not None:
        text += "\n" + QCoreApplication.translate("CallInfo", "%1 km, bearing %2°").replace("%1", fmt.number(d["km"])).replace(
            "%2", fmt.number(d["bearing"]))
    return text


class LookupDialog(QDialog):
    """The HamQTH account: kept in secrets.json, never in settings.json."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(self.tr("Callsign lookup"))
        lay = QVBoxLayout(self)
        intro = QLabel(self.tr("Names, QTHs and locators come from HamQTH, which is free: make an account at "
                               "hamqth.com and enter it here. Answers are kept on this computer, so calls "
                               "already looked up still show with no internet. The password is kept with the "
                               "radio password, never in the settings file or a diagnostics bundle."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.enabled = QCheckBox(self.tr("Look up callsigns"))
        self.enabled.setChecked(bool(window.settings.lookup.enabled))
        self.user = QLineEdit(window.secrets.get("hamqth_user", ""))
        self.password = QLineEdit(window.secrets.get("hamqth_password", ""))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(self.enabled)
        form.addRow(self.tr("HamQTH username"), self.user)
        form.addRow(self.tr("HamQTH password"), self.password)
        lay.addLayout(form)
        self.state = QLabel(self._state())
        self.state.setObjectName("dim")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _state(self) -> str:
        cb = self.window.ctl.callbook
        if cb.problem == "auth":
            return self.tr("HamQTH refused the username or password.")
        if cb.problem == "network":
            return self.tr("HamQTH could not be reached; trying again shortly.")
        return self.tr("%1 calls known on this computer.").replace("%1", str(len(cb.entries)))

    def save(self) -> None:
        w = self.window
        w.settings.lookup.enabled = self.enabled.isChecked()
        w.secrets["hamqth_user"] = self.user.text().strip()
        w.secrets["hamqth_password"] = self.password.text()
        w.ctl.secrets.update({"hamqth_user": w.secrets["hamqth_user"],
                              "hamqth_password": w.secrets["hamqth_password"]})
        w.ctl.callbook.set_credentials(w.secrets["hamqth_user"], w.secrets["hamqth_password"])
        w.save_settings()
        self.accept()
