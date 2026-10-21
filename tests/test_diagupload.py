"""The opt-in diagnostics upload (diagupload.py, ui/diagsend.py) on a real
window on the simulator: consent, never while transmitting or metered,
what is sent (scrubbed, and the shape the endpoint accepts), faults from
the radio link's counters and from an earlier run's hard crash, and the
endpoint refusing the app as it does today. The endpoint is a stand-in on
loopback that applies openhamshack-os server/diag/lib.php's acceptance
rules (ohs_upload, feature/diag-upload fa54127) - with "packet" in its
app list, as it will be once its owner adds it (PLAN-0.2 D8). Nothing
reaches the real site."""
from __future__ import annotations

import collections
import gzip
import http.server
import json
import re
import threading
import urllib.parse
from pathlib import Path

import pytest

from conftest import station_settings, wait_for
from shack_packet_app import applog, diagupload, host
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.window import MainWindow

PASSWORD = "hunter2-lan-pass"
# lib.php: OHS_VERSION, and the install id's pattern.
VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}(\+[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$")


class Endpoint:
    """lib.php's checks, in order, and a SHACK- code for a report it accepts."""

    def __init__(self, apps=("nb", "ham", "packet")):
        ep = self
        self.apps = apps
        self.received: list[tuple[dict, dict]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                status, answer = ep.judge(q, body)
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/diag/upload.php"

    def judge(self, q: dict, body: bytes) -> tuple[int, dict]:
        app, version, install = q.get("app", ""), q.get("version", ""), q.get("install", "")
        if app not in self.apps:
            return 400, {"error": "unknown app"}
        if len(version) > 40 or not VERSION_RE.match(version):
            return 400, {"error": "bad version"}
        if not re.match(r"^[0-9a-f]{32}$", install):
            return 400, {"error": "bad install id"}
        if len(body) > 2097152:
            return 413, {"error": "more than 2 MB"}
        if body[:2] != b"\x1f\x8b":
            return 400, {"error": "not gzip"}
        doc = json.loads(gzip.decompress(body))
        if (doc.get("schema") != 1 or doc.get("app") != app or doc.get("version") != version
                or doc.get("install") != install):
            return 400, {"error": "not a diagnostics report"}
        self.received.append((q, doc))
        return 200, {"code": f"SHACK-7KQ{len(self.received)}"}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def endpoint():
    ep = Endpoint()
    yield ep
    ep.close()


@pytest.fixture
def window(qapp, endpoint):
    made = []

    def make(consent=True, version="0.2.0+beta1", metered=False, url=None, **set_kw):
        s = station_settings(**set_kw)
        s.radio.username = "brian"
        s.internet.diag_upload = consent
        s.internet.diag_consent = diagupload.consent_stamp() if consent else ""
        secrets = {"lan_password": PASSWORD}
        ctl = Controller(s, secrets=secrets, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40, 50)))
        clock = Clock()
        win = MainWindow(s, secrets, controller=ctl, persist=False,
                         diagsend=dict(url=url or endpoint.url, clock=clock, metered=lambda: metered,
                                       version=version, timeout=5.0))
        win.clock = clock
        # What start() does, without its timer: the test drives tick().
        win.diagsend.watcher.install()
        win.show()
        win.start(first_run_check=False)
        made.append(win)
        return win
    yield make
    for w in made:
        w.diagsend.stop()
        w.close()
    qapp.processEvents()


def sent(win, qapp, n=1):
    assert wait_for(lambda: not win.diagsend.sending, 10, app=qapp)


def test_the_switch_alone_is_not_consent_and_settings_writes_the_stamp(window, qapp):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    win = window(consent=False)
    win.settings.internet.diag_upload = True                     # a hand-edited file
    assert not win.diagsend.enabled()
    win.settings.internet.diag_consent = "0@2026-10-01T00:00:00Z"  # agreed to an older text
    assert not win.diagsend.enabled()
    dlg = SettingsDialog(win.settings, win, "internet")
    assert not dlg.diag_upload.isChecked()                       # shown as off: no consent to the current text
    dlg.diag_upload.setChecked(True)
    s = dlg.result_settings()
    assert s.internet.diag_upload and diagupload.consented(s.internet.diag_consent)


def test_a_manual_report_is_scrubbed_and_shaped_as_the_endpoint_wants(window, qapp, endpoint):
    import logging
    win = window(consent=False)                                  # Send now works whatever the switch says
    # The program log the app installs at start (__main__), here too.
    logging.getLogger().addHandler(applog.ring)
    try:
        logging.getLogger("test").warning("connecting with password %s from %s", PASSWORD, Path.home() / "x")
        assert win.diagsend.send("manual")
        sent(win, qapp)
    finally:
        logging.getLogger().removeHandler(applog.ring)
    assert win.diagsend.last_error == ""
    (q, doc), = endpoint.received
    assert q["app"] == "packet" and q["version"] == "0.2.0+beta1" and re.match(r"^[0-9a-f]{32}$", q["install"])
    assert doc["kind"] == "manual" and doc["config"]["station"]["callsign"] == "GM4BRI"
    whole = json.dumps(doc)
    assert PASSWORD not in whole and str(Path.home()) not in whole
    assert "<redacted>" in doc["log"]
    assert "SHACK-7KQ1" in win.diagsend.status_text()


def test_nothing_is_sent_while_transmitting_or_on_a_metered_link(window, qapp, endpoint, monkeypatch):
    win = window()
    real = win.ctl.status
    monkeypatch.setattr(win.ctl, "status", lambda: dict(real(), transmitting=True))
    assert win.diagsend.send("manual") is False
    assert "transmitting" in win.diagsend.status_text()
    diagupload.notable("radio-lost", "test")
    win.clock.t += 3600
    assert win.diagsend.tick() == ""                             # put off, not dropped
    monkeypatch.setattr(win.ctl, "status", real)
    win.diagsend.metered = lambda: True
    assert win.diagsend.tick() == ""
    win.diagsend.metered = lambda: False
    assert win.diagsend.tick() == "fault"
    sent(win, qapp)
    assert [doc["trigger"] for _q, doc in endpoint.received] == ["radio-lost"]


def test_a_watchdog_counted_by_the_radio_link_becomes_a_fault_report(window, qapp, endpoint):
    win = window()
    assert wait_for(lambda: win.ctl.link is not None, 5, app=qapp)
    # The simulator keeps no counters; a real link's (radios.base) are a
    # Counter like this one, which is all the uploader reads.
    win.ctl.link.stats = collections.Counter()
    win.diagsend.tick()                                          # the counters' first look
    win.ctl.link.stats["hard-watchdog"] += 1
    win.ctl.link.stats["rx_dropped"] += 3                        # a few: not worth a report
    assert win.diagsend.tick() == ""                             # heard; waits for the lines after it
    win.clock.t += diagupload.SETTLE_S
    assert win.diagsend.tick() == "fault"
    sent(win, qapp)
    (_q, doc), = endpoint.received
    assert doc["trigger"] == "watchdog" and [t["trigger"] for t in doc["triggers"]] == ["watchdog"]
    # One fault report per half hour, however many follow.
    win.ctl.link.stats["unkey-unconfirmed"] += 1
    win.diagsend.tick()
    win.clock.t += diagupload.SETTLE_S
    assert win.diagsend.tick() == ""


def test_with_the_switch_off_faults_are_not_saved_up(window, qapp, endpoint):
    win = window(consent=False)
    diagupload.notable("crash", "test")
    win.clock.t += 3600
    assert win.diagsend.tick() == "" and win.diagsend.plan.pending == []
    assert endpoint.received == []


def test_an_earlier_runs_hard_crash_is_reported_and_a_plain_start_is_not(window, qapp, endpoint):
    fatal = host.log_dir() / applog.FATAL_NAME
    fatal.parent.mkdir(parents=True, exist_ok=True)
    fatal.write_text("--- started 2026-10-18 10:00:00\n")
    win = window()
    win.diagsend._check_fatal_file()                              # the first look takes the size
    with open(fatal, "a") as f:
        f.write("--- started 2026-10-18 11:00:00\n")
    win.diagsend._check_fatal_file()
    assert win.diagsend.plan.pending == []
    with open(fatal, "a") as f:
        f.write("Fatal Python error: Segmentation fault\n\nCurrent thread 0x00007f (most recent call first):\n")
    win.diagsend._check_fatal_file()
    assert [e[0] for e in win.diagsend.plan.pending] == ["crash"]


def test_todays_endpoint_refusing_packet_is_a_quiet_retry(window, qapp):
    ep = Endpoint(apps=("nb", "ham"))                            # lib.php:41 as it is on 18/10/2026
    try:
        win = window(url=ep.url)
        diagupload.notable("radio-lost", "test")
        win.diagsend.tick()
        win.clock.t += diagupload.SETTLE_S
        assert win.diagsend.tick() == "fault"
        sent(win, qapp)
        assert "unknown app" in win.diagsend.last_error
        assert win.diagsend.state.get("retryAt") > win.clock.t   # tried again later
        assert [e[0] for e in win.diagsend.plan.pending] == ["radio-lost"]
    finally:
        ep.close()


def test_a_window_the_program_did_not_start_never_uploads(qapp):
    s = station_settings()
    win = MainWindow(s, {}, controller=Controller(s), persist=False)
    win.start(first_run_check=False)
    try:
        assert win.diagsend.timer is None and win.updates.timer is None
    finally:
        win.close()
    assert True                            # only __main__.main() starts it
