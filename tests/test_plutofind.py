"""Finding a Pluto's address (plutofind, the setup check's Find button):
the combined scan, the per-backend fallback, the direct probes, one entry
per radio, and that a probe only reads. Every libiio here is a fake: no
radio, no network."""
from __future__ import annotations

import threading
import time

from shack_packet_app import plutofind
from shack_packet_app.plutofind import Found


class FakeContext:
    """A context that records everything asked of it; any write fails the test."""

    def __init__(self, iio, uri):
        self.iio, self.uri = iio, uri
        radio = iio.radios.get(uri)
        if radio is None:
            raise OSError(f"no answer at {uri}")
        self.attrs = dict(radio)

    def set_timeout(self, ms):
        self.iio.timeouts.append(ms)

    def find_device(self, name):
        self.iio.asked.append((self.uri, name))
        return object() if name == "ad9361-phy" and self.attrs.get("hw_model") else None

    def __setattr__(self, name, value):
        assert name in ("iio", "uri", "attrs"), f"a probe wrote {name}"
        object.__setattr__(self, name, value)


class FakeIio:
    """`scan`: what scan_contexts() returns, or an exception it raises;
    `radios`: {uri: context attributes} for what answers a direct open."""

    def __init__(self, scan=None, radios=None):
        self.scan, self.radios = scan if scan is not None else {}, radios or {}
        self.opened, self.asked, self.timeouts = [], [], []

    def scan_contexts(self):
        if isinstance(self.scan, Exception):
            raise self.scan
        return dict(self.scan)

    def Context(self, uri):                         # noqa: N802 - libiio's name
        self.opened.append(uri)
        return FakeContext(self, uri)


def run(iio, *, backends=None, direct=("ip:192.168.2.1", "ip:pluto.local"), avahi=True, names=None):
    backends = backends or {}

    def backend_scan(_iio, backend):
        got = backends.get(backend, {})
        if isinstance(got, Exception):
            raise got
        return got
    return plutofind.find(iio, timeout_s=0.5, backend_scan=backend_scan, direct=lambda: list(direct),
                          avahi_running=lambda: avahi, resolve=lambda name, _t: (names or {}).get(name))


PLUTO = {"hw_model": "Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)", "hw_serial": "104473dc59"}


def test_a_pluto_on_usb_is_found_by_the_scan_and_a_laptop_sensor_is_not():
    iio = FakeIio(scan={"usb:1.7.5": "0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial=104473dc59",
                        "local:": "Local devices"})
    out = run(iio)
    assert out.found == [Found("usb:1.7.5", "0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), "
                                            "serial=104473dc59")]
    assert out.notes == []


def test_a_pluto_on_another_subnet_is_found_by_probing_the_hosts_usb_network():
    # The Windows tester's case: the Pluto set to 192.168.3.1, this
    # computer 192.168.3.10 on its USB network, and the scan finding nothing.
    uris = plutofind.direct_candidates(["127.0.0.1", "10.0.0.7", "192.168.3.10"])
    assert uris == ["ip:192.168.2.1", "ip:pluto.local", "ip:192.168.3.1"]
    iio = FakeIio(radios={"ip:192.168.3.1": PLUTO})
    out = run(iio, direct=uris)
    assert out.found == [Found("ip:192.168.3.1", "192.168.3.1 (Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)), "
                                                 "serial=104473dc59")]
    assert set(iio.opened) == set(uris)


def test_nothing_found_is_an_empty_list_not_an_error():
    out = run(FakeIio())
    assert out.found == [] and out.notes == []


def test_something_that_answers_but_is_not_a_pluto_is_not_offered():
    out = run(FakeIio(radios={"ip:192.168.2.1": {"hw_model": ""}}))
    assert out.found == []


