"""Writing a message: packet mail (to CALL or CALL@BBS) or Winlink email
(to callsigns and internet addresses, with attachments). One dialog, two
modes, because what differs is only which fields there are and what an
address may be; the checks are the mail client's (mailclient.py), so
what this accepts is what will be sent.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from shack_packet import b2f
from shack_packet.stationmail import split_address

from . import fmt


class ComposeDialog(QDialog):
    def __init__(self, parent=None, *, winlink: bool = False, to: str = "", subject: str = "", body: str = "",
                 max_attachments_kb: int = 20):
        super().__init__(parent)
        self.winlink = winlink
        self.max_bytes = max(0, int(max_attachments_kb)) * 1024
        self.files: list[b2f.Attachment] = []
        self.setWindowTitle(self.tr("New Winlink email") if winlink else self.tr("New packet mail"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.to = QLineEdit(to)
        if winlink:
            self.to.setPlaceholderText(self.tr("Callsigns or email addresses, comma separated"))
            self.cc = QLineEdit()
            self.cc.setPlaceholderText(self.tr("Optional"))
        else:
            self.to.setPlaceholderText(self.tr("CALL, or CALL@BBS (e.g. G4XYZ@GB7XYZ)"))
            self.cc = None
        self.subject = QLineEdit(subject)
        self.subject.setMaxLength(80 if not winlink else 128)
        form.addRow(self.tr("To"), self.to)
        if self.cc is not None:
            form.addRow(self.tr("Cc"), self.cc)
        form.addRow(self.tr("Subject"), self.subject)
        lay.addLayout(form)
        self.body = QPlainTextEdit(body)
        lay.addWidget(self.body, 1)
        if winlink:
            row = QHBoxLayout()
            self.attach_list = QListWidget()
            self.attach_list.setMaximumHeight(70)
            side = QWidget()
            col = QVBoxLayout(side)
            col.setContentsMargins(0, 0, 0, 0)
            self.attach = QPushButton(self.tr("Attach…"))
            self.attach.clicked.connect(self._attach)
            self.detach = QPushButton(self.tr("Remove"))
            self.detach.clicked.connect(self._detach)
            col.addWidget(self.attach)
            col.addWidget(self.detach)
            row.addWidget(self.attach_list, 1)
            row.addWidget(side)
            lay.addLayout(row)
        self.note = QLabel()
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        warn = QLabel(self.tr("Nothing sent by radio is private: anyone may read it on the air. "
                              "Never send passwords or anything you would not put on a postcard."))
        warn.setWordWrap(True)
        warn.setObjectName("dim")
        lay.addWidget(warn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Queue to send"))
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._show_size()

    # -- attachments (Winlink) ----------------------------------------------------------
    def add_file(self, path: str) -> bool:
        p = Path(path)
        try:
            data = p.read_bytes()
        except OSError:
            self.note.setText(self.tr("That file could not be read."))
            return False
        if sum(len(f.data) for f in self.files) + len(data) > self.max_bytes:
            self.note.setText(self.tr("Attachments are limited to %1 kB on one message (Settings › Winlink).")
                              .replace("%1", fmt.number(self.max_bytes / 1024)))
            return False
        self.files.append(b2f.Attachment(p.name, data))
        self.attach_list.addItem(self.tr("%1 (%2 kB)").replace("%1", p.name)
                                 .replace("%2", fmt.number(len(data) / 1024, 1)))
        self._show_size()
        return True

    def _attach(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Attach a file"))
        if path:
            self.add_file(path)

    def _detach(self):
        row = self.attach_list.currentRow()
        if 0 <= row < len(self.files):
            self.files.pop(row)
            self.attach_list.takeItem(row)
            self._show_size()

    def _show_size(self):
        if not self.winlink:
            return
        total = sum(len(f.data) for f in self.files)
        self.note.setText(self.tr("Attachments: %1 of %2 kB. At 1200 baud each kB takes about ten seconds.")
                          .replace("%1", fmt.number(total / 1024, 1)).replace("%2", fmt.number(self.max_bytes / 1024)))

    # -- the result ---------------------------------------------------------------------
    def addresses(self) -> list[str]:
        return [a.strip() for a in self.to.text().replace(";", ",").split(",") if a.strip()]

    def check(self) -> str:
        """"" when it can be queued, else what is wrong (shown)."""
        to = self.addresses()
        if not to:
            return self.tr("Who is it for?")
        if not self.subject.text().strip():
            return self.tr("Give it a subject.")
        if not self.winlink:
            if len(to) != 1:
                return self.tr("Packet mail goes to one station; send each its own.")
            try:
                split_address(to[0])
            except ValueError:
                return self.tr("“%1” is not a callsign, or CALL@BBS.").replace("%1", to[0])
        else:
            for a in to + ([c.strip() for c in self.cc.text().split(",") if c.strip()] if self.cc else []):
                if "@" not in a and not _looks_like_call(a):
                    return self.tr("“%1” is neither a callsign nor an email address.").replace("%1", a)
        return ""

    def _ok(self):
        why = self.check()
        if why:
            self.note.setText(why)
            return
        self.accept()


def _looks_like_call(text: str) -> bool:
    from shack_packet.identity import is_callsign
    return is_callsign(text.strip().upper().split("-")[0])
