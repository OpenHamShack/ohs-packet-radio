"""Shared fixtures. Every test gets its own settings and data folders (never
the developer's own), Qt runs offscreen, and no test opens a real radio,
SDR or sound device: radios are the simulator or shack_rig's fakes, sound
devices shack_rig.sound.FakeBackend, the SDR the synthetic band."""
from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SHACK_LOCALE", "en")
# No window looks for a real control pad: one on the developer's desk must
# not be taken over by a test (tests that want a pad give it a fake one).
os.environ["SHACK_PACKET_NO_PAD"] = "1"


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    monkeypatch.setenv("SHACK_PACKET_CONF", str(tmp_path / "conf"))
    monkeypatch.setenv("SHACK_PACKET_DATA", str(tmp_path / "data"))
    # The folder every OpenHamShack app shares (the pad's lock file).
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return tmp_path


@pytest.fixture(scope="session")
def qapp():
    from shack_packet_app.ui import qtapp
    app = qtapp.make_app([sys.argv[0]])
    yield app


def wait_for(cond, timeout: float = 10.0, step: float = 0.02, app=None) -> bool:
    """Until cond() or the timeout, processing Qt events if an app is given."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if app is not None:
            app.processEvents()
        if cond():
            return True
        time.sleep(step)
    if app is not None:
        app.processEvents()
    return bool(cond())


def station_settings(**kw):
    from shack_packet_app.settings import Settings
    s = Settings()
    s.station.callsign = kw.pop("callsign", "GM4BRI")
    s.station.locator = kw.pop("locator", "IO85JW")
    s.station.licence_class = kw.pop("licence_class", "full")
    s.tx.set_up = kw.pop("set_up", True)
    s.setup_done = kw.pop("setup_done", True)
    for k, v in kw.items():
        section, _, name = k.partition("__")
        setattr(getattr(s, section), name, v)
    return s
