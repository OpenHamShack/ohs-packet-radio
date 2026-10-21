"""Every key the engine, the radio links, the SDR monitor and the
controller report, as the sentence the operator reads.

The libraries say what happened as stable keys (identity.REFUSALS,
radios.REASONS, datalink.REASONS, guard.REFUSALS, sdr.REASONS...) and leave
the words to the app, so they can be translated whole: the tables here are
built when asked for, after the language is installed, each string inside
QCoreApplication.translate so the catalog tools find it. A key nobody has
written a sentence for yet is shown as the key itself, which a tester can
quote and a developer can grep, rather than as nothing.
"""
from __future__ import annotations

from PyQt6.QtCore import QCoreApplication

# Spelled out at every call: pylupdate6 and the string check recognise only
# QCoreApplication.translate(...) (and a bare translate(...)). The alias
# `T = QCoreApplication.translate` these tables used left every sentence
# here out of the catalog, so nothing in them could ever be translated
# (found when the first catalog was extracted, 18/10/2026).


def reason(key: str) -> str:
    table = {
        # transmit (identity.REFUSALS, txarm, controller)
        "no-callsign": QCoreApplication.translate("Texts", "There is no valid callsign, so nothing may be sent."),
        "transmit-disabled": QCoreApplication.translate("Texts", "Transmit is not armed."),
        "no-radio": QCoreApplication.translate("Texts", "The radio is not connected."),
        "not-set-up": QCoreApplication.translate(
            "Texts", "Transmit has not been set up on this computer (Station › Setup check)."),
        "band-plan": QCoreApplication.translate(
            "Texts", "The band plan does not allow packet on this frequency for your licence."),
        "receive-only": QCoreApplication.translate("Texts", "This radio is set up to receive only."),
        "armed": QCoreApplication.translate("Texts", "Transmit armed."),
        "disarmed": QCoreApplication.translate("Texts", "Transmit disarmed."),
        "expired": QCoreApplication.translate("Texts", "The transmit arming expired."),
        "stopped": QCoreApplication.translate("Texts", "Transmit disarmed: the station was stopped."),
        "radio-lost": QCoreApplication.translate("Texts", "Transmit disarmed: the radio went away."),
        "turnaround": QCoreApplication.translate("Texts", "Turnaround measured."),
        "turnaround-failed": QCoreApplication.translate("Texts", "The turnaround measurement did not finish."),
        "arm-refused": QCoreApplication.translate("Texts", "Transmit cannot be armed."),
        # the radio's own VFO and mode knob (tuning.py, 0.3.2; radios.REASONS)
        "retuned": QCoreApplication.translate(
            "Texts", "The radio was retuned to another channel, so this connection was dropped without sending "
                     "anything on the new one. Connect again there if the station is there."),
        "radio-mode": QCoreApplication.translate(
            "Texts", "The radio is not in FM, so transmit is held. Put it back in FM (FM-D or DATA-FM) to send "
                     "packet."),
        "radio-memory": QCoreApplication.translate(
            "Texts", "The radio's data band is on a memory channel, so transmit is held: the app cannot see "
                     "the memory's frequency. Put the band back in VFO."),
        "radio-retuning": QCoreApplication.translate(
            "Texts", "The radio is being retuned; arm once it has settled on its new channel."),
        "off-channel": QCoreApplication.translate(
            "Texts", "The radio's VFO was turned off the channel, so it was not keyed."),
        "off-mode": QCoreApplication.translate("Texts", "The radio is not in FM, so it was not keyed."),
        # the control pad (padactions.py, Controller.halt and beacon_now)
        "halted": QCoreApplication.translate("Texts", "Transmit stopped from the control pad, and disarmed."),
        "not-armed": QCoreApplication.translate("Texts", "No beacon sent: transmit is not armed."),
        "no-beacon": QCoreApplication.translate(
            "Texts", "No beacon sent: beaconing is off, or there is no locator (Settings › APRS)."),
        "too-soon": QCoreApplication.translate(
            "Texts", "No beacon sent: one went less than a minute ago."),
        "beacon-sent": QCoreApplication.translate("Texts", "Position beacon queued."),
        "arm-press-again": QCoreApplication.translate("Texts", "Press the arm key again within 3 seconds to arm transmit."),
        "no-chat": QCoreApplication.translate("Texts", "There is no chat to disconnect."),
        "no-station-chosen": QCoreApplication.translate(
            "Texts", "Choose a station to connect to first: type it on the Connect page, or pick one on the APRS page."),
        # the radio links (radios.REASONS)
        "not-open": QCoreApplication.translate("Texts", "The radio link is not open."),
        "claim-lost": QCoreApplication.translate("Texts", "Another program holds the radio."),
        "radio-refused": QCoreApplication.translate("Texts", "The radio would not key."),
        "radio-error": QCoreApplication.translate("Texts", "The radio link failed."),
        "radio-auth": QCoreApplication.translate(
            "Texts", "The radio refused the username and password. Check them in the setup check (the radio's "
                     "network settings), then press Reconnect: it is not tried again until then."),
        "watchdog": QCoreApplication.translate("Texts", "The transmitter was keyed for too long and was unkeyed."),
        "disconnected": QCoreApplication.translate("Texts", "The radio went away."),
        "unkey-failed": QCoreApplication.translate(
            "Texts", "The radio could not be told to stop transmitting; it is being told again."),
        "unkey-sent": QCoreApplication.translate("Texts", "The radio has been told to stop transmitting."),
        "unkey-unconfirmed": QCoreApplication.translate(
            "Texts", "The radio has not confirmed it stopped transmitting. Check it now, and switch it to "
                     "receive by hand if it is still on the air."),
        "busy-stuck": QCoreApplication.translate(
            "Texts", "The radio has said its channel is busy for a long time: is its squelch "
                     "open?"),
        "vox": QCoreApplication.translate(
            "Texts", "PTT is by VOX: the radio keys on the audio after its own delay and holds on for its "
             "own hang time. Set its VOX delay as short as it goes."),
        "setup": QCoreApplication.translate("Texts", "The radio could not be set up for packet."),
        "setup-report": QCoreApplication.translate("Texts", "The radio was set up for packet."),
        "setup-restored": QCoreApplication.translate(
            "Texts", "The radio's settings were put back as they were found."),
        "setup-recovered": QCoreApplication.translate(
            "Texts", "Settings the last session left changed on the radio were put back first."),
        "setup-warning": QCoreApplication.translate("Texts", "Something about this radio to check (Station › "
                                                             "What the app changed on the radio)."),
        "missing-library": QCoreApplication.translate("Texts", "A library this radio needs is not installed."),
        "link-failed": QCoreApplication.translate("Texts", "The radio did not connect."),
        # the paired controller's radios (ohsclient.py, D1)
        "controller-reserved": QCoreApplication.translate(
            "Texts", "Your OpenHamShack controller has reserved this radio, so this computer does not open it."),
        "controller-in-use": QCoreApplication.translate(
            "Texts", "Your OpenHamShack controller is using this radio, so this computer does not open it. "
                     "Stop the app using it on the controller first."),
        "link-open": QCoreApplication.translate("Texts", "The radio is connected."),
        "opening": QCoreApplication.translate("Texts", "Connecting to the radio…"),
        # connected mode (datalink.REASONS)
        "connected": QCoreApplication.translate("Texts", "Connected."),
        "connected-by-peer": QCoreApplication.translate("Texts", "The other station connected."),
        "refused": QCoreApplication.translate("Texts", "The other station refused the connection."),
        "timeout": QCoreApplication.translate("Texts", "No answer: the connection timed out."),
        "closed": QCoreApplication.translate("Texts", "Disconnected."),
        "closed-by-peer": QCoreApplication.translate("Texts", "The other station disconnected."),
        "dm-received": QCoreApplication.translate("Texts", "The other station says it is not connected."),
        "reset-by-peer": QCoreApplication.translate(
            "Texts", "The other station reset the link; some text may be lost."),
        "reset-frmr": QCoreApplication.translate("Texts", "The link was reset after a frame was rejected."),
        "reset-nr": QCoreApplication.translate("Texts", "The link was reset after a numbering error."),
        "reset-ua": QCoreApplication.translate("Texts", "The link was reset after an unexpected answer."),
        "reset-n1": QCoreApplication.translate("Texts", "The link was reset after a frame that was too long."),
        "peer-busy": QCoreApplication.translate("Texts", "The other station is busy and has asked us to wait."),
        "peer-ready": QCoreApplication.translate("Texts", "The other station is ready again."),
        "fallback-v20": QCoreApplication.translate("Texts", "The other station uses AX.25 2.0; connected as 2.0."),
        "connecting": QCoreApplication.translate("Texts", "Connecting…"),
        # IP (ip.REFUSALS, guard.REFUSALS)
        "no-route": QCoreApplication.translate("Texts", "No route to that address."),
        "arp-failed": QCoreApplication.translate("Texts", "No station answered for that address."),
        "too-big": QCoreApplication.translate("Texts", "Too long for the interface."),
        "queue-full": QCoreApplication.translate("Texts", "Too many packets waiting."),
        "not-ipv4": QCoreApplication.translate("Texts", "Only IPv4 is carried over AX.25."),
        "malformed": QCoreApplication.translate("Texts", "Not a whole IPv4 packet."),
        "tls": QCoreApplication.translate("Texts", "Encrypted (TLS)."),
        "dtls": QCoreApplication.translate("Texts", "Encrypted (DTLS)."),
        "quic": QCoreApplication.translate("Texts", "Encrypted (QUIC)."),
        "ssh": QCoreApplication.translate("Texts", "Encrypted (SSH)."),
        "ipsec-esp": QCoreApplication.translate("Texts", "Encrypted (IPsec)."),
        "ike": QCoreApplication.translate("Texts", "IPsec key exchange (sets up encryption)."),
        "wireguard": QCoreApplication.translate("Texts", "Encrypted (WireGuard)."),
        "openvpn": QCoreApplication.translate("Texts", "Encrypted (OpenVPN)."),
        "denied-port": QCoreApplication.translate("Texts", "A port whose service is always encrypted."),
        "encrypted-flow": QCoreApplication.translate("Texts", "Part of a connection already refused as encrypted."),
        "fragment-of-refused": QCoreApplication.translate("Texts", "A fragment of a refused packet."),
        "policy": QCoreApplication.translate("Texts", "Refused by this station's policy."),
        "bad-address": QCoreApplication.translate("Texts", "The IP address in Settings is not valid."),
        # the SDR (sdr.REASONS)
        "no-library": QCoreApplication.translate("Texts", "The library this receiver needs is not installed."),
        "no-device": QCoreApplication.translate("Texts", "No receiver was found."),
        "busy": QCoreApplication.translate("Texts", "The receiver is in use by another program."),
        "open-failed": QCoreApplication.translate("Texts", "The receiver could not be opened."),
        "setup-failed": QCoreApplication.translate("Texts", "The receiver would not take a setting."),
        "rate-unsupported": QCoreApplication.translate("Texts", "The receiver cannot run at that sample rate."),
        "no-channels": QCoreApplication.translate(
            "Texts", "No channels are chosen for the SDR monitor (Settings › SDR monitor)."),
        "plan-too-wide": QCoreApplication.translate(
            "Texts", "The chosen channels are spread wider than the SDR can receive at once: choose fewer, or "
                     "closer together (Settings › SDR monitor)."),
        "out-of-range": QCoreApplication.translate("Texts", "The receiver cannot tune that frequency."),
        "read-failed": QCoreApplication.translate("Texts", "The receiver stopped sending samples."),
        "not-receiver": QCoreApplication.translate("Texts", "That address is not a receiver of this kind."),
        "tx-not-quiet": QCoreApplication.translate(
            "Texts", "The Pluto's transmitter could not be confirmed off, so it was not used."),
        "bad-file": QCoreApplication.translate("Texts", "The recording cannot be played."),
        "ended": QCoreApplication.translate("Texts", "The recording has ended."),
        "failed": QCoreApplication.translate("Texts", "The SDR monitor stopped."),
        "started": QCoreApplication.translate("Texts", "The SDR monitor is running."),
        # an SDR as the station's radio (0.2: radios.REASONS, shack_pluto, the arming)
        "sdr-filter": QCoreApplication.translate(
            "Texts", "Transmit with an SDR needs your word that a band-pass filter is fitted (Station › Setup "
                     "check, Your radio)."),
        "tx-unguarded": QCoreApplication.translate(
            "Texts", "The transmitter guard on the Pluto is not running, so the Pluto is receive-only."),
        "tx-firmware": QCoreApplication.translate(
            "Texts", "This radio's firmware is too old to transmit safely. Update it, and it will transmit."),
        "guard-no-ssh": QCoreApplication.translate(
            "Texts", "The transmitter guard could not log in to the Pluto over SSH: check its address and "
                     "password."),
        "guard-no-network": QCoreApplication.translate(
            "Texts", "The transmitter guard needs the Pluto's network address (ip:…), not a USB address."),
        "guard-failed": QCoreApplication.translate("Texts", "The transmitter guard did not start on the Pluto."),
        "guard-not-quiet": QCoreApplication.translate(
            "Texts", "The transmitter guard could not switch the Pluto's transmitter off."),
        "guard-lost": QCoreApplication.translate(
            "Texts", "The transmitter guard on the Pluto stopped, so transmitting is off."),
        "enable-firmware": QCoreApplication.translate(
            "Texts", "This Pluto's firmware has not been checked with the AD9364 setting. Use ADI's "
                     "\"customizing\" page by hand, or ADI's firmware."),
        "enable-unconfirmed": QCoreApplication.translate(
            "Texts", "Confirm first that a dummy load, or nothing, is on the Pluto's TX port: it transmits "
                     "briefly as it restarts."),
        "enable-done": QCoreApplication.translate("Texts", "This Pluto is already set up for 70 MHz-6 GHz."),
        "enable-ssh": QCoreApplication.translate("Texts", "The Pluto's settings could not be changed over SSH."),
        "enable-verify": QCoreApplication.translate(
            "Texts", "The Pluto's settings did not read back as written. Nothing was restarted."),
        "enable-not-back": QCoreApplication.translate(
            "Texts", "The Pluto did not come back after restarting. Check it, and switch it off if it is "
                     "transmitting."),
        "enable-not-quiet": QCoreApplication.translate(
            "Texts", "The Pluto came back but its transmitter would not switch off. Unplug it now."),
        "enable-range": QCoreApplication.translate(
            "Texts", "The Pluto came back, but not set up for 70 MHz-6 GHz."),
        # the fast modes (shack_packet.fastsettings.PROBLEMS, 0.2.1)
        "fast-unknown-mode": QCoreApplication.translate("Texts", "The fast-mode setting names no mode this program "
                                                                 "knows (Settings › Fast modes)."),
        "fast-ladder": QCoreApplication.translate(
            "Texts", "Link adaptation's top and floor modes do not make a ladder: the floor must be the more "
                     "robust (Settings › Fast modes)."),
        "fast-window": QCoreApplication.translate("Texts", "The window is 1-63 frames (Settings › Fast modes)."),
        "fast-paclen": QCoreApplication.translate("Texts", "The packet length is 1-2048 bytes (Settings › Fast "
                                                           "modes)."),
        "fast-txdelay": QCoreApplication.translate("Texts", "TXDELAY is 0-1000 ms (Settings › Fast modes)."),
        "fast-turnaround": QCoreApplication.translate("Texts", "The turnaround is 0-500 ms (Settings › Fast "
                                                               "modes)."),
        "fast-ptt-output": QCoreApplication.translate("Texts", "That PTT output does not exist (Settings › Fast "
                                                               "modes)."),
        "fast-relay": QCoreApplication.translate("Texts", "The relay lead and tail are 0-500 ms (Settings › Fast "
                                                          "modes)."),
        "fast-drive-cap": QCoreApplication.translate(
            "Texts", "The amplifier's drive cap is -89.75 to 0 dB (Settings › Fast modes)."),
        "fast-adapt-start": QCoreApplication.translate(
            "Texts", "The mode the last path test found is not on link adaptation's ladder."),
        "fast-out-of-plan": QCoreApplication.translate(
            "Texts", "This mode is wider than the band plan allows on this frequency. Wide data belongs in "
                     "23 cm high speed data (1299.000-1299.750 or 1241.000-1241.750 MHz, 150 kHz channels: "
                     "1299.525 MHz suits every mode up to 150 kHz) or 13 cm 2311-2315 MHz."),
        "drive-step": QCoreApplication.translate(
            "Texts", "The radio is not yet reconnected at this step's gain: wait until it is, then send."),
        "xo-out-of-range": QCoreApplication.translate(
            "Texts", "That is more than a crystal is ever off (200 ppm). Measure again."),
        # the link test (linkctl.py; shack_packet returnpath.REASONS)
        "linktest-receiver": QCoreApplication.translate(
            "Texts", "This station is receiving in a link test: it never transmits until it is its turn to send."),
        "linktest-lost": QCoreApplication.translate(
            "Texts", "Transmit disarmed: the link test's internet return path was lost."),
        "return-path-lost": QCoreApplication.translate(
            "Texts", "The internet return path was lost; the link was dropped without transmitting."),
        "return-path-off": QCoreApplication.translate(
            "Texts", "The link test ended; the link was dropped without transmitting."),
        "relay-closed": QCoreApplication.translate("Texts", "The relay closed the connection."),
        "relay-silent": QCoreApplication.translate("Texts", "Nothing from the relay for several seconds."),
        "peer-left": QCoreApplication.translate("Texts", "The other station left the link test."),
        "peer-silent": QCoreApplication.translate("Texts", "Nothing from the other station for several seconds."),
        "peer-stopped": QCoreApplication.translate("Texts", "The other station stopped the test."),
        "timeout": QCoreApplication.translate("Texts", "A step of the test took too long."),
        "send-refused": QCoreApplication.translate("Texts", "The test data could not be sent."),
        "station-stopped": QCoreApplication.translate("Texts", "The station stopped."),
        "unreachable": QCoreApplication.translate(
            "Texts", "The relay could not be reached. Check this computer's internet connection, or for a "
                     "relay on a controller, its address and that this computer is on its network (Tailscale)."),
        "refused": QCoreApplication.translate("Texts", "The relay refused: another run of this callsign may have "
                                                       "replaced this one."),
        "linktest-already": QCoreApplication.translate("Texts", "A link test is already running."),
        "linktest-role": QCoreApplication.translate("Texts", "Choose RF sender or RF receiver."),
        "linktest-peer": QCoreApplication.translate(
            "Texts", "Type the other station's callsign, with its SSID if it uses one (not your own)."),
        "linktest-session": QCoreApplication.translate(
            "Texts", "A session name is 3 to 40 letters, digits, - or _, the same at both stations."),
        "linktest-relay": QCoreApplication.translate("Texts", "The relay address should be host:port."),
        "linktest-not-ready": QCoreApplication.translate(
            "Texts", "The other station has not joined yet."),
    }
    if key in table:
        return table[key]
    # The IC-9700 page's keys (satellites, DD, the scope) live with it.
    from .rigtexts import reason as rig_reason
    return rig_reason(key) or key


