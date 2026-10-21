"""The setup check: first run, and Station › Setup check… after that.

It walks the operator through the station in the order that proves each
step before the next depends on it, and offers transmit last:

1. **Station**: callsign (with SSID), locator, country and licence class.
   A callsign is not required to listen; without a valid one nothing can
   ever be sent (the engine refuses to build a frame).
2. **Radio**: which radio and how it is reached - an Icom found on the LAN
   (or typed), a serial port and a sound device picked from lists, or the
   simulator to try everything with no radio.
2a. **On the radio**: the settings the operator must make on the radio
   itself for that radio and connection (radiomenu.py), and what the app
   will set on its own. Before the radio is opened, so a login or a CI-V
   port mode that is wrong is fixed before the next page tries it.
3. **Audio**: the radio is opened (receive only) and the receive level is
   shown live, so a dead or clipping sound path shows before anything else.
4. **Receive test** on the APRS channel (144.800 in Region 1, from the
   band-plan dataset): what it decodes, live.
5. **Transmit**, only then: off unless the operator ticks it, possible only
   with a valid callsign, and even then a session's transmit needs Arm,
   which expires. The turnaround measurement keys the radio, so it is
   offered only with both "I am at the radio" and "a dummy load is
   fitted" ticked.

Opening the radio here writes to it (frequency, FM-D, simplex, data
source) as the link always does, and closing puts it back; the tester
guide says so. Cancel puts the station back as it was.
"""
from __future__ import annotations

import html

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
                             QWizard, QWizardPage)
from shack_packet.identity import is_callsign
from shack_packet.radios.turnaround import CONFIRM

from .. import discover, grid, presets
from ..links import power_status
from ..settings import ARM_MAX_MINUTES, ARM_MIN_MINUTES, RADIO_KINDS, SDR_RADIO_KINDS, SDR_TX_KINDS
from . import fmt, radiochanges, radiomenu, sdrpages, texts
from .widgets import Fold, LevelMeter


def _note(text: str, name: str = "dim") -> QLabel:
    w = QLabel(text)
    w.setWordWrap(True)
    w.setObjectName(name)
    return w


def call_ok(text: str) -> bool:
    call, _, ssid = text.strip().upper().partition("-")
    return is_callsign(call) and (not ssid or (ssid.isdigit() and 0 <= int(ssid) <= 15))


class StationPage(QWizardPage):
    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle(self.tr("Your station"))
        self.setSubTitle(self.tr("Who you are and where. Listening needs none of it; transmitting needs a valid "
                                 "callsign and your licence class."))
        f = QFormLayout(self)
        self.call = QLineEdit()
        self.call.setPlaceholderText(self.tr("e.g. GM4BRI"))
        self.ssid = QSpinBox()
        self.ssid.setRange(0, 15)
        self.locator = QLineEdit()
        self.locator.setPlaceholderText(self.tr("e.g. IO85JW"))
        self.country = QComboBox()
        self.licence = QComboBox()
        self.feedback = _note("")
        f.addRow(self.tr("Callsign"), self.call)
        f.addRow(self.tr("SSID"), self.ssid)
        f.addRow(self.tr("Locator"), self.locator)
        f.addRow(self.tr("Country"), self.country)
        f.addRow(self.tr("Licence class"), self.licence)
        f.addRow(self.feedback)
        self.country.addItem(self.tr("From my callsign"), "")
        for iso, name in presets.countries():
            self.country.addItem(name, iso)
        for w in (self.call, self.locator):
            w.textChanged.connect(self._changed)
        self.country.currentIndexChanged.connect(self._classes)
        self.call.editingFinished.connect(self._classes)

    def initializePage(self):
        st = self.wiz.s.station
        self.call.setText(st.callsign)
        self.ssid.setValue(int(st.ssid))
        self.locator.setText(st.locator)
        self.country.setCurrentIndex(max(0, self.country.findData(st.country)))
        self._classes()
        self.licence.setCurrentIndex(max(0, self.licence.findData(st.licence_class)))
        self._changed()

    def _profile(self) -> dict:
        p = {"callsign": self.call.text().strip().upper().partition("-")[0]}
        if self.country.currentData():
            p["country"] = self.country.currentData()
        return p

    def _classes(self):
        keep = self.licence.currentData()
        self.licence.clear()
        self.licence.addItem(self.tr("Not chosen"), "")
        for cid, name in presets.licence_classes(self._profile()):
            self.licence.addItem(name, cid)
        self.licence.setCurrentIndex(max(0, self.licence.findData(keep)))

    def _changed(self):
        call = self.call.text().strip()
        loc = self.locator.text().strip()
        if call and not call_ok(call):
            self.feedback.setObjectName("bad")
            self.feedback.setText(self.tr("%1 is not a callsign this station can identify with (a prefix, a digit, "
                                          "then letters; no aliases such as WIDE1 or N0CALL).").replace("%1", call))
        elif loc and not grid.valid(loc):
            self.feedback.setObjectName("bad")
            self.feedback.setText(self.tr("%1 is not a Maidenhead locator (two letters, two digits, two letters).")
                                  .replace("%1", loc))
        elif not call:
            self.feedback.setObjectName("warn")
            self.feedback.setText(self.tr("Without a callsign the station listens only."))
        else:
            self.feedback.setObjectName("good")
            self.feedback.setText(self.tr("Looks right."))
        self.feedback.setStyleSheet("")
        self.completeChanged.emit()

    def isComplete(self):
        call, loc = self.call.text().strip(), self.locator.text().strip()
        return (not call or call_ok(call)) and (not loc or grid.valid(loc))

    def validatePage(self):
        st = self.wiz.s.station
        # "GM4BRI-7" typed whole is taken as the call and its SSID.
        call, _, ssid = self.call.text().strip().upper().partition("-")
        st.callsign = call
        st.ssid = int(ssid) if ssid.isdigit() else self.ssid.value()
        st.locator = self.locator.text().strip().upper()
        st.country = self.country.currentData() or ""
        st.licence_class = self.licence.currentData() or ""
        return True


