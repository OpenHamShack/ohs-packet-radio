"""This app as a controller's client (ohsclient.py, Settings › Controller,
the setup check's radio page): pairing, the radio list, and the D1 rule -
a radio the controller has reserved or is using is never opened from this
computer, and anything else is opened as in 0.1.

The controller is shack_client.testing's reference controller (the same
pairing code a real one runs) served on loopback, with the broker's
device list added in the shape dock360's broker.devices_view() gives
(feature/ohs-edition 96100c9). No radio is opened: the "IC-9700 on the
LAN" is the simulator behind a link that records whether open() was called."""
from __future__ import annotations

import json

import pytest

from conftest import station_settings, wait_for
from shack_client.discovery import Controller as Advert
from shack_client.testing import ReferenceController
from shack_packet_app import diagnostics, ohsclient, settings as settings_mod
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand

RADIO_IP = "192.0.2.10"           # TEST-NET-1: never a real radio


def device(id_, cls, address="", **extra):
    row = {"id": id_, "class": cls, "label": extra.pop("label", id_), "reach": "network" if address else "usb",
           "exclusive": True, "txCapable": True, "declaredBy": [], "source": "configured", "present": True,
           "holder": None, "queue": []}
    if address:
        row["identity"] = {"address": address}
    row.update(extra)
    return row


class BrokerController(ReferenceController):
    """The reference controller plus GET api/shack/v1/broker/devices, which
    needs a device token (view level) as the real route does."""

    def __init__(self, path, **kw):
        super().__init__(path, **kw)
        self.broker_devices: list[dict] = []

    def handle(self, method, path, headers, body):
        if method == "GET" and path.split("?")[0].lstrip("/") == "api/shack/v1/broker/devices":
            self.requests.append((method, path))
            if self.offline:
                return 503, {"error": "offline", "code": "offline"}
            if self._device(headers) is None:
                return 401, {"error": "This device has been removed.", "code": "token_revoked"}
            return 200, {"devices": self.broker_devices, "txInhibit": False, "onAir": []}
        return super().handle(method, path, headers, body)


@pytest.fixture
def box(tmp_path):
    c = BrokerController(tmp_path / "services.db", name="GM4BRI")
    url = c.serve()
    c.url = url
    yield c
    c.close()


def paired(box, s=None):
    """Settings and secrets paired with `box`, through the real exchange."""
    s = s or station_settings()
    secrets = {}
    ohsclient.ControllerClient(s.controller, secrets).pair(box.url, "brian", "pw")
    return s, secrets


# ---------------------------------------------------------------- pairing and listing
def test_pairing_keeps_the_token_out_of_settings_and_out_of_diagnostics(box, folders):
    s, secrets = paired(box)
    token = secrets[ohsclient.SECRET_KEY]
    assert token.startswith("shackdev_")
    assert s.controller.url == box.url and s.controller.name == "GM4BRI" and s.controller.device_id
    settings_mod.save(s)
    settings_mod.save_secrets(secrets)
    assert token not in settings_mod.settings_path().read_text()
    ctl = Controller(s, secrets=secrets, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40, 50)))
    texts = diagnostics.contents(ctl)
    assert not any(token in t for t in texts.values())
    # Pairing again from this computer is the same clientId, so the
    # controller replaces its token rather than adding a second device.
    first_client = s.controller.client_id
    ohsclient.ControllerClient(s.controller, secrets).pair(box.url, "brian", "pw")
    assert s.controller.client_id == first_client


def test_a_second_factor_is_asked_for_and_a_wrong_password_said(box):
    from shack_client.pairing import PairingError
    box.users["brian"]["totp"] = "123456"
    client = ohsclient.ControllerClient(station_settings().controller, {})
    with pytest.raises(PairingError) as e:
        client.pair(box.url, "brian", "pw")
    assert e.value.code == "totp_required" and not client.paired
    client.pair(box.url, "brian", "pw", "123456")
    assert client.paired


def test_the_radio_list_says_reserved_in_use_on_air_and_free(box):
    s, secrets = paired(box)
    box.broker_devices = [
        device("ic9700-1", "ic9700", RADIO_IP, label="IC-9700",
               holder={"app": "shack-icom", "claimId": "cl_1", "mode": "rx", "onAir": False, "since": ""}),
        device("ic705-1", "ic705", "192.0.2.11", holder={"app": "shack-icom", "mode": "tx", "onAir": True}),
        device("pluto-1", "pluto", "192.0.2.12", reserved={"app": "shack-bbs"}),
        device("rtl-1", "rtlsdr", usbId="0bda:2838"),
        device("ptt-0", "ptt-0"), {"id": "gpu", "class": "render", "holders": ["shack-datv"]},
    ]
    radios = {r.id: r for r in ohsclient.ControllerClient(s.controller, secrets).radios()}
    assert set(radios) == {"ic9700-1", "ic705-1", "pluto-1", "rtl-1"}          # radios only
    assert [radios[i].state for i in ("ic9700-1", "ic705-1", "pluto-1", "rtl-1")] == \
        ["in-use", "on-air", "reserved", "free"]
    assert radios["pluto-1"].reserved_by == "shack-bbs" and radios["ic9700-1"].holder == "shack-icom"
    assert not radios["rtl-1"].blocks and all(radios[i].blocks for i in ("ic9700-1", "ic705-1", "pluto-1"))