def linktest_state(state: str) -> str:
    return {
        "idle": QCoreApplication.translate("Texts", "Not joined"),
        "joining": QCoreApplication.translate("Texts", "Joining…"),
        "waiting": QCoreApplication.translate("Texts", "Joined; waiting for the other station"),
        "ready": QCoreApplication.translate("Texts", "Both stations joined"),
        "lost": QCoreApplication.translate("Texts", "Return path LOST"),
        "failed": QCoreApplication.translate("Texts", "Could not join"),
    }.get(state, state)


def linktest_role(role: str) -> str:
    return {
        "rf-sender": QCoreApplication.translate("Texts", "RF sender (antenna on the Pluto's TX port)"),
        "rf-receiver": QCoreApplication.translate("Texts", "RF receiver (antenna on the Pluto's RX port; never transmits)"),
    }.get(role, role)


def radio_kind(kind: str) -> str:
    return {
        "simulator": QCoreApplication.translate("Texts", "Simulator (no radio)"),
        "ic-9700": QCoreApplication.translate("Texts", "Icom IC-9700"),
        "ic-705": QCoreApplication.translate("Texts", "Icom IC-705"),
        "ftx-1": QCoreApplication.translate("Texts", "Yaesu FTX-1"),
        "tm-v71": QCoreApplication.translate("Texts", "Kenwood TM-V71 (PC port + a sound-card interface)"),
        "soundcard": QCoreApplication.translate("Texts", "Any radio on a sound card"),
        "none": QCoreApplication.translate("Texts", "No radio (SDR monitor only)"),
        "pluto": QCoreApplication.translate("Texts", "ADALM-Pluto (SDR, transmit and receive)"),
        # Transmit is marked experimental until a HackRF has been heard on
        # the air (Brian, 18/10/2026: released in 0.2.0 untested on air);
        # receive is not marked. Drop the mark once it has been.
        "hackrf": QCoreApplication.translate("Texts", "HackRF One (SDR: receive; transmit experimental, untested "
                                                      "on air)"),
        "sdr-rx": QCoreApplication.translate("Texts", "Receive-only SDR (RTL-SDR, rtl_tcp, SDRplay)"),
    }.get(kind, kind)