class RadioPage(QWizardPage):
    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle(self.tr("Your radio"))
        self.setSubTitle(self.tr("Which radio, and how this computer reaches it. The simulator needs nothing: "
                                 "try it first."))
        lay = QVBoxLayout(self)
        # 0.3.0: Find my radio first (findmyradio.py), the fields it fills
        # folded under it - the radio's kind stays in sight.
        from .findradio import FindRadioBox
        self.finder = FindRadioBox(self._current, self._picked, secrets=lambda: self.wiz.secrets,
                                   release=wiz.release_radio, restore=wiz.start_test,
                                   controller=self._controller_client, sound_backend=wiz.sound_backend)
        lay.addWidget(self.finder)
        top = QFormLayout()
        self.kind = QComboBox()
        # The SDRs (0.2) after the radios, "no radio" still last.
        for k in tuple(k for k in RADIO_KINDS if k != "none") + SDR_RADIO_KINDS + ("none",):
            self.kind.addItem(texts.radio_kind(k), k)
        top.addRow(self.tr("Radio"), self.kind)
        lay.addLayout(top)
        self.details = Fold(self.tr("Connection details (filled in by Find my radio, or set them here)"))
        details = QVBoxLayout(self.details.body)
        details.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.details)
        self.stack = QStackedWidget()
        details.addWidget(self.stack)
        self.pages = {}
        self.pages["simulator"] = self._simple(self.tr(
            "A pretend 2 m channel inside the app, with a tracker, a station that answers messages and a node that "
            "answers connections. Nothing is transmitted on any radio."))
        self.pages["icom"] = self._icom()
        self.pages["ftx-1"] = self._yaesu()
        self.pages["tm-v71"] = self._kenwood()
        self.pages["soundcard"] = self._soundcard()
        self.pages["none"] = self._simple(self.tr(
            "No radio: the app receives through the SDR monitor only (Settings › SDR monitor) and cannot transmit."))
        self.sdr = sdrpages.SdrRadioWidgets(self)
        self.sdr.release_radio = wiz.release_radio
        self.sdr.restore_radio = wiz.start_test
        self.pages.update(self.sdr.pages(self.stack))
        refresh = QPushButton(self.tr("Look again for ports and sound devices"))
        refresh.clicked.connect(self._lists)
        details.addWidget(refresh)
        # The paired controller's radios (ohsclient.py, D1): which it has
        # reserved or is using, so the operator sees why one will not open.
        self.on_controller = _note("")
        self.on_controller.hide()
        lay.addWidget(self.on_controller)
        from .controllerpage import _Worker
        self._worker = _Worker(self)
        self._worker.done.connect(self._controller_radios)
        self.kind.currentIndexChanged.connect(self._kind)

    def _current(self):
        """The wizard's settings with this page's fields in them: what Find
        starts from and Test reads."""
        self.validatePage()
        return self.wiz.s

    def _picked(self, new):
        """Find my radio's choice into the wizard's settings, and the page
        refilled from them."""
        self.wiz.s.radio = new.radio
        self.wiz.s.sdr_radio = new.sdr_radio
        self.initializePage()

    def _controller_client(self):
        from .. import ohsclient
        client = ohsclient.ControllerClient(self.wiz.s.controller, self.wiz.secrets,
                                            transport=getattr(self.wiz.ctl, "_ohs_transport", None))
        return client if client.paired else None

    def _simple(self, text):
        w = QWidget()
        QVBoxLayout(w).addWidget(_note(text, ""))
        self.stack.addWidget(w)
        return w

    def _combo(self, editable: bool = True) -> QComboBox:
        c = QComboBox()
        c.setEditable(editable)
        return c

    def _icom(self):
        w = QWidget()
        f = QFormLayout(w)
        self.transport = QComboBox()
        self.transport.addItem(self.tr("LAN or Wi-Fi"), "lan")
        self.transport.addItem(self.tr("USB cable"), "usb")
        self.host = QLineEdit()
        self.host.setPlaceholderText(self.tr("the radio's IP address"))
        self.find = QPushButton(self.tr("Find on the network"))
        self.find.clicked.connect(self._find)
        self.found = _note("")
        hrow = QHBoxLayout()
        hrow.addWidget(self.host, 1)
        hrow.addWidget(self.find)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.civ = self._combo()
        self.icom_in = self._combo(False)
        self.icom_out = self._combo(False)
        self.min_power = QCheckBox(self.tr("Lowest power (test)"))
        f.addRow(self.tr("Connection"), self.transport)
        f.addRow(self.tr("Address"), hrow)
        f.addRow("", self.found)
        f.addRow(self.tr("Port"), self.port)
        f.addRow(self.tr("Network user"), self.user)
        f.addRow(self.tr("Network password"), self.password)
        f.addRow(self.tr("CI-V port (USB)"), self.civ)
        f.addRow(self.tr("Radio's sound input (USB)"), self.icom_in)
        f.addRow(self.tr("Radio's sound output (USB)"), self.icom_out)
        f.addRow("", self.min_power)
        f.addRow(_note(self.tr("Sets RF power to minimum before the first transmission of a session, and refuses "
                               "to key if the radio does not confirm it - still not nought watts, and a dummy "
                               "load is still required. On for this test build; put back to normal use by hand "
                               "once the real-radio test is done.")))
        f.addRow(_note(self.tr("USB: leave the CI-V port blank to find it. The next page lists what to set on the "
                               "radio.")))
        self.stack.addWidget(w)
        return w

    def _yaesu(self):
        w = QWidget()
        f = QFormLayout(w)
        self.cat = self._combo()
        self.y_ptt = QComboBox()
        self.y_ptt.addItem(self.tr("By CAT (TX1;)"), "cat")
        self.y_ptt.addItem(self.tr("RTS on the Standard COM port"), "rts")
        self.y_ptt.addItem(self.tr("DTR on the Standard COM port"), "dtr")
        self.y_ptt_port = self._combo()
        self.y_in = self._combo(False)
        self.y_out = self._combo(False)
        f.addRow(self.tr("CAT port (Enhanced COM)"), self.cat)
        f.addRow(self.tr("PTT"), self.y_ptt)
        f.addRow(self.tr("PTT port"), self.y_ptt_port)
        f.addRow(self.tr("Radio's sound input"), self.y_in)
        f.addRow(self.tr("Radio's sound output"), self.y_out)
        f.addRow(_note(self.tr("The FTX-1 shows two ports: the Enhanced one is CAT. The next page lists what to set "
                               "on the radio.")))
        self.stack.addWidget(w)
        return w

    def _kenwood(self):
        """A TM-V71: its PC port for control, an interface on its DATA jack
        for audio and PTT (links.py, shack_packet.radios.kenwood)."""
        w = QWidget()
        f = QFormLayout(w)
        self.k_cat = self._combo()
        self.k_baud = QComboBox()
        for b in (9600, 19200, 38400, 57600):
            self.k_baud.addItem(self.tr("%1 baud").replace("%1", str(b)), b)
        self.k_speed = QComboBox()
        self.k_speed.addItem(self.tr("1200 (AFSK, what every 2 m station speaks)"), 1200)
        self.k_speed.addItem(self.tr("9600 (G3RUH: needs the PR9 pin and a 9600 station at the far end)"), 9600)
        self.k_in = self._combo(False)
        self.k_out = self._combo(False)
        self.k_ptt = QComboBox()
        for key, name in (("rts", self.tr("RTS on the interface's serial port (Digirig)")),
                          ("dtr", self.tr("DTR on the interface's serial port")),
                          ("cm108", self.tr("A CM108 sound chip's GPIO pin (AllScan, AIOC, home-built)")),
                          ("vox", self.tr("The interface's own VOX (SignaLink)")),
                          ("none", self.tr("None: receive only"))):
            self.k_ptt.addItem(name, key)
        self.k_ptt_port = self._combo()
        self.k_gpio = QSpinBox()
        self.k_gpio.setRange(1, 8)
        f.addRow(self.tr("PC port (control)"), self.k_cat)
        f.addRow(self.tr("PC port speed (radio menu 519)"), self.k_baud)
        f.addRow(self.tr("Packet speed (DATA jack)"), self.k_speed)
        f.addRow(self.tr("Interface sound input"), self.k_in)
        f.addRow(self.tr("Interface sound output"), self.k_out)
        f.addRow(self.tr("PTT"), self.k_ptt)
        f.addRow(self.tr("PTT port or CM108 device"), self.k_ptt_port)
        f.addRow(self.tr("CM108 GPIO pin"), self.k_gpio)
        f.addRow(_note(self.tr("Two cables: the radio's PC port (8-pin mini-DIN, a PG-5G or compatible USB cable) "
                               "for control, and a sound-card interface on its DATA jack (6-pin mini-DIN) for audio "
                               "and PTT. The app never keys the radio over the PC port. The next page lists what "
                               "to set on the radio.")))
        self.stack.addWidget(w)
        return w

    def _soundcard(self):
        w = QWidget()
        f = QFormLayout(w)
        self.s_in = self._combo(False)
        self.s_out = self._combo(False)
        self.s_ptt = QComboBox()
        for key, name in (("rts", self.tr("RTS on a serial port")), ("dtr", self.tr("DTR on a serial port")),
                          ("vox", self.tr("VOX (the radio keys on the audio)")), ("none", self.tr("None: receive only"))):
            self.s_ptt.addItem(name, key)
        self.s_ptt_port = self._combo()
        f.addRow(self.tr("Sound input (from the radio)"), self.s_in)
        f.addRow(self.tr("Sound output (to the radio)"), self.s_out)
        f.addRow(self.tr("PTT"), self.s_ptt)
        f.addRow(self.tr("PTT port"), self.s_ptt_port)
        self.stack.addWidget(w)
        return w

    def _lists(self):
        ports = discover.serial_ports()
        devices = discover.sound_devices(self.wiz.sound_backend)
        ins = [d["name"] for d in devices if d.get("inputs")]
        outs = [d["name"] for d in devices if d.get("outputs")]
        for c in (self.civ, self.cat, self.y_ptt_port, self.s_ptt_port, self.k_cat, self.k_ptt_port):
            keep = c.currentText()
            c.clear()
            c.addItem("")
            for dev, desc in ports:
                c.addItem(f"{dev}  {desc}".strip() if desc else dev, dev)
            c.setCurrentText(keep)
        for c, names in ((self.icom_in, ins), (self.y_in, ins), (self.s_in, ins), (self.k_in, ins),
                         (self.icom_out, outs), (self.y_out, outs), (self.s_out, outs), (self.k_out, outs)):
            keep = c.currentText()
            c.clear()
            c.addItem("")
            c.addItems(names)
            c.setCurrentText(keep)
        if not devices:
            self.found.setText(self.tr("No sound devices found (is PortAudio installed?)."))

    def _find(self):
        self.found.setText(self.tr("Looking…"))
        self.found.repaint()
        radios = discover.find_icoms([self.host.text().strip()] if self.host.text().strip() else [])
        if radios:
            self.host.setText(radios[0].host)
            self.found.setText(self.tr("Found: %1").replace("%1", ", ".join(r.host for r in radios)))
        else:
            self.found.setText(self.tr("No radio answered. Type its address: on the radio, Menu › SET › Network "
                                       "shows it."))

    def _kind(self):
        k = self.kind.currentData()
        key = "icom" if k in ("ic-9700", "ic-705") else k
        self.stack.setCurrentWidget(self.pages[key])
        # An SDR's panel is its checks and tools, not details Find fills
        # in: open whenever an SDR is chosen.
        if k in SDR_RADIO_KINDS:
            self.details.set_open(True)

    def initializePage(self):
        r = self.wiz.s.radio
        self._lists()
        self.kind.setCurrentIndex(max(0, self.kind.findData(r.kind)))
        self.transport.setCurrentIndex(max(0, self.transport.findData(r.transport)))
        self.host.setText(r.host)
        self.port.setValue(int(r.port))
        self.user.setText(r.username)
        self.password.setText(self.wiz.secrets.get("icom_password", ""))
        self.civ.setCurrentText(r.civ_device)
        self.min_power.setChecked(bool(r.minimum_power_for_test))
        self.cat.setCurrentText(r.cat_device)
        self.k_cat.setCurrentText(r.cat_device)
        self.k_baud.setCurrentIndex(max(0, self.k_baud.findData(int(r.cat_baud or 9600))))
        self.k_speed.setCurrentIndex(max(0, self.k_speed.findData(int(r.data_speed or 1200))))
        self.k_ptt.setCurrentIndex(max(0, self.k_ptt.findData(r.ptt)))
        self.k_gpio.setValue(int(r.cm108_gpio or 3))
        for c in (self.icom_in, self.y_in, self.s_in, self.k_in):
            c.setCurrentText(r.audio_in)
        for c in (self.icom_out, self.y_out, self.s_out, self.k_out):
            c.setCurrentText(r.audio_out)
        self._ask_controller()
        self.y_ptt.setCurrentIndex(max(0, self.y_ptt.findData(r.ptt)))
        self.s_ptt.setCurrentIndex(max(0, self.s_ptt.findData(r.ptt)))
        for c in (self.y_ptt_port, self.s_ptt_port, self.k_ptt_port):
            c.setCurrentText(r.ptt_device)
        self.sdr.load(self.wiz.s, self.wiz.secrets)
        self._kind()

    def _ask_controller(self):
        from .. import ohsclient
        client = ohsclient.ControllerClient(self.wiz.s.controller, self.wiz.secrets,
                                            transport=getattr(self.wiz.ctl, "_ohs_transport", None))
        if not client.paired:
            self.on_controller.hide()
            return
        self.on_controller.setText(self.tr("Asking %1 which radios it manages…").replace(
            "%1", client.settings.name or client.settings.url))
        self.on_controller.show()
        self._worker.run("radios", client.radios)

    def _controller_radios(self, _what, res):
        from .controllerpage import radio_line
        name = self.wiz.s.controller.name or self.wiz.s.controller.url
        if isinstance(res, Exception):
            self.on_controller.setText(self.tr("%1 did not list its radios, so this computer opens the radio "
                                               "chosen here directly.").replace("%1", name))
        elif not res:
            self.on_controller.setText(self.tr("%1 manages no radios.").replace("%1", name))
        else:
            self.on_controller.setText(self.tr("Radios on %1:").replace("%1", name) + "\n"
                                       + "\n".join("• " + radio_line(r) for r in res))

    @staticmethod
    def _port(c: QComboBox) -> str:
        return (c.currentData() or c.currentText().split("  ")[0]).strip()

    def validatePage(self):
        r = self.wiz.s.radio
        k = r.kind = self.kind.currentData()
        if k in ("ic-9700", "ic-705"):
            r.transport = self.transport.currentData()
            r.host, r.port, r.username = self.host.text().strip(), self.port.value(), self.user.text().strip()
            r.civ_device = self._port(self.civ)
            r.audio_in, r.audio_out = self.icom_in.currentText(), self.icom_out.currentText()
            r.minimum_power_for_test = self.min_power.isChecked()
            self.wiz.secrets["icom_password"] = self.password.text()
        elif k == "ftx-1":
            r.cat_device, r.ptt = self._port(self.cat), self.y_ptt.currentData()
            r.ptt_device = self._port(self.y_ptt_port)
            r.audio_in, r.audio_out = self.y_in.currentText(), self.y_out.currentText()
        elif k == "tm-v71":
            r.cat_device, r.cat_baud = self._port(self.k_cat), int(self.k_baud.currentData())
            r.data_speed = int(self.k_speed.currentData())
            r.audio_in, r.audio_out = self.k_in.currentText(), self.k_out.currentText()
            r.ptt, r.ptt_device = self.k_ptt.currentData(), self._port(self.k_ptt_port)
            r.cm108_gpio = self.k_gpio.value()
        elif k == "soundcard":
            r.audio_in, r.audio_out = self.s_in.currentText(), self.s_out.currentText()
            r.ptt, r.ptt_device = self.s_ptt.currentData(), self._port(self.s_ptt_port)
        elif k in SDR_RADIO_KINDS:
            self.sdr.save(self.wiz.s, self.wiz.secrets, k)
        return True


