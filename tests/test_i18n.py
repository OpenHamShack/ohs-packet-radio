"""Translation (OpenHamShack I18N.md section 5): the string check passes,
the committed .qm is what its .ts compiles to, English plurals come out
right in the window, a second catalog loads (the en_XA pseudo-locale, on
the live window), and an unknown language falls back to English."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import station_settings, wait_for

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("i18n_tool", REPO / "tools" / "i18n.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "tools" / "i18n.py"), *args], capture_output=True, text=True,
                          timeout=300, cwd=REPO)


def test_the_string_check_passes():
    r = run_tool("check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_string_check_ignores_agent_worktrees_inside_the_repo():
    """A worktree under .claude/ is another checkout of this repo, with its
    own strings: the check must judge this one only (it failed on the main
    checkout while a locked agent worktree sat there)."""
    import shutil
    import uuid
    probe = REPO / ".claude" / "worktrees" / f"probe-{uuid.uuid4().hex[:8]}"
    made_claude = not (REPO / ".claude").exists()
    try:
        (probe / "src").mkdir(parents=True)
        (probe / "src" / "probe.py").write_text('from PyQt6.QtWidgets import QLabel\n'
                                                'QLabel("A sentence nobody has translated")\n')
        r = run_tool("check")
        assert r.returncode == 0 and "probe" not in r.stdout, r.stdout + r.stderr
    finally:
        shutil.rmtree(REPO / ".claude" if made_claude else probe, ignore_errors=True)


def test_the_committed_catalog_is_what_its_ts_compiles_to():
    r = run_tool("compile", "--check")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.fixture
def language(qapp):
    """Install a language for one test, and English again afterwards."""
    from shack_packet_app.ui import i18n
    yield lambda tag: i18n.install(qapp, tag)
    i18n.install(qapp, "en")


def test_english_plurals_in_the_setup_check(qapp, window_with_wizard):
    win, wiz = window_with_wizard
    page = wiz.receive
    page.heard = lambda: [_Row()]
    page.tick()
    assert page.count.text() == "1 frame decoded"
    page.heard = lambda: [_Row(), _Row(), _Row()]
    page.tick()
    assert page.count.text() == "3 frames decoded"


def test_a_second_catalog_loads_and_english_fills_its_gaps(qapp, tmp_path, monkeypatch, language):
    from PyQt6.QtCore import QCoreApplication
    from shack_packet_app.ui import i18n, texts
    from shack_packet_app.ui.window import MainWindow
    tool.write_pseudo(tmp_path)
    (tmp_path / "shack_packet_en.qm").write_bytes((i18n.CATALOG_DIR / "shack_packet_en.qm").read_bytes())
    monkeypatch.setattr(i18n, "CATALOG_DIR", tmp_path)
    assert language("en_XA") == "en_XA"
    win = MainWindow(station_settings(), {}, persist=False)
    try:
        # A label made by the window, and a sentence made on demand.
        assert win.nav.item(0).text() == tool.pseudo_text("Monitor")
        assert texts.reason("expired") == tool.pseudo_text("The transmit arming expired.")
        # English is installed beneath it, so a plural the pseudo catalog
        # has comes out in English's two forms, pseudo-translated.
        assert QCoreApplication.translate("ReceivePage", "%n frame(s) decoded", "", 2) == \
            tool.pseudo_text("%n frames decoded").replace("%n", "2")
    finally:
        win.close()
    assert i18n.choose(["xx_YY", "fr"]) == "en"                    # unknown: English


class _Row:
    seq, direction, text, time = 1, "rx", "G4SIM>APRS:>hello", 0.0


@pytest.fixture
def window_with_wizard(qapp):
    from shack_packet_app.controller import Controller
    from shack_packet_app.simband import SimBand
    from shack_packet_app.ui.window import MainWindow
    s = station_settings()
    ctl = Controller(s, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0)))
    win = MainWindow(s, {}, controller=ctl, persist=False)
    win.start(first_run_check=False)
    win.open_setup()
    assert wait_for(lambda: win.wizard is not None, 2, app=qapp)
    yield win, win.wizard
    if win.wizard is not None:
        win.wizard.reject()
    win.close()