def sdr_rx_source(kind: str) -> str:
    """The receive-only SDRs an `sdr-rx` radio can be (settings.SdrRadioSettings.rx_source)."""
    return {
        "rtlsdr": QCoreApplication.translate("Texts", "RTL-SDR on USB"),
        "rtl_tcp": QCoreApplication.translate("Texts", "rtl_tcp server"),
        "sdrplay": QCoreApplication.translate("Texts", "SDRplay RSP (needs SDRplay's own API installed)"),
    }.get(kind, kind)


def pluto_family(family: str) -> str:
    """shack_pluto.identity's firmware families."""
    return {
        "adi": QCoreApplication.translate("Texts", "ADI firmware (or a clone of it)"),
        "tezuka": QCoreApplication.translate("Texts", "Tezuka firmware"),
        "plutodvb2": QCoreApplication.translate("Texts", "PlutoDVB2 firmware"),
        "unknown": QCoreApplication.translate("Texts", "unrecognised firmware"),
    }.get(family, family)


def fast_mode(name: str) -> str:
    """shack_packet.modem's modes, with what each needs (README, the fast
    and wide modes)."""
    return {
        "afsk1200": QCoreApplication.translate("Texts", "1200 baud AFSK (every packet station)"),
        "g3ruh9600": QCoreApplication.translate("Texts", "9600 baud G3RUH FSK (10 kHz, a 25 kHz channel)"),
        "g3ruh19200": QCoreApplication.translate("Texts", "19 200 baud G3RUH FSK (21 kHz, a 50 kHz channel)"),
        "g3ruh38400": QCoreApplication.translate("Texts", "38 400 baud G3RUH FSK (42 kHz, 23 cm high speed data)"),
        "gfsk76800": QCoreApplication.translate("Texts", "76 800 baud GFSK (83 kHz, 23 cm high speed data; "
                                                         "OpenHamShack only)"),
        "gfsk153600": QCoreApplication.translate("Texts", "153 600 baud GFSK (166 kHz, 13 cm 2311-2315 MHz only; "
                                                          "OpenHamShack only)"),
        "4fsk38400": QCoreApplication.translate("Texts", "4-FSK 76.8 kbit/s (71 kHz, 23 cm high speed data; "
                                                         "OpenHamShack only)"),
        "4fsk76800": QCoreApplication.translate("Texts", "4-FSK 153.6 kbit/s (141 kHz, 23 cm high speed data; "
                                                         "OpenHamShack only)"),
    }.get(name, name)


