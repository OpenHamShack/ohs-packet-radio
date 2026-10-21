"""What the operator must set on the radio itself, per radio and connection,
for the setup check's "On the radio" page.

Opening a link already sets everything the app can reach over CAT or CI-V
and has a source for (shack_rig's data set-up: frequency, FM with data,
simplex and tones off, noise reduction, blanker, notch, VOX, monitor, AFC
and the compressor off, the data source; each read first, put back on
close, and put back by the next start after a crash). The `by_app` steps
say so; the setup check's listening page then lists every one, before and
after. What is left here is what no command reaches, or what the app
should not change behind the operator's back: the network login, the CI-V
USB port mode, the audio levels until one is measured on the air, the
squelch, the PTT line. Those are listed here as instructions, in the order
the operator meets them in the radio's menu.

**Where the words come from.** The IC-9700's CI-V USB Port and MOD Input
items are from Icom's CI-V reference as openhamshack-nb's
docs/ic9700-research.md cites it (p.7, p.8-13), and USB MOD Level 50% is
what QO-100 NB's users run. The FTX-1 items are from Yaesu's CAT manual
(~/vendor/manuals/yaesu/, p.2-3): CAT-1 RATE under OPERATION SETTING ›
GENERAL, RPTT SELECT under RADIO SETTING › MODE DATA. Anything not from a
maker's document or a real radio - the IC-9700's network items and AF
output path, everything on the IC-705 (its DATA MOD is unsourced, so the
link does not write it), the FTX-1's level items - is `checked=False`, and
the page marks it, so a tester whose radio says something else reports it
rather than doubting themselves. Change a flag only with a source.

Each step is translated whole (`QCoreApplication.translate`), menu paths
included: Icom and Yaesu localise their menus too, and a translator who
knows the radio can write the path as that language's menu shows it.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QCoreApplication

# Spelled out at every call: pylupdate6 and the string check recognise only
# QCoreApplication.translate(...); an alias leaves the sentences out of the
# catalog (texts.py had that trouble).


@dataclass(frozen=True)
class Step:
    text: str
    #: False: the operator does this on the radio. True: the app does it
    #: when it opens the radio, listed so the operator is not surprised.
    by_app: bool = False
    #: Seen on a real radio of this model (see the module docstring).
    checked: bool = True


def steps(kind: str, transport: str = "lan", ptt: str = "", source: str = "", speed: int = 1200) -> list[Step]:
    """The steps for this radio kind (settings.RADIO_KINDS), its Icom
    transport ("lan" or "usb") and its PTT choice; for a receive-only SDR,
    its source (settings.SdrRadioSettings.rx_source); for a TM-V71, its
    DATA jack speed."""
    if kind in ("pluto", "hackrf", "sdr-rx"):
        return sdr_steps(kind, source)
    squelch = Step(QCoreApplication.translate(
        "RadioMenu", "Set the FM squelch so it just closes on a quiet channel. The app reads an open squelch as "
                     "\"channel busy\" and waits; left open, nothing is ever sent."))
    icom_driver = Step(QCoreApplication.translate(
        "RadioMenu", "Windows only: install Icom's USB driver before plugging the radio in."))
    if kind == "ic-9700":
        if transport == "usb":
            out = [
                icom_driver,
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Connectors › CI-V › CI-V USB Port: Unlink from [REMOTE] "
                                 "(the factory setting).")),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Connectors › USB AF/IF Output › AF Output Level: about 50%."),
                     checked=False),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Connectors › MOD Input › USB MOD Level: 50%.")),
                squelch,
            ]
        else:
            out = [
                Step(QCoreApplication.translate(
                    "RadioMenu", "Connect the radio's LAN socket to your network.")),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Network › Network Control: ON."), checked=False),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Network › Network User1 ID and Password: set them, and type the "
                                 "same here."), checked=False),
                Step(QCoreApplication.translate(
                    "RadioMenu", "Close any other program using the radio over the network (RS-BA1, wfview): "
                                 "the radio serves one at a time.")),
                squelch,
            ]
        out.append(Step(QCoreApplication.translate(
            "RadioMenu", "The app tunes the radio and selects FM-D on the wide filter, simplex with no tone; "
                         "switches noise reduction, the noise blanker, auto notch, the compressor, VOX, monitor "
                         "and AFC off; sets DATA MOD to the connection in use and DATA OFF MOD to MIC, and the "
                         "audio squelch to this computer off. It puts every one back when it closes, and lists "
                         "them on the next page."), by_app=True))
        return out
    if kind == "ic-705":
        if transport == "usb":
            out = [
                icom_driver,
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Connectors › CI-V › CI-V USB Port: Unlink from [REMOTE]."),
                     checked=False),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Connectors › MOD Input › DATA MOD: USB. The app does not change "
                                 "this on the IC-705."), checked=False),
                squelch,
            ]
        else:
            out = [
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › WLAN Set: join the radio to the same network as this computer."),
                     checked=False),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › WLAN Set › Remote Settings › Network User1 ID and Password: set "
                                 "them, and type the same here."), checked=False),
                Step(QCoreApplication.translate(
                    "RadioMenu", "MENU › SET › Connectors › MOD Input › DATA MOD: WLAN. The app does not "
                                 "change this on the IC-705."), checked=False),
                squelch,
            ]
        out.append(Step(QCoreApplication.translate(
            "RadioMenu", "The app tunes the radio, selects FM-D, sets simplex with no tone, and switches noise "
                         "reduction, the noise blanker, auto notch, the compressor, VOX and monitor off; it puts "
                         "them back when it closes."), by_app=True))
        return out
    if kind == "ftx-1":
        out = [
            Step(QCoreApplication.translate(
                "RadioMenu", "Windows only: install Yaesu's USB (virtual COM port) driver before plugging the "
                             "radio in.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "The radio shows two serial ports: choose the Enhanced one as the CAT port.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "OPERATION SETTING › GENERAL › CAT-1 RATE: 38400 (the factory setting).")),
        ]
        if ptt in ("rts", "dtr"):
            out.append(Step(QCoreApplication.translate(
                "RadioMenu", "RADIO SETTING › MODE DATA › RPTT SELECT: the same line chosen here (RTS or DTR).")))
        else:
            out.append(Step(QCoreApplication.translate(
                "RadioMenu", "RADIO SETTING › MODE DATA › RPTT SELECT: OFF (the factory setting). PTT is sent as "
                             "a CAT command.")))
        out += [
            Step(QCoreApplication.translate(
                "RadioMenu", "RADIO SETTING › MODE DATA › USB OUT LEVEL and USB MOD GAIN: start at the middle."),
                 checked=False),
            squelch,
            Step(QCoreApplication.translate(
                "RadioMenu", "The app tunes the radio, selects DATA-FM, sets simplex with no tone, switches DNR, "
                             "DNF, the noise blanker, the processor, VOX and monitor off, and sets MOD SOURCE to "
                             "USB; it puts them back when it closes."), by_app=True),
        ]
        return out
    if kind == "tm-v71":
        return tmv71_steps(ptt, speed, squelch)
    if kind == "soundcard":
        out = [
            Step(QCoreApplication.translate(
                "RadioMenu", "Tune the radio to the packet channel yourself, in FM, simplex, with no tone: the app "
                             "cannot reach this radio.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "If the radio has a data socket, use it and set it for 1200 baud.")),
        ]
        if ptt == "vox":
            out.append(Step(QCoreApplication.translate(
                "RadioMenu", "Switch the radio's VOX on, with its delay as short as the radio allows.")))
        elif ptt in ("rts", "dtr"):
            out.append(Step(QCoreApplication.translate(
                "RadioMenu", "Wire the interface's PTT to the serial line chosen here (RTS or DTR).")))
        else:
            out.append(Step(QCoreApplication.translate(
                "RadioMenu", "No PTT: this radio can receive only.")))
        out += [
            Step(QCoreApplication.translate(
                "RadioMenu", "Set the radio's audio out so packet sits in the meter's shaded band (next page).")),
            squelch,
        ]
        return out
    if kind == "simulator":
        return [Step(QCoreApplication.translate(
            "RadioMenu", "Nothing: the simulator is inside the app."), by_app=True)]
    return [Step(QCoreApplication.translate(
        "RadioMenu", "Nothing: with no radio the app receives through the SDR monitor only."), by_app=True)]


def tmv71_steps(ptt: str, speed: int, squelch: Step) -> list[Step]:
    """A Kenwood TM-V71: its PC port for control, an interface on its DATA
    jack for packet. Menu numbers 517-520 are from Kenwood's RC-D710 manual
    (its "AUX <RC-D710 + TM-V71>" table, p. 24) and match hamvoip.org's
    note from a TM-V71's own panel (517 DATBND, 518 DATSPD, 519 PCSPD); the
    DATA jack's pins and levels are from the same manual (p. 17) and that
    note. How a Digirig or SignaLink cable is wired is the interface
    maker's, not seen here, so those steps are unchecked."""
    out = [
        Step(QCoreApplication.translate(
            "RadioMenu", "Connect the radio's PC port (the 8-pin mini-DIN on the back) to this computer with a "
                         "PG-5G or compatible USB cable, and install that cable's driver if Windows asks."),
             checked=False),
        Step(QCoreApplication.translate(
            "RadioMenu", "Menu 519 (PC PORT BAUDRATE): the same speed chosen here. 9600 is the factory "
                         "setting.")),
        Step(QCoreApplication.translate(
            "RadioMenu", "Put the band you will use on the right band with its BAND key (2 m or 70 cm): the "
                         "app cannot move a band between VHF and UHF over the PC port, so it uses whichever "
                         "band, A or B, is already there.")),
        Step(QCoreApplication.translate(
            "RadioMenu", "DATA jack (6-pin mini-DIN): pin 1 PKD is the audio into the radio, 2 ground, 3 PKS "
                         "keys the radio when pulled low, 4 PR9 is the receiver's discriminator (flat: 9600), "
                         "5 PR1 the 1200 audio, 6 SQC the squelch.")),
    ]
    if speed == 9600:
        out += [
            Step(QCoreApplication.translate(
                "RadioMenu", "9600 needs the interface's receive audio from PR9 (pin 4), not PR1 (pin 5): PR1 "
                             "is filtered for 1200 and 9600 does not get through it.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "Transmit level: at 9600 the radio's PKD wants about 2 V peak to peak for full "
                             "deviation. Start with the interface's output low and raise it until another "
                             "receiver decodes you, aiming for about 3 kHz deviation; too much drives the "
                             "radio's limiter and 9600 stops decoding.")),
        ]
    else:
        out.append(Step(QCoreApplication.translate(
            "RadioMenu", "Transmit level: at 1200 the radio's PKD wants only about 40 mV peak to peak, so the "
                         "interface's output goes near the bottom of its range. Raise it until another receiver "
                         "decodes you cleanly, aiming for about 3 kHz deviation.")))
    if ptt in ("rts", "dtr"):
        out.append(Step(QCoreApplication.translate(
            "RadioMenu", "The interface's PTT (the serial line chosen here) must pull PKS (pin 3) low: a "
                         "Digirig with its Kenwood 6-pin mini-DIN cable is wired so, keyed by RTS."), checked=False))
    elif ptt == "cm108":
        out.append(Step(QCoreApplication.translate(
            "RadioMenu", "The CM108 pin chosen here (GPIO 3 on most boards) must pull PKS (pin 3) low. On Linux "
                         "the sound chip's HID device needs a udev rule for your user, as Dire Wolf's "
                         "99-direwolf-cmedia.rules gives.")))
    elif ptt == "vox":
        out.append(Step(QCoreApplication.translate(
            "RadioMenu", "A SignaLink keys the radio itself on the audio: wire or jumper it for the TM-V71 DATA "
                         "jack (TX audio pin 1, PTT pin 3, RX audio pin 4 or 5, ground pin 2) and turn its DLY "
                         "knob as short as it goes."), checked=False))
    else:
        out.append(Step(QCoreApplication.translate("RadioMenu", "No PTT: this radio can receive only.")))
    out += [
        Step(QCoreApplication.translate(
            "RadioMenu", "Menu 520 (SQC SOURCE): leave it as it is. The app reads the squelch over the PC port, "
                         "not from the SQC pin.")),
        squelch,
        Step(QCoreApplication.translate(
            "RadioMenu", "The app puts the chosen band in VFO mode, tunes it, selects FM simplex with no tone, "
                         "and sets menu 517 (EXT. DATA BAND) to that band and menu 518 (EXT. DATA SPEED) to the "
                         "packet speed - which also sets PKD's input level. It puts every one back when it "
                         "closes, and never keys the radio over the PC port."), by_app=True),
    ]
    return out


