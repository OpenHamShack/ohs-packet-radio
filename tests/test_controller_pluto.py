"""A Pluto on the OpenHamShack controller (0.2.1): its address built from
the paired controller's, the setup check's choice and network warning,
and the D1 rule for it - a Pluto the controller has reserved or is using
is never opened from here, whether or not the row says `reserved` at all.

The controller is test_ohsclient's (shack_client's reference controller
with a broker device list, on loopback). No Pluto is opened: the station's
link is the simulator's radio, recording whether open() was called."""
from __future__ import annotations

import pytest

from conftest import wait_for
from shack_packet_app import ohsclient
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from test_ohsclient import RecordingLink, box, device, paired  # noqa: F401 - the fixture
from test_sdr_radio import radio_page  # noqa: F401 - the fixture


def test_the_controllers_pluto_address_is_built_from_the_paired_controller():
    assert ohsclient.controller_pluto_uri("http://ohs-box.local:8800/") == \
        "pluto://ohs-box.local?iiod=30431&ssh=30422"
    assert ohsclient.controller_pluto_uri("") == ""
    assert ohsclient.is_controller_pluto("pluto://127.0.0.1?iiod=30431&ssh=30422", "http://127.0.0.1:8800/")
    assert not ohsclient.is_controller_pluto("ip:192.168.2.1", "http://127.0.0.1:8800/")
    assert not ohsclient.is_controller_pluto("usb:1.7.5", "http://127.0.0.1:8800/")


@pytest.fixture
def pluto_station(box):  # noqa: F811
    made = []

    def make(row=None, uri=None):
        box.broker_devices = [row] if row else []
        s, secrets = paired(box)
        s.radio.kind = "pluto"
        s.sdr_radio.pluto_uri = uri or ohsclient.controller_pluto_uri(s.controller.url)
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


def test_a_pluto_the_controller_has_reserved_is_never_opened(pluto_station):
    c = pluto_station(device("pluto-1", "pluto", label="Pluto", reserved={"app": "shack-packet-bbs"}))
    assert c.link_error == "controller-reserved" and "shack-packet-bbs" in c.link_error_detail
    assert c.links[0].opened is False


def test_a_pluto_the_controller_is_using_is_never_opened_without_a_reserved_field(pluto_station):
    c = pluto_station(device("pluto-1", "pluto", label="Pluto", holder={"app": "shack-qo100", "mode": "rx"}))
    assert c.link_error == "controller-in-use" and c.links[0].opened is False


def test_a_free_controller_pluto_and_this_computers_own_pluto_are_opened(pluto_station):
    assert pluto_station(device("pluto-1", "pluto", label="Pluto")).links[0].opened is True
    held = device("pluto-1", "pluto", label="Pluto", reserved=True)
    assert pluto_station(held, uri="ip:192.168.2.1").links[0].opened is True   # on this computer's USB


def _choose_controller(page, box, qapp):  # noqa: F811
    s, secrets = paired(box)
    page.wiz.s.controller = s.controller
    page.wiz.secrets.update(secrets)
    page.sdr.pluto_where.setCurrentIndex(page.sdr.pluto_where.findData("controller"))
    page.sdr._where_changed()
    return s


def test_choosing_the_controller_fills_the_address_and_says_the_network_matters(radio_page, box, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    box.broker_devices = [device("pluto-1", "pluto", label="Pluto")]
    s = _choose_controller(page, box, qapp)
    assert page.sdr.pluto_uri.text() == ohsclient.controller_pluto_uri(s.controller.url)
    note = page.sdr.where_note
    assert wait_for(lambda: "is free" in note.text(), 5, app=qapp)
    assert "10 MB/s" in note.text() and "Wi-Fi" in note.text() and "25 seconds" in note.text()
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_uri.startswith("pluto://")
    page.sdr.pluto_where.setCurrentIndex(page.sdr.pluto_where.findData("direct"))
    page.sdr._where_changed()
    assert page.sdr.pluto_uri.text() == "ip:192.168.2.1" and page.sdr.where_note.isHidden()


def test_the_setup_check_says_when_the_controllers_pluto_is_reserved(radio_page, box, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    box.broker_devices = [device("pluto-1", "pluto", label="Pluto", reserved={"app": "shack-packet-bbs"})]
    _choose_controller(page, box, qapp)
    note = page.sdr.where_note
    assert wait_for(lambda: "reserved" in note.text(), 5, app=qapp)
    assert note.objectName() == "bad" and "shack-packet-bbs" in note.text()


def test_choosing_the_controller_unpaired_offers_pairing_and_fills_the_address_it_gives(radio_page, box, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    box.broker_devices = [device("pluto-1", "pluto", label="Pluto")]
    asked = []

    def pair_here():                                # what pairing in PairDialog leaves behind
        asked.append(1)
        ohsclient.ControllerClient(wiz.s.controller, wiz.secrets).pair(box.url, "brian", "pw")
    page.sdr.pair_controller = pair_here
    page.sdr.pluto_where.setCurrentIndex(page.sdr.pluto_where.findData("controller"))
    page.sdr._where_changed()
    assert asked == [1]
    assert page.sdr.pluto_uri.text() == ohsclient.controller_pluto_uri(box.url)
    assert wait_for(lambda: "is free" in page.sdr.where_note.text(), 5, app=qapp)
    page.sdr._where_changed()                       # paired now: not asked again
    assert asked == [1]


def test_choosing_the_controller_with_none_paired_puts_in_the_address_template(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    page.sdr.pair_controller = lambda: None         # closed without pairing
    page.sdr.pluto_where.setCurrentIndex(page.sdr.pluto_where.findData("controller"))
    page.sdr._where_changed()
    assert page.sdr.pluto_uri.text() == "pluto://host?iiod=30431&ssh=30422"
    assert page.sdr.pluto_uri.selectedText() == "host"
    assert "replace host" in page.sdr.where_note.text() and page.sdr.where_note.objectName() == "bad"
    page.validatePage()
    assert wiz.s.sdr_radio.pluto_uri == "pluto://host?iiod=30431&ssh=30422"
    page.sdr.pluto_where.setCurrentIndex(page.sdr.pluto_where.findData("direct"))
    page.sdr._where_changed()
    assert page.sdr.pluto_uri.text() == "ip:192.168.2.1"


def test_the_pairing_dialog_is_the_controller_page_finding_controllers_as_it_opens(radio_page, box, qapp):  # noqa: F811
    from shack_client.discovery import Controller as Advert
    from shack_packet_app.ui.sdrpages import PairDialog
    wiz, page, _ = radio_page
    found = [Advert("GM4BRI", "a1b2c3d4", box.url, ["packet"])]
    client = ohsclient.ControllerClient(wiz.s.controller, wiz.secrets, discoverer=lambda t: found)
    d = PairDialog(page, client)
    assert wait_for(lambda: d.page.found.count() == 1, 5, app=qapp)
    d.page.found.itemClicked.emit(d.page.found.item(0))
    d.page.user.setText("brian")
    d.page.password.setText("pw")
    d.page.pair()
    assert wait_for(lambda: client.paired, 5, app=qapp)
    assert wiz.s.controller.url == box.url and ohsclient.SECRET_KEY in wiz.secrets   # the setup check's copies
    d.reject()