def usb_mode_why(mode: str) -> str:
    """Why a Pluto's USB network mode is the one recommended (plutosetup)."""
    return {
        "ecm": QCoreApplication.translate("Texts", "macOS has no RNDIS, and ECM works without a driver."),
        "rndis": QCoreApplication.translate(
            "Texts", "on Linux NCM stalls receive until the Pluto's watchdog restarts it, and Windows needs "
                     "RNDIS for ADI's driver."),
    }.get(mode, "")


def ptt_output(key: str) -> str:
    """A Pluto's PTT outputs (radios/pluto.py PTT_OUTPUTS)."""
    return {
        "none": QCoreApplication.translate("Texts", "None"),
        "gpo0": QCoreApplication.translate("Texts", "GPO0, driven by this program"),
        "gpo1": QCoreApplication.translate("Texts", "GPO1, driven by this program"),
        "gpo2": QCoreApplication.translate("Texts", "GPO2, driven by this program"),
        "gpo3": QCoreApplication.translate("Texts", "GPO3, driven by this program"),
        "firmware": QCoreApplication.translate("Texts", "The firmware's own GPO0 (PlutoDVB2, Tezuka)"),
    }.get(key, key)


def guard_state(state: str) -> str:
    """PlutoLink.guard_state, for the setup check."""
    return {
        "off": QCoreApplication.translate("Texts", "Transmitter guard: not needed (transmit is not set up)."),
        "running": QCoreApplication.translate("Texts", "Transmitter guard: running on the Pluto."),
        "none": QCoreApplication.translate("Texts", "Transmitter guard: none, so the Pluto is receive-only."),
        "failed": QCoreApplication.translate("Texts", "Transmitter guard: NOT running, so the Pluto is "
                                                      "receive-only."),
    }.get(state, state)