class RadioMenuPage(QWizardPage):
    """What to set on the radio for the radio and connection just chosen."""

    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle(self.tr("On the radio"))
        lay = QVBoxLayout(self)
        self.yours = QLabel()
        self.yours.setWordWrap(True)
        self.yours.setTextFormat(Qt.TextFormat.RichText)
        self.app_does = _note("")
        self.app_does.setTextFormat(Qt.TextFormat.RichText)
        self.unchecked = _note(self.tr("* Not yet confirmed on a real radio of this model. If yours shows "
                                       "something different, please say so when you send diagnostics."), "warn")
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.addWidget(self.yours)
        il.addWidget(self.app_does)
        il.addWidget(self.unchecked)
        il.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        self.steps: list[radiomenu.Step] = []

    def initializePage(self):
        r = self.wiz.s.radio
        self.setSubTitle(self.tr("Settings to make on the %1 itself before it is opened on the next page.")
                         .replace("%1", texts.radio_kind(r.kind)))
        self.steps = radiomenu.steps(r.kind, r.transport, r.ptt, source=self.wiz.s.sdr_radio.rx_source,
                                     speed=int(r.data_speed or 1200))
        yours = [s for s in self.steps if not s.by_app]
        done = [s for s in self.steps if s.by_app]

        def item(s):
            return "<li>" + html.escape(s.text) + ("&nbsp;*" if not s.checked else "") + "</li>"
        if yours:
            self.yours.setText("<ol>" + "".join(item(s) for s in yours) + "</ol>")
        else:
            self.yours.setText(html.escape(self.tr("Nothing to set.")))
        self.app_does.setText("".join("<p>" + html.escape(s.text) + "</p>" for s in done))
        self.app_does.setVisible(bool(done))
        self.unchecked.setVisible(any(not s.checked for s in self.steps))


