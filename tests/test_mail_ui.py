"""The 0.3.0 mail client and APRS extras in the window, offscreen on the
simulator: writing packet mail and watching it go, Winlink email in its
own tab, the Settings tabs (the Winlink password into secrets, never
settings), and objects placed from the map."""
from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import station_settings, wait_for
from shack_packet_app import settings as settings_mod
from shack_packet_app.controller import Controller
from shack_packet_app.settings_extra import WINLINK_SECRET
from shack_packet_app.simband import SIM_FRIEND, SIM_RMS, SimBand
from shack_packet_app.ui.window import PAGES, MainWindow


def quiet_band():
    return SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0), beacon_every_s=120)


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    made = []

    def make(s=None, secrets=None):
        s = s if s is not None else station_settings()
        ctl = Controller(s, secrets=secrets or {}, band_factory=quiet_band)
        win = MainWindow(s, secrets or {}, controller=ctl, persist=True)
        win.show()
        made.append(win)
        win.start(first_run_check=False)
        assert wait_for(lambda: ctl.link_state == "open", 5, app=qapp)
        return win
    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def fill(**values):
    def hook(dlg):
        for name, value in values.items():
            widget = getattr(dlg, name)
            if hasattr(widget, "setPlainText"):
                widget.setPlainText(value)
            else:
                widget.setText(value)
    return hook


def test_writing_packet_mail_and_watching_it_go(window, qapp):
    win = window()
    win.nav.setCurrentRow(PAGES.index("mailbox"))
    page = win.pages["mailbox"]
    packet = page.packet
    page.dialog_hook = fill(to="not a call!", subject="x", body="y")
    assert not packet.compose()                       # refused: not a callsign
    page.dialog_hook = fill(to=SIM_FRIEND.lower(), subject="Sked", body="2 m at 2000?")
    assert packet.compose()
    assert "once transmit is armed" in packet.note.text()
    assert packet.outbox_shown() and packet.out_table.rowCount() == 1
    assert packet.out_table.item(0, 1).text() == SIM_FRIEND and packet.out_table.item(0, 2).text() == "Queued"
    win.ctl.arm(5)

    def state():
        win.tick()
        return packet.out_table.item(0, 2).text()
    assert wait_for(lambda: state().startswith("Delivered via"), 60, app=qapp), state()
    assert state() == f"Delivered via {SIM_FRIEND}-1"
    packet.out_table.selectRow(0)
    assert "Subject: Sked" in packet.body.toPlainText()


def test_winlink_tab_writes_offline_and_connects_only_when_it_can(window, qapp):
    s = station_settings(winlink__gateways=[f"{SIM_RMS}-10"], winlink__gateway=f"{SIM_RMS}-10")
    win = window(s, secrets={WINLINK_SECRET: "secret"})
    win.nav.setCurrentRow(PAGES.index("mailbox"))
    page = win.pages["mailbox"]
    page.tabs.setCurrentWidget(page.winlink)
    wl = page.winlink
    page.dialog_hook = fill(to="friend@example.com, G4XYZ", subject="From the shack", body="Hello")
    assert wl.compose()
    assert wl.folder.currentData() == "outbox" and wl.table.rowCount() == 1
    assert wl.table.item(0, 2).text() == "friend@example.com, G4XYZ"
    wl.refresh()
    assert wl.gateway.currentText() == f"{SIM_RMS}-10"
    wl._connect()
    assert "Arm transmit first" in wl.note.text()
    win.ctl.arm(5)
    wl._connect()

    def done():
        win.tick()
        return win.ctl.mailclient.wl_last.get("state") in ("done", "failed")
    assert wait_for(done, 120, app=qapp)
    assert "sent 1, received 1" in wl.note.text()
    wl.folder.setCurrentIndex(0)
    wl.refresh()
    assert wl.table.item(0, 4).text() == "Welcome to the simulated gateway" and wl.table.item(0, 3).text() == "new"
    wl.table.selectRow(0)
    assert "From: test@example.com" in wl.body.toPlainText()


def test_settings_tabs_keep_the_password_out_of_settings(window, qapp, folders):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    win = window()
    dlg = SettingsDialog(win.settings, win, "mail", secrets=win.secrets)
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Mail"
    x = dlg.extras
    x.home.setText("gb7sbb-1")
    x.partners.setPlainText("GB7XYZ-1 via MB7NXX routes GB7XYZ\n")
    x.wl_password.setText("Hunter2Secret")
    x.wl_gateways.setPlainText("gb7rms-10\nGB7ABC-10 via MB7NXX")
    x.gps_source.setCurrentIndex(x.gps_source.findData("manual"))
    x.manual.setText("55.9, -3.1")
    x.ig_on.setChecked(True)
    s = dlg.result_settings()
    secrets = dlg.result_secrets()
    assert s.mail.home_bbs == "GB7SBB-1" and s.mail.partners == ["GB7XYZ-1 via MB7NXX routes GB7XYZ"]
    assert s.winlink.gateways == ["GB7RMS-10", "GB7ABC-10 VIA MB7NXX"] and s.winlink.gateway == "GB7RMS-10"
    assert s.gps.source == "manual" and s.igate.enabled and not s.igate.tx
    assert secrets[WINLINK_SECRET] == "Hunter2Secret"
    win.apply_settings(s, secrets)
    on_disk = settings_mod.settings_path().read_text(encoding="utf-8")
    assert "Hunter2Secret" not in on_disk and json.loads(on_disk)["mail"]["home_bbs"] == "GB7SBB-1"
    assert settings_mod.load_secrets()[WINLINK_SECRET] == "Hunter2Secret"
    assert win.ctl.position() == (55.9, -3.1)


def test_an_object_placed_from_the_map_is_listed_drawn_and_killed(window, qapp):
    win = window()
    win.nav.setCurrentRow(PAGES.index("aprs"))
    page = win.pages["aprs"]
    page.objects_hook = fill(name="NET", comment="Net 2000 on 145.500", symbol="/r")
    assert page.place_here(55.91, -3.15)
    assert win.settings.objects.items[0]["name"] == "NET" and win.settings.objects.items[0]["lat"] == 55.91
    win.tick()
    assert any(st["name"] == "NET" and st.get("kind") == "own" for st in page.map.stations)
    dlg = page.open_objects()
    assert dlg.list.count() == 1 and "NET" in dlg.list.item(0).text()
    dlg.list.setCurrentRow(0)
    dlg._kill()
    assert dlg.list.count() == 0 and win.settings.objects.items == []
    page.objects_hook = fill(name="   ")                 # no name: refused, nothing placed
    assert not page.place_here(55.9, -3.1)
