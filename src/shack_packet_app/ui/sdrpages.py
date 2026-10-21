"""The setup check's "Your radio" panels for an SDR as the station's radio
(0.2, PLAN-0.2 §3.6): a Pluto, a HackRF One, a receive-only SDR.

Kept out of wizard.py so the radio page only gains three entries in its
stack and a load and a save; everything SDR is here.

**The Pluto panel** reaches the Pluto through plutosetup.Tools:

- *Check this Pluto* reads it (nothing written): model, firmware family,
  chip setting and range against the channel, and the crystal correction
  kept for it. Run on a thread, so a Pluto that is not there costs the
  window nothing.
- *Enable 70 MHz-6 GHz (AD9364)…* appears only when the channel is out of
  reach and the firmware is ADI's (D5); on Tezuka and PlutoDVB2 the panel
  says why it is not offered. It asks for both confirmations - a dummy load
  or nothing on TX, and the operator at the Pluto - because the Pluto
  transmits as it restarts (GUIDE §5); releases the station's radio first;
  runs on a thread; and says each outcome in words.
- *USB network mode* (0.2.1): the check reads it over SSH and says which
  mode suits the computer the Pluto is plugged into (plutosetup); a
  *Switch to ...* button runs the change as the AD9364 enable runs: both
  confirmations, the station's radio released, the reboot under the quiet
  watcher, and the seconds until quiet said afterwards. On Windows, a USB
  Pluto that does not answer gets the driver guidance (a link to ADI's
  driver; its licence does not clearly allow bundling it).
- *Find* (0.2.3), beside the address: plutofind's search (OpenHamShack
  NB's, ported) on a thread - libiio's scan, then direct probes of the
  likely addresses, reading only. One radio found fills the address; more
  are offered in a list. Nothing found says what was tried, and on Windows
  the driver guidance. Offered for a Pluto on this computer or the network
  only: the controller's Pluto has its own address, and a found address
  that is the paired controller's is put in as that (its pluto:// address,
  asked whether the Pluto is reserved or in use), never as a plain ip:.
- *Crystal correction*: one measurement (this Pluto's carrier read on a
  calibrated receiver, or a known signal read on this Pluto), the ppm it
  gives, Save and Forget, filed under the checked Pluto's key.

**Transmit gain** (0.2.2) is a dropdown of 0 to -60 dB in 2 dB steps, not
a spin box: Brian found the spin box's arrows too small to use on a Mac,
and a 2 dB step is as fine as anyone sets a Pluto by hand (the drive-level
helper steps 2 dB too). Gains above the band's amplifier drive cap are
shown but cannot be chosen (`tx_gain_step`).

**The panel scrolls** (0.2.2): on macOS its wrapped notes, the crystal
correction's fields and its buttons were squeezed into each other at the
setup check's size, because a wizard page does not grow for wrapped text.
In a scroll area each row keeps the height it needs.

**Transmit warnings** (D9, Brian 18/10/2026): the Pluto and HackRF panels
each carry the band-filter confirmation (settings.SdrRadioSettings.
filter_confirmed), without which transmit cannot be set up or armed; the
HackRF panel says its RF amplifier stays off and what a crash mid-over
leaves.
"""
from __future__ import annotations

import math
import threading

from PyQt6.QtCore import QCoreApplication, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
                             QVBoxLayout, QWidget)

from .. import plutofind, plutosetup
from ..settings import SdrRadioSettings
from . import fmt, texts


#: ADI's Windows USB driver for the Pluto: its releases page, where
#: PlutoSDR-M2k-USB-Drivers.exe is. Linked, never bundled (`_checked`).
DRIVER_LINK = "https://github.com/analogdevicesinc/plutosdr-m2k-drivers-win/releases"

#: The Pluto transmit gains the dropdown offers, dB (module docstring).
TX_GAIN_STEPS = tuple(float(-g) for g in range(0, 61, 2))
#: A new Pluto's transmit gain, settings' default. 0.2.1 started at -10 dB,
#: close to full power into whatever is connected; -30 dB is a level to
#: start from into a dummy load or an amplifier, and raise.
NEW_PLUTO_TX_GAIN_DB = SdrRadioSettings.pluto_tx_gain_db
#: The address put in for a Pluto on a controller that is not paired: the
#: controller's forwarded IIO and SSH ports, "host" for the operator to
#: replace (ohsclient.controller_pluto_uri builds the same from a pairing).
CONTROLLER_PLUTO_TEMPLATE = "pluto://host?iiod=30431&ssh=30422"


def tx_gain_step(gain_db: float, cap_db: float | None = None) -> float:
    """The dropdown's gain for a saved `gain_db` under the drive cap
    `cap_db`: the nearest step at or below both, so a 0.2.1 setting such as
    -9.5 dB opens as -10 dB and nothing is ever raised. A gain or cap below
    the lowest step is kept exactly (the dropdown adds it as an entry of its
    own): rounding it up to -60 dB would raise the power."""
    g = float(gain_db)
    if cap_db is not None:
        g = min(g, float(cap_db))
    if g < TX_GAIN_STEPS[-1]:
        return g
    return min(0.0, math.floor(g / 2.0 + 1e-9) * 2.0)


def db_text(db: float) -> str:
    """"−30 dB", with a true minus sign, for the dropdown."""
    n = fmt.number(db, 0 if float(db).is_integer() else 2)
    return QCoreApplication.translate("SdrRadioWidgets", "%1 dB").replace("%1", n.replace("-", "\u2212"))