def test_discovery_leaves_a_classic_box_out():
    found = [Advert("GM4BRI", "a1b2c3d4", "http://192.0.2.5/", ["icom"]),
             Advert("Old box", "", "http://192.0.2.6:8080/", ["qo100wb"], classic=True)]
    client = ohsclient.ControllerClient(station_settings().controller, {}, discoverer=lambda t: found)
    assert [c.name for c in client.discover()] == ["GM4BRI"]


# ---------------------------------------------------------------- the D1 rule at the radio
class RecordingLink:
    """The simulator's radio, recording whether the station opened it."""

    def __init__(self, inner):
        self.inner = inner
        self.opened = False

    def open(self):
        self.opened = True
        return self.inner.open()

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
def station(box):
    made = []

    def make(holder=None, reserved=None, transport="lan", host=RADIO_IP):
        box.broker_devices = [device("ic9700-1", "ic9700", RADIO_IP, label="IC-9700", holder=holder,
                                     **({"reserved": reserved} if reserved else {}))]
        s, secrets = paired(box)
        s.radio.kind, s.radio.transport, s.radio.host = "ic-9700", transport, host
        links = []

        def link_factory(radio, **kw):
            band = SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0))
            band.start()
            links.append(RecordingLink(band.radio()))
            return links[-1]
        c = Controller(s, secrets=secrets, link_factory=link_factory)
        c.links = links
        made.append(c)
        c.start()
        assert wait_for(lambda: c.link_state in ("open", "failed"), 10)
        return c
    yield make
    for c in made:
        c.stop()


def test_a_lan_radio_the_controller_is_using_is_never_opened(station):
    c = station(holder={"app": "shack-icom", "mode": "rx", "onAir": False})
    assert c.link_state == "failed" and c.link_error == "controller-in-use"
    assert "IC-9700" in c.link_error_detail and "shack-icom" in c.link_error_detail
    assert c.links[0].opened is False


def test_a_lan_radio_the_controller_has_reserved_is_never_opened(station):
    c = station(reserved=True)
    assert c.link_error == "controller-reserved" and c.links[0].opened is False


def test_a_radio_the_controller_lists_free_or_on_this_computers_usb_is_opened(station):
    assert station().links[0].opened is True                                 # listed, nobody holds it
    held = {"app": "shack-icom", "mode": "rx", "onAir": False}
    assert station(holder=held, transport="usb").links[0].opened is True     # this computer's own USB
    assert station(holder=held, host="192.0.2.99").links[0].opened is True  # a LAN radio it does not list


def test_a_controller_that_stops_answering_keeps_its_last_word(station, box):
    c = station(holder={"app": "shack-icom", "mode": "rx", "onAir": False})
    assert c.link_error == "controller-in-use"
    box.offline = True
    c.reconnect()                     # a reconnect asks again: no answer, and the last list still says held
    c.stop()
    c.start()
    assert wait_for(lambda: c.link_state in ("open", "failed"), 10)
    assert c.link_error == "controller-in-use" and not any(l.opened for l in c.links)


def test_a_pluto_on_its_own_usb_address_never_matches_the_controllers(box):
    s, secrets = paired(box)
    box.broker_devices = [device("pluto-1", "pluto", "192.168.2.1", holder={"app": "shack-qo100nb", "mode": "tx",
                                                                             "onAir": True})]
    s.sdr.source, s.sdr.pluto_uri = "pluto", "ip:192.168.2.1"
    client = ohsclient.ControllerClient(s.controller, secrets)
    assert client.conflicts(s.radio, s.sdr) == []
    box.broker_devices[0]["identity"]["address"] = "192.0.2.12"
    s.sdr.pluto_uri = "ip:192.0.2.12"
    assert [(what, r.id) for what, r in client.conflicts(s.radio, s.sdr)] == [("sdr", "pluto-1")]