def sdr_steps(kind: str, source: str = "") -> list[Step]:
    """An SDR as the station's radio (0.2, PLAN-0.2 §3.6). An SDR has no
    menu: what matters is what is between it and the air. The band-filter
    and dummy-load steps are D9's warnings (Brian, 18/10/2026), said here
    and again when transmit is set up and armed. Everything about a Pluto's
    guard and AD9364 setting is from shack_pluto, measured on two Plutos
    on 18/10/2026; the HackRF's crash behaviour is read from its firmware's
    source, not yet seen on a radio, so it is marked unconfirmed."""
    filt = Step(QCoreApplication.translate(
        "RadioMenu", "Fit a band-pass filter for the band between the SDR's TX port and anything that radiates: "
                     "an SDR's harmonics are strong, and it has no filter of its own."))
    dummy = Step(QCoreApplication.translate(
        "RadioMenu", "Keep a dummy load on the TX port until the filter, and any amplifier after it, are proven "
                     "on a dummy load."))
    if kind == "pluto":
        return [
            filt, dummy,
            Step(QCoreApplication.translate(
                "RadioMenu", "Reach the Pluto by its network address (ip:192.168.2.1 over USB): the app logs in "
                             "over SSH to start a transmitter guard on it. ADI's password is analog.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "A Pluto set up as an AD9363 (the factory setting) cannot tune 2 m. \"Check this "
                             "Pluto\" says so and offers to change it on ADI firmware; the Pluto restarts, and "
                             "transmits briefly as it does, so a dummy load or nothing on TX.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "When transmit is set up, the app starts a guard on the Pluto that switches the "
                             "transmitter off if this program stops, freezes or keys for too long. Without it the "
                             "Pluto is receive-only."), by_app=True),
            Step(QCoreApplication.translate(
                "RadioMenu", "The app writes this Pluto's crystal correction, if you have measured it, each time "
                             "it opens; the Pluto forgets it when it restarts."), by_app=True),
        ]
    if kind == "hackrf":
        return [
            Step(QCoreApplication.translate(
                "RadioMenu", "Transmitting through a HackRF is experimental: it has not been tested on the air "
                             "yet. Receiving works as with any SDR. Transmit into a dummy load first, and tell us "
                             "what you find.")),
            filt, dummy,
            Step(QCoreApplication.translate(
                "RadioMenu", "Leave the HackRF's own RF amplifier off; use an amplifier after the band filter for "
                             "more power.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "Windows: no driver to install (the HackRF asks Windows for its own). Firmware "
                             "2023.01 or later is needed to transmit.")),
            Step(QCoreApplication.translate(
                "RadioMenu", "If this program is killed while transmitting, the HackRF stops the signal but keeps "
                             "a weak carrier until the program is started again or the HackRF is unplugged."),
                 checked=False),
            Step(QCoreApplication.translate(
                "RadioMenu", "The app switches the HackRF's transmitter off first every time it opens it, and "
                             "after every transmission."), by_app=True),
        ]
    if source == "sdrplay":
        return [Step(QCoreApplication.translate(
            "RadioMenu", "Install SDRplay's own API from sdrplay.com (the app cannot include it), and close "
                         "SDRconnect: the two cannot share the receiver.")),
            Step(QCoreApplication.translate("RadioMenu", "Receive only: this radio never transmits."), by_app=True)]
    if source == "rtl_tcp":
        return [Step(QCoreApplication.translate(
            "RadioMenu", "Start rtl_tcp on the computer the dongle is plugged into, and type its address here.")),
            Step(QCoreApplication.translate("RadioMenu", "Receive only: this radio never transmits."), by_app=True)]
    return [Step(QCoreApplication.translate(
        "RadioMenu", "Windows only: give the dongle the WinUSB driver once with Zadig (Options › List All Devices, "
                     "Bulk-In, Interface 0).")),
        Step(QCoreApplication.translate("RadioMenu", "Receive only: this radio never transmits."), by_app=True)]