def fill_tx_gain(combo, gain_db: float, cap_db: float | None) -> float:
    """Fill `combo` with the Pluto transmit gains, `gain_db` chosen as
    tx_gain_step gives it under the drive cap `cap_db`; the chosen gain.
    Entries above the cap stay in the list, greyed, so the operator sees
    why -2 dB is not there to pick. Shared by the setup check's Pluto
    page and Settings › Channel (0.2.3), so the two cannot disagree."""
    value = tx_gain_step(gain_db, cap_db)
    combo.blockSignals(True)
    combo.clear()
    steps = list(TX_GAIN_STEPS)
    if value not in steps:
        steps.append(value)                         # below the lowest step: kept, never raised
    for g in steps:
        combo.addItem(db_text(g), g)
        if cap_db is not None and g > float(cap_db) + 1e-9:
            combo.model().item(combo.count() - 1).setEnabled(False)
    combo.setCurrentIndex(combo.findData(value))
    combo.blockSignals(False)
    return value


def _note(text: str, name: str = "dim") -> QLabel:
    w = QLabel(text)
    w.setWordWrap(True)
    w.setObjectName(name)
    return w


class _Job:
    """One call on a thread of its own, its result picked up by a timer on
    the window's thread (Qt widgets are touched only there)."""

    def __init__(self, fn, done, parent):
        self.result = self.error = None
        self.finished = False

        def run():
            try:
                self.result = fn()
            except Exception as e:                  # noqa: BLE001 - shown by `done`
                self.error = e
            self.finished = True
        self.timer = QTimer(parent)
        self.timer.timeout.connect(self._poll)
        self.done = done
        threading.Thread(target=run, name="setup-sdr", daemon=True).start()
        self.timer.start(50)

    def _poll(self):
        if self.finished:
            self.timer.stop()
            self.done(self.result, self.error)


def _why(e: Exception) -> str:
    reason = getattr(e, "reason", "")
    detail = getattr(e, "detail", "") or (str(e) if not reason else "")
    return (texts.reason(reason) if reason else "") + (f" ({detail})" if detail else "")


class EnableDialog(QDialog):
    """Both confirmations before the AD9364 enable restarts the Pluto; with
    `title` and `text`, before any other change that restarts it (the USB
    network mode), in the same words for what the restart does."""

    def __init__(self, parent, title: str = "", text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title or self.tr("Enable 70 MHz-6 GHz"))
        lay = QVBoxLayout(self)
        lay.addWidget(_note(self.tr("This transmits."), "bad"))
        lay.addWidget(_note(text or self.tr(
            "The Pluto's settings are saved on this computer, then changed so it runs as an AD9364, and the "
            "Pluto restarts. As it starts up its transmitter comes on by itself until the app switches it off, "
            "a second or so after the Pluto answers again."), ""))
        self.dummy = QCheckBox(self.tr("A dummy load, or nothing, is on the Pluto's TX port"))
        self.present = QCheckBox(self.tr("I am at the Pluto"))
        lay.addWidget(self.dummy)
        lay.addWidget(self.present)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        for c in (self.dummy, self.present):
            c.toggled.connect(self._gate)
        self._gate()

    def confirmed(self) -> bool:
        return self.dummy.isChecked() and self.present.isChecked()

    def _gate(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.confirmed())


class FoundDialog(QDialog):
    """The Plutos Find found, to pick one: address and what it says it is."""

    def __init__(self, parent, found):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        self.setWindowTitle(self.tr("Plutos found"))
        lay = QVBoxLayout(self)
        lay.addWidget(_note(self.tr("Choose the Pluto to use. A numeric address is listed first: it keeps working "
                                    "on a computer that cannot look names up."), ""))
        self.list = QListWidget()
        for f in found:
            item = QListWidgetItem(f"{f.uri}   {f.description}")
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        lay.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(520, 260)

    def chosen(self):
        item = self.list.currentItem()
        from PyQt6.QtCore import Qt
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


class PairDialog(QDialog):
    """Pairing with a controller from the setup check: Settings ›
    Controller's page (controllerpage.ControllerPage) as it is, finding
    controllers as it opens, and a Close button."""

    def __init__(self, parent, client):
        super().__init__(parent)
        from .controllerpage import ControllerPage
        self.setWindowTitle(self.tr("Pair with your controller"))
        lay = QVBoxLayout(self)
        self.page = ControllerPage(client, self)
        lay.addWidget(self.page)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(560, 480)
        if not client.paired:
            self.page.find()


