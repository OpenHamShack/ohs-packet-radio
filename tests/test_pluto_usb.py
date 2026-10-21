"""Check this Pluto, 0.2.1: the USB network mode read over SSH and the one
recommended for the computer it is plugged into, the guided switch run as
the AD9364 enable is (confirmation, backup, read back, reboot under the
quiet watcher), a Pluto on the controller checked through its forwarded
ports, and the Windows driver guidance. Every SSH command, libiio context
and watcher here is a stand-in: no Pluto is reached and nothing reboots."""
from __future__ import annotations

import pytest

from conftest import wait_for
from shack_packet_app import plutosetup
from test_sdr_radio import FakeTools, _ident, radio_page  # noqa: F401 - the fixture


class FakeRunner:
    """SshRunner's run/detach, answering from a table; every call must
    carry a timeout, as every SSH call in the safety path does."""

    def __init__(self, answers=None, env="usb_ethernet_mode=ncm\n"):
        self.answers = dict(answers or {})
        self.env = env
        self.calls = []

    def run(self, command, timeout=None):
        assert timeout is not None, command
        self.calls.append(command)
        if command == "fw_printenv":
            return 0, self.env
        if command.startswith("fw_setenv usb_ethernet_mode "):
            self.env = f"usb_ethernet_mode={command.split()[-1]}\n"
            return 0, ""
        for prefix, answer in self.answers.items():
            if command.startswith(prefix):
                return answer
        return 1, ""

    def detach(self, command):
        self.calls.append(("detach", command))


class FakeWatcher:
    made = []

    def __init__(self, iio, uri, error=None):
        from shack_pluto.ad9364 import EnableResult
        self.uri, self.error, self.result = uri, error, EnableResult(quiet_after_s=0.4)
        self.started = self.is_armed = False
        FakeWatcher.made.append(self)

    def start(self):
        self.started = True
        return self

    def armed(self):
        assert self.started, "the watcher starts before the reboot is sent"
        self.is_armed = True

    def join(self, timeout=None):
        assert timeout is not None


def test_the_usb_mode_is_read_from_the_environment_and_the_running_gadget():
    m = plutosetup.parse_usb_mode("usb_ethernet_mode=ncm\n---\n/sys/kernel/config/usb_gadget/composite_gadget/"
                                  "functions/ncm.usb0\n/sys/kernel/config/usb_gadget/composite_gadget/"
                                  "functions/acm.usb0\n")
    assert (m.saved, m.running) == ("ncm", "ncm")
    unset = plutosetup.parse_usb_mode("## Error: \"usb_ethernet_mode\" not defined\n---\n.../functions/rndis.0\n")
    assert (unset.saved, unset.running) == ("", "rndis")
    assert plutosetup.usb_mode(FakeRunner({"fw_printenv usb_ethernet_mode": (0, "---\n")})).running == ""


def test_the_recommended_mode_is_ecm_on_macos_and_rndis_elsewhere():
    assert plutosetup.recommended_usb_mode("darwin") == "ecm"
    assert plutosetup.recommended_usb_mode("linux") == plutosetup.recommended_usb_mode("win32") == "rndis"
    assert plutosetup.is_usb_gadget_address("ip:192.168.2.1")
    assert not plutosetup.is_usb_gadget_address("pluto://ohs-box?iiod=30431&ssh=30422")
    assert not plutosetup.is_usb_gadget_address("usb:1.7.5")


def _switch(tmp_path, runner, mode="rndis", confirmed=True, error=None):
    return plutosetup.switch_usb_mode(_ident(), runner, object(), tmp_path, mode, operator_confirmed=confirmed,
                                      watcher_factory=lambda iio, uri: FakeWatcher(iio, uri, error))


def test_the_switch_needs_the_operators_word_and_changes_nothing_without_it(tmp_path):
    from shack_pluto.ad9364 import EnableError
    runner = FakeRunner()
    with pytest.raises(EnableError) as e:
        _switch(tmp_path, runner, confirmed=False)
    assert e.value.reason == "enable-unconfirmed" and runner.calls == []
    with pytest.raises(EnableError):
        _switch(tmp_path, runner, mode="wifi")
    assert runner.calls == []


