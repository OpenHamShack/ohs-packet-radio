"""Find my radio, the box (findmyradio.py says what it does and why): at
the top of the setup assistant's radio page and in Settings › Radio.

One button looks everywhere on a thread of its own and shows what it found
as a list of cards; "Use this radio" fills every field; the radio's sound
devices are asked for only when they could not be matched; what still has
to be set on the radio itself is said only for the radio picked; "Test the
connection" reads its frequency and mode and changes nothing. The station
lets go of the radio while the box looks and tests (`release`/`restore`),
or its own port would read as "in use".
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from .. import findmyradio
from .widgets import Fold, label


def guidance_text(key: str, cand=None) -> str:
    from PyQt6.QtCore import QCoreApplication as Q
    return {
        "lan-user": Q.translate("FindRadio", "On the radio, MENU › SET › Network › Network User1: the ID and "
                                             "password here must match exactly, capitals included."),
        "civ-baud": Q.translate("FindRadio", "Set the radio's CI-V USB Baud Rate to 115200 (MENU › SET › "
                                             "Connectors › CI-V): slower works, but leaves no room for the scope."),
        "9700-usb-tx": Q.translate("FindRadio", "The IC-9700's transmit audio over USB has proved unreliable: "
                                                "connect it by LAN to transmit, and keep USB for receiving."),
        "unlink-remote": Q.translate("FindRadio", "If another program (RS-BA1, wfview) is connected to the "
                                                  "radio, close it, and on the radio choose Unlink from [REMOTE]."),
        "unsupported": Q.translate("FindRadio", "This app cannot drive this radio yet."),
        "tmv71-ptt": Q.translate("FindRadio", "PTT is the interface's, not the radio's: choose how it keys "
                                              "(RTS for a Digirig, a CM108 pin, or a SignaLink's VOX) and its "
                                              "port in the details below."),
        "tmv71-band": Q.translate("FindRadio", "The app uses whichever band, A or B, is already on the "
                                               "channel's band (2 m or 70 cm) and points the DATA jack at it "
                                               "(menu 517); it cannot move a band between VHF and UHF itself."),
        "tmv71-9600": Q.translate("FindRadio", "For 9600 the interface must take its receive audio from PR9 (DATA "
                                               "pin 4); the app sets menu 518 to 9600, which also changes the "
                                               "transmit level the radio expects."),
        "tmv71-baud": Q.translate("FindRadio", "Check the radio's PC port speed (menu 519) matches the one chosen "
                                               "here, and that the PC port cable is the radio's (PG-5G or "
                                               "compatible), not the DATA jack's."),
        "controller-only": Q.translate("FindRadio", "Used through the controller's own apps, not from here."),
        "controller-held": Q.translate("FindRadio", "The controller has it reserved or in use just now."),
    }.get(key, key)


def card_text(c: findmyradio.Candidate) -> str:
    from PyQt6.QtCore import QCoreApplication as Q
    lines = [c.title]
    if c.detail:
        lines.append(c.detail)
    radio = c.fields.get("radio", {})
    if radio.get("audio_out"):
        lines.append(Q.translate("FindRadio", "Sound: %1 / %2").replace("%1", radio.get("audio_in", "")).replace(
            "%2", radio["audio_out"]))
    elif c.ask_audio:
        lines.append(Q.translate("FindRadio", "Sound devices: choose below"))
    return "\n".join(lines)


class FindRadioBox(QGroupBox):
    """`settings()` gives the settings to start from, `on_pick(settings)`
    receives them filled in; `release()`/`restore()` free and retake the
    radio around a search or test (optional)."""

    def __init__(self, settings, on_pick, *, secrets=None, release=None, restore=None, controller=None,
                 sound_backend=None, parent=None):
        super().__init__(parent)
        self.setTitle(self.tr("Find my radio"))
        self.settings, self.on_pick = settings, on_pick
        self.secrets = secrets or (lambda: {})
        self.release, self.restore = release, restore
        self.controller = controller
        self.sound_backend = sound_backend
        self.search: findmyradio.Search | None = None
        self.picked: findmyradio.Candidate | None = None
        self._result = None
        lay = QVBoxLayout(self)
        lay.addWidget(label(self.tr("Plug the radio in and switch it on, then let the app look for it: on the "
                                    "serial ports, on the network, and on a paired controller. It only asks "
                                    "questions; nothing is changed on the radio."), "dim", wrap=True))
        row = QHBoxLayout()
        self.find_btn = QPushButton(self.tr("Find my radio"))
        self.find_btn.setObjectName("primary")
        self.find_btn.clicked.connect(self.find)
        self.subnet = QCheckBox(self.tr("Also look at every address on this network"))
        self.subnet.setToolTip(self.tr("Sends one small \"are you there\" packet to each address: finds an Icom "
                                       "on the LAN when the broadcast does not reach it"))
        row.addWidget(self.find_btn)
        row.addWidget(self.subnet, 1)
        lay.addLayout(row)
        self.status = label("", "dim", wrap=True)
        lay.addWidget(self.status)
        self.cards = QListWidget()
        self.cards.setMinimumHeight(90)
        self.cards.currentRowChanged.connect(self._selected)
        self.cards.hide()
        lay.addWidget(self.cards)
        self.audio = QWidget()
        af = QFormLayout(self.audio)
        af.setContentsMargins(0, 0, 0, 0)
        self.audio_in = QComboBox()
        self.audio_out = QComboBox()
        af.addRow(label(self.tr("The radio's sound devices could not be told apart from others: choose them "
                                "(on Windows they are often named after the radio, or \"USB Audio CODEC\")."),
                        "dim", wrap=True))
        af.addRow(self.tr("Sound from the radio"), self.audio_in)
        af.addRow(self.tr("Sound to the radio"), self.audio_out)
        self.audio.hide()
        lay.addWidget(self.audio)
        self.guidance = label("", wrap=True)
        lay.addWidget(self.guidance)
        buttons = QHBoxLayout()
        self.use_btn = QPushButton(self.tr("Use this radio"))
        self.use_btn.clicked.connect(self.use)
        self.test_btn = QPushButton(self.tr("Test the connection"))
        self.test_btn.setToolTip(self.tr("Reads the radio's frequency and mode; changes nothing"))
        self.test_btn.clicked.connect(self.test)
        buttons.addWidget(self.use_btn)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.tested = label("", wrap=True)
        lay.addWidget(self.tested)
        self.looked = Fold(self.tr("What was looked at"))
        self.notes = label("", "dim", wrap=True)
        QVBoxLayout(self.looked.body).addWidget(self.notes)
        lay.addWidget(self.looked)
        self.use_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        # Empty labels and the choose/test buttons take no room until there
        # is something to say or choose (the first look at the page showed
        # a box half blank).
        self.buttons = [self.use_btn, self.test_btn]
        for w in (self.status, self.guidance, self.tested, *self.buttons):
            w.hide()

    @staticmethod
    def _say(lab, text: str) -> None:
        lab.setText(text)
        lab.setVisible(bool(text))

    # -- looking --------------------------------------------------------------------------

    def find(self, wait: bool = False):
        self.find_btn.setEnabled(False)
        self._say(self.status, self.tr("Looking… (up to half a minute)"))
        self.cards.clear()
        self.cards.hide()
        self.audio.hide()
        self._say(self.guidance, "")
        if self.release:
            self.release()
        s = self.settings()
        subnet = self.subnet.isChecked()
        controller = self.controller() if callable(self.controller) else self.controller
        box: dict = {}

        def go():
            try:
                box["search"] = findmyradio.search(s, subnet=subnet, controller=controller,
                                                   sound_backend=self.sound_backend)
            except Exception as e:                  # noqa: BLE001 - said in the box
                box["error"] = str(e)
        t = threading.Thread(target=go, name="findmyradio", daemon=True)
        t.start()
        if wait:
            t.join(60)
            self._found(box)
            return

        def check():
            if t.is_alive():
                QTimer.singleShot(200, check)
                return
            self._found(box)
        QTimer.singleShot(200, check)

    def _found(self, box):
        self.find_btn.setEnabled(True)
        if self.restore:
            self.restore()
        if "error" in box:
            self._say(self.status, self.tr("Looking failed: %1").replace("%1", box["error"]))
            return
        self.search = sr = box["search"]
        self.notes.setText("\n".join(f"{w}: {t}" for w, t in sr.notes) or self.tr("Nothing to report."))
        if not sr.candidates:
            self._say(self.status, self.tr("No radio found. Check it is switched on and plugged in, then look "
                                        "again - or set it up by hand below. \"What was looked at\" says what "
                                        "each port did."))
            return
        self._say(self.status, self.tr("Found %n radio(s) in %1 s. Choose one:", "", len(sr.candidates)).replace(
            "%1", str(sr.seconds)))
        for c in sr.candidates:
            item = QListWidgetItem(card_text(c))
            if not c.supported:
                item.setForeground(self.palette().placeholderText())
            self.cards.addItem(item)
        self.cards.show()
        for b in self.buttons:
            b.show()
        self.cards.setCurrentRow(0)

    def _selected(self, row: int):
        sr = self.search
        c = sr.candidates[row] if sr and 0 <= row < len(sr.candidates) else None
        self.use_btn.setEnabled(bool(c and c.supported))
        self.test_btn.setEnabled(False)
        self._say(self.tested, "")
        self.audio.setVisible(bool(c and c.ask_audio))
        if c and c.ask_audio:
            from .. import discover
            devices = discover.sound_devices(self.sound_backend)
            for combo, key in ((self.audio_in, "inputs"), (self.audio_out, "outputs")):
                combo.clear()
                combo.addItem("")
                combo.addItems([d["name"] for d in devices if d.get(key)])
        self._say(self.guidance, "\n".join("• " + guidance_text(g) for g in (c.guidance if c else [])))

    # -- choosing and testing --------------------------------------------------------------

    def current(self) -> findmyradio.Candidate | None:
        sr, row = self.search, self.cards.currentRow()
        return sr.candidates[row] if sr and 0 <= row < len(sr.candidates) else None

    def use(self):
        c = self.current()
        if c is None or not c.supported:
            return
        if c.ask_audio:
            c.fields.setdefault("radio", {}).update(audio_in=self.audio_in.currentText(),
                                                    audio_out=self.audio_out.currentText())
        self.picked = c
        new = findmyradio.apply(self.settings(), c)
        self.on_pick(new)
        self.test_btn.setEnabled(c.how in ("usb", "lan") and c.kind in findmyradio.SUPPORTED)
        self._say(self.status, self.tr("%1 chosen: every field below is filled in.").replace("%1", c.title))

    def test(self, wait: bool = False):
        self.test_btn.setEnabled(False)
        self._say(self.tested, self.tr("Testing…"))
        if self.release:
            self.release()
        s, secrets = self.settings(), self.secrets()
        box: dict = {}

        def go():
            box["r"] = findmyradio.test_connection(s, secrets)
        t = threading.Thread(target=go, name="findmyradio-test", daemon=True)
        t.start()

        def done():
            if self.restore:
                self.restore()
            self.test_btn.setEnabled(True)
            r = box.get("r") or findmyradio.TestResult(False, error="no answer")
            self._result = r
            if r.ok:
                text = self.tr("It answers: %1 MHz, %2").replace("%1", f"{r.frequency_hz / 1e6:.4f}").replace(
                    "%2", r.mode or "?")
                if r.radio:
                    text += f" ({r.radio})"
            else:
                text = self.tr("No good: %1").replace("%1", r.error)
                text += "".join("\n• " + guidance_text(g) for g in r.guidance)
            self._say(self.tested, text)
        if wait:
            t.join(findmyradio.TEST_TIMEOUT_S + 10)
            done()
            return

        def check():
            if t.is_alive():
                QTimer.singleShot(200, check)
                return
            done()
        QTimer.singleShot(200, check)