class AudioPage(QWizardPage):
    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle(self.tr("Connect and listen"))
        self.setSubTitle(self.tr("The radio is opened for receive only. Packet audio should sit in the shaded "
                                 "band when a station is heard."))
        lay = QVBoxLayout(self)
        self.state = _note("", "")
        self.meter = LevelMeter()
        self.level = _note("")
        self.retry = QPushButton(self.tr("Try again"))
        self.retry.clicked.connect(lambda: self.wiz.ctl.reconnect())
        #: What the link set on the radio for packet (radiochanges.py).
        self.changes = _note("")
        self.changes.setTextFormat(Qt.TextFormat.RichText)
        changes = QScrollArea()
        changes.setWidgetResizable(True)
        changes.setFrameShape(QScrollArea.Shape.NoFrame)
        changes.setWidget(self.changes)
        lay.addWidget(self.state)
        lay.addWidget(self.meter)
        lay.addWidget(self.level)
        lay.addWidget(self.retry)
        lay.addWidget(changes, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def initializePage(self):
        self.wiz.start_test()
        self.timer.start(100)
        self.tick()

    def cleanupPage(self):
        self.timer.stop()

    def tick(self):
        ctl = self.wiz.ctl
        st = ctl.status()
        text = texts.link_state(st["link"])
        if st["link"] == "failed":
            text += " - " + texts.reason(st["linkError"] or "radio-error")
            if ctl.link_error_detail:
                text += f" ({ctl.link_error_detail})"
        if st["radio"] == "pluto" and st["link"] == "open" and ctl.link is not None:
            # The guard on the Pluto (shack_pluto.guard): whether this
            # station may transmit at all with it, said where the operator
            # first sees the radio open.
            gs = getattr(ctl.link, "guard_state", "none")
            text += " " + texts.guard_state(gs)
            if gs == "failed" and getattr(ctl.link, "unguarded_why", None):
                text += f" ({ctl.link.unguarded_why})"
        self.state.setText(self.tr("%1: %2").replace("%1", texts.radio_kind(st["radio"])).replace("%2", text))
        self.retry.setVisible(st["link"] == "failed")
        changed = radiochanges.html_report(ctl.radio_setup) if st["link"] == "open" else ""
        if changed != self.changes.text():
            self.changes.setText(changed)
        tap = ctl.tap
        if ctl.settings.radio.kind == "simulator":
            self.meter.set_level(None)
            self.level.setText(self.tr("The simulator carries frames, not audio, so there is no level to show."))
        elif tap is None:
            self.meter.set_level(None)
            self.level.setText(self.tr("No audio link."))
        else:
            lvl = tap.level()
            self.meter.set_level(lvl)
            if lvl is None:
                self.level.setText(self.tr("No audio from the radio yet."))
            else:
                peak, rms = lvl
                self.level.setText(self.tr("Level %1 dBFS (peak %2 dBFS)").replace("%1", fmt.number(rms, 1))
                                   .replace("%2", fmt.number(peak, 1)))


class ReceivePage(QWizardPage):
    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle(self.tr("Receive test"))
        lay = QVBoxLayout(self)
        self.what = _note("", "")
        self.count = _note("", "heading")
        self.lines = QPlainTextEdit()
        self.lines.setReadOnly(True)
        lay.addWidget(self.what)
        lay.addWidget(self.count)
        lay.addWidget(self.lines, 1)
        lay.addWidget(_note(self.tr("Nothing heard can just mean a quiet channel: APRS stations usually beacon "
                                    "every 10-30 minutes. You can carry on and come back to this later.")))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self._from = 0

    def initializePage(self):
        ctl = self.wiz.ctl
        self.setSubTitle(self.tr("Listening on %1 for any packet station.").replace(
            "%1", fmt.mhz(ctl.settings.channel.frequency_hz)))
        self.what.setText(self.tr("Frames decoded so far appear below as they arrive."))
        with ctl._lock:
            self._from = ctl.rows[-1].seq if ctl.rows else 0
        self.timer.start(200)
        self.tick()

    def cleanupPage(self):
        self.timer.stop()

    def heard(self) -> list:
        ctl = self.wiz.ctl
        with ctl._lock:
            return [r for r in ctl.rows if r.seq > self._from and r.direction == "rx"]

    def tick(self):
        rows = self.heard()
        self.count.setObjectName("good" if rows else "")
        self.count.setStyleSheet("")
        self.count.setText(self.tr("%n frame(s) decoded", "", len(rows)))
        text = "\n".join(f"{fmt.utc_time(r.time)}  {r.text}" for r in rows[-12:])
        if text != self.lines.toPlainText():
            self.lines.setPlainText(text)


class TransmitPage(QWizardPage):
    def __init__(self, wiz):
        super().__init__()
        self.wiz = wiz
        self.setTitle(self.tr("Transmit"))
        self.setSubTitle(self.tr("Off unless you switch it on. Even then, nothing is sent until you press Arm, "
                                 "and the arming ends by itself."))
        lay = QVBoxLayout(self)
        self.allow = QCheckBox(self.tr("Let this computer transmit with my callsign"))
        self.allow.toggled.connect(self._allow)
        self.why = _note("")
        lay.addWidget(self.allow)
        lay.addWidget(self.why)
        row = QFormLayout()
        self.minutes = QSpinBox()
        self.minutes.setRange(ARM_MIN_MINUTES, ARM_MAX_MINUTES)
        self.minutes.setSuffix(self.tr(" min"))
        row.addRow(self.tr("Each arming lasts"), self.minutes)
        lay.addLayout(row)
        box = QGroupBox(self.tr("Measure the radio's turnaround (optional)"))
        bl = QVBoxLayout(box)
        # Said first and in the warning colour: this is the one button in
        # the app that transmits without a frame to send, and a tester must
        # not find that out from the radio's TX lamp.
        bl.addWidget(_note(self.tr("This transmits."), "bad"))
        bl.addWidget(_note(self.tr("It keys the radio three times for under a second each, with a tone, to time how "
                                   "fast it comes up to power, and sets the preamble from it. Only with you at the "
                                   "radio and a dummy load on its antenna socket.")))
        self.present = QCheckBox(self.tr("I am at the radio"))
        self.dummy = QCheckBox(self.tr("A dummy load is fitted in place of the antenna"))
        self.measure = QPushButton(self.tr("Transmit and measure"))
        self.measure.clicked.connect(self._measure)
        self.result = _note("")
        # The real-radio dummy-load test's minimum-power step (GUIDE.md): what
        # the radio link says about transmit power, read fresh every tick -
        # "pending" until the first key, then "minimum (0%)" once confirmed,
        # or the radio's own refusal if it did not answer.
        self.power_note = _note("")
        for w in (self.present, self.dummy, self.measure, self.result, self.power_note):
            bl.addWidget(w)
        for c in (self.present, self.dummy):
            c.toggled.connect(self._gates)
        lay.addWidget(box)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._gates)

    def initializePage(self):
        s = self.wiz.s
        callsign_ok = bool(s.station.callsign) and call_ok(s.station.callsign)
        self.allow.blockSignals(True)
        self.allow.setChecked(bool(s.tx.set_up) and callsign_ok)
        self.allow.blockSignals(False)
        sdr_blocked = (s.radio.kind == "sdr-rx"
                       or (s.radio.kind in SDR_TX_KINDS and not s.sdr_radio.filter_confirmed))
        if sdr_blocked:
            self.allow.blockSignals(True)
            self.allow.setChecked(False)
            self.allow.blockSignals(False)
        self.allow.setEnabled(callsign_ok and s.radio.kind != "none" and not sdr_blocked)
        if not callsign_ok:
            self.why.setText(self.tr("Transmit needs a valid callsign: go back to Your station to set one."))
        elif s.radio.kind == "none":
            self.why.setText(self.tr("There is no radio to transmit with."))
        elif s.radio.kind == "sdr-rx":
            self.why.setText(self.tr("This SDR receives only."))
        elif sdr_blocked:
            self.why.setText(texts.reason("sdr-filter"))
        elif s.radio.kind in SDR_TX_KINDS:
            self.why.setText(self.tr("Packet carries your callsign in every frame, which identifies the station. "
                                     "An SDR sends its harmonics too: the band filter stays fitted, and a dummy "
                                     "load stays on until the filter and any amplifier are proven."))
        else:
            self.why.setText(self.tr("Packet carries your callsign in every frame, which identifies the station."))
        self.minutes.setValue(int(s.tx.arm_minutes))
        self.present.setChecked(False)
        self.dummy.setChecked(False)
        self.timer.start(250)
        self._gates()

    def cleanupPage(self):
        self.timer.stop()

    def _allow(self, on: bool):
        self.wiz.s.tx.set_up = bool(on)
        # The link was made receive-only; a transmit path needs it made again.
        self.wiz.start_test()
        self._gates()

    def measure_possible(self) -> bool:
        ctl = self.wiz.ctl
        return (self.allow.isChecked() and self.present.isChecked() and self.dummy.isChecked()
                and ctl.settings.radio.kind in ("ic-9700", "ic-705", "ftx-1", "soundcard")
                and ctl.link_state == "open" and ctl.can_transmit())

    def _gates(self):
        self.measure.setEnabled(self.measure_possible())
        ctl = self.wiz.ctl
        power = power_status(ctl.settings.radio, ctl.link) if ctl.link is not None else None
        self.power_note.setText(texts.rf_power_status(*power) if power else "")

    def _measure(self):
        if not self.measure_possible():
            return
        ctl = self.wiz.ctl
        ok, why, detail = ctl.arm(1)
        if not ok:
            self.result.setText(texts.reason(why) + (f" {detail}" if detail else ""))
            return
        self.result.setText(self.tr("Measuring…"))
        ctl.measure_turnaround(CONFIRM, done=lambda: None)

    def show_turnaround(self, detail: dict):
        self.result.setText(self.tr("Turnaround %1 ms; TXDELAY %2 ms.").replace(
            "%1", fmt.number(detail.get("turnaround_s", 0) * 1000)).replace(
            "%2", fmt.number(detail.get("txdelay_s", 0) * 1000)))

    def validatePage(self):
        s = self.wiz.s
        s.tx.set_up = self.allow.isChecked()
        s.tx.arm_minutes = self.minutes.value()
        self.timer.stop()
        return True