def rf_power_status(key: str, detail: str = "") -> str:
    """links.power_status's key, as the arming dialog and the turnaround
    tool show it. `detail` is the library's own sentence for "failed" -
    not translated, as radio-refused detail never is elsewhere in this app."""
    if key == "failed":
        return QCoreApplication.translate("Texts", "RF power: %1").replace("%1", detail)
    return {
        "minimum": QCoreApplication.translate("Texts", "RF power: minimum (0%)."),
        "pending": QCoreApplication.translate(
            "Texts", "RF power: will be set to minimum (0%) before the first transmission."),
        "manual": QCoreApplication.translate("Texts", "Set power on the radio by hand."),
        "sdr": QCoreApplication.translate(
            "Texts", "SDR transmit: power is its transmit gain, milliwatts before any amplifier. Keep the band "
                     "filter fitted, and a dummy load on until the filter and amplifier are proven."),
    }.get(key, key)


def setup_setting(key: str) -> str:
    """A radio setting the data set-up reads, sets or puts back
    (shack_rig.icom.datasetup, shack_rig.yaesu.datasetup), by its key."""
    return {
        "frequency": QCoreApplication.translate("Texts", "Frequency"),
        "mode": QCoreApplication.translate("Texts", "Mode and filter"),
        "simplex": QCoreApplication.translate("Texts", "Simplex (no repeater shift)"),
        "shift": QCoreApplication.translate("Texts", "Repeater shift"),
        "tones": QCoreApplication.translate("Texts", "CTCSS and DCS tones"),
        "tone": QCoreApplication.translate("Texts", "Repeater tone"),
        "tsql": QCoreApplication.translate("Texts", "Tone squelch"),
        "dtcs": QCoreApplication.translate("Texts", "DTCS"),
        "nr": QCoreApplication.translate("Texts", "Noise reduction"),
        "nb": QCoreApplication.translate("Texts", "Noise blanker"),
        "an": QCoreApplication.translate("Texts", "Auto notch"),
        "comp": QCoreApplication.translate("Texts", "Speech compressor"),
        "vox": QCoreApplication.translate("Texts", "VOX"),
        "monitor": QCoreApplication.translate("Texts", "Monitor"),
        "afc": QCoreApplication.translate("Texts", "AFC"),
        "data_mod": QCoreApplication.translate("Texts", "DATA MOD (transmit audio in data mode)"),
        "data_off_mod": QCoreApplication.translate("Texts", "DATA OFF MOD (transmit audio with data off)"),
        "mod_level": QCoreApplication.translate("Texts", "MOD level (transmit audio level)"),
        "af_sql": QCoreApplication.translate("Texts", "Squelch on the audio to this computer"),
        "squelch": QCoreApplication.translate("Texts", "Squelch level"),
        "mod_source_data": QCoreApplication.translate("Texts", "MOD SOURCE (DATA)"),
        "mod_source_fm": QCoreApplication.translate("Texts", "MOD SOURCE (FM)"),
        "mod_source": QCoreApplication.translate("Texts", "MOD SOURCE"),
        "mod_gain_data": QCoreApplication.translate("Texts", "USB MOD GAIN (DATA)"),
        "mod_gain_fm": QCoreApplication.translate("Texts", "USB MOD GAIN (FM)"),
        "switches": QCoreApplication.translate("Texts", "Noise reduction, notch, VOX and monitor"),
    }.get(key, key)


