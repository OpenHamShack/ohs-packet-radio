"""tools/dd-chatter-test.py in its dry run: the whole supervised sequence
against FakeIcom - DD at minimum power, the pretend operator's TRANSMIT
presses, the polls, the chatter, the restore - with no packet leaving the
computer and the radio put back exactly."""
from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "dd-chatter-test.py"


def _tool():
    spec = importlib.util.spec_from_file_location("dd_chatter_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_dry_run_finds_the_chatter_and_puts_the_radio_back(tmp_path, monkeypatch):
    sent = []
    real_sendto = socket.socket.sendto
    monkeypatch.setattr(socket.socket, "sendto",
                        lambda self, data, *a: sent.append(a[-1]) or real_sendto(self, data, *a))
    out = tmp_path / "result.json"
    result = _tool().main(["--dry-run", "--yes", "--seconds", "3", "--journal", str(tmp_path / "j.json"),
                           "--out", str(out)])
    assert {ln["key"]: ln["status"] for ln in result["setup"]} == {
        "frequency": "set", "mode": "set", "rps": "set", "rf_power": "set", "dd_packet_output": "report"}
    assert result["answered"] == result["polls"] > 5
    assert result["on_air_before_s"] == 0, "TX INHIBIT off, but nothing on the LAN yet: no transmission"
    assert result["on_air_during_chatter_s"] > 0 and "WENT TO AIR" in result["verdict"]
    assert {ln["status"] for ln in result["restored"]} == {"restored"}
    assert result["radio_after"]["freq"] == 145_825_000 and result["radio_after"]["mode"] == 0x05
    assert json.loads(out.read_text())["verdict"] == result["verdict"]
    assert not (tmp_path / "j.json").exists(), "nothing left journalled"
    assert all(addr[0] == "127.0.0.1" for addr in sent if isinstance(addr, tuple)), \
        "the dry run's only traffic is the fake radio's, on loopback"


def test_a_real_run_is_refused_without_a_person_or_off_the_channels(tmp_path):
    tool = _tool()
    with pytest.raises(SystemExit):
        tool.main(["--host", "192.0.2.1", "--user", "op", "--channel", "1298500000"])
    with pytest.raises(SystemExit):
        tool.main(["--seconds", "1"])
    args = type("A", (), {"yes": True})()
    with pytest.raises(SystemExit):
        tool._prompt("press it", args, None)