def test_the_switch_backs_up_writes_reads_back_and_reboots_under_the_watcher(tmp_path):
    FakeWatcher.made.clear()
    runner = FakeRunner()
    out = _switch(tmp_path, runner)
    assert runner.calls == ["fw_printenv", "fw_setenv usb_ethernet_mode rndis", "fw_printenv",
                            ("detach", "sleep 1; reboot")]
    assert out.backup.read_text() == "usb_ethernet_mode=ncm\n" and out.quiet_after_s == 0.4
    w = FakeWatcher.made[-1]
    assert w.is_armed and w.uri == "ip:192.168.2.1"


def test_a_setting_that_does_not_read_back_is_never_rebooted(tmp_path):
    from shack_pluto.ad9364 import EnableError

    class Stubborn(FakeRunner):
        def run(self, command, timeout=None):
            if command.startswith("fw_setenv"):
                self.calls.append(command)
                return 0, ""                        # says yes, changes nothing
            return super().run(command, timeout)
    runner = Stubborn()
    with pytest.raises(EnableError) as e:
        _switch(tmp_path, runner)
    assert e.value.reason == "enable-verify" and not any(isinstance(c, tuple) for c in runner.calls)


def test_a_pluto_that_does_not_come_back_quiet_is_said(tmp_path):
    from shack_pluto.ad9364 import EnableError
    with pytest.raises(EnableError) as e:
        _switch(tmp_path, FakeRunner(), error=EnableError("enable-not-quiet"))
    assert e.value.reason == "enable-not-quiet"


class FakeCtx:
    def __init__(self, uri):
        self.uri = uri
        self.attrs = {"hw_model": "Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)", "fw_version": "v0.38",
                      "hw_serial": "104473dc59", "ad9361-phy,model": "ad9364"}
        self.timeout = None

    def set_timeout(self, ms):
        self.timeout = ms

    def find_device(self, name):
        return None


def test_a_pluto_on_the_controller_is_checked_through_its_forwarded_ports(tmp_path, monkeypatch):
    from shack_pluto import identity
    made = []

    class Iio:
        @staticmethod
        def Context(uri):
            made.append(FakeCtx(uri))
            return made[-1]
    monkeypatch.setattr(plutosetup, "_iio", lambda: Iio)
    monkeypatch.setattr(identity, "mqtt_answers", lambda host, timeout=0.5: pytest.fail("1883 asked"))
    runner = FakeRunner({identity.PROBE_COMMAND[:20]: (0, "/root/watchconsoletx.sh\n/etc/init.d/S95bgcript\n"),
                         "fw_printenv usb_ethernet_mode": (0, "usb_ethernet_mode=rndis\n---\n.../rndis.0\n")})
    tools = plutosetup.Tools(tmp_path, platform="darwin")
    monkeypatch.setattr(tools, "runner", lambda uri, password: runner)
    uri = "pluto://ohs-box?iiod=30431&ssh=30422"
    result = tools.check(uri, "analog", 1_299_525_000)
    assert made[0].uri == "ip:ohs-box" and made[0].timeout == plutosetup.IIO_TIMEOUT_MS
    assert result.ident.family == "plutodvb2" and result.ident.extra["address"] == uri
    assert result.ident.uri == "ip:ohs-box"                         # what a reboot watcher opens
    assert result.usb.running == "rndis" and result.usb_recommended == "rndis"   # the controller is Linux


# -- the setup check -----------------------------------------------------------------------------

class UsbTools(FakeTools):
    def __init__(self, ident, usb, recommended, platform="darwin"):
        super().__init__(ident, reach=True, enable="")
        self.usb, self.recommended, self.platform = usb, recommended, platform

    def check(self, uri, password, hz):
        r = super().check(uri, password, hz)
        r.usb, r.usb_recommended = self.usb, self.recommended
        return r

    def set_usb_mode(self, ident, password, mode, *, operator_confirmed):
        from shack_pluto.ad9364 import EnableResult
        self.calls.append(("usb", mode, operator_confirmed))
        return EnableResult(backup="/data/pluto-env/y.env", quiet_after_s=0.31)