def retuned(d: dict) -> str:
    """The status bar's sentence for a "retuned" event (tuning.py)."""
    mhz = f"{int(d.get('hz', 0)) / 1e6:.4f}"
    if d.get("by") != "radio":
        return QCoreApplication.translate("Texts", "Channel %1 MHz.").replace("%1", mhz)
    out = QCoreApplication.translate("Texts", "The radio was retuned: the channel is now %1 MHz.").replace("%1", mhz)
    gone = []
    if d.get("links"):
        gone.append(QCoreApplication.translate("Texts", "%n connection(s) dropped", "", int(d["links"])))
    if d.get("frames"):
        gone.append(QCoreApplication.translate("Texts", "%n queued frame(s) cancelled", "", int(d["frames"])))
    if d.get("messages"):
        gone.append(QCoreApplication.translate("Texts", "%n APRS message(s) cancelled", "", int(d["messages"])))
    if gone:
        out += " " + QCoreApplication.translate("Texts", "Nothing meant for the old channel was sent on the new "
                                                         "one: %1.").replace("%1", ", ".join(gone))
    if d.get("refusal"):
        out += " " + QCoreApplication.translate("Texts", "Transmit DISARMED: %1").replace("%1", reason(d["refusal"]))
        if d.get("detail"):
            out += " " + str(d["detail"])
    elif d.get("armed"):
        out += " " + QCoreApplication.translate("Texts", "Still armed: packet is allowed there.")
    return out