class SdrRadioWidgets:
    """The three panels, for RadioPage's stack (module docstring)."""

    def __init__(self, page, tools: plutosetup.Tools | None = None):
        self.page = page
        self.tools = tools or plutosetup.Tools()
        self.checked: plutosetup.CheckResult | None = None
        self.job: _Job | None = None
        #: Asked before the enable restarts the Pluto; the wizard sets it
        #: (it releases the station's radio). A test sets its own.
        self.release_radio = lambda: None
        self.restore_radio = lambda: None
        self.confirm_enable = self._ask_enable
        self.confirm_usb = self._ask_usb
        #: Asked when the controller is chosen and none is paired; returns
        #: when the operator is done. A test sets its own.
        self.pair_controller = self._ask_pair
        #: Find's search (plutofind.find) and, with more than one Pluto
        #: found, the operator's pick of them (a Found, or None). A test
        #: sets its own.
        self.finder = plutofind.find
        self.choose_pluto = self._ask_pluto
        self.find_job: _Job | None = None

    def tr(self, text: str) -> str:
        # The context pylupdate6 gives `self.tr` in this class: not the
        # page's, which Qt's own tr() would look the sentence up under.
        return QCoreApplication.translate("SdrRadioWidgets", text)

    def pages(self, stack) -> dict:
        out = {"pluto": self._pluto(), "hackrf": self._hackrf(), "sdr-rx": self._sdrrx()}
        for w in out.values():
            stack.addWidget(w)
        return out

    # -- the Pluto ----------------------------------------------------------------------

    def _filter_box(self, lay) -> QCheckBox:
        """The band-filter confirmation, added to `lay`. Its sentence is a
        wrapped label beside the box, not the box's own text: a check box
        never wraps, and in the scrolling panel the sentence ran off its
        right edge. Clicking the sentence ticks the box, as the box's own
        text would."""
        text = self.tr("A band-pass filter is fitted between this SDR and anything that radiates, and a dummy load "
                       "until it is proven")
        tip = self.tr("An SDR's harmonics are strong and it has no filter of its own. Transmit cannot be set up "
                      "without this.")
        c = QCheckBox()
        c.setAccessibleName(text)
        label = _note(text, "")
        label.mouseReleaseEvent = lambda _e: c.toggle()
        row = QHBoxLayout()
        row.addWidget(c, 0)
        row.addWidget(label, 1)
        for w in (c, label):
            w.setToolTip(tip)
        lay.addLayout(row)
        return c

    def _pluto(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        f = QFormLayout()
        self.pluto_uri = QLineEdit()
        self.pluto_password = QLineEdit()
        self.pluto_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.pluto_tx = QComboBox()
        self.pluto_tx.setEditable(False)
        self.tx_cap_note = _note("")
        self.tx_cap_note.hide()
        self._fill_tx(NEW_PLUTO_TX_GAIN_DB, None)
        self.pluto_rx_auto = QCheckBox(self.tr("Automatic"))
        self.pluto_rx = QDoubleSpinBox()
        self.pluto_rx.setRange(0.0, 73.0)
        self.pluto_rx.setSuffix(self.tr(" dB"))
        self.pluto_rx_auto.toggled.connect(lambda on: self.pluto_rx.setEnabled(not on))
        rx = QHBoxLayout()
        rx.addWidget(self.pluto_rx_auto)
        rx.addWidget(self.pluto_rx, 1)
        self.pluto_where = QComboBox()
        self.pluto_where.addItem(self.tr("On this computer's USB, or on the network"), "direct")
        self.pluto_where.addItem(self.tr("On the OpenHamShack controller"), "controller")
        self.pluto_where.activated.connect(lambda _i: self._where_changed())
        self.where_note = _note("", "")
        self.where_note.hide()
        f.addRow(self.tr("Where"), self.pluto_where)
        self.find_btn = QPushButton(self.tr("Find"))
        self.find_btn.setToolTip(self.tr("Look for a Pluto on this computer's USB and on the network"))
        self.find_btn.clicked.connect(self.find)
        address = QHBoxLayout()
        address.addWidget(self.pluto_uri, 1)
        address.addWidget(self.find_btn)
        self.find_note = _note("", "")
        self.find_note.hide()
        f.addRow(self.tr("Address"), address)
        f.addRow("", self.find_note)
        f.addRow("", self.where_note)
        f.addRow(self.tr("SSH password"), self.pluto_password)
        f.addRow(self.tr("Transmit gain"), self.pluto_tx)
        f.addRow("", self.tx_cap_note)
        f.addRow(self.tr("Receive gain"), rx)
        lay.addLayout(f)
        self.pluto_filter = self._filter_box(lay)
        fast = QHBoxLayout()
        self.fast_btn = QPushButton(self.tr("Fast modes and amplifier…"))
        self.fast_btn.clicked.connect(self.edit_fast)
        fast.addWidget(self.fast_btn)
        fast.addStretch(1)
        lay.addLayout(fast)
        lay.addWidget(_note(self.tr("With an amplifier: put the band-pass filter AFTER the amplifier, and power "
                                    "the amplifier only for transmit runs (a Pluto that restarts transmits by "
                                    "itself until it is switched off)."), "warn"))
        row = QHBoxLayout()
        self.check_btn = QPushButton(self.tr("Check this Pluto"))
        self.check_btn.clicked.connect(self.check)
        self.enable_btn = QPushButton(self.tr("Enable 70 MHz-6 GHz (AD9364)…"))
        self.enable_btn.clicked.connect(self.enable)
        self.enable_btn.setVisible(False)
        self.usb_btn = QPushButton("")
        self.usb_btn.clicked.connect(self.switch_usb)
        self.usb_btn.setVisible(False)
        row.addWidget(self.check_btn)
        row.addWidget(self.enable_btn)
        row.addWidget(self.usb_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.check_text = _note("", "")
        lay.addWidget(self.check_text)
        lay.addWidget(self._xo_box())
        lay.addStretch(1)
        self._xo_enable()
        return self._scrolled(w)

    @staticmethod
    def _scrolled(w: QWidget) -> QScrollArea:
        """`w` in a frameless scroll area that only ever scrolls up and down
        (module docstring: the panel scrolls)."""
        from PyQt6.QtCore import Qt
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(w)
        return area

    def _xo_box(self) -> QGroupBox:
        """The crystal correction. Its notes and buttons are rows of a box
        layout, and only the three fields a form: a wrapped label spanning a
        form row was given too little height on macOS and drawn over the
        buttons. The two frequencies start blank (a blank one gives no ppm)
        until a check fills in the channel's (`_xo_defaults`): 46 MHz, the
        spin box's floor, meant nothing."""
        box = QGroupBox(self.tr("Crystal correction"))
        bl = QVBoxLayout(box)
        self.xo_now = _note("")
        bl.addWidget(self.xo_now)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.xo_mode = QComboBox()
        self.xo_mode.addItem(self.tr("This Pluto's carrier, read on a calibrated receiver"), "tx")
        self.xo_mode.addItem(self.tr("A signal of known frequency, read on this Pluto"), "rx")
        self.xo_expected = QDoubleSpinBox()
        self.xo_measured = QDoubleSpinBox()
        for sp in (self.xo_expected, self.xo_measured):
            # 0 is "not given" and shows blank; anything typed is a frequency.
            sp.setRange(0.0, 6000.0)
            sp.setDecimals(6)
            sp.setSpecialValueText(" ")
            sp.setSuffix(self.tr(" MHz"))
            sp.setValue(0.0)
        form.addRow(self.tr("Measured"), self.xo_mode)
        form.addRow(self.tr("It should be"), self.xo_expected)
        form.addRow(self.tr("It was"), self.xo_measured)
        bl.addLayout(form)
        self.xo_ppm = _note("")
        bl.addWidget(self.xo_ppm)
        for sp in (self.xo_expected, self.xo_measured):
            sp.valueChanged.connect(self._xo_preview)
        self.xo_mode.currentIndexChanged.connect(self._xo_preview)
        xr = QHBoxLayout()
        self.xo_save = QPushButton(self.tr("Save for this Pluto"))
        self.xo_save.clicked.connect(self.save_xo)
        self.xo_forget = QPushButton(self.tr("Forget"))
        self.xo_forget.clicked.connect(self.forget_xo)
        xr.addWidget(self.xo_save)
        xr.addWidget(self.xo_forget)
        xr.addStretch(1)
        bl.addLayout(xr)
        bl.addWidget(_note(self.tr("Measure with no correction saved. The correction is written each time the Pluto "
                                   "is opened: the Pluto forgets it when it restarts.")))
        return box

    # -- the transmit gain (0.2.2) ------------------------------------------------------------

    def _fill_tx(self, gain_db: float, cap_db: float | None) -> None:
        """The dropdown's entries under the cap, `gain_db` chosen
        (tx_gain_step). Entries above the cap stay in the list, greyed, so
        the operator sees why -2 dB is not there to pick."""
        fill_tx_gain(self.pluto_tx, gain_db, cap_db)
        if cap_db is None:
            self.tx_cap_note.hide()
        else:
            from ..sdrplan import band_of
            self.tx_cap_note.setText(self.tr("The amplifier's drive cap on %1 is %2: no more gain than that is "
                                             "used (Fast modes and amplifier).")
                                     .replace("%1", band_of(self._channel_hz()) or "?").replace("%2", db_text(cap_db)))
            self.tx_cap_note.show()

    def tx_gain(self) -> float:
        return float(self.pluto_tx.currentData())

    def _tx_cap(self) -> float | None:
        """The drive cap for the setup check's channel: the band the Fast
        modes dialog beside it shows and sets, so the two always agree.
        The link applies the cap of the band in use as well (links.py)."""
        return self.page.wiz.s.fast.drive_cap(self._channel_hz())

    def _refresh_tx(self) -> None:
        self._fill_tx(self.tx_gain(), self._tx_cap())

    # -- a Pluto on the controller (0.2.1) ------------------------------------------------------

    def _where_changed(self):
        """The operator chose where the Pluto is. On the controller: its
        address from the paired controller (ohsclient.controller_pluto_uri),
        the network warning, and the controller asked whether its Pluto is
        reserved or in use. Back to direct: the USB address."""
        from .. import ohsclient
        self.find_btn.setVisible(self.pluto_where.currentData() != "controller")
        self.find_note.hide()
        if self.pluto_where.currentData() != "controller":
            if self.pluto_uri.text().startswith("pluto://"):
                self.pluto_uri.setText("ip:192.168.2.1")
            self.where_note.hide()
            return
        ctl = self.page.wiz.s.controller
        if not self._client().paired:
            # Paired here and now, with Settings › Controller's own page,
            # rather than sending the operator off to it (0.2.1 left the USB
            # address in place and only said to pair first).
            self.pair_controller()
        uri = ohsclient.controller_pluto_uri(ctl.url) if self._client().paired else ""
        if not uri:
            self.pluto_uri.setText(CONTROLLER_PLUTO_TEMPLATE)
            self.pluto_uri.setFocus()
            self.pluto_uri.setSelection(len("pluto://"), len("host"))
            self.where_note.setObjectName("bad")
            self.where_note.setStyleSheet("")
            self.where_note.setText(self.tr("No controller is paired: replace host with the controller's name or "
                                            "address, or pair with it in Settings › Controller."))
            self.where_note.show()
            return
        self.pluto_uri.setText(uri)
        self._controller_note(None)
        self.controller_job = _Job(self._client().radios, self._controller_answered, self.page)

    def _client(self):
        """The setup check's controller client: its copies of the settings
        and secrets, so a pairing made here is kept only if the setup check
        is finished, as everything else on it is."""
        from .. import ohsclient
        return ohsclient.ControllerClient(self.page.wiz.s.controller, self.page.wiz.secrets,
                                          transport=getattr(self.page.wiz.ctl, "_ohs_transport", None))

    def _ask_pair(self) -> None:
        """Settings › Controller's page in a dialog, already looking for
        controllers; closed when the operator is done (paired or not)."""
        d = PairDialog(self.page, self._client())
        self.pair_dialog = d
        d.exec()

    def _controller_answered(self, radios, error):
        from .. import ohsclient
        if error is not None:
            self._controller_note(self.tr("The controller did not say whether its Pluto is free (%1): this "
                                          "computer checks again when it opens it.").replace("%1", _why(error)))
            return
        held = ohsclient.pluto_conflict(radios)
        if held is not None:
            key = "controller-reserved" if held.reserved else "controller-in-use"
            from .controllerpage import radio_line
            self._controller_note(texts.reason(key) + " " + radio_line(held), bad=True)
        elif not any(r.cls in ohsclient.PLUTO_CLASSES for r in radios):
            self._controller_note(self.tr("The controller lists no Pluto: is one plugged into it?"), bad=True)
        else:
            self._controller_note(self.tr("The controller's Pluto is free."))

    def _controller_note(self, extra, bad: bool = False):
        """The network warning, always, with what the controller said."""
        text = self.tr("A Pluto on the controller is used over the network: its samples are about 10 MB/s each "
                       "way (2.4576 MS/s), so use a wired connection to the controller. Over Wi-Fi or a slow "
                       "tailnet path samples are lost, and with them frames. PlutoDVB2's SSH is slow to answer, "
                       "so starting the transmitter guard can take up to 25 seconds.")
        self.where_note.setObjectName("bad" if bad else "warn")
        self.where_note.setStyleSheet("")
        self.where_note.setText(text + (" " + extra if extra else ""))
        self.where_note.show()

    def _channel_hz(self) -> int:
        return int(self.page.wiz.test_settings().channel.frequency_hz)

    def _family(self) -> str:
        """The firmware family of the Pluto at the address typed now: the
        check's, if it checked this address, else what was saved for it."""
        uri = self.pluto_uri.text().strip()
        if self.checked is not None and self.checked.ident is not None and self.checked.ident.uri == uri:
            return self.checked.ident.family
        r = self.page.wiz.s.sdr_radio
        return r.pluto_family if r.pluto_uri == uri else ""

    def edit_fast(self):
        """The fast modes and the amplifier (ui/fastpanel.py) for the setup
        check's settings; the drive-level helper is Settings' (it runs on
        the station as it is in force)."""
        from .fastpanel import FastDialog
        s = self.page.wiz.s
        d = FastDialog(self.page, s.fast, family=self._family(), frequency=self._channel_hz)
        self.fast_dialog = d
        if d.exec():
            s.fast = d.panel.result()
            self._refresh_tx()                      # a cap set or lowered there applies here at once

    def check(self):
        if self.job is not None and not self.job.finished:
            return
        uri, password, hz = self.pluto_uri.text().strip(), self.pluto_password.text(), self._channel_hz()
        self.check_text.setText(self.tr("Checking…"))
        self.check_btn.setEnabled(False)
        self.job = _Job(lambda: self.tools.check(uri, password, hz), self._checked, self.page)

    def _checked(self, result, error):
        self.check_btn.setEnabled(True)
        self.checked = result
        self.usb_btn.setVisible(False)
        if error is not None:
            self.enable_btn.setVisible(False)
            text = self.tr("No Pluto answered at %1: %2").replace("%1", self.pluto_uri.text()).replace(
                "%2", _why(error) or str(error))
            if getattr(self.tools, "platform", "") == "win32" and plutosetup.is_usb_gadget_address(
                    self.pluto_uri.text().strip()):
                text += " " + self._driver_hint()
            self.check_text.setText(text)
            self._xo_enable()
            return
        ident = result.ident
        lines = [self.tr("%1, %2 (%3).").replace("%1", ident.hw_model or self.tr("a Pluto"))
                 .replace("%2", texts.pluto_family(ident.family)).replace("%3", ident.fw_version or "?")]
        if ident.tx_range:
            lines.append(self.tr("Set up as %1: tunes %2 to %3.").replace("%1", ident.chip.upper() or "?")
                         .replace("%2", fmt.mhz(ident.tx_range[0])).replace("%3", fmt.mhz(ident.tx_range[1])))
        hz = fmt.mhz(result.channel_hz)
        if result.reach is True:
            lines.append(self.tr("%1 is in reach.").replace("%1", hz))
        elif result.enable == "offer":
            lines.append(self.tr("%1 is out of reach. The AD9364 setting would bring it in.").replace("%1", hz))
        elif result.enable == "refused":
            lines.append(self.tr("%1 is out of reach.").replace("%1", hz) + " " + texts.reason("enable-firmware"))
        elif result.reach is False:
            lines.append(self.tr("%1 is out of this Pluto's reach.").replace("%1", hz))
        if ident.key is None:
            lines.append(self.tr("This Pluto gives no serial number or address to remember it by, so a crystal "
                                 "correction cannot be kept for it."))
        lines.append(self._usb_line(result))
        self.check_text.setText(" ".join(lines))
        self.enable_btn.setVisible(result.enable == "offer")
        self._xo_defaults(result.channel_hz)
        self._xo_enable()

    def _driver_hint(self) -> str:
        # ADI's driver is linked, not bundled: its licence limits use to
        # Analog Devices parts and its installer carries Microsoft's
        # DPInst, whose terms it does not state.
        return self.tr(
            "On Windows a Pluto on USB needs Analog Devices' PlutoSDR-M2k USB driver before it has a "
            "network. It is not included with this program: download PlutoSDR-M2k-USB-Drivers.exe from "
            "%1, install it, then unplug the Pluto and plug it in again.").replace("%1", DRIVER_LINK)

    # -- finding the Pluto's address (0.2.3) ----------------------------------------------------

    def find(self):
        """Find (module docstring). The search and the question whether a
        found address is the paired controller's (a name lookup) both run
        on the thread; only the answer comes back to the window."""
        if self.find_job is not None and not self.find_job.finished:
            return
        from .. import ohsclient
        ctl_url = self.page.wiz.s.controller.url if self._client().paired else ""
        finder = self.finder

        def search():
            result = finder()
            ours = {f.uri for f in result.found if ctl_url and ohsclient.is_controller_pluto(f.uri, ctl_url)}
            return result, ours
        self.find_btn.setEnabled(False)
        self._find_note(self.tr("Looking for a Pluto on USB and on the network…"))
        self.find_job = _Job(search, self._found, self.page)

    def _find_note(self, text: str, name: str = "") -> None:
        self.find_note.setObjectName(name)
        self.find_note.setStyleSheet("")
        self.find_note.setText(text)
        self.find_note.setVisible(bool(text))

    def _found(self, answer, error):
        self.find_btn.setEnabled(True)
        if error is not None:                       # plutofind.find never raises; a test's finder might
            self._find_note(self.tr("The search failed (%1).").replace("%1", _why(error) or str(error)), "bad")
            return
        result, ours = answer
        if not result.found:
            self._find_note(self._nothing_found(result.notes), "bad")
            return
        pick = result.found[0] if len(result.found) == 1 else self.choose_pluto(result.found)
        if pick is None:
            self._find_note("")
            return
        if pick.uri in ours:
            # The paired controller's Pluto: through the controller, as
            # "Where" would put it, never round its reservation.
            self.pluto_where.setCurrentIndex(self.pluto_where.findData("controller"))
            self._where_changed()
            return
        self.pluto_uri.setText(pick.uri)
        text = self.tr("Found %1: %2.").replace("%1", pick.uri).replace("%2", pick.description)
        if pick.uri.startswith("usb:"):
            text += " " + texts.reason("guard-no-network")
        self._find_note(text + " " + self.tr("Check this Pluto to read it."))

    def _nothing_found(self, notes) -> str:
        lines = [self.tr("No Pluto found. Searched USB and the network, and tried ip:192.168.2.1, ip:pluto.local "
                         "and this computer's USB networks directly.")]
        if plutofind.NOTE_NO_LIBIIO in notes:
            lines = [self.tr("No Pluto can be found: the libiio library could not be loaded. Reinstalling this "
                             "program puts it back.")]
        if plutofind.NOTE_NO_AVAHI in notes:
            lines.append(self.tr("Finding a Pluto on the network by name needs avahi-daemon (sudo apt install "
                                 "avahi-daemon)."))
        platform = getattr(self.tools, "platform", "")
        if platform == "win32":
            lines.append(self._driver_hint())
        elif platform == "darwin":
            lines.append(self.tr("A Mac reaches a Pluto's USB network only when the Pluto is in ECM mode: macOS has "
                                 "no RNDIS."))
        lines.append(self.tr("A Pluto on the network can also be typed in as ip: and its address."))
        return " ".join(lines)

    def _ask_pluto(self, found):
        d = FoundDialog(self.page, found)
        self.found_dialog = d
        return d.chosen() if d.exec() else None

    def _usb_line(self, result) -> str:
        """The USB network mode, and the switch offered when it is not the
        one recommended for the computer the Pluto is plugged into. A
        pluto:// Pluto is plugged into another computer (the controller):
        its mode is said as that computer's, never judged for this one
        (0.2.1 said "That is the one to use here" of it; 0.2.2). Off RNDIS
        it is still offered the switch, as advice for the controller
        (plutosetup: it runs Linux, where NCM stalls). Any other network
        address may be this computer's own USB Pluto by name (pluto.local),
        so it is only said, as before, with no advice either way."""
        usb, want = result.usb, result.usb_recommended
        if usb is None:
            return self.tr("Its USB network mode could not be read (no SSH).")
        now = (usb.running or usb.saved or "?").upper()
        if self.pluto_uri.text().strip().startswith("pluto://"):
            line = self.tr("USB network: %1, the mode the computer this Pluto is plugged into uses with it.").replace(
                "%1", now)
            if want and (usb.running or usb.saved) != want:
                line += " " + self.tr("The controller runs Linux, where %1 is the mode to use.").replace(
                    "%1", want.upper())
                self.usb_btn.setText(self.tr("Switch to %1…").replace("%1", want.upper()))
                self.usb_btn.setVisible(True)
            return line
        line = self.tr("USB network: %1.").replace("%1", now)
        if want and (usb.running or usb.saved) != want:
            line += " " + self.tr("%1 is the one to use here: %2").replace("%1", want.upper()).replace(
                "%2", texts.usb_mode_why(want))
            self.usb_btn.setText(self.tr("Switch to %1…").replace("%1", want.upper()))
            self.usb_btn.setVisible(True)
        elif want:
            line += " " + self.tr("That is the one to use here.")
        return line

    def _ask_usb(self, mode: str) -> bool:
        d = EnableDialog(self.page, self.tr("Change the USB network mode"), self.tr(
            "The Pluto's settings are saved on this computer, then its USB network is set to %1, and the Pluto "
            "restarts. As it starts up its transmitter comes on by itself until the app switches it off, a second "
            "or so after the Pluto answers again.").replace("%1", mode.upper()))
        return bool(d.exec()) and d.confirmed()

    def switch_usb(self):
        """The guided USB mode switch, as the AD9364 enable: both
        confirmations, the station's radio released, the reboot under the
        quiet watcher on a thread (plutosetup.switch_usb_mode)."""
        result = self.checked
        if result is None or not result.usb_recommended or (self.job is not None and not self.job.finished):
            return
        mode = result.usb_recommended
        if not self.confirm_usb(mode):
            return
        self.release_radio()
        self.check_text.setText(self.tr("Saving the Pluto's settings, changing them and restarting it. This "
                                        "takes up to a minute; leave the Pluto plugged in."))
        for b in (self.usb_btn, self.enable_btn, self.check_btn):
            b.setEnabled(False)
        password = self.pluto_password.text()
        self.job = _Job(lambda: self.tools.set_usb_mode(result.ident, password, mode, operator_confirmed=True),
                        lambda out, err: self._usb_switched(mode, out, err), self.page)

    def _usb_switched(self, mode, outcome, error):
        for b in (self.usb_btn, self.enable_btn, self.check_btn):
            b.setEnabled(True)
        self.restore_radio()
        if error is not None:
            self.check_text.setText(self.tr("Not done: %1").replace("%1", _why(error) or str(error)))
            return
        self.usb_btn.setVisible(False)
        self.check_text.setText(self.tr("Done: the Pluto's USB network is now %1. Its transmitter was switched off "
                                        "%2 s after it answered again; its old settings are saved in %3.")
                                .replace("%1", mode.upper()).replace("%2", fmt.number(outcome.quiet_after_s or 0.0, 2))
                                .replace("%3", str(outcome.backup)))

    def _ask_enable(self) -> bool:
        d = EnableDialog(self.page)
        return bool(d.exec()) and d.confirmed()

    def enable(self):
        result = self.checked
        if result is None or result.enable != "offer" or (self.job is not None and not self.job.finished):
            return
        if not self.confirm_enable():
            return
        self.release_radio()
        self.check_text.setText(self.tr("Saving the Pluto's settings, changing them and restarting it. This "
                                        "takes up to a minute; leave the Pluto plugged in."))
        self.enable_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        password = self.pluto_password.text()
        self.job = _Job(lambda: self.tools.enable(result.ident, password, operator_confirmed=True), self._enabled,
                        self.page)

    def _enabled(self, outcome, error):
        self.enable_btn.setEnabled(True)
        self.check_btn.setEnabled(True)
        self.restore_radio()
        if error is not None:
            self.check_text.setText(self.tr("Not done: %1").replace("%1", _why(error) or str(error)))
            return
        quiet = outcome.quiet_after_s
        self.enable_btn.setVisible(False)
        self.check_text.setText(self.tr("Done: the Pluto now tunes 70 MHz-6 GHz. Its transmitter was switched off "
                                        "%1 s after it answered again; its old settings are saved in %2.")
                                .replace("%1", fmt.number(quiet or 0.0, 2)).replace("%2", str(outcome.backup)))

    def _xo_key(self) -> str | None:
        return self.checked.ident.key if self.checked is not None and self.checked.ident is not None else None

    def _xo_enable(self):
        key = self._xo_key()
        for w in (self.xo_save, self.xo_forget, self.xo_mode, self.xo_expected, self.xo_measured):
            w.setEnabled(key is not None)
        if key is None:
            self.xo_now.setText(self.tr("Check the Pluto first."))
        elif self.checked.xo is None:
            self.xo_now.setText(self.tr("None saved for this Pluto."))
        else:
            from shack_pluto.xo import ppm_of
            self.xo_now.setText(self.tr("Saved for this Pluto: %1 ppm.").replace(
                "%1", fmt.number(ppm_of(self.checked.xo), 2)))
        self._xo_preview()

    def _xo_defaults(self, channel_hz: int) -> None:
        """A checked Pluto's measurement starts from the channel it was
        checked on: what a carrier sent there should read. Only fields still
        blank, so a check never overwrites what the operator typed; "It was"
        stays blank until it is read on the receiver."""
        if channel_hz and not self.xo_expected.value():
            self.xo_expected.setValue(channel_hz / 1e6)

    def _xo_ppm(self) -> float | None:
        exp, got = self.xo_expected.value() * 1e6, self.xo_measured.value() * 1e6
        if not exp or not got:
            return None
        return plutosetup.ppm_from(self.xo_mode.currentData(), exp, got)

    def _xo_preview(self):
        ppm = self._xo_ppm()
        self.xo_ppm.setText("" if ppm is None else self.tr("This Pluto's crystal is %1 ppm out.").replace(
            "%1", fmt.number(ppm, 2)))

    def save_xo(self):
        key, ppm = self._xo_key(), self._xo_ppm()
        if key is None or ppm is None:
            return
        how = "tx carrier on a calibrated receiver" if self.xo_mode.currentData() == "tx" else "known signal"
        try:
            value = self.tools.save_xo(key, ppm, how)
        except Exception as e:                      # noqa: BLE001 - shown
            self.xo_ppm.setText(_why(e))
            return
        self.checked.xo = value
        self._xo_enable()

    def forget_xo(self):
        key = self._xo_key()
        if key is None:
            return
        self.tools.forget_xo(key)
        self.checked.xo = None
        self._xo_enable()

    # -- the HackRF -----------------------------------------------------------------------

    def _hackrf(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        f = QFormLayout()
        self.hackrf_serial = QLineEdit()
        self.hackrf_serial.setPlaceholderText(self.tr("blank: the only one plugged in"))
        self.hackrf_tx = QSpinBox()
        self.hackrf_tx.setRange(0, 47)
        self.hackrf_tx.setSuffix(self.tr(" dB"))
        self.hackrf_amp = QCheckBox(self.tr("The HackRF's own RF amplifier (+14 dB)"))
        self.hackrf_lna = QSpinBox()
        self.hackrf_lna.setRange(0, 40)
        self.hackrf_lna.setSingleStep(8)
        self.hackrf_vga = QSpinBox()
        self.hackrf_vga.setRange(0, 62)
        self.hackrf_vga.setSingleStep(2)
        f.addRow(self.tr("Serial number"), self.hackrf_serial)
        f.addRow(self.tr("Transmit gain"), self.hackrf_tx)
        f.addRow("", self.hackrf_amp)
        f.addRow(self.tr("Receive LNA gain"), self.hackrf_lna)
        f.addRow(self.tr("Receive VGA gain"), self.hackrf_vga)
        lay.addLayout(f)
        self.hackrf_filter = self._filter_box(lay)
        lay.addWidget(_note(self.tr("Transmit is experimental: not yet tested on the air."), "warn"))
        lay.addWidget(_note(self.tr("Leave the amplifier off: use one after the band filter for more power. A "
                                    "HackRF sends and receives in turn, never both. If this program is killed while "
                                    "it transmits, the HackRF stops the signal but keeps a weak carrier until the "
                                    "program starts again or it is unplugged."), "warn"))
        lay.addStretch(1)
        return w

    # -- a receive-only SDR ---------------------------------------------------------------------

    def _sdrrx(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.rx_source = QComboBox()
        for k in ("rtlsdr", "rtl_tcp", "sdrplay"):
            self.rx_source.addItem(texts.sdr_rx_source(k), k)
        self.rx_host = QLineEdit()
        self.rx_port = QSpinBox()
        self.rx_port.setRange(1, 65535)
        self.rx_index = QSpinBox()
        self.rx_index.setRange(0, 15)
        self.rx_serial = QLineEdit()
        self.rx_gain_auto = QCheckBox(self.tr("Automatic"))
        self.rx_gain = QDoubleSpinBox()
        self.rx_gain.setRange(0.0, 60.0)
        self.rx_gain.setSuffix(self.tr(" dB"))
        self.rx_gain_auto.toggled.connect(lambda on: self.rx_gain.setEnabled(not on))
        g = QHBoxLayout()
        g.addWidget(self.rx_gain_auto)
        g.addWidget(self.rx_gain, 1)
        self.rx_ppm = QDoubleSpinBox()
        self.rx_ppm.setRange(-200.0, 200.0)
        self.rx_ppm.setDecimals(2)
        self.rx_ppm.setSuffix(self.tr(" ppm"))
        f.addRow(self.tr("Receiver"), self.rx_source)
        f.addRow(self.tr("rtl_tcp address"), self.rx_host)
        f.addRow(self.tr("rtl_tcp port"), self.rx_port)
        f.addRow(self.tr("Dongle number"), self.rx_index)
        f.addRow(self.tr("Serial number"), self.rx_serial)
        f.addRow(self.tr("Gain"), g)
        f.addRow(self.tr("Crystal error"), self.rx_ppm)
        f.addRow(_note(self.tr("Receive only: the station can monitor and decode with it, never transmit. For "
                               "an SDRplay RSP, gain is SDRplay's gain reduction (20-59 dB).")))
        return w

    # -- settings in and out ----------------------------------------------------------------

    def load(self, s, secrets: dict) -> None:
        r: SdrRadioSettings = s.sdr_radio
        self.pluto_uri.setText(r.pluto_uri)
        self.pluto_where.setCurrentIndex(self.pluto_where.findData(
            "controller" if r.pluto_uri.startswith("pluto://") else "direct"))
        if r.pluto_uri.startswith("pluto://"):
            self._controller_note(None)
        self.find_btn.setVisible(not r.pluto_uri.startswith("pluto://"))
        self.find_note.hide()
        self.pluto_password.setText(secrets.get("pluto_password", "analog"))
        gain = float(r.pluto_tx_gain_db)
        if s.radio.kind != "pluto" and gain == -10.0:
            # 0.2.1's default, saved by a station that has never used a
            # Pluto: it starts at the new default as a new station does.
            gain = NEW_PLUTO_TX_GAIN_DB
        self._fill_tx(gain, self._tx_cap())
        self.pluto_rx_auto.setChecked(r.pluto_rx_gain_db is None)
        self.pluto_rx.setValue(float(r.pluto_rx_gain_db or 25.0))
        self.pluto_rx.setEnabled(r.pluto_rx_gain_db is not None)
        self.pluto_filter.setChecked(bool(r.filter_confirmed))
        self.hackrf_filter.setChecked(bool(r.filter_confirmed))
        self.hackrf_serial.setText(r.hackrf_serial)
        self.hackrf_tx.setValue(int(r.hackrf_tx_vga_db))
        self.hackrf_amp.setChecked(bool(r.hackrf_amp))
        self.hackrf_lna.setValue(int(r.hackrf_lna_db))
        self.hackrf_vga.setValue(int(r.hackrf_vga_db))
        self.rx_source.setCurrentIndex(max(0, self.rx_source.findData(r.rx_source)))
        self.rx_host.setText(r.rx_host)
        self.rx_port.setValue(int(r.rx_port))
        self.rx_index.setValue(int(r.rx_index))
        self.rx_serial.setText(r.rx_serial)
        self.rx_gain_auto.setChecked(r.rx_gain_db is None)
        self.rx_gain.setValue(float(r.rx_gain_db or 30.0))
        self.rx_gain.setEnabled(r.rx_gain_db is not None)
        self.rx_ppm.setValue(float(r.rx_ppm))

    def save(self, s, secrets: dict, kind: str) -> None:
        r: SdrRadioSettings = s.sdr_radio
        if kind == "pluto":
            family = self._family()                 # before the address is replaced: it compares them
            r.pluto_uri = self.pluto_uri.text().strip() or "ip:192.168.2.1"
            r.pluto_tx_gain_db = tx_gain_step(self.tx_gain(), self._tx_cap())
            r.pluto_rx_gain_db = None if self.pluto_rx_auto.isChecked() else float(self.pluto_rx.value())
            r.filter_confirmed = self.pluto_filter.isChecked()
            r.pluto_family = family
            secrets["pluto_password"] = self.pluto_password.text()
        elif kind == "hackrf":
            r.hackrf_serial = self.hackrf_serial.text().strip()
            r.hackrf_tx_vga_db = int(self.hackrf_tx.value())
            r.hackrf_amp = self.hackrf_amp.isChecked()
            r.hackrf_lna_db, r.hackrf_vga_db = int(self.hackrf_lna.value()), int(self.hackrf_vga.value())
            r.filter_confirmed = self.hackrf_filter.isChecked()
        elif kind == "sdr-rx":
            r.rx_source = self.rx_source.currentData()
            r.rx_host, r.rx_port = self.rx_host.text().strip(), int(self.rx_port.value())
            r.rx_index, r.rx_serial = int(self.rx_index.value()), self.rx_serial.text().strip()
            r.rx_gain_db = None if self.rx_gain_auto.isChecked() else float(self.rx_gain.value())
            r.rx_ppm = float(self.rx_ppm.value())
