"""APRS objects and items this station places on the map (0.3.0): a net's
meeting point, a repeater, an event. Placed by a right click on the APRS
page's map, or typed in; listed, moved and killed here.

What is sent is aprs_services.py's (ohs-libs): each one every Settings ›
Weather "Objects every" minutes (never under 10), a killed one three
times so maps take it away. Only while transmit is armed, like every
frame. The list survives a restart (settings.objects).
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QPushButton, QVBoxLayout)

from . import fmt


class ObjectDialog(QDialog):
    """One object: name, where, symbol, comment, object or item."""

    def __init__(self, parent=None, d: dict | None = None, lat: float = 0.0, lon: float = 0.0):
        super().__init__(parent)
        d = d or {}
        self.setWindowTitle(self.tr("APRS object"))
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.name = QLineEdit(str(d.get("name", "")))
        self.name.setMaxLength(9)
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90, 90)
        self.lat.setDecimals(4)
        self.lat.setValue(float(d.get("lat", lat)))
        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180, 180)
        self.lon.setDecimals(4)
        self.lon.setValue(float(d.get("lon", lon)))
        self.symbol = QLineEdit(str(d.get("symbol", "/.")))
        self.symbol.setMaxLength(2)
        self.symbol.setToolTip(self.tr("Common ones: /r repeater, /- house, /. small cross, /; event, /E eyeball, "
                                       "/h hospital, \\N net"))
        self.comment = QLineEdit(str(d.get("comment", "")))
        self.comment.setMaxLength(43)
        self.item = QCheckBox(self.tr("An item (no time stamp: a thing, not an event)"))
        self.item.setChecked(bool(d.get("item", False)))
        f.addRow(self.tr("Name (up to 9)"), self.name)
        f.addRow(self.tr("Latitude"), self.lat)
        f.addRow(self.tr("Longitude"), self.lon)
        f.addRow(self.tr("Symbol (table and code)"), self.symbol)
        f.addRow(self.tr("Comment"), self.comment)
        f.addRow(self.item)
        lay.addLayout(f)
        self.note = QLabel()
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def result_dict(self) -> dict:
        return {"name": self.name.text().strip(), "lat": self.lat.value(), "lon": self.lon.value(),
                "symbol": self.symbol.text() if len(self.symbol.text()) == 2 else "/.",
                "comment": self.comment.text(), "item": self.item.isChecked()}


class ObjectsDialog(QDialog):
    """The list: add, edit (move) and kill."""

    def __init__(self, window, *, edit_hook=None):
        super().__init__(window)
        self.window = window
        #: Tests fill the edit dialog in here instead of a person.
        self.edit_hook = edit_hook
        self.setWindowTitle(self.tr("My APRS objects"))
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.add = QPushButton(self.tr("Add…"))
        self.add.clicked.connect(lambda: self.edit(None))
        self.change = QPushButton(self.tr("Edit…"))
        self.change.clicked.connect(lambda: self.edit(self.current()))
        self.kill = QPushButton(self.tr("Kill"))
        self.kill.setToolTip(self.tr("Tell every map to take it away: sent three times, then forgotten."))
        self.kill.clicked.connect(self._kill)
        for b in (self.add, self.change, self.kill):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.note = QLabel(self.tr("Objects are sent only while transmit is armed, each every %1 minutes.")
                           .replace("%1", str(window.ctl.settings.objects.every_min)))
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        lay.addWidget(close)
        self.fill()

    def items(self) -> list[dict]:
        return list(self.window.ctl.settings.objects.items)

    def fill(self) -> None:
        self.list.clear()
        for d in self.items():
            self.list.addItem(self.tr("%1 at %2, %3 %4").replace("%1", str(d["name"]))
                              .replace("%2", fmt.number(float(d["lat"]), 4))
                              .replace("%3", fmt.number(float(d["lon"]), 4)).replace("%4", str(d.get("comment", ""))))

    def current(self) -> dict | None:
        row = self.list.currentRow()
        items = self.items()
        return items[row] if 0 <= row < len(items) else None

    def edit(self, d: dict | None, lat: float | None = None, lon: float | None = None) -> bool:
        home = self.window.ctl.position() or (0.0, 0.0)
        dlg = ObjectDialog(self, d, lat if lat is not None else home[0], lon if lon is not None else home[1])
        if self.edit_hook is not None:
            self.edit_hook(dlg)
        elif not dlg.exec():
            return False
        new = dlg.result_dict()
        if d is not None and d["name"].upper() != new["name"].upper():
            self.window.ctl.aprsx.kill(d["name"])          # renamed: the old name goes from the maps
        why = self.window.ctl.aprsx.place(new)
        if why:
            self.note.setText(self.tr("That object cannot be sent: a name of 1 to 9 letters, digits or spaces, "
                                      "and a symbol of two characters."))
            return False
        self.window.save_settings()
        self.fill()
        return True

    def _kill(self) -> None:
        d = self.current()
        if d is not None:
            self.window.ctl.aprsx.kill(d["name"])
            self.window.save_settings()
            self.fill()
