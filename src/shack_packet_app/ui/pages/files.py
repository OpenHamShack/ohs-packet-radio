"""Files: every file and picture sent or received over a chat session,
their progress, and what to do with each - accept, decline, cancel,
resume - plus where received files go, and 7PLUS for files posted
through BBSs (files.py; ohs-libs FILES.md).

Received files are listed and their folder can be shown; the app never
opens or runs one. A picture's thumbnail is drawn by Qt's own decoder
(pictures.thumbnail) and nothing else touches it. Settings for transfers
live here, beside the transfers, rather than in the Settings dialog:
they matter at the moment a file arrives.

Files can be dropped here as well as on a chat tab; with more than one
connected session the drop asks which station (ui/filesend.py).
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                             QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from .. import fmt, tables
from ..filesend import EstimateTable, drop_paths, mode_name, reason_text, size_text, span_text, state_text


class FilesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._rev = -1
        self._rows: list = []
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)

        where = QHBoxLayout()
        self.folder = QLabel()
        self.folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.change = QPushButton(self.tr("Change…"))
        self.change.clicked.connect(self._choose_folder)
        self.show_folder = QPushButton(self.tr("Show folder"))
        self.show_folder.setToolTip(self.tr("Opens the folder in your file manager. The app never opens a "
                                            "received file itself."))
        self.show_folder.clicked.connect(self._open_folder)
        where.addWidget(QLabel(self.tr("Received files go to")))
        where.addWidget(self.folder, 1)
        where.addWidget(self.change)
        where.addWidget(self.show_folder)
        lay.addLayout(where)

        opts = QHBoxLayout()
        self.auto_resume = QCheckBox(self.tr("Resume a file I already accepted without asking again"))
        self.compress = QCheckBox(self.tr("Compress for OpenHamShack stations"))
        self.compress.setToolTip(self.tr("DEFLATE, a published compression anyone can undo - not encryption. "
                                         "Skipped for pictures and archives, which are compressed already."))
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 2000)
        self.max_mb.setSuffix(self.tr(" MB"))
        self.protocol = QComboBox()
        self.protocol.addItem(self.tr("OpenHamShack"), "ohs")
        self.protocol.addItem(self.tr("YAPP (other software)"), "yapp")
        for w in (self.auto_resume, self.compress):
            w.toggled.connect(self._save)
        self.max_mb.valueChanged.connect(self._save)
        self.protocol.activated.connect(self._save)
        opts.addWidget(self.auto_resume)
        opts.addWidget(self.compress)
        opts.addWidget(QLabel(self.tr("Decline offers over")))
        opts.addWidget(self.max_mb)
        opts.addWidget(QLabel(self.tr("Send with")))
        opts.addWidget(self.protocol)
        opts.addStretch(1)
        lay.addLayout(opts)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = tables.make([self.tr("Time (UTC)"), self.tr("Way"), self.tr("Station"), self.tr("Size"),
                                  self.tr("Done"), self.tr("Speed"), self.tr("Protocol"), self.tr("State"),
                                  self.tr("File")])
        self.table.itemSelectionChanged.connect(self._selected)
        ll.addWidget(self.table, 1)
        self.empty = QLabel(self.tr("No files yet. Drop a file or picture on a connected chat tab (or here), or "
                                    "use Send file… on the Connect / Chat page."))
        self.empty.setObjectName("dim")
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        row = QHBoxLayout()
        self.accept_btn = QPushButton(self.tr("Accept"))
        self.accept_btn.clicked.connect(lambda: self._act("accept"))
        self.decline_btn = QPushButton(self.tr("Decline"))
        self.decline_btn.clicked.connect(lambda: self._act("decline"))
        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.cancel_btn.clicked.connect(lambda: self._act("cancel"))
        self.resume_btn = QPushButton(self.tr("Resume"))
        self.resume_btn.setToolTip(self.tr("Offer the file again on a connected session to the same station. "
                                           "It carries on from what the other station already has."))
        self.resume_btn.clicked.connect(lambda: self._act("resume"))
        self.send_btn = QPushButton(self.tr("Send file…"))
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(lambda: self.window.files_ui.send_file())
        for b in (self.accept_btn, self.decline_btn, self.cancel_btn, self.resume_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.send_btn)
        ll.addLayout(row)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(170, 130)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rl.addWidget(self.preview)
        rl.addWidget(self.details)
        # The selected file's time at every mode (as in the Send dialog), so
        # "how long would this take at 9600" is answered where it is asked.
        self.estimates = EstimateTable()
        self.estimates.table.hide()
        rl.addWidget(self.estimates.table)
        rl.addStretch(1)
        box = QGroupBox(self.tr("7PLUS (files posted through BBSs)"))
        bl = QVBoxLayout(box)
        hint = QLabel(self.tr("Encode a file into 7PLUS parts to post as BBS messages, or put a file back "
                              "together from parts you saved or captured."))
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        bl.addWidget(hint)
        lines = QHBoxLayout()
        self.lines = QSpinBox()
        self.lines.setRange(10, 512)
        self.lines.setSuffix(self.tr(" lines per part"))
        self.lines.valueChanged.connect(self._save)
        lines.addWidget(self.lines)
        lines.addStretch(1)
        bl.addLayout(lines)
        b7 = QHBoxLayout()
        self.enc7 = QPushButton(self.tr("Encode…"))
        self.enc7.clicked.connect(self._encode7)
        self.dec7 = QPushButton(self.tr("Decode files…"))
        self.dec7.clicked.connect(self._decode7_files)
        self.paste7 = QPushButton(self.tr("Decode pasted text…"))
        self.paste7.clicked.connect(self._decode7_paste)
        b7.addWidget(self.enc7)
        b7.addWidget(self.dec7)
        b7.addWidget(self.paste7)
        bl.addLayout(b7)
        rl.addWidget(box)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        self._load()

    # -- settings ------------------------------------------------------------------

    @property
    def ctl(self):
        return self.window.ctl

    def _load(self):
        s = self.window.settings.files
        for w, v in ((self.auto_resume, s.auto_resume), (self.compress, s.compress)):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        for w, v in ((self.max_mb, s.max_mb), (self.lines, s.sevenplus_lines)):
            w.blockSignals(True)
            w.setValue(int(v))
            w.blockSignals(False)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(s.protocol)))
        self.folder.setText(str(self.ctl.files.folder()))

    def _save(self, *_):
        s = self.window.settings.files
        s.auto_resume = self.auto_resume.isChecked()
        s.compress = self.compress.isChecked()
        s.max_mb = self.max_mb.value()
        s.protocol = self.protocol.currentData()
        s.sevenplus_lines = self.lines.value()
        self.ctl.files.apply()
        self.window.save_settings()

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, self.tr("Received files go to"),
                                                str(self.ctl.files.folder()))
        if path:
            self.set_folder(path)

    def set_folder(self, path: str):
        self.window.settings.files.folder = path
        self.ctl.files.apply()
        self.window.save_settings()
        self.folder.setText(str(self.ctl.files.folder()))

    def _open_folder(self):
        folder = self.ctl.files.folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -- the list --------------------------------------------------------------------

    def current(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def _act(self, what: str):
        t = self.current()
        if t is None:
            return
        files = self.ctl.files
        if what == "accept":
            files.accept(t)
        elif what == "decline":
            files.decline(t)
        elif what == "cancel":
            files.cancel(t)
        elif what == "resume" and not files.resume(t):
            self._tell(self.tr("Resume"),
                                    self.tr("Connect to %1 first (Connect / Chat), then press Resume.")
                                    .replace("%1", t.peer))
        self.refresh(force=True)

    def _selected(self):
        t = self.current()
        self._buttons(t)
        if t is None:
            self.preview.clear()
            self.details.clear()
            self.estimates.table.hide()
            return
        lines = [f"<b>{_esc(t.name)}</b>", size_text(t.size) + f" · {_esc(t.mime)}"]
        if t.sha256 and t.protocol == "ohs":
            lines.append(self.tr("SHA-256 %1").replace("%1", t.sha256[:16] + "…"))
        if t.reason:
            lines.append(reason_text(t.reason))
        if t.direction == "in" and t.state == "done" and t.path is not None:
            lines.append(self.tr("Saved as %1").replace("%1", _esc(str(t.path))))
        lines += self._timing(t)
        self.details.setText("<br>".join(lines))
        self.preview.clear()
        if t.direction == "in" and t.state == "done" and t.path is not None and t.mime.startswith("image/"):
            from ... import pictures
            image = pictures.thumbnail(t.path)
            if image is not None:
                self.preview.setPixmap(QPixmap.fromImage(image))

    def _timing(self, t) -> list[str]:
        """How long this file took or will take, and the table for it at
        every mode: the bytes on the air (compressed, when they were), so
        a file sent compressed is reckoned as it went."""
        files = self.ctl.files
        size = max(1, t.size)
        nbytes, chunks = size, None
        if t.compress and t.sent:
            # What went so far says how well it compresses.
            nbytes, chunks = int(size * min(1.0, t.wire_bytes / max(1, t.sent))), -(-size // 1024)
        rows = files.estimates(nbytes, peer=t.peer, protocol=t.protocol, chunks=chunks)
        self.estimates.fill(rows, self.ctl.settings.radio.kind)
        self.estimates.table.show()
        out = []
        now = next((r for r in rows if r.current and r.available), None)
        if t.active and t.state not in ("offered", "offering") and now is not None:
            left = now.best_s * max(0, size - t.done) / size
            out.append(self.tr("About %1 left at %2").replace("%1", span_text(left)).replace(
                "%2", mode_name(now.mode)))
        elif t.state == "done" and t.started is not None and t.finished is not None:
            out.append(self.tr("Took %1").replace("%1", span_text(t.finished - t.started)))
        return out

    def _buttons(self, t):
        self.accept_btn.setEnabled(t is not None and t.direction == "in" and t.state == "offered")
        self.decline_btn.setEnabled(t is not None and t.direction == "in" and t.state == "offered")
        self.cancel_btn.setEnabled(t is not None and t.active and t.state != "offered")
        self.resume_btn.setEnabled(t is not None and t.direction == "out" and not t.active and t.state != "done")

    def refresh(self, force: bool = False):
        files = self.ctl.files
        rev = files.rev
        busy = any(t.active for t in files.transfers())
        if rev == self._rev and not busy and not force:
            return
        self._rev = rev
        keep = self.current()
        self._rows = list(reversed(files.transfers()))
        rows = []
        for t in self._rows:
            when = fmt.utc_time(self.ctl.wall_of(t.started)) if t.started is not None else ""
            rate = t.rate_bps(self.ctl.clock()) if t.state in ("sending", "receiving") or t.finished else None
            pct = QTableWidgetItem(f"{int(t.fraction * 100)} %")
            rows.append([when, "↑" if t.direction == "out" else "↓", t.peer, size_text(t.size), pct,
                         self.tr("%1 bit/s").replace("%1", fmt.number(rate)) if rate else "",
                         "OHS" if t.protocol == "ohs" else "YAPP", state_text(t.state), t.name])
        tables.fill(self.table, rows)
        self.empty.setVisible(not rows)
        if keep is not None and keep in self._rows:
            self.table.selectRow(self._rows.index(keep))
        self._buttons(self.current())

    def reset(self):
        self._load()
        self.refresh(force=True)

    # -- drops -------------------------------------------------------------------------

    def dragEnterEvent(self, e):
        if drop_paths(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = drop_paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.window.files_ui.send_file(paths[0])

    def _tell(self, title: str, text: str) -> None:
        self.last_message = text
        _box(self, title, text)

    # -- 7PLUS -------------------------------------------------------------------------

    def _encode7(self, path: str | None = None, out_dir: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self.tr("Encode to 7PLUS"))
            if not path:
                return
        if out_dir is None:
            out_dir = QFileDialog.getExistingDirectory(self, self.tr("Save the 7PLUS parts in"),
                                                       str(Path(path).parent))
            if not out_dir:
                return
        try:
            parts = self.ctl.files.sevenplus_encode(Path(path), Path(out_dir), self.lines.value())
        except (OSError, ValueError) as e:
            self._tell(self.tr("Encode to 7PLUS"), str(e))
            return []
        self._tell(self.tr("Encode to 7PLUS"),
                                self.tr("%1 parts in %2. Post each one as a message of its own.")
                                .replace("%1", str(len(parts))).replace("%2", str(out_dir)))
        return parts

    def _decode7_files(self, paths=None):
        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(self, self.tr("7PLUS parts or saved messages"))
            if not paths:
                return
        return self._decode7([Path(p) for p in paths])

    def _decode7_paste(self, text: str | None = None):
        if text is None:
            text, ok = QInputDialog.getMultiLineText(self, self.tr("Decode 7PLUS"),
                                                     self.tr("Paste the 7PLUS parts (every line of them):"))
            if not ok or not text:
                return
        return self._decode7([text.encode("latin-1", "replace")])

    def _decode7(self, sources):
        try:
            result, saved = self.ctl.files.sevenplus_decode(sources)
        except OSError as e:
            self._tell(self.tr("Decode 7PLUS"), str(e))
            return None
        if saved is not None:
            msg = self.tr("Saved %1 (%2) in the received files folder.").replace("%1", saved.name).replace(
                "%2", size_text(result.size))
        elif result.stripped:
            msg = self.tr("These parts lost the top bit of every byte on the way (a 7-bit path), so they "
                          "cannot be decoded. Ask for them by a route that keeps 8 bits.")
        elif not result.parts:
            msg = self.tr("No 7PLUS parts were found in that.")
        else:
            parts = sorted({p for p, _ in result.missing})
            msg = self.tr("%1 is not complete yet: %2 lines missing, from parts %3.").replace(
                "%1", result.name).replace("%2", str(len(result.missing))).replace(
                "%3", ", ".join(str(p) for p in parts))
        self._tell(self.tr("Decode 7PLUS"), msg)
        return result, saved


def _box(parent, title: str, text: str) -> None:
    """A message that does not block: the window keeps ticking (and the
    tests keep running) while it is up."""
    box = QMessageBox(QMessageBox.Icon.Information, title, text, QMessageBox.StandardButton.Ok, parent)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.open()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