def test_a_failed_scan_falls_back_per_backend_and_one_raising_backend_does_not_cost_the_other():
    iio = FakeIio(scan=OSError(26, "Text file busy"))
    out = run(iio, avahi=False, backends={
        b"usb": {"usb:1.7.5": "0456:b673 (PlutoSDR), serial=104473dc59"},
        b"ip": OSError(26, "Text file busy")})
    assert [f.uri for f in out.found] == ["usb:1.7.5"]
    assert out.notes == [plutofind.NOTE_NO_AVAHI]
    out = run(FakeIio(scan=OSError("x")), avahi=True, backends={b"usb": OSError("x"), b"ip": {
        "ip:192.168.2.1": "192.168.2.1 (Analog Devices PlutoSDR Rev.B (Z7010-AD9364)), serial=abc"}})
    assert [f.uri for f in out.found] == ["ip:192.168.2.1"] and out.notes == [plutofind.NOTE_SCAN_FAILED]


def test_no_libiio_is_a_note_not_a_traceback():
    def missing():
        raise OSError("libiio.dll not found")
    out = plutofind.find(missing, timeout_s=0.1)
    assert out.found == [] and out.notes == [plutofind.NOTE_NO_LIBIIO]


def test_one_radio_seen_three_ways_is_one_entry_at_its_numeric_address():
    # usb: and ip: for one Pluto share a serial; the mDNS name resolves to
    # the same number; the direct probe repeats it. The number is kept and
    # the name stays in the description.
    iio = FakeIio(scan={
        "usb:1.7.5": "0456:b673 (PlutoSDR), serial=104473dc59",
        "ip:pluto.local": "pluto.local (Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)), serial=104473dc59"},
        radios={"ip:192.168.2.1": PLUTO})
    out = run(iio, names={"pluto.local": "192.168.2.1"})
    assert len(out.found) == 1
    assert out.found[0].uri == "ip:192.168.2.1" and out.found[0].description.startswith("pluto.local (")


def test_two_radios_with_different_serials_are_never_merged():
    iio = FakeIio(scan={"usb:1.7.5": "0456:b673 (PlutoSDR), serial=AAA",
                        "usb:1.8.5": "0456:b673 (PlutoSDR), serial=BBB"})
    assert [f.uri for f in run(iio).found] == ["usb:1.7.5", "usb:1.8.5"]


def test_a_probe_only_reads_and_is_given_a_short_timeout():
    iio = FakeIio(radios={"ip:192.168.2.1": PLUTO})
    # A write would raise inside the probe and lose the radio, so finding
    # it is the proof that nothing was written.
    assert [f.uri for f in run(iio).found] == ["ip:192.168.2.1"]
    assert iio.asked == [("ip:192.168.2.1", "ad9361-phy")]
    assert iio.timeouts and max(iio.timeouts) <= 1000


def test_an_address_that_hangs_costs_the_deadline_not_the_kernels_retry_budget():
    gate = threading.Event()

    class Hanging(FakeIio):
        def Context(self, uri):                     # noqa: N802
            if uri == "ip:pluto.local":
                gate.wait(10)                       # a dead address on a live subnet
            return super().Context(uri)
    t0 = time.monotonic()
    out = run(Hanging(radios={"ip:192.168.2.1": PLUTO}))
    gate.set()
    assert time.monotonic() - t0 < 2.0 and [f.uri for f in out.found] == ["ip:192.168.2.1"]


# -- the setup check's Find button -----------------------------------------------------------

import pytest  # noqa: E402

from conftest import wait_for  # noqa: E402
from test_ohsclient import box, device, paired  # noqa: E402,F401 - the fixture
from test_sdr_radio import radio_page  # noqa: E402,F401 - the fixture

ONE = Found("ip:192.168.3.1", "192.168.3.1 (Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)), serial=104473dc59")
TWO = Found("usb:1.8.5", "0456:b673 (PlutoSDR), serial=BBB")


@pytest.fixture
def pluto_panel(radio_page, qapp):  # noqa: F811
    wiz, page, _ = radio_page
    page.sdr.choose_pluto = lambda found: pytest.fail("asked to choose with one Pluto found")
    return page.sdr


def test_find_runs_off_the_window_thread_and_fills_the_one_pluto_it_found(pluto_panel, qapp):
    sdr = pluto_panel
    gate, calls = threading.Event(), []

    def finder():
        calls.append(threading.current_thread().name)
        gate.wait(5)                                # a slow search: the window must keep going
        return plutofind.FindResult([ONE])
    sdr.finder = finder
    sdr.find_btn.click()
    assert wait_for(lambda: calls, 2, app=qapp)
    assert calls == ["setup-sdr"] and not sdr.find_btn.isEnabled()
    assert "Looking for a Pluto" in sdr.find_note.text()
    sdr.find_btn.click()                            # a second press while searching starts nothing
    gate.set()
    assert wait_for(lambda: sdr.pluto_uri.text() == "ip:192.168.3.1", 3, app=qapp)
    assert sdr.find_btn.isEnabled() and len(calls) == 1
    assert "Found ip:192.168.3.1" in sdr.find_note.text() and "104473dc59" in sdr.find_note.text()


