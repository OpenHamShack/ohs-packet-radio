"""Settings › Mail, GPS, iGate and Weather (0.3.0): the tabs for the mail
client and the APRS extras, added to the Settings dialog by one call so
the dialog itself changes by a few lines (settingsdlg.py).

Every switch here starts off. The Winlink password goes to secrets.json
(settings_extra.WINLINK_SECRET), never settings.json, so a diagnostics
bundle or a settings file passed to a friend cannot carry it.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QSpinBox, QWidget)

from shack_packet import aprsis

from ..settings_extra import WINLINK_SECRET


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("dim")
    return label


def _lines(edit: QPlainTextEdit) -> list[str]:
    return [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]


class ExtrasTabs:
    TABS = ("mail", "gps", "igate", "weather")

    def __init__(self, dialog):
        self.d = dialog
        self.s = dialog.s
        self._mail()
        self._gps()
        self._igate()
        self._weather()

    def tr(self, text: str) -> str:
        return self.d.tr(text)

    def _page(self, title: str) -> QFormLayout:
        w = QWidget()
        form = QFormLayout(w)
        self.d.tabs.addTab(w, title)
        return form

    # -- mail ---------------------------------------------------------------------------
    def _mail(self):
        f = self._page(self.tr("Mail"))
        m, w = self.s.mail, self.s.winlink
        f.addRow(QLabel(self.tr("<b>Packet mail</b> (store-and-forward between stations)")))
        self.home = QLineEdit(m.home_bbs)
        self.home.setPlaceholderText(self.tr("e.g. GB7XYZ-1, or your controller's BBS GM4BRI-1"))
        self.home_via = QLineEdit(m.home_via)
        self.home_via.setPlaceholderText(self.tr("Digipeaters, if it needs any"))
        self.partners = QPlainTextEdit("\n".join(str(p) for p in m.partners))
        self.partners.setPlaceholderText(self.tr("One per line: GB7ABC-1 via MB7NXX routes GB7ABC,#79"))
        self.partners.setMaximumHeight(70)
        self.direct = QCheckBox(self.tr("Try the addressee's own station first"))
        self.direct.setChecked(bool(m.direct))
        self.direct_ssid = QSpinBox()
        self.direct_ssid.setRange(0, 15)
        self.direct_ssid.setValue(int(m.direct_ssid))
        self.collect = QSpinBox()
        self.collect.setRange(0, 1440)
        self.collect.setSuffix(self.tr(" min"))
        self.collect.setSpecialValueText(self.tr("only when asked"))
        self.collect.setValue(int(m.collect_every_min))
        f.addRow(self.tr("Home BBS"), self.home)
        f.addRow(self.tr("Through"), self.home_via)
        f.addRow(self.tr("Forwarding partners"), self.partners)
        f.addRow(self.direct)
        f.addRow(self.tr("Their mailbox SSID"), self.direct_ssid)
        f.addRow(self.tr("Collect mail every"), self.collect)
        f.addRow(_note(self.tr("Mail goes only while transmit is armed. A message for CALL@BBS goes to a partner "
                               "that routes that BBS, else to your home BBS; one for CALL alone goes to that "
                               "station's mailbox, then to your home BBS.")))
        f.addRow(QLabel(self.tr("<b>Winlink email</b> (through an RMS packet gateway)")))
        self.wl_account = QLineEdit(w.account)
        self.wl_account.setPlaceholderText(self.tr("Blank for your station's callsign"))
        self.wl_password = QLineEdit(self.d.secrets.get(WINLINK_SECRET, ""))
        self.wl_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.wl_gateways = QPlainTextEdit("\n".join(str(g) for g in w.gateways))
        self.wl_gateways.setPlaceholderText(self.tr("One per line: GB7XYZ-10, or GB7XYZ-10 via MB7NXX"))
        self.wl_gateways.setMaximumHeight(70)
        self.wl_max = QSpinBox()
        self.wl_max.setRange(0, 500)
        self.wl_max.setSuffix(self.tr(" kB"))
        self.wl_max.setValue(int(w.max_attachments_kb))
        f.addRow(self.tr("Winlink account"), self.wl_account)
        f.addRow(self.tr("Password"), self.wl_password)
        f.addRow(self.tr("Gateways"), self.wl_gateways)
        f.addRow(self.tr("Attachments up to"), self.wl_max)
        f.addRow(_note(self.tr("The password stays on this computer: the gateway sends a challenge and only the "
                               "answer goes on the air. Find gateways near you at winlink.org/RMSChannels; "
                               "Winlink's own list needs a key it gives only to registered programs, so the list "
                               "here is the one you type.")))

    # -- GPS ------------------------------------------------------------------------------
    def _gps(self):
        f = self._page(self.tr("GPS"))
        g = self.s.gps
        self.gps_source = QComboBox()
        for key, name in (("none", self.tr("None: use the locator")), ("serial", self.tr("A GPS on a serial port")),
                          ("gpsd", self.tr("gpsd (Linux)")), ("manual", self.tr("A position I type in"))):
            self.gps_source.addItem(name, key)
        self.gps_source.setCurrentIndex(max(0, self.gps_source.findData(g.source)))
        self.gps_port = QLineEdit(g.serial_port)
        self.gps_port.setPlaceholderText(self.tr("e.g. /dev/ttyACM0 or COM5"))
        self.gps_baud = QComboBox()
        for b in (4800, 9600, 19200, 38400, 115200):
            self.gps_baud.addItem(str(b), b)
        self.gps_baud.setCurrentIndex(max(0, self.gps_baud.findData(int(g.serial_baud))))
        self.gpsd = QLineEdit(f"{g.gpsd_host}:{g.gpsd_port}")
        self.manual = QLineEdit(g.manual)
        self.manual.setPlaceholderText(self.tr("Latitude, longitude: 55.9533, -3.1883"))
        self.smart = QCheckBox(self.tr("Beacon from the GPS with SmartBeaconing"))
        self.smart.setChecked(bool(g.smart))
        self.mobile_symbol = QLineEdit(g.mobile_symbol)
        self.mobile_symbol.setMaxLength(2)

        def spin(value, lo, hi, suffix):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(0)
            sp.setSuffix(suffix)
            sp.setValue(float(value))
            return sp
        self.fast_speed = spin(g.fast_speed_kmh, 10, 300, self.tr(" km/h"))
        self.fast_rate = spin(g.fast_rate_s, 60, 3600, self.tr(" s"))
        self.slow_speed = spin(g.slow_speed_kmh, 1, 50, self.tr(" km/h"))
        self.slow_rate = spin(g.slow_rate_s, 300, 7200, self.tr(" s"))
        self.turn_min = spin(g.turn_min_deg, 5, 90, self.tr("°"))
        self.turn_time = spin(g.turn_time_s, 15, 600, self.tr(" s"))
        f.addRow(self.tr("Position from"), self.gps_source)
        f.addRow(self.tr("Serial port"), self.gps_port)
        f.addRow(self.tr("Speed"), self.gps_baud)
        f.addRow(self.tr("gpsd at"), self.gpsd)
        f.addRow(self.tr("Position"), self.manual)
        f.addRow(self.smart)
        f.addRow(self.tr("Symbol when moving"), self.mobile_symbol)
        f.addRow(self.tr("Fast above"), self.fast_speed)
        f.addRow(self.tr("…beacon every"), self.fast_rate)
        f.addRow(self.tr("Slow below"), self.slow_speed)
        f.addRow(self.tr("…beacon every"), self.slow_rate)
        f.addRow(self.tr("Corner: turned at least"), self.turn_min)
        f.addRow(self.tr("…and not within"), self.turn_time)
        f.addRow(_note(self.tr("SmartBeaconing beacons often when you move fast and turn, seldom when you are "
                               "still. It needs Beacon this station's position on (Settings › APRS), and like every "
                               "beacon, transmit armed. Never faster than once a minute.")))

    # -- iGate --------------------------------------------------------------------------
    def _igate(self):
        f = self._page(self.tr("iGate"))
        g = self.s.igate
        self.ig_on = QCheckBox(self.tr("Pass what I hear to APRS-IS (an iGate)"))
        self.ig_on.setChecked(bool(g.enabled))
        self.ig_server = QLineEdit(g.server)
        self.ig_filter = QLineEdit(g.filter)
        self.ig_tx = QCheckBox(self.tr("Also send messages from APRS-IS to stations heard here"))
        self.ig_tx.setChecked(bool(g.tx))
        self.ig_via = QLineEdit(g.tx_via)
        call = self.s.station.address() if hasattr(self.s.station, "address") else self.s.station.callsign
        code = aprsis.passcode(call) if call else None
        f.addRow(self.ig_on)
        f.addRow(self.tr("Server"), self.ig_server)
        f.addRow(self.tr("Filter"), self.ig_filter)
        f.addRow(self.ig_tx)
        f.addRow(self.tr("Path on air"), self.ig_via)
        f.addRow(_note(self.tr("Connects out to the server; nothing listens here. Your APRS-IS passcode, worked "
                               "out from your callsign, is %1. From the internet only messages to stations heard "
                               "directly in the last 30 minutes go on the air, at most six a minute, and only while "
                               "transmit is armed.").replace("%1", str(code) if code is not None else "-")))

    # -- weather and telemetry ---------------------------------------------------------
    def _weather(self):
        f = self._page(self.tr("Weather"))
        w, t, o = self.s.weather, self.s.telemetry, self.s.objects
        self.wx_source = QComboBox()
        for key, name in (("none", self.tr("None")), ("peet", self.tr("Peet Bros ULTIMETER (data logger mode)")),
                          ("davis", self.tr("Davis Vantage (LOOP)")),
                          ("file", self.tr("A file another program writes (JSON or CSV)"))):
            self.wx_source.addItem(name, key)
        self.wx_source.setCurrentIndex(max(0, self.wx_source.findData(w.source)))
        self.wx_port = QLineEdit(w.serial_port)
        self.wx_baud = QComboBox()
        for b in (2400, 9600, 19200):
            self.wx_baud.addItem(str(b), b)
        self.wx_baud.setCurrentIndex(max(0, self.wx_baud.findData(int(w.serial_baud))))
        self.wx_file = QLineEdit(w.file)
        self.wx_every = QSpinBox()
        self.wx_every.setRange(5, 60)
        self.wx_every.setSuffix(self.tr(" min"))
        self.wx_every.setValue(max(5, int(w.every_min)))
        self.wx_comment = QLineEdit(w.comment)
        self.wx_comment.setMaxLength(40)
        self.tm_on = QCheckBox(self.tr("Send telemetry: frames heard, sent, digipeated and gated"))
        self.tm_on.setChecked(bool(t.enabled))
        self.tm_every = QSpinBox()
        self.tm_every.setRange(10, 120)
        self.tm_every.setSuffix(self.tr(" min"))
        self.tm_every.setValue(max(10, int(t.every_min)))
        self.obj_every = QSpinBox()
        self.obj_every.setRange(10, 120)
        self.obj_every.setSuffix(self.tr(" min"))
        self.obj_every.setValue(max(10, int(o.every_min)))
        f.addRow(self.tr("Weather station"), self.wx_source)
        f.addRow(self.tr("Serial port"), self.wx_port)
        f.addRow(self.tr("Speed"), self.wx_baud)
        f.addRow(self.tr("File"), self.wx_file)
        f.addRow(self.tr("Report every"), self.wx_every)
        f.addRow(self.tr("Comment"), self.wx_comment)
        f.addRow(_note(self.tr("A file can have any of temp_c, temp_f, humidity, wind_kmh, wind_mph, wind_dir, "
                               "gust_kmh, pressure_hpa, rain_hour_mm, rain_24h_mm, rain_day_mm (or _in): WeeWX, "
                               "Cumulus or a WS-2300 program can write one. Old readings are never sent.")))
        f.addRow(self.tm_on)
        f.addRow(self.tr("Telemetry every"), self.tm_every)
        f.addRow(self.tr("Objects every"), self.obj_every)

    # -- the result ----------------------------------------------------------------------
    def apply(self, s, secrets: dict) -> None:
        m, w = s.mail, s.winlink
        m.home_bbs = self.home.text().strip().upper()
        m.home_via = self.home_via.text().strip().upper()
        m.partners = _lines(self.partners)
        m.direct, m.direct_ssid = self.direct.isChecked(), self.direct_ssid.value()
        m.collect_every_min = self.collect.value()
        w.account = self.wl_account.text().strip().upper()
        w.gateways = [g.upper() for g in _lines(self.wl_gateways)]
        if w.gateway not in w.gateways:
            w.gateway = w.gateways[0] if w.gateways else ""
        w.max_attachments_kb = self.wl_max.value()
        pw = self.wl_password.text()
        if pw:
            secrets[WINLINK_SECRET] = pw
        else:
            secrets.pop(WINLINK_SECRET, None)
        g = s.gps
        g.source = self.gps_source.currentData()
        g.serial_port, g.serial_baud = self.gps_port.text().strip(), int(self.gps_baud.currentData())
        host, _, port = self.gpsd.text().strip().partition(":")
        g.gpsd_host = host or "127.0.0.1"
        g.gpsd_port = int(port) if port.isdigit() else 2947
        g.manual = self.manual.text().strip()
        g.smart = self.smart.isChecked()
        g.mobile_symbol = self.mobile_symbol.text() if len(self.mobile_symbol.text()) == 2 else "/>"
        g.fast_speed_kmh, g.fast_rate_s = self.fast_speed.value(), self.fast_rate.value()
        g.slow_speed_kmh, g.slow_rate_s = self.slow_speed.value(), self.slow_rate.value()
        g.turn_min_deg, g.turn_time_s = self.turn_min.value(), self.turn_time.value()
        i = s.igate
        i.enabled, i.tx = self.ig_on.isChecked(), self.ig_tx.isChecked()
        i.server = self.ig_server.text().strip() or "rotate.aprs2.net:14580"
        i.filter = self.ig_filter.text().strip()
        i.tx_via = self.ig_via.text().strip().upper()
        wx = s.weather
        wx.source = self.wx_source.currentData()
        wx.serial_port, wx.serial_baud = self.wx_port.text().strip(), int(self.wx_baud.currentData())
        wx.file = self.wx_file.text().strip()
        wx.every_min, wx.comment = self.wx_every.value(), self.wx_comment.text()
        s.telemetry.enabled, s.telemetry.every_min = self.tm_on.isChecked(), self.tm_every.value()
        s.objects.every_min = self.obj_every.value()
