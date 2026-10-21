"""Settings: the channel, the services' switches, IP and the SDR. The
station profile, the radio and transmit live in the setup check, which
tests each as it goes; everything else is here.

OK applies by rebuilding the station (controller.apply), which starts
disarmed: a settings change is one of the moments NB's first safety rule
names, and the same rule holds here.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget)

from .. import diagupload, presets, sdrplan, tiles
from ..sdrmon import SOURCES
from . import fmt, texts


class SettingsDialog(QDialog):
    TABS = ("channel", "fast", "aprs", "digi", "chat", "notify", "ip", "sdr", "map", "pad", "internet", "controller",
            "radio", "mail", "gps", "igate", "weather")

    def __init__(self, settings, parent=None, tab: str = "channel", secrets: dict | None = None,
                 ohs_transport=None, ohs_discoverer=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Settings"))
        self.s = settings.copy()
        # Pairing writes the device token: into this copy, kept by the
        # window only if the dialog is accepted.
        self.secrets = dict(secrets or {})
        self._ohs = dict(transport=ohs_transport, discoverer=ohs_discoverer)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self._channel()
        self._fast(parent)
        self._aprs()
        self._digi()
        self._chat()
        self._notify()
        self._ip()
        self._sdr()
        self._map()
        self._pad(parent)
        self._internet()
        self._controller()
        self._radio(parent)
        from .extrasettings import ExtrasTabs           # 0.3.0: Mail, GPS, iGate, Weather
        self.extras = ExtrasTabs(self)
        if tab in self.TABS:
            self.tabs.setCurrentIndex(self.TABS.index(tab))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(self.sdr_ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _page(self, title: str) -> QFormLayout:
        w = QWidget()
        form = QFormLayout(w)
        self.tabs.addTab(w, title)
        return form

    def _channel(self):
        f = self._page(self.tr("Channel"))
        self.preset = QComboBox()
        for p in presets.presets(self.s.station.profile()):
            label = p.label if p.enabled else self.tr("%1 (not for your licence)").replace("%1", p.label)
            self.preset.addItem(label, (p.id, p.hz))
            if not p.enabled:
                self.preset.setItemData(self.preset.count() - 1, p.why, Qt.ItemDataRole.ToolTipRole)
        self.preset.addItem(self.tr("Another frequency"), ("custom", None))
        idx = next((i for i in range(self.preset.count()) if self.preset.itemData(i)[0] == self.s.channel.preset),
                   self.preset.count() - 1)
        self.preset.setCurrentIndex(idx)
        self.freq = QDoubleSpinBox()
        # Up to 13 cm: the widest fast mode (gfsk153600) belongs in its high
        # speed data segment, 2311-2315 MHz. The band plan still judges it.
        self.freq.setRange(28.0, 2450.0)
        self.freq.setDecimals(4)
        self.freq.setSuffix(self.tr(" MHz"))
        self.freq.setValue(self.s.channel.frequency_hz / 1e6)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.fx25 = QComboBox()
        for key, name in ((None, self.tr("Off: plain AX.25 (every station decodes it)")),
                          ("auto", self.tr("FX.25, check bytes chosen by size")), (16, self.tr("FX.25, 16 check bytes")),
                          (32, self.tr("FX.25, 32 check bytes")), (64, self.tr("FX.25, 64 check bytes"))):
            self.fx25.addItem(name, key)
        self.fx25.setCurrentIndex(max(0, self.fx25.findData(self.s.channel.fx25)))
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        f.addRow(self.tr("Preset"), self.preset)
        f.addRow(self.tr("Frequency"), self.freq)
        f.addRow(self.tr("Forward error correction"), self.fx25)
        f.addRow("", self.verdict)
        # The Pluto's transmit gain (0.2.3): the setup check's dropdown, here
        # too so it can be turned down or up without going through the
        # setup check (GM0OTB's test, 19/10/2026). Shown for a Pluto only.
        from .sdrpages import NEW_PLUTO_TX_GAIN_DB
        self.tx_gain = QComboBox()
        self.tx_gain_note = QLabel()
        self.tx_gain_note.setWordWrap(True)
        self.tx_gain_note.setObjectName("dim")
        gain = float(self.s.sdr_radio.pluto_tx_gain_db)
        self._tx_gain_saved = NEW_PLUTO_TX_GAIN_DB if self.s.radio.kind != "pluto" and gain == -10.0 else gain
        if self.s.radio.kind == "pluto":
            f.addRow(self.tr("Pluto transmit gain"), self.tx_gain)
            f.addRow("", self.tx_gain_note)
        self.freq.valueChanged.connect(self._judge)
        self._preset_changed()

    def _preset_changed(self):
        pid, hz = self.preset.currentData()
        self.freq.setEnabled(pid == "custom")
        if hz:
            self.freq.setValue(hz / 1e6)
        self._judge()

    def _judge(self):
        hz = round(self.freq.value() * 1e6)
        v = presets.judge(self.s.station.profile(), hz)
        if v["allowed"]:
            self.verdict.setObjectName("good")
            self.verdict.setText(self.tr("The band plan allows packet on %1 for your licence.").replace("%1", fmt.mhz(hz)))
        else:
            self.verdict.setObjectName("bad")
            self.verdict.setText(v["refusal"])
        self.verdict.setStyleSheet("")

    def _tx_gain_cap(self) -> float | None:
        """The drive cap for the channel's band as the Fast modes tab has it
        now, unsaved edits included: the cap OK will save."""
        return self.fast_panel.result().drive_cap(int(round(self.freq.value() * 1e6)))

    def _refresh_tx_gain(self) -> None:
        from ..sdrplan import band_of
        from .sdrpages import db_text, fill_tx_gain
        current = self.tx_gain.currentData()
        cap = self._tx_gain_cap()
        fill_tx_gain(self.tx_gain, self._tx_gain_saved if current is None else float(current), cap)
        if cap is None:
            self.tx_gain_note.setText(self.tr("No amplifier drive cap is set for this band (Fast modes)."))
        else:
            self.tx_gain_note.setText(self.tr("The amplifier's drive cap on %1 is %2: no more gain than that is "
                                              "used (Fast modes).").replace(
                "%1", band_of(int(round(self.freq.value() * 1e6))) or "?").replace("%2", db_text(cap)))

    def _fast(self, window):
        """Settings › Fast modes (ui/fastpanel.py), following the Channel
        tab's frequency as it is changed. The drive-level helper is offered
        only from the window, whose station it runs on, and for a Pluto."""
        from .fastpanel import FastPanel
        pluto = self.s.radio.kind == "pluto"
        self.window_ref = window
        helper = self._drive_helper if pluto and getattr(window, "ctl", None) is not None else None
        self.fast_panel = FastPanel(self.s.fast, family=self.s.sdr_radio.pluto_family, is_pluto=pluto,
                                    frequency=lambda: int(round(self.freq.value() * 1e6)),
                                    set_frequency=self._set_frequency, drive_helper=helper)
        self.tabs.addTab(self.fast_panel, self.tr("Fast modes"))
        self.freq.valueChanged.connect(lambda _v: self.fast_panel.refresh())
        # The gain follows the band's cap: on a frequency change, and on
        # coming back to the Channel tab after the cap was edited.
        self.freq.valueChanged.connect(lambda _v: self._refresh_tx_gain())
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tx_gain())
        self._refresh_tx_gain()

    def _set_frequency(self, hz: int):
        self.preset.setCurrentIndex(self.preset.findData(("custom", None)))
        self.freq.setValue(hz / 1e6)

    def _drive_helper(self, band: str):
        """The drive-level helper on the window's station, with the
        settings in force now (not this dialog's unsaved ones): the cap it
        finds, or None."""
        from .. import fastmodes
        from .fastpanel import DriveDialog
        from PyQt6.QtWidgets import QMessageBox
        win = self.window_ref
        session = fastmodes.DriveSession(win.ctl, win.settings)
        if session.band != band:
            QMessageBox.information(self, self.tr("Find the drive level"), self.tr(
                "The station is on another band. Press OK to use this frequency first, then find the drive "
                "level."))
            return None
        dlg = DriveDialog(self, session)
        dlg.exec()
        return dlg.cap

    def _aprs(self):
        f = self._page(self.tr("APRS"))
        self.beacon = QCheckBox(self.tr("Beacon this station's position"))
        self.beacon.setChecked(self.s.aprs.beacon)
        self.every = QSpinBox()
        self.every.setRange(10, 120)
        self.every.setSuffix(self.tr(" min"))
        self.every.setValue(int(self.s.aprs.every_min))
        self.symbol = QLineEdit(self.s.aprs.symbol)
        self.symbol.setMaxLength(2)
        self.comment = QLineEdit(self.s.aprs.comment)
        self.comment.setMaxLength(40)
        self.path = QLineEdit(self.s.aprs.path)
        f.addRow(self.beacon)
        f.addRow(self.tr("Every"), self.every)
        f.addRow(self.tr("Symbol (table and code)"), self.symbol)
        f.addRow(self.tr("Comment"), self.comment)
        f.addRow(self.tr("Path"), self.path)
        note = QLabel(self.tr("The position is your locator's square centre. Fixed stations rarely need to "
                              "beacon more often than every 30 minutes; nothing beacons more often than every "
                              "5 minutes, and only while transmit is armed."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        f.addRow(note)

    def _digi(self):
        f = self._page(self.tr("Digipeater"))
        self.digi = QCheckBox(self.tr("Repeat other stations' frames (a digipeater)"))
        self.digi.setChecked(self.s.digi.enabled)
        self.aliases = QLineEdit(self.s.digi.aliases)
        self.aliases.setPlaceholderText(self.tr("Aliases, comma separated (optional)"))
        f.addRow(self.digi)
        f.addRow(self.tr("Aliases"), self.aliases)
        note = QLabel(self.tr("Off unless you run a digipeater: it answers to your callsign and WIDEn-N, and "
                              "transmits only while armed."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        f.addRow(note)

    def _chat(self):
        f = self._page(self.tr("Chat and mailbox"))
        self.incoming = QCheckBox(self.tr("Let stations connect to me for a chat"))
        self.incoming.setChecked(self.s.chat.accept_incoming)
        self.mb_ssid = QSpinBox()
        self.mb_ssid.setRange(0, 15)
        self.mb_ssid.setValue(int(self.s.mailbox.ssid))
        self.third = QCheckBox(self.tr("Let callers leave messages for other stations"))
        self.third.setChecked(self.s.mailbox.third_party)
        f.addRow(self.incoming)
        f.addRow(self.tr("Mailbox SSID"), self.mb_ssid)
        f.addRow(self.third)

    def _notify(self):
        """Settings › Notifications (0.2.3; notify.py, ui/notifier.py,
        notifysound.py)."""
        from PyQt6.QtWidgets import QHBoxLayout
        f = self._page(self.tr("Notifications"))
        n = self.s.notify
        self.n_on = QCheckBox(self.tr("Tell me when something arrives while I am not looking at it"))
        self.n_on.setChecked(n.enabled)
        self.n_messages = QCheckBox(self.tr("APRS messages to me"))
        self.n_messages.setChecked(n.messages)
        self.n_chat = QCheckBox(self.tr("Lines from a station in Connect / Chat"))
        self.n_chat.setChecked(n.chat)
        self.n_mailbox = QCheckBox(self.tr("Messages left in my mailbox"))
        self.n_mailbox.setChecked(n.mailbox)
        self.n_sound = QCheckBox(self.tr("Play a sound"))
        self.n_sound.setChecked(n.sound)
        self.n_kind = QComboBox()
        for key, name in (("beep", self.tr("Beep")), ("spoken", self.tr("Spoken: 'You've got mail!' (retro)")),
                          ("system-voice", self.tr("Spoken by this computer's own voice")),
                          ("custom", self.tr("Custom sound file…"))):
            self.n_kind.addItem(name, key)
        self.n_kind.setCurrentIndex(max(0, self.n_kind.findData(n.sound_kind)))
        self.n_file = QLineEdit(n.sound_file)
        self.n_file.setPlaceholderText(self.tr("A WAV file"))
        self.n_browse = QPushButton(self.tr("Choose…"))
        self.n_browse.clicked.connect(self._notify_browse)
        self.n_test = QPushButton(self.tr("Play it"))
        self.n_test.clicked.connect(self._notify_test)
        file_row = QHBoxLayout()
        file_row.addWidget(self.n_file, 1)
        file_row.addWidget(self.n_browse)
        sound_row = QHBoxLayout()
        sound_row.addWidget(self.n_kind, 1)
        sound_row.addWidget(self.n_test)
        f.addRow(self.n_on)
        f.addRow(self.n_messages)
        f.addRow(self.n_chat)
        f.addRow(self.n_mailbox)
        self.n_mail_status = QCheckBox(self.tr("Mail I sent was delivered, or could not be"))
        self.n_mail_status.setChecked(n.mail_status)
        f.addRow(self.n_mail_status)
        f.addRow(self.n_sound)
        f.addRow(self.tr("Sound"), sound_row)
        f.addRow(self.tr("Sound file"), file_row)
        note = QLabel(self.tr("The retro sound is a short chime and a recorded voice (Cori, a free voice made "
                              "with Piper). Your computer's own voice says the same words in whatever voice it has. "
                              "Where the computer cannot speak or play the file, it beeps. Unread items are counted "
                              "in the list on the left either way."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        f.addRow(note)
        for w in (self.n_on, self.n_sound):
            w.toggled.connect(lambda _on: self._notify_enable())
        self.n_kind.currentIndexChanged.connect(lambda _i: self._notify_enable())
        self._notify_enable()

    def _notify_enable(self):
        on = self.n_on.isChecked()
        for w in (self.n_messages, self.n_chat, self.n_mailbox, self.n_mail_status, self.n_sound):
            w.setEnabled(on)
        sound = on and self.n_sound.isChecked()
        self.n_kind.setEnabled(sound)
        self.n_test.setEnabled(sound)
        custom = sound and self.n_kind.currentData() == "custom"
        self.n_file.setEnabled(custom)
        self.n_browse.setEnabled(custom)

    def _notify_browse(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, self.tr("A sound for notifications"), self.n_file.text(),
                                              self.tr("WAV sound (*.wav)"))
        if path:
            self.n_file.setText(path)

    def _notify_test(self):
        from .notifier import phrase
        player = getattr(self, "n_player", None)
        if player is None:
            from PyQt6.QtWidgets import QApplication
            from .. import notifysound
            player = self.n_player = notifysound.Player(beep=QApplication.beep)
        player.play(self.n_kind.currentData(), phrase("mailbox"), self.n_file.text().strip(), notice="mailbox")

    def _ip(self):
        f = self._page(self.tr("IP"))
        self.ip_on = QCheckBox(self.tr("IP over AX.25"))
        self.ip_on.setChecked(self.s.ip.enabled)
        self.ip_addr = QLineEdit(self.s.ip.address)
        self.ip_addr.setPlaceholderText(self.tr("e.g. 44.131.1.1"))
        self.ip_net = QLineEdit(self.s.ip.network)
        self.ip_net.setPlaceholderText(self.tr("e.g. 44.131.0.0/16"))
        self.ip_mode = QComboBox()
        self.ip_mode.addItem(self.tr("Datagram (UI frames)"), "datagram")
        self.ip_mode.addItem(self.tr("Virtual circuit (connected)"), "vc")
        self.ip_mode.setCurrentIndex(max(0, self.ip_mode.findData(self.s.ip.mode)))
        f.addRow(self.ip_on)
        f.addRow(self.tr("Address"), self.ip_addr)
        f.addRow(self.tr("Network on this channel"), self.ip_net)
        f.addRow(self.tr("Mode"), self.ip_mode)

    def _sdr(self):
        f = self._page(self.tr("SDR monitor"))
        self.sdr_source = QComboBox()
        for k in SOURCES:
            self.sdr_source.addItem(texts.sdr_source(k), k)
        self.sdr_source.setCurrentIndex(max(0, self.sdr_source.findData(self.s.sdr.source)))
        self.tcp_host = QLineEdit(self.s.sdr.rtltcp_host)
        self.tcp_port = QSpinBox()
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(int(self.s.sdr.rtltcp_port))
        self.dev_index = QSpinBox()
        self.dev_index.setRange(0, 15)
        self.dev_index.setValue(int(self.s.sdr.device_index))
        self.pluto = QLineEdit(self.s.sdr.pluto_uri)
        self.ppm = QDoubleSpinBox()
        self.ppm.setRange(-100, 100)
        self.ppm.setDecimals(1)
        self.ppm.setValue(float(self.s.sdr.ppm))
        self.feed = QCheckBox(self.tr("Add stations it hears to the APRS list and map"))
        self.feed.setChecked(self.s.sdr.feed_heard)
        f.addRow(self.tr("Source"), self.sdr_source)
        f.addRow(self.tr("rtl_tcp host"), self.tcp_host)
        f.addRow(self.tr("rtl_tcp port"), self.tcp_port)
        f.addRow(self.tr("RTL-SDR number"), self.dev_index)
        f.addRow(self.tr("Pluto address"), self.pluto)
        f.addRow(self.tr("Crystal error (ppm)"), self.ppm)
        f.addRow(self.feed)
        # The channels (sdrplan.py): a band, its presets ticked or not, and
        # typed frequencies; refused, with OK off, when they do not fit
        # what the SDR captures at its sample rate.
        self.sdr_band = QComboBox()
        for b in sdrplan.BANDS:
            self.sdr_band.addItem(b, b)
        self.sdr_band.setCurrentIndex(max(0, self.sdr_band.findData(self.s.sdr.band)))
        self.sdr_chans = QListWidget()
        self.sdr_chans.setMaximumHeight(170)
        self.sdr_extra = QLineEdit(", ".join(f"{hz / 1e6:.4f}" for hz in self.s.sdr.extra_hz))
        self.sdr_extra.setPlaceholderText(self.tr("Other frequencies in MHz, comma separated (optional)"))
        self.sdr_fit = QLabel()
        self.sdr_fit.setWordWrap(True)
        f.addRow(self.tr("Band"), self.sdr_band)
        f.addRow(self.tr("Channels"), self.sdr_chans)
        f.addRow(self.tr("Also listen on"), self.sdr_extra)
        f.addRow("", self.sdr_fit)
        self._sdr_band_changed(first=True)
        self.sdr_band.currentIndexChanged.connect(lambda _i: self._sdr_band_changed())
        self.sdr_chans.itemChanged.connect(lambda _it: self._sdr_judge())
        self.sdr_extra.textChanged.connect(lambda _t: self._sdr_judge())

    def _sdr_band_changed(self, first: bool = False):
        band = self.sdr_band.currentData()
        chosen = set(self.s.sdr.channels) if first and band == self.s.sdr.band else set()
        self.sdr_chans.blockSignals(True)
        self.sdr_chans.clear()
        for p in sdrplan.candidates(self.s.station.profile(), band):
            # The preset's label as written, with the frequency beside a name
            # ("APRS 144.800" is both; "ISS 145.825" too; a bare number is one).
            item = QListWidgetItem(p.label)
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if not chosen or p.id in chosen else Qt.CheckState.Unchecked)
            self.sdr_chans.addItem(item)
        self.sdr_chans.blockSignals(False)
        self._sdr_judge()

    def sdr_choice(self):
        """(band, preset ids, typed Hz) as the tab stands; ids are [] when
        every preset is ticked, so a preset added in a later version is
        watched too."""
        items = [self.sdr_chans.item(i) for i in range(self.sdr_chans.count())]
        ids = [it.data(Qt.ItemDataRole.UserRole) for it in items if it.checkState() == Qt.CheckState.Checked]
        if len(ids) == len(items):
            ids = []
        elif not ids:
            ids = ["-"]                             # none ticked: only the typed ones
        extra = []
        for part in self.sdr_extra.text().replace(";", ",").split(","):
            try:
                mhz = float(part.strip())
            except ValueError:
                continue
            if 1.0 <= mhz <= 6000.0:
                extra.append(int(round(mhz * 1e6)))
        return self.sdr_band.currentData(), ids, extra

    def _sdr_judge(self):
        import copy
        band, ids, extra = self.sdr_choice()
        trial = copy.copy(self.s.sdr)
        trial.band, trial.channels, trial.extra_hz = band, ids, extra
        chans = sdrplan.channels(trial, self.s.station.profile())
        f = sdrplan.fit(chans, trial.rate)
        if not chans:
            self.sdr_fit.setObjectName("bad")
            self.sdr_fit.setText(texts.reason("no-channels"))
        elif not f.ok:
            self.sdr_fit.setObjectName("bad")
            self.sdr_fit.setText(self.tr("%1 channels spread over %2, but the SDR receives %3 at once: choose "
                                         "fewer, or closer together.").replace("%1", str(len(chans)))
                                 .replace("%2", fmt.khz(f.span_hz)).replace("%3", fmt.khz(f.usable_hz)))
        else:
            self.sdr_fit.setObjectName("good")
            self.sdr_fit.setText(self.tr("%1 channels spread over %2 of the %3 the SDR receives at once.")
                                 .replace("%1", str(len(chans))).replace("%2", fmt.khz(f.span_hz))
                                 .replace("%3", fmt.khz(f.usable_hz)))
        self.sdr_fit.setStyleSheet("")
        self.sdr_ok = bool(chans) and f.ok
        ok = getattr(self, "ok_button", None)
        if ok is not None:
            ok.setEnabled(self.sdr_ok)

    def _map(self):
        """Settings › Map: the tracks, and OpenStreetMap tiles - off unless
        ticked, and said plainly to be the one thing that fetches from the
        internet (tiles.py has the rules they are fetched under)."""
        f = self._page(self.tr("Map"))
        self.track_min = QSpinBox()
        self.track_min.setRange(5, 24 * 60)
        self.track_min.setSuffix(self.tr(" min"))
        self.track_min.setValue(int(self.s.map.track_minutes))
        f.addRow(self.tr("Keep tracks for"), self.track_min)
        base = QLabel(self.tr("The map is drawn from data that comes with the program and needs no internet: "
                              "coastlines and borders from Natural Earth (public domain) and towns from "
                              "GeoNames (CC BY 4.0, geonames.org)."))
        base.setWordWrap(True)
        base.setObjectName("dim")
        f.addRow(base)
        self.tiles_on = QCheckBox(self.tr("Show OpenStreetMap under the map (fetches map pictures from the internet)"))
        self.tiles_on.setChecked(self.s.map.tiles)
        self.tile_url = QLineEdit(self.s.map.tile_url)
        self.tile_url.setPlaceholderText(tiles.OSM_URL)
        self.tile_cache = QSpinBox()
        self.tile_cache.setRange(20, 5000)
        self.tile_cache.setSuffix(self.tr(" MB"))
        self.tile_cache.setValue(int(self.s.map.cache_mb))
        note = QLabel(self.tr("Off unless you tick it. When on, only the map pictures for the area you are "
                              "looking at are fetched, from the tile server below (OpenStreetMap's own unless you "
                              "change it), and kept on this computer so they are not fetched again for a week. "
                              "Map data © OpenStreetMap contributors. Without the internet the built-in map "
                              "shows through."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        f.addRow(self.tiles_on)
        f.addRow(self.tr("Tile server"), self.tile_url)
        f.addRow(self.tr("Keep at most"), self.tile_cache)
        f.addRow(note)

    def _pad(self, window):
        """Settings › Control pad: is the AKP03E there, what each key does
        (page 1, padmap.py has the rules), and a key test. The status is
        read twice a second while the dialog is open."""
        from .. import padmap
        f = self._page(self.tr("Control pad"))
        self.pad_link = getattr(window, "pad", None)
        self.pad_on = QCheckBox(self.tr("Use an Ajazz AKP03E control pad when one is plugged in"))
        self.pad_on.setChecked(self.s.pad.enabled)
        self.pad_state = QLabel()
        self.pad_state.setWordWrap(True)
        f.addRow(self.pad_on)
        f.addRow(self.tr("Status"), self.pad_state)
        note = QLabel(self.tr("No key transmits by itself. ARM needs two presses of the same key within 3 "
                              "seconds and then makes the same checks as Arm on the station bar; DISARM, HALT "
                              "and DISCONNECT act on the first press; BEACON sends only while armed."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        f.addRow(note)
        self.pad_boxes = {}
        try:
            self.pad_map = padmap.PadMap(self.s.pad.maps)
        except Exception as e:                      # noqa: BLE001 - no shack_pad here: say so, keep the dialog
            self.pad_map = None
            f.addRow(QLabel(self.tr("Keys cannot be changed here: %1").replace("%1", str(e))))
        if self.pad_map is not None:
            for cid, _slot in self.pad_map.rows():
                box = QComboBox()
                for aid in self.pad_map.choices(cid):
                    box.addItem(texts.pad_action(aid), aid)
                box.currentIndexChanged.connect(lambda _i, c=cid, b=box: self._pad_chosen(c, b))
                self.pad_boxes[cid] = box
                f.addRow(texts.pad_control(cid), box)
            self._pad_show()
        self.pad_reset = QPushButton(self.tr("Back to the standard keys"))
        self.pad_reset.clicked.connect(self._pad_reset)
        self.pad_test = QPushButton(self.tr("Test the keys"))
        self.pad_test.setCheckable(True)
        self.pad_test.toggled.connect(self._pad_test)
        self.pad_test.setEnabled(self.pad_link is not None)
        f.addRow(self.pad_reset)
        f.addRow(self.pad_test)
        self.pad_timer = QTimer(self)
        self.pad_timer.timeout.connect(self._pad_status)
        self.pad_timer.start(500)
        self._pad_status()

    def _pad_show(self):
        for cid, box in self.pad_boxes.items():
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(self.pad_map.current(cid))))
            box.blockSignals(False)

    def _pad_chosen(self, cid, box):
        self.pad_map.set(cid, box.currentData())
        self._pad_show()

    def _pad_reset(self):
        if self.pad_map is not None:
            self.pad_map.reset()
            self._pad_show()

    def _pad_test(self, on):
        """While ticked, each press lights its key and nothing acts
        (shack_pad's test mode); it ends by itself after a minute."""
        if self.pad_link is None:
            return
        if on:
            self.pad_link.start_test()
        else:
            self.pad_link.stop_test()

    def _pad_status(self):
        from . import padlink
        st = self.pad_link.status() if self.pad_link is not None else None
        self.pad_state.setText(padlink.describe(st, self.s.pad.enabled))
        if self.pad_link is not None and self.pad_test.isChecked() and not self.pad_link.testing():
            self.pad_test.setChecked(False)

    def done(self, r):
        if getattr(self, "pad_link", None) is not None and self.pad_link.testing():
            self.pad_link.stop_test()
        self.pad_timer.stop()
        super().done(r)

    def _internet(self):
        f = self._page(self.tr("Internet"))
        self.check_updates = QCheckBox(self.tr("Check for updates when the program starts, and once a day"))
        self.check_updates.setChecked(self.s.internet.check_updates)
        f.addRow(self.check_updates)
        note = QLabel(self.tr("A check fetches openhamshack.com/desktop/packet/update.json and its signature, and "
                              "sends nothing but the program's name and version. Nothing is installed without "
                              "you, and never while transmitting or while a station is connected."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        f.addRow(note)
        on = self.s.internet.diag_upload and diagupload.consented(self.s.internet.diag_consent)
        self.diag_upload = QCheckBox(self.tr("Send diagnostics to OpenHamShack by themselves"))
        self.diag_upload.setChecked(on)
        f.addRow(self.diag_upload)
        from .diagsend import what_is_sent
        consent = QLabel(what_is_sent())
        consent.setWordWrap(True)
        consent.setObjectName("dim")
        f.addRow(consent)

    def _controller(self):
        from .. import ohsclient
        from .controllerpage import ControllerPage
        self.controller_page = ControllerPage(ohsclient.ControllerClient(self.s.controller, self.secrets, **self._ohs))
        self.tabs.addTab(self.controller_page, self.tr("Controller"))

    def _radio(self, window):
        """Find my radio (0.3.0, findmyradio.py): a pick changes this
        dialog's copy of the radio settings, kept on OK as everything here
        is. Everything else about the radio is the setup assistant's."""
        from .findradio import FindRadioBox
        w = QWidget()
        lay = QVBoxLayout(w)
        ctl = getattr(window, "ctl", None)

        def release():
            # The station lets go while Find looks and Test reads, or the
            # radio's own port reads as "in use"; taken back afterwards.
            if ctl is not None:
                t = window.settings.copy()
                t.radio.kind = "none"
                ctl.apply(t, self.secrets)

        def restore():
            if ctl is not None:
                ctl.apply(window.settings, self.secrets)

        def picked(new):
            self.s.radio, self.s.sdr_radio = new.radio, new.sdr_radio
            self.radio_now.setText(self._radio_summary())
        self.finder = FindRadioBox(lambda: self.s, picked, secrets=lambda: self.secrets,
                                   release=release if ctl else None, restore=restore if ctl else None,
                                   sound_backend=getattr(ctl, "sound_backend", None))
        lay.addWidget(self.finder)
        self.radio_now = QLabel(self._radio_summary())
        self.radio_now.setWordWrap(True)
        lay.addWidget(self.radio_now)
        note = QLabel(self.tr("Everything else about the radio, and setting it up by hand, is in the Setup "
                              "check (its Radio page)."))
        note.setWordWrap(True)
        note.setObjectName("dim")
        lay.addWidget(note)
        lay.addStretch(1)
        self.tabs.addTab(w, self.tr("Radio"))

    def _radio_summary(self) -> str:
        r = self.s.radio
        where = {"lan": r.host, "usb": r.civ_device}.get(r.transport, "") if r.kind in ("ic-9700", "ic-705") else \
            (r.cat_device if r.kind in ("ftx-1", "tm-v71") else
             (self.s.sdr_radio.pluto_uri if r.kind == "pluto" else ""))
        return self.tr("This station's radio: %1 %2").replace("%1", texts.radio_kind(r.kind)).replace(
            "%2", f"({where})" if where else "")

    def result_secrets(self) -> dict:
        return self.secrets

    def result_settings(self):
        s = self.s
        pid, _hz = self.preset.currentData()
        s.channel.preset = pid
        s.channel.frequency_hz = int(round(self.freq.value() * 1e6))
        s.channel.fx25 = self.fx25.currentData()
        s.fast = self.fast_panel.result()
        if s.radio.kind == "pluto":
            from .sdrpages import tx_gain_step
            s.sdr_radio.pluto_tx_gain_db = tx_gain_step(float(self.tx_gain.currentData()),
                                                        s.fast.drive_cap(s.channel.frequency_hz))
        s.aprs.beacon = self.beacon.isChecked()
        s.aprs.every_min = self.every.value()
        s.aprs.symbol = self.symbol.text() if len(self.symbol.text()) == 2 else "/-"
        s.aprs.comment = self.comment.text()
        s.aprs.path = self.path.text().strip().upper()
        s.digi.enabled = self.digi.isChecked()
        s.digi.aliases = self.aliases.text().strip().upper()
        s.chat.accept_incoming = self.incoming.isChecked()
        s.mailbox.ssid = self.mb_ssid.value()
        s.mailbox.third_party = self.third.isChecked()
        n = s.notify
        n.enabled, n.sound = self.n_on.isChecked(), self.n_sound.isChecked()
        n.messages, n.chat, n.mailbox = self.n_messages.isChecked(), self.n_chat.isChecked(), self.n_mailbox.isChecked()
        n.mail_status = self.n_mail_status.isChecked()
        n.sound_kind, n.sound_file = self.n_kind.currentData(), self.n_file.text().strip()
        s.ip.enabled = self.ip_on.isChecked()
        s.ip.address = self.ip_addr.text().strip()
        s.ip.network = self.ip_net.text().strip()
        s.ip.mode = self.ip_mode.currentData()
        s.sdr.source = self.sdr_source.currentData()
        s.sdr.rtltcp_host = self.tcp_host.text().strip()
        s.sdr.rtltcp_port = self.tcp_port.value()
        s.sdr.device_index = self.dev_index.value()
        s.sdr.pluto_uri = self.pluto.text().strip()
        s.sdr.ppm = self.ppm.value()
        s.sdr.feed_heard = self.feed.isChecked()
        s.sdr.band, s.sdr.channels, s.sdr.extra_hz = self.sdr_choice()
        s.map.track_minutes = self.track_min.value()
        s.map.tiles = self.tiles_on.isChecked()
        url = self.tile_url.text().strip()
        s.map.tile_url = url if tiles.valid_template(url) else tiles.OSM_URL
        s.map.cache_mb = self.tile_cache.value()
        s.pad.enabled = self.pad_on.isChecked()
        if self.pad_map is not None:
            s.pad.maps = self.pad_map.maps()
        s.internet.check_updates = self.check_updates.isChecked()
        # The consent stamp is written here and nowhere else: turning the
        # switch on with the text above in front of the operator is the
        # consent (diagupload's docstring). A stamp for an older
        # CONSENT_VERSION is replaced only by turning it on again here.
        s.internet.diag_upload = self.diag_upload.isChecked()
        if s.internet.diag_upload and not diagupload.consented(s.internet.diag_consent):
            s.internet.diag_consent = diagupload.consent_stamp()
        self.extras.apply(s, self.secrets)
        return s