def setup_status(status: str) -> str:
    """shack_rig.datasetup.STATUSES' keys, as the operator reads them."""
    return {
        "set": QCoreApplication.translate("Texts", "changed"),
        "already": QCoreApplication.translate("Texts", "already right"),
        "report": QCoreApplication.translate("Texts", "left as it is"),
        "unavailable": QCoreApplication.translate("Texts", "not on this radio"),
        "unread": QCoreApplication.translate("Texts", "did not answer, so left alone"),
        "failed": QCoreApplication.translate("Texts", "could not be changed"),
        "manual": QCoreApplication.translate("Texts", "set it on the radio"),
        "set-unrestorable": QCoreApplication.translate(
            "Texts", "changed; the radio cannot report it, so it is not put back"),
        "restored": QCoreApplication.translate("Texts", "put back"),
        "restore-failed": QCoreApplication.translate("Texts", "could not be put back; it will be tried at the next "
                                                              "start"),
        "recovered": QCoreApplication.translate("Texts", "put back after the last session ended without doing so"),
        "kept": QCoreApplication.translate("Texts", "changed on the radio during the session, so left as you set it"),
    }.get(status, status)


def setup_value(value: str | None) -> str:
    """A value as the library shows it ("on", "LAN", "FM-D FIL1", "50%"):
    the two words are translated, the rest are the radio's own."""
    if value is None:
        return ""
    return {"on": QCoreApplication.translate("Texts", "on"), "off": QCoreApplication.translate("Texts", "off"),
            "simplex": QCoreApplication.translate("Texts", "simplex")}.get(value, value)


