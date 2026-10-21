"""The AKP03E control pad: the rules of padactions.py on their own (a fake
window and a fake clock), the remap editor's reset, and the whole path on
the simulator - shack_pad's real AKP03E driver reading input reports from
FakeHid, the input host, the window and the station."""
from __future__ import annotations

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import padactions, padmap


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.is_armed = False

    def armed(self):
        return self.is_armed

    def __getattr__(self, name):
        if name in ("arm", "disarm", "halt", "beacon_now", "connect", "disconnect", "page"):
            return lambda *a: self.calls.append((name,) + a)
        raise AttributeError(name)


@pytest.fixture
def pad():
    clock, bridge = Clock(), FakeBridge()
    return padactions.PadActions(bridge, clock=clock), bridge, clock


def test_arm_needs_a_second_press_of_the_same_key_within_three_seconds(pad):
    acts, bridge, clock = pad
    acts.perform("arm", "K1")
    assert bridge.calls == [] and acts.arm_pending()
    clock.t += 2.9
    acts.perform("arm", "K1")
    assert bridge.calls == [("arm",)] and not acts.arm_pending()


def test_a_second_press_too_late_or_on_another_key_does_not_arm(pad):
    acts, bridge, clock = pad
    acts.perform("arm", "K1")
    clock.t += 3.1
    assert not acts.arm_pending()                # the key stops saying "press again"
    acts.perform("arm", "K1")                    # a new first press, not a second
    assert bridge.calls == []
    acts.perform("arm", "KP1")                   # the arm action on another control
    assert bridge.calls == []
    acts.perform("arm", "KP1")
    assert bridge.calls == [("arm",)]


def test_any_other_key_between_the_two_presses_cancels_the_arming(pad):
    acts, bridge, clock = pad
    acts.perform("arm", "K1")
    acts.perform("page:next", "B2")
    acts.perform("arm", "K1")
    assert ("arm",) not in bridge.calls and acts.arm_pending()


def test_stopping_acts_on_the_first_press_and_cancels_a_half_made_arming(pad):
    acts, bridge, clock = pad
    acts.perform("arm", "K1")
    acts.perform("disarm", "K2")
    acts.perform("arm", "K1")
    acts.perform("halt", "K6")
    acts.perform("disconnect", "K5")
    assert bridge.calls == [("disarm",), ("halt",), ("disconnect",)]
    assert not acts.arm_pending()


def test_beacon_is_only_asked_for_while_armed(pad):
    acts, bridge, _clock = pad
    acts.perform("beacon", "K3")
    assert bridge.calls == []
    bridge.is_armed = True
    acts.perform("beacon", "K3")
    assert bridge.calls == [("beacon_now",)]


def test_an_action_this_version_does_not_know_is_ignored(pad):
    acts, bridge, _clock = pad
    acts.perform("ptt", "K1")
    acts.perform("transmit", "K1")
    acts.perform("something-new", "K1")
    assert bridge.calls == []


def test_the_declaration_is_valid_and_has_nothing_that_keys():
    from shack_pad import actions
    decl, layout = padactions.declaration()
    actions.validate(decl)
    acts, notes = actions.load(decl)
    assert notes == []
    assert actions.tx_actions(acts) == []
    # The shipped layout names only actions that exist: nothing falls back.
    from shack_pad import mapping
    from shack_pad.drivers import by_model
    for model in ("ajazz-akp03e", "keypad"):
        page, notes = mapping.default_page(by_model(model), layout, acts)
        assert notes == [], model
    page, _ = mapping.default_page(by_model("ajazz-akp03e"), layout, acts)
    assert page["K1"]["press"] == "arm" and page["K6"]["press"] == "halt"


def test_a_remap_reset_is_stored_as_an_empty_entry_and_reads_back_as_the_default():
    other = {"qo100nb": {"ajazz-akp03e": {"pages": [{"K2": {"press": "mute"}}]}}}
    pm = padmap.PadMap(other)
    pm.set("K2", "beacon")
    # An ordinary action may be on two keys: K3 keeps its BEACON too.
    assert pm.current("K2") == "beacon" and pm.current("K3") == "beacon"
    stored = pm.maps()
    assert stored["qo100nb"] == other["qo100nb"]          # another app's map is left alone
    assert padmap.PadMap(stored).current("K2") == "beacon"
    again = padmap.PadMap(stored)
    again.reset()
    reset = again.maps()
    # Written as None, not removed: a removed entry would come back from a
    # synced profile, which never deletes a field.
    assert padmap.KIND in reset and reset[padmap.KIND] == {padmap.MODEL: None}
    assert padmap.PadMap(reset).current("K2") == "disarm"
    assert padmap.PadMap(reset).is_default()


# -- the whole path, on the simulator ------------------------------------------------------


@pytest.fixture
def padwin(qapp, folders, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from shack_pad.drivers.ajazz import AKP03E
    from shack_pad.fake import FakeHid

    from shack_packet_app.controller import Controller
    from shack_packet_app.simband import SimBand
    from shack_packet_app.ui.window import MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    s = station_settings(aprs__beacon=True)
    ctl = Controller(s, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0)))
    win = MainWindow(s, {}, controller=ctl, persist=False)
    win.show()
    win.start(first_run_check=False)
    hid = FakeHid()
    link = win.start_pad(store_dir=folders / "store", open_device=lambda: AKP03E(hid))
    assert link is not None
    assert wait_for(lambda: link.status()["state"] == "serving", 5, app=qapp)
    assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
    yield win, hid
    win.close()
    qapp.processEvents()