class SetupWizard(QWizard):
    """`window` owns the controller; the wizard tries its settings on it as
    it goes and hands the result back (window.setup_finished)."""

    def __init__(self, window, settings, secrets, *, sound_backend=None):
        super().__init__(window)
        self.window = window
        self.ctl = window.ctl
        self.s = settings.copy()
        self.secrets = dict(secrets)
        self.sound_backend = sound_backend
        self.original = (settings.copy(), dict(secrets))
        self.setWindowTitle(self.tr("Setup check"))
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.station = StationPage(self)
        self.radio = RadioPage(self)
        self.radio_menu = RadioMenuPage(self)
        self.audio = AudioPage(self)
        self.receive = ReceivePage(self)
        self.transmit = TransmitPage(self)
        for p in (self.station, self.radio, self.radio_menu, self.audio, self.receive, self.transmit):
            self.addPage(p)
        self.resize(720, 560)

    def test_settings(self):
        """The settings being tried, tuned to the APRS channel for the test."""
        t = self.s.copy()
        aprs = next((p for p in presets.presets(t.station.profile()) if p.id == "aprs"), None)
        if aprs is not None:
            t.channel.preset, t.channel.frequency_hz = "aprs", aprs.hz
        return t

    def start_test(self):
        self.ctl.apply(self.test_settings(), self.secrets)

    def release_radio(self):
        """Let go of the radio while something else uses it (the Pluto's
        AD9364 enable restarts it); start_test takes it back."""
        t = self.test_settings()
        t.radio.kind = "none"
        self.ctl.apply(t, self.secrets)

    def event_seen(self, e) -> None:
        if e.kind == "tx" and e.reason == "turnaround":
            self.s.radio.turnaround_s = float(e.detail.get("turnaround_s", 0)) or None
            self.transmit.show_turnaround(e.detail)
        elif e.kind == "tx" and e.reason == "turnaround-failed":
            self.transmit.result.setText(texts.reason("turnaround-failed"))

    def accept(self):
        self.s.setup_done = True
        if self.s.radio.turnaround_s is None and self.ctl.settings.radio.turnaround_s:
            self.s.radio.turnaround_s = self.ctl.settings.radio.turnaround_s
        super().accept()
        self.window.setup_finished(self.s, self.secrets)

    def reject(self):
        super().reject()
        settings, secrets = self.original
        self.window.setup_cancelled(settings, secrets)