def test_the_check_offers_the_switch_to_the_recommended_mode_behind_both_confirmations(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    tools = page.sdr.tools = UsbTools(_ident(chip="ad9364", tx_range=(70e6, 6e9), rx_range=(70e6, 6e9)),
                                      plutosetup.UsbMode("rndis", "rndis"), "ecm")
    released, restored = [], []
    page.sdr.release_radio = lambda: released.append(1)
    page.sdr.restore_radio = lambda: restored.append(1)
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    assert "USB network: RNDIS" in page.sdr.check_text.text() and "macOS has no RNDIS" in page.sdr.check_text.text()
    assert not page.sdr.usb_btn.isHidden() and "ECM" in page.sdr.usb_btn.text()
    page.sdr.confirm_usb = lambda mode: False
    page.sdr.switch_usb()
    assert not released and not [c for c in tools.calls if c[0] == "usb"]
    page.sdr.confirm_usb = lambda mode: mode == "ecm"
    page.sdr.switch_usb()
    assert wait_for(lambda: restored, 3, app=qapp)
    assert released == [1] and ("usb", "ecm", True) in tools.calls
    assert "now ECM" in page.sdr.check_text.text() and "0.31" in page.sdr.check_text.text()


def test_a_pluto_already_in_the_right_mode_is_not_offered_a_switch(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    page.sdr.tools = UsbTools(_ident(), plutosetup.UsbMode("", "rndis"), "rndis", platform="linux")
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    assert "the one to use here" in page.sdr.check_text.text() and page.sdr.usb_btn.isHidden()


def test_windows_with_no_answer_on_usb_is_pointed_at_adis_driver(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page

    class Gone(UsbTools):
        def check(self, uri, password, hz):
            raise OSError("Connection timed out")
    page.sdr.tools = Gone(None, None, "", platform="win32")
    page.sdr.check()
    assert wait_for(lambda: "No Pluto answered" in page.sdr.check_text.text(), 3, app=qapp)
    assert "plutosdr-m2k-drivers-win/releases" in page.sdr.check_text.text()
    assert "not included" in page.sdr.check_text.text()
    page.sdr.tools.platform = "linux"
    page.sdr.check()
    assert wait_for(lambda: not page.sdr.check_btn.isEnabled() or "No Pluto" in page.sdr.check_text.text(), 3,
                    app=qapp)
    assert wait_for(lambda: page.sdr.check_btn.isEnabled(), 3, app=qapp)
    assert "drivers-win" not in page.sdr.check_text.text()


def test_a_pluto_on_another_computer_has_its_mode_reported_as_that_computers(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    page.sdr.pluto_uri.setText("pluto://ohs-box?iiod=30431&ssh=30422")
    page.sdr.tools = UsbTools(_ident(uri="ip:ohs-box"), plutosetup.UsbMode("rndis", "rndis"), "rndis")
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    text = page.sdr.check_text.text()
    assert "USB network: RNDIS, the mode the computer this Pluto is plugged into uses" in text
    assert "to use here" not in text and page.sdr.usb_btn.isHidden()
    # A Pluto by name may be on this computer's USB: said, not advised on.
    page.sdr.pluto_uri.setText("ip:pluto.local")
    page.sdr.tools = UsbTools(_ident(uri="ip:pluto.local"), plutosetup.UsbMode("rndis", "rndis"), "")
    page.sdr.checked = None
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    text = page.sdr.check_text.text()
    assert "USB network: RNDIS." in text and "plugged into" not in text and "to use here" not in text


def test_a_controllers_pluto_off_rndis_is_offered_the_switch_as_advice_for_the_controller(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    page.sdr.pluto_uri.setText("pluto://ohs-box?iiod=30431&ssh=30422")
    page.sdr.tools = UsbTools(_ident(uri="ip:ohs-box"), plutosetup.UsbMode("ncm", "ncm"), "rndis", platform="darwin")
    page.sdr.check()
    assert wait_for(lambda: page.sdr.checked is not None, 3, app=qapp)
    text = page.sdr.check_text.text()
    assert "USB network: NCM, the mode the computer" in text and "controller runs Linux" in text
    assert "to use here" not in text and "macOS" not in text
    assert not page.sdr.usb_btn.isHidden() and "RNDIS" in page.sdr.usb_btn.text()