def press(hid, code):
    from shack_pad.fake import akp03e_report
    hid.push(akp03e_report(code, 1), akp03e_report(code, 0))


def test_the_pad_arms_on_a_double_press_beacons_and_halts(padwin, qapp):
    win, hid = padwin
    press(hid, 3)                                     # BEACON while disarmed: nothing
    press(hid, 1)                                     # ARM, once
    assert wait_for(lambda: "again" in win.statusBar().currentMessage(), 3, app=qapp)
    assert not win.ctl.status()["armed"]
    keys = win.pad.host.faces("ajazz-akp03e")           # what the pad's screens show
    assert (keys["K1"].l1, keys["K1"].l2) == ("PRESS", "AGAIN")
    assert keys["K3"].tone == "disabled"                # BEACON: not while disarmed
    press(hid, 1)                                     # and again
    assert wait_for(lambda: win.ctl.status()["armed"], 3, app=qapp)
    assert win.statusBar().currentMessage() == "Transmit armed."
    keys = win.pad.host.faces("ajazz-akp03e")
    assert keys["K1"].l1 == "ARMED" and keys["K1"].l2 in ("15:00", "14:59") and keys["K3"].tone == "normal"
    press(hid, 3)                                     # BEACON now: a frame goes out
    # Nothing else on this station sends: the quiet band's stations do not
    # beacon for 40 s, and no message or chat is open.
    assert wait_for(lambda: any(r.direction == "tx" for r in win.ctl.rows), 10, app=qapp)
    assert win.statusBar().currentMessage() == "Position beacon queued."
    press(hid, 3)                                     # again at once: refused, not queued
    assert wait_for(lambda: "less than a minute" in win.statusBar().currentMessage(), 3, app=qapp)
    press(hid, 6)                                     # HALT
    assert wait_for(lambda: not win.ctl.status()["armed"], 3, app=qapp)
    assert wait_for(lambda: "control pad" in win.statusBar().currentMessage(), 3, app=qapp)


def test_the_pad_steps_through_the_pages_and_connects_to_the_chosen_station(padwin, qapp):
    from shack_packet_app.simband import NODE
    from shack_packet_app.ui.window import PAGES
    win, hid = padwin
    press(hid, 0x30)                                  # B2: next page
    assert wait_for(lambda: win.nav.currentRow() == 1, 3, app=qapp)
    press(hid, 0x25)                                  # B1: previous page
    press(hid, 0x25)                                  # and round to the last
    assert wait_for(lambda: win.nav.currentRow() == len(PAGES) - 1, 3, app=qapp)
    win.pages["chat"].call.setText(NODE)
    win.tick()
    press(hid, 4)                                     # CONNECT
    assert wait_for(lambda: win.current_page() is win.pages["chat"] and win.ctl.sessions, 3, app=qapp)
    sess = next(iter(win.ctl.sessions.values()))
    assert sess.remote == NODE
    press(hid, 5)                                     # DISCONNECT
    assert wait_for(lambda: sess.state in ("disconnecting", "disconnected"), 10, app=qapp)


def test_settings_shows_the_pad_and_a_reset_reaches_the_pad_as_an_empty_entry(padwin, qapp):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    win, hid = padwin
    dlg = SettingsDialog(win.settings, win, "pad")
    assert dlg.tabs.currentIndex() == SettingsDialog.TABS.index("pad")
    assert "is connected" in dlg.pad_state.text()
    box = dlg.pad_boxes["K2"]
    box.setCurrentIndex(box.findData("beacon"))
    win.apply_settings(dlg.result_settings())
    dlg.reject()
    assert win.pad.host.faces("ajazz-akp03e")["K2"].l1 == "BEACON"
    dlg = SettingsDialog(win.settings, win, "pad")
    assert dlg.pad_boxes["K2"].currentData() == "beacon"
    dlg.pad_reset.click()
    assert dlg.pad_boxes["K2"].currentData() == "disarm"
    new = dlg.result_settings()
    dlg.reject()
    assert new.pad.maps == {"packet": {"ajazz-akp03e": None}}
    win.apply_settings(new)
    assert win.pad.host.faces("ajazz-akp03e")["K2"].l1 == "DISARM"
    # The key test: a press lights its key and does nothing.
    dlg = SettingsDialog(win.settings, win, "pad")
    dlg.pad_test.setChecked(True)
    assert win.pad.testing()
    press(hid, 1)
    press(hid, 1)
    wait_for(lambda: False, 0.5, app=qapp)
    assert not win.ctl.status()["armed"]
    dlg.reject()
    assert not win.pad.testing()


def test_turning_the_pad_off_lets_it_go(padwin, qapp):
    win, hid = padwin
    new = win.settings.copy()
    new.pad.enabled = False
    win.apply_settings(new)
    assert win.pad is None
    assert hid.closed
