"""links.power_status: what the arming dialog and the turnaround tool show
about transmit power, read from a radio link's own flags (IcomLink's
`_power_min_confirmed`/`_power_min_failed` in shack_rig's packet-sdr
branch). A plain-object fake stands in for the link - this is a pure
function and does not need a radio, real or simulated."""
from __future__ import annotations

from shack_packet_app.links import power_status
from shack_packet_app.settings import RadioSettings


class _FakeLink:
    def __init__(self, confirmed=False, failed=None):
        self._power_min_confirmed = confirmed
        self._power_min_failed = failed


def test_icom_with_the_test_setting_on():
    radio = RadioSettings(kind="ic-9700", minimum_power_for_test=True)
    assert power_status(radio, _FakeLink()) == ("pending", "")
    assert power_status(radio, _FakeLink(confirmed=True)) == ("minimum", "")
    assert power_status(radio, _FakeLink(failed="no answer")) == ("failed", "no answer")


def test_icom_with_the_test_setting_off_says_nothing():
    radio = RadioSettings(kind="ic-705", minimum_power_for_test=False)
    assert power_status(radio, _FakeLink(confirmed=True)) is None


def test_yaesu_and_soundcard_say_set_it_by_hand():
    assert power_status(RadioSettings(kind="ftx-1"), _FakeLink()) == ("manual", "")
    assert power_status(RadioSettings(kind="soundcard"), _FakeLink()) == ("manual", "")


def test_the_simulator_and_no_radio_say_nothing():
    assert power_status(RadioSettings(kind="simulator"), _FakeLink()) is None
    assert power_status(RadioSettings(kind="none"), _FakeLink()) is None