def test_with_several_found_the_operator_chooses_and_cancel_changes_nothing(pluto_panel, qapp):
    sdr = pluto_panel
    sdr.pluto_uri.setText("ip:192.168.2.1")
    offered = []
    sdr.finder = lambda: plutofind.FindResult([ONE, TWO])
    sdr.choose_pluto = lambda found: offered.append(list(found)) or None
    sdr.find()
    assert wait_for(lambda: offered, 3, app=qapp)
    assert offered == [[ONE, TWO]] and sdr.pluto_uri.text() == "ip:192.168.2.1"
    sdr.choose_pluto = lambda found: found[1]
    sdr.find()
    assert wait_for(lambda: sdr.pluto_uri.text() == "usb:1.8.5", 3, app=qapp)
    assert "network address" in sdr.find_note.text()          # a USB address cannot carry the TX guard


def test_the_list_dialog_returns_the_pluto_picked(qapp):
    from shack_packet_app.ui.sdrpages import FoundDialog
    d = FoundDialog(None, [ONE, TWO])
    assert d.chosen() == ONE                        # the preferred one is picked already
    d.list.setCurrentRow(1)
    assert d.chosen() == TWO


def test_nothing_found_says_what_was_tried_and_on_windows_the_driver(pluto_panel, qapp):
    sdr = pluto_panel

    class WindowsTools:
        platform = "win32"
    sdr.tools = WindowsTools()
    sdr.pluto_uri.setText("ip:192.168.2.1")
    sdr.finder = lambda: plutofind.FindResult([], [])
    sdr.find()
    assert wait_for(lambda: "No Pluto found" in sdr.find_note.text(), 3, app=qapp)
    assert "PlutoSDR-M2k-USB-Drivers.exe" in sdr.find_note.text() and sdr.find_note.objectName() == "bad"
    assert sdr.pluto_uri.text() == "ip:192.168.2.1"
    sdr.finder = lambda: plutofind.FindResult([], [plutofind.NOTE_NO_LIBIIO])
    sdr.find()
    assert wait_for(lambda: "libiio" in sdr.find_note.text(), 3, app=qapp)


def test_find_is_not_offered_for_the_controllers_pluto(pluto_panel, qapp):
    sdr = pluto_panel
    sdr.pair_controller = lambda: None
    sdr.pluto_where.setCurrentIndex(sdr.pluto_where.findData("controller"))
    sdr._where_changed()
    assert sdr.find_btn.isHidden()
    sdr.pluto_where.setCurrentIndex(sdr.pluto_where.findData("direct"))
    sdr._where_changed()
    assert not sdr.find_btn.isHidden()


def test_a_found_address_on_the_paired_controller_goes_through_the_controller(pluto_panel, box, qapp):  # noqa: F811
    # Found by its plain address, the controller's Pluto would be opened
    # round the controller's reservation; Find puts it in as the
    # controller's and the page asks the controller, as Where does.
    sdr = pluto_panel
    box.broker_devices = [device("pluto-1", "pluto", label="Pluto", reserved={"app": "shack-packet-bbs"})]
    s, secrets = paired(box)
    sdr.page.wiz.s.controller = s.controller
    sdr.page.wiz.secrets.update(secrets)
    sdr.finder = lambda: plutofind.FindResult([Found("ip:127.0.0.1", "127.0.0.1 (PlutoSDR), serial=CTL")])
    sdr.find()
    assert wait_for(lambda: sdr.pluto_uri.text().startswith("pluto://"), 3, app=qapp)
    assert sdr.pluto_where.currentData() == "controller" and sdr.find_btn.isHidden()
    assert wait_for(lambda: "reserved" in sdr.where_note.text(), 5, app=qapp)