def setup_warning(key: str, detail: str = "") -> str:
    """A setup-warning's key, as a sentence; the library's own for one not
    written here."""
    got = {
        "usb-tx-slips": QCoreApplication.translate(
            "Texts", "This radio's own USB sound device drops and repeats samples when it transmits: harmless "
                     "to FT8, but enough to stop 1200 baud packet decoding on some radios. Use the network "
                     "(LAN) connection to transmit packet if you can."),
        "usb-hub-sdr": QCoreApplication.translate(
            "Texts", "The radio's USB sound device shares a USB hub with an SDR. If packet over USB fails, "
                     "give the radio a USB port of its own."),
    }.get(key)
    return got or detail or key


def sdr_source(kind: str) -> str:
    return {
        "none": QCoreApplication.translate("Texts", "Off"),
        "synthetic": QCoreApplication.translate("Texts", "Synthetic band (no SDR)"),
        "rtl_tcp": QCoreApplication.translate("Texts", "rtl_tcp server"),
        "rtlsdr": QCoreApplication.translate("Texts", "RTL-SDR on USB"),
        "pluto": QCoreApplication.translate("Texts", "ADALM-Pluto (receive only)"),
    }.get(kind, kind)


def link_state(state: str) -> str:
    return {
        "none": QCoreApplication.translate("Texts", "No radio"),
        "opening": QCoreApplication.translate("Texts", "Connecting"),
        "open": QCoreApplication.translate("Texts", "Connected"),
        "failed": QCoreApplication.translate("Texts", "Not connected"),
        "closed": QCoreApplication.translate("Texts", "Closed"),
    }.get(state, state)


def message_state(state: str) -> str:
    return {
        "queued": QCoreApplication.translate("Texts", "Waiting"),
        "sent": QCoreApplication.translate("Texts", "Sent, no ack yet"),
        "acked": QCoreApplication.translate("Texts", "Acknowledged"),
        "rejected": QCoreApplication.translate("Texts", "Rejected"),
        "timeout": QCoreApplication.translate("Texts", "Never acknowledged"),
        "cancelled": QCoreApplication.translate("Texts", "Cancelled: the radio was retuned"),
        "received": QCoreApplication.translate("Texts", "Received"),
    }.get(state, state)


def role(key: str) -> str:
    return {
        "netrom-node": QCoreApplication.translate("Texts", "NET/ROM node"),
        "netrom": QCoreApplication.translate("Texts", "NET/ROM traffic"),
        "answers": QCoreApplication.translate("Texts", "Answers connections"),
        "connected": QCoreApplication.translate("Texts", "Connected-mode"),
        "bbs": QCoreApplication.translate("Texts", "BBS"),
    }.get(key, key)


def direction(d: str) -> str:
    return {"rx": QCoreApplication.translate("Texts", "RX"), "tx": QCoreApplication.translate("Texts", "TX"),
            "sdr": QCoreApplication.translate("Texts", "SDR")}.get(d, d)


def pad_action(aid: str) -> str:
    """A control pad action's name (data/pad-actions.json ids)."""
    t = QCoreApplication.translate
    return {
        "none": t("Texts", "Nothing"),
        "arm": t("Texts", "Arm transmit (press twice)"),
        "disarm": t("Texts", "Disarm"),
        "halt": t("Texts", "Stop transmitting (HALT)"),
        "beacon": t("Texts", "Beacon now (while armed)"),
        "connect": t("Texts", "Connect a chat"),
        "disconnect": t("Texts", "Disconnect the chat"),
        "page:next": t("Texts", "Next page"),
        "page:prev": t("Texts", "Previous page"),
        "power": t("Texts", "Power (RF power, or a Pluto's TX gain)"),
        "squelch": t("Texts", "Squelch"),
        "none-turn": t("Texts", "Nothing"),
    }.get(aid, aid)


def pad_control(cid: str) -> str:
    """An AKP03E control's name: K1-K6 the picture keys, B1-B3 the small
    buttons, D1-D3 the knobs (pressed)."""
    t = QCoreApplication.translate
    if cid.startswith("K"):
        return t("Texts", "Key %1").replace("%1", cid[1:])
    if cid.startswith("B"):
        return t("Texts", "Button %1").replace("%1", cid[1:])
    return t("Texts", "Knob %1, pressed").replace("%1", cid[1:])
