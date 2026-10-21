"""Self-update in the window (ui/update.py), on the simulator and Qt's
offscreen platform, against a release folder signed by tools/update-feed.py
with a test key and served over local HTTP: the banner, Help › Check for
updates…, when checks happen, and Install through to the command - which
a stand-in records instead of running. Nothing reaches the real site and
nothing is installed."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from conftest import station_settings, wait_for
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.window import MainWindow

pytest.importorskip("cryptography")
from test_update import DEB, EXE, Served, key_file, new_key, write_release  # noqa: E402

GOOD_SIGNER = ("Valid", "CN=360 Technology, O=360 Technology, L=Example, C=GB")


@pytest.fixture
def site(tmp_path):
    k, keys = new_key()
    keyfile = key_file(k, tmp_path / "key" / "ed25519.key")
    rel = tmp_path / "rel"
    res = write_release(rel, keyfile, "0.2.0", {DEB.format(v="0.2.0"): b"deb " * 4000,
                                                EXE.format(v="0.2.0"): b"MZ " * 4000})
    assert res.returncode == 0, res.stderr
    server = Served(rel)
    yield {"keys": keys, "server": server, "feed": server.url + "update.json"}
    server.close()


@pytest.fixture
def window(qapp, site):
    made = []

    def make(**updates):
        ran, after_exit = [], []
        opts = dict(feed_url=site["feed"], keys=site["keys"], plat="linux", arch="amd64", kind="deb",
                    version="0.1.2", pkexec="/usr/bin/pkexec", runner=lambda argv: (ran.append(argv), (0, ""))[1],
                    at_exit=after_exit.append)
        opts.update(updates)
        s = station_settings()
        ctl = Controller(s, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0)))
        win = MainWindow(s, {}, controller=ctl, persist=False, updates=opts)
        win.ran, win.after_exit = ran, after_exit
        win.show()
        win.start(first_run_check=False)
        made.append(win)
        return win
    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def checked(win, qapp):
    win.updates.check(manual=True)
    assert wait_for(lambda: not win.updates.checking, 10, app=qapp)
    return win.updates.result


def test_a_new_version_shows_the_banner_and_installs_through_pkexec_apt(window, qapp):
    win = window()
    assert checked(win, qapp).status == "available"
    assert win.update_banner.isVisible()
    assert win.update_banner.label.text() == "Packet Radio 0.2.0 is available"
    QTest.mouseClick(win.update_banner.btn_primary, Qt.MouseButton.LeftButton)
    assert wait_for(lambda: win.updates.phase == "installed", 10, app=qapp), win.updates.message
    (argv,) = win.ran
    assert argv[:4] == ["/usr/bin/pkexec", "apt-get", "install", "-y"]
    assert argv[4].endswith(DEB.format(v="0.2.0"))
    assert win.update_banner.btn_primary.text() == "Restart now"


def test_nothing_installs_while_transmitting_or_with_a_station_connected(window, qapp, monkeypatch):
    win = window()
    checked(win, qapp)
    real = win.ctl.status
    monkeypatch.setattr(win.ctl, "status", lambda: dict(real(), transmitting=True))
    win.tick()
    assert not win.update_banner.btn_primary.isEnabled()
    assert "transmitting" in win.update_banner.btn_primary.toolTip()
    win.updates.install()
    assert win.updates.phase == "offer" and win.ran == []
    monkeypatch.setattr(win.ctl, "status", real)

    class Connected:
        connected = True
    win.ctl.sessions[99] = Connected()
    win.tick()
    assert "station is connected" in win.update_banner.btn_primary.toolTip()
    win.updates.install()
    assert win.updates.phase == "offer" and win.ran == []
    del win.ctl.sessions[99]
    win.tick()
    assert win.update_banner.btn_primary.isEnabled()


def test_a_copy_not_installed_from_an_installer_never_checks_by_itself(window, qapp, site):
    win = window(kind="")
    assert win.updates.timer is None
    wait_for(lambda: False, 0.3, app=qapp)
    assert site["server"].requests == []
    # Help › Check for updates… still checks, and says why it cannot install.
    dlg = win.updates.open_dialog()
    assert wait_for(lambda: not win.updates.checking, 10, app=qapp)
    assert "not installed from an installer" in dlg.status.text()
    assert not dlg.btn_install.isVisible()
    dlg.close()


def test_with_the_setting_off_no_automatic_check_happens(window, qapp, site):
    win = window()
    win.settings.internet.check_updates = False
    win.updates.tick()
    assert not win.updates.checking and site["server"].requests == []
    win.settings.internet.check_updates = True
    win.updates.tick()
    assert wait_for(lambda: win.updates.result is not None, 10, app=qapp)


def test_offline_shows_no_banner_and_help_says_why(window, qapp):
    win = window(feed_url="http://127.0.0.1:9/update.json")
    assert checked(win, qapp).status == "offline"
    assert not win.update_banner.isVisible()
    dlg = win.updates.open_dialog()
    assert wait_for(lambda: not win.updates.checking, 10, app=qapp)
    assert dlg.status.text().startswith("Could not check at")
    dlg.close()


def test_windows_checks_the_signer_and_runs_the_installer_only_after_exit(window, qapp):
    win = window(plat="windows", kind="exe", authenticode=lambda p: GOOD_SIGNER)
    checked(win, qapp)
    win.updates.install()
    assert wait_for(lambda: win.after_exit, 10, app=qapp), win.updates.message
    (argv,) = win.after_exit
    assert argv[0].endswith(EXE.format(v="0.2.0")) and "/RELAUNCH=1" in argv
    assert win.ran == [] and not win.isVisible()          # closed the normal way, the installer not yet run


def test_windows_refuses_an_installer_signed_by_anyone_else(window, qapp):
    win = window(plat="windows", kind="exe", authenticode=lambda p: ("Valid", "CN=Mallory, O=Mallory"))
    checked(win, qapp)
    win.updates.install()
    assert wait_for(lambda: win.updates.phase == "failed", 10, app=qapp)
    assert "not 360 Technology" in win.update_banner.label.text()
    assert win.after_exit == [] and win.isVisible()
