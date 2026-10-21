"""The station strip along the bottom of the window: radio, channel, busy,
transmit, last transmission, SDR - and the one Arm button.

Arm is a deliberate act: it asks the controller, which checks callsign,
setup, radio and band plan and says which gate refused; armed, the button
turns red and counts down the arming's minutes, and pressing it again
disarms at once. The countdown is read from the station each tick, never
kept here, so what it shows is what the radio link will be told when it
next asks.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import fmt, texts
from .widgets import Lamp


class StationBar(QWidget):
    arm_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)
        self.call = QLabel()
        self.call.setObjectName("heading")
        self.radio_lamp = Lamp()
        self.radio = QLabel()
        self.freq = QLabel()
        self.busy_lamp = Lamp()
        self.busy = QLabel(self.tr("Busy"))
        self.tx_lamp = Lamp()
        self.tx = QLabel(self.tr("TX"))
        self.last_tx = QLabel()
        self.last_tx.setObjectName("dim")
        self.sdr = QLabel()
        self.sdr.setObjectName("dim")
        self.arm = QPushButton()
        self.arm.setObjectName("arm")
        self.arm.setCheckable(True)
        self.arm.clicked.connect(self._clicked)
        for w in (self.call, self.radio_lamp, self.radio, self.freq, self.busy_lamp, self.busy, self.tx_lamp,
                  self.tx, self.last_tx):
            lay.addWidget(w)
        lay.addStretch(1)
        lay.addWidget(self.sdr)
        lay.addWidget(self.arm)
        self.update_status({})

    def _clicked(self):
        self.arm_clicked.emit()

    def update_status(self, st: dict) -> None:
        call = st.get("callsign") or self.tr("No callsign")
        self.call.setText(call)
        link = st.get("link", "none")
        self.radio_lamp.set({"open": "rx", "opening": "warn", "failed": "tx"}.get(link))
        self.radio.setText(self.tr("%1: %2").replace("%1", texts.radio_kind(st.get("radio", "none")))
                           .replace("%2", texts.link_state(link)))
        if link == "failed" and st.get("linkError"):
            self.radio.setToolTip(texts.reason(st["linkError"]))
        else:
            power = st.get("rfPower")
            self.radio.setToolTip(texts.rf_power_status(*power) if power else "")
        # The channel, which follows the radio's own VFO (tuning.py); while
        # the radio is between channels its dial is shown and transmit held,
        # and a mode packet is not sent in is shown beside it.
        hz = st.get("radioHz") or st.get("frequencyHz")
        text = fmt.mhz(hz) if hz else ""
        if st.get("radioHz"):
            text = self.tr("%1 (radio retuned; transmit held)").replace("%1", text)
        if st.get("radioModeBad"):
            text = self.tr("%1, %2: transmit held").replace("%1", text).replace("%2", st.get("radioMode", ""))
        self.freq.setText(text)
        self.busy_lamp.set("carrier" if st.get("busy") else None)
        self.busy.setToolTip(self.tr("Lit while the channel is busy: our own carrier detect or the radio's squelch."))
        self.tx_lamp.set("tx" if st.get("transmitting") else None)
        last = st.get("lastTx")
        self.last_tx.setText(self.tr("Last TX %1 UTC").replace("%1", fmt.utc_time(last)) if last else
                             self.tr("Not transmitted yet"))
        sdr = st.get("sdr", "off")
        self.sdr.setText({"running": self.tr("SDR monitor on"), "failed": self.tr("SDR monitor stopped")}
                         .get(sdr, ""))
        armed = bool(st.get("armed"))
        self.arm.setChecked(armed)
        if armed:
            self.arm.setText(self.tr("ARMED %1").replace("%1", fmt.minutes_seconds(st.get("armedRemainingS", 0))))
            self.arm.setToolTip(self.tr("Transmit is armed. Press to disarm now."))
        else:
            self.arm.setText(self.tr("Arm transmit"))
            self.arm.setToolTip(self.tr("Allow this station to transmit for a while. It disarms by itself."))
