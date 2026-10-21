"""The sentences for the keys the IC-9700 page's features report
(rigfeatures.py, adaptpower.py, sat/, shack_packet's IcomControl and DD
link), kept apart from texts.py so workstream F's additions touch that
shared table in one line; texts.reason() falls back to this."""
from __future__ import annotations

from PyQt6.QtCore import QCoreApplication


def reason(key: str) -> str | None:
    table = {
        "sat-below-horizon": QCoreApplication.translate(
            "RigTexts", "The satellite is below the minimum elevation: transmit to it only during a pass."),
        "sat-no-elements": QCoreApplication.translate(
            "RigTexts", "There are no orbital elements for this satellite: update them (IC-9700 › Satellites)."),
        "sat-old-elements": QCoreApplication.translate(
            "RigTexts", "The satellite's orbital elements are over 30 days old, so its passes cannot be trusted: "
                        "update them (IC-9700 › Satellites)."),
        "sat-no-locator": QCoreApplication.translate(
            "RigTexts", "Satellite passes need the station's locator (Settings › Station)."),
        "sat-los": QCoreApplication.translate(
            "RigTexts", "The satellite went below the minimum elevation, so transmit was disarmed."),
        "satellite-setup": QCoreApplication.translate(
            "RigTexts", "The radio could not be set up for the satellite's Doppler; the pass is tracked without "
                        "it."),
        "dd-refused": QCoreApplication.translate(
            "RigTexts", "The IC-9700 would not go into DD on that channel; it was put back as it was."),
        "dd-channel": QCoreApplication.translate(
            "RigTexts", "DD is only used on the 23 cm high-speed data channels, 1299.075 to 1299.675 MHz."),
        "dd-address": QCoreApplication.translate(
            "RigTexts", "This computer's DD address is not set, or is not one of its own addresses."),
        "not-open": QCoreApplication.translate("RigTexts", "The radio is not connected."),
    }
    return table.get(key)
