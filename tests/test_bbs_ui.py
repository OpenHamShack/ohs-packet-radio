"""The 0.3.0 windows, offscreen, on the simulator against GB7SIM: a BBS
read through the mail helper and its paging buttons, unread counts on
chat tabs, the Nodes page connecting through a neighbour, the hosted
room's tab, the Log page's export, and callsign tooltips from a fake
HamQTH."""
from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from conftest import station_settings, wait_for
from shack_packet_app import callbook, settings as settings_mod
from shack_packet_app.controller import Controller
from shack_packet_app.simband import NODE, SimBand
from shack_packet_app.ui.window import MainWindow

BBS = {"name": "SIM BBS", "text": "connect GB7SIM\nwait ? for help\nsend BBS\nwait de GB7SIM>\n"}


def band():
    return SimBand(55.95, -3.2, first_beacon_s=(60.0, 70.0), beacon_every_s=120, first_nodes_s=0.5)


@pytest.fixture
def window(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    made = []

    def make(s=None, secrets=None, **kw):
        s = s if s is not None else station_settings()
        ctl = Controller(s, secrets=secrets or {}, band_factory=band)
        win = MainWindow(s, secrets or {}, controller=ctl, persist=True,
                         notifier={"active": lambda: True, "tray_available": lambda: False}, **kw)
        win.messages_shown = shown
        win.show()
        made.append(win)
        win.start(first_run_check=False)
        assert wait_for(lambda: ctl.link_state == "open", 5, app=qapp)
        return win
    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def ticking(win, cond, timeout=20.0, app=None):
    def check():
        win.tick()
        return cond()
    return wait_for(check, timeout, app=app)


def test_a_bbs_is_read_through_the_mail_helper_and_its_paging_buttons(window, qapp):
    win = window()
    win.settings.bbs.scripts = [BBS]
    win.ctl.band.node.leave_for("GM4BRI", "Sked", "Are you on tonight?")
    win.ctl.arm(5)
    win.go_to("chat")
    page = win.pages["chat"]
    page._fill_scripts()
    run = next(a for a in page.script_menu.actions() if a.text() == "SIM BBS")
    run.trigger()
    view = next(iter(page.views.values()))
    assert ticking(win, lambda: view.side.isVisible() and view.side.currentWidget() is view.panels["bbs"],
                   30, qapp), view.screen.toPlainText()
    mail = view.panels["bbs"]
    mail.list_all.click()
    assert ticking(win, lambda: view.paging.isVisible(), 20, qapp), view.screen.toPlainText()
    view.more.click()
    assert ticking(win, lambda: mail.table.rowCount() == 15, 20, qapp), view.screen.toPlainText()
    assert not view.paging.isVisible()
    assert mail.table.item(0, 5).text() == "Sked"
    mail.table.selectRow(0)
    mail.read.click()
    assert ticking(win, lambda: "Are you on tonight?" in mail.reader.toPlainText(), 20, qapp)
    # The raw terminal still shows everything, the helper's commands included.
    text = view.screen.toPlainText()
    assert "> L" in text and "> R " in text and "Are you on tonight?" in text


def test_a_tab_behind_another_counts_its_unread_lines(window, qapp):
    win = window()
    win.ctl.arm(5)
    win.go_to("chat")
    page = win.pages["chat"]
    a = win.ctl.connect(NODE)
    assert ticking(win, lambda: a.connected and a.id in page.views, 20, qapp)
    page.call.setText("G4SIM")                       # a second tab, in front; it never answers
    page.go.click()
    behind = page.views[a.id]
    assert page.tabs.currentWidget() is not behind
    win.ctl.send(a.id, "hello")
    assert ticking(win, lambda: behind.unread >= 1, 20, qapp)
    index = page.tabs.indexOf(behind)
    assert page.tabs.tabText(index).endswith(f"({behind.unread})")
    page.tabs.setCurrentWidget(behind)
    win.tick()
    assert behind.unread == 0 and page.tabs.tabText(index) == NODE


def test_the_nodes_page_connects_through_a_neighbour(window, qapp):
    win = window()
    win.ctl.arm(5)
    win.go_to("nodes")
    page = win.pages["nodes"]
    assert ticking(win, lambda: page.table.rowCount() >= 4, 15, qapp)
    calls = [page.table.item(i, 1).text() for i in range(page.table.rowCount())]
    assert calls[0] == NODE                          # heard directly: first
    page.table.selectRow(calls.index("GB7SJ"))
    sess = page.connect_selected()
    assert win.current_key() == "chat"
    assert [s.arg for s in sess.script.steps] == ["GB7SIM", "C SJ"]
    assert ticking(win, lambda: any("Welcome to GB7SJ" in l.text for l in sess.lines), 30, qapp)


def test_the_room_tab_opens_the_room_and_says_when_nobody_can_join(window, qapp):
    win = window()
    win.go_to("chat")
    page = win.pages["chat"]
    page.host.click()
    room = page.room_view
    room.name.setText("Test room")
    room.toggle.click()
    win.tick()
    assert win.ctl.room.open and win.ctl.room.address() == "GM4BRI-4"
    assert "not armed" in room.state.text() and not room.name.isEnabled()
    assert win.settings.room.name == "Test room"
    room.toggle.click()
    assert not win.ctl.room.open


def test_the_log_page_filters_and_exports_what_it_shows(window, qapp, tmp_path):
    win = window()
    from shack_packet_app.logbook import Contact
    for call, f in (("G0ABC", 144_800_000), ("G4XYZ", 432_500_000)):
        win.ctl.logbook.add(Contact(call=call, station=call, time_on=1_792_000_000.0, time_off=1_792_000_300.0,
                                    freq_hz=f, kind="chat"))
    win.go_to("log")
    page = win.pages["log"]
    win.tick()
    assert page.table.rowCount() == 2
    page.filter.setText("70cm")
    win.tick()
    assert page.table.rowCount() == 1 and page.table.item(0, 3).text() == "G4XYZ"
    out = page.export("adi", str(tmp_path / "log.adi"))
    text = open(out, encoding="ascii").read()
    assert "<CALL:5>G4XYZ" in text and "G0ABC" not in text
    page.auto["auto_bbs"].click()
    assert win.settings.log.auto_bbs


class FakeHamQth:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "u=" in url:
            return b"<HamQTH><session><session_id>s1</session_id></session></HamQTH>"
        return (b"<HamQTH><search><callsign>g4sim</callsign><adr_name>Sim Station</adr_name><qth>Leith</qth>"
                b"<grid>IO85KX</grid></search></HamQTH>")


def test_callsign_tooltips_come_from_the_lookup_and_the_account_stays_secret(window, qapp):
    win = window()
    fake = FakeHamQth()
    win.ctl.callbook = callbook.Callbook(None, "", "", fetch=fake, threaded=False)
    win.open_lookup()
    dlg = win.lookup_dialog
    dlg.user.setText("gm4bri")
    dlg.password.setText("top-secret")
    dlg.save()
    assert win.secrets["hamqth_password"] == "top-secret"
    saved = json.loads(settings_mod.settings_path().read_text())
    assert "top-secret" not in json.dumps(saved)
    assert json.loads(settings_mod.secrets_path().read_text())["hamqth_password"] == "top-secret"
    from shack_packet_app.ui import callinfo
    assert "looking up" in callinfo.tooltip(win.ctl, "G4SIM-9")
    assert win.ctl.callbook.run_one()
    tip = callinfo.tooltip(win.ctl, "G4SIM-9")
    assert tip.startswith("G4SIM: Sim Station, Leith") and "IO85KX" in tip and " km, bearing " in tip
    # The Monitor shows it on a frame from that station.
    from shack_packet_app.controller import MonitorRow
    model = win.pages["monitor"].model
    model.set_rows([MonitorRow(1, 0.0, "rx", "radio", "G4SIM-9>APRS:hi", source="G4SIM-9")])
    assert model.data(model.index(0, 4), Qt.ItemDataRole.ToolTipRole) == tip