def test_the_sdr_monitor_does_not_open_a_pluto_the_controller_holds(box):
    s, secrets = paired(box)
    box.broker_devices = [device("pluto-1", "pluto", "192.0.2.12", holder={"app": "shack-qo100nb", "mode": "rx"})]
    s.sdr.source, s.sdr.pluto_uri = "pluto", "ip:192.0.2.12"
    opened = []
    c = Controller(s, secrets=secrets, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40, 50)),
                   sdr_factory=lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(AssertionError("opened")))
    c.start()
    try:
        events = []

        def failed():
            while not c.events.empty():
                events.append(c.events.get_nowait())
            return any(e.kind == "sdr" and e.reason == "failed" for e in events)
        assert wait_for(failed, 10)
        e = next(e for e in events if e.kind == "sdr" and e.reason == "failed")
        assert e.detail["reason"] == "controller-in-use" and opened == [] and c.sdr is None
    finally:
        c.stop()


# ---------------------------------------------------------------- the window
def test_settings_controller_pairs_lists_and_the_window_keeps_the_token(qapp, box, folders):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    from shack_packet_app.ui.window import MainWindow
    box.broker_devices = [device("ic9700-1", "ic9700", RADIO_IP, label="IC-9700",
                                 holder={"app": "shack-icom", "mode": "rx", "onAir": False})]
    s = station_settings()
    win = MainWindow(s, {}, controller=Controller(s, band_factory=lambda: SimBand(55.95, -3.2,
                                                                                 first_beacon_s=(40, 50))))
    try:
        dlg = SettingsDialog(win.settings, win, "controller", secrets=win.secrets)
        page = dlg.controller_page
        assert page.state.text() == "Not paired with a controller." and not page.unpair_btn.isEnabled()
        page.address.setText(box.url)
        page.user.setText("brian")
        page.password.setText("pw")
        page.pair()
        assert wait_for(lambda: page.radios.count() == 1, 10, app=qapp), page.result.text()
        assert page.password.text() == ""                                  # used once, never kept
        assert page.radios.item(0).text() == f"IC-9700 ({RADIO_IP}): in use by shack-icom: not used from this computer"
        assert page.state.text() == "Paired with GM4BRI as brian."
        win.apply_settings(dlg.result_settings(), dlg.result_secrets())
        saved = json.loads(settings_mod.settings_path().read_text())
        assert saved["controller"]["name"] == "GM4BRI"
        assert settings_mod.load_secrets()[ohsclient.SECRET_KEY].startswith("shackdev_")
        assert win.ctl.ohs.paired
    finally:
        win.close()


def test_the_setup_checks_radio_page_shows_the_controllers_radios(qapp, box, folders):
    from shack_packet_app.ui.window import MainWindow
    box.broker_devices = [device("ic9700-1", "ic9700", RADIO_IP, label="IC-9700", reserved=True)]
    s, secrets = paired(box)
    win = MainWindow(s, secrets, controller=Controller(s, secrets=secrets, band_factory=lambda: SimBand(
        55.95, -3.2, first_beacon_s=(40, 50))), persist=False)
    win.start(first_run_check=False)
    try:
        win.open_setup()
        wiz = win.wizard
        wiz.radio.initializePage()
        assert wait_for(lambda: "Radios on GM4BRI" in wiz.radio.on_controller.text(), 10, app=qapp), \
            wiz.radio.on_controller.text()
        assert f"IC-9700 ({RADIO_IP}): reserved by the controller: not used from this computer" in \
            wiz.radio.on_controller.text()
        wiz.reject()
    finally:
        win.close()


def test_a_broker_reservation_is_named_by_the_broker_and_an_older_broker_falls_back():
    """The edition with reservations (dock360 feature/ohs-edition-030-c)
    puts `reserved` on the row; an older one only names the holder, and a
    radio held by the controller's BBS is still shown as reserved by it."""
    holder = {"app": "shack-packet", "claimId": "cl_9", "mode": "rx", "onAir": False}
    new = ohsclient.parse_device({"id": "ic9700-1", "class": "ic9700", "holder": holder,
                                  "reserved": {"app": "shack-packet", "name": "Packet BBS GM4BRI",
                                               "claimId": "cl_9", "reason": "BBS GM4BRI-1 on 144.9500 MHz"}})
    assert new.state == "reserved" and new.blocks and new.reserved_by == "Packet BBS GM4BRI"
    old = ohsclient.parse_device({"id": "ic9700-1", "class": "ic9700", "holder": holder})
    assert old.state == "reserved" and old.reserved_by == ohsclient.BBS_LABEL
    other = ohsclient.parse_device({"id": "ic9700-1", "class": "ic9700",
                                    "holder": {"app": "shack-icom", "mode": "rx", "onAir": False}})
    assert other.state == "in-use" and not other.reserved
