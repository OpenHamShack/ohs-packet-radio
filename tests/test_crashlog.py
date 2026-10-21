"""A crash leaves a traceback in the program's own log folder, where the
next diagnostics bundle finds it. Each case runs in its own interpreter:
the hooks are process-wide, and one case ends in a segfault on purpose."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def run(tmp_path, body: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, SHACK_PACKET_DATA=str(tmp_path / "data"), SHACK_PACKET_CONF=str(tmp_path / "conf"),
               QT_QPA_PLATFORM="offscreen")
    code = textwrap.dedent("""
        from shack_packet_app import applog
        applog.install(to_file=True)
        applog.install_crash_hooks()
    """) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)


def test_an_exception_in_a_qt_slot_is_logged_and_the_app_carries_on(tmp_path):
    # Without the hook PyQt6 aborts on this (0.1.0's Save diagnostics).
    p = run(tmp_path, """
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        crashed = []
        applog.on_crash(lambda: crashed.append(1))
        app = QApplication([])
        def slot():
            raise RuntimeError("boom-in-slot")
        QTimer.singleShot(0, slot)
        QTimer.singleShot(100, app.quit)
        app.exec()
        print("survived", len(crashed))
    """)
    assert p.returncode == 0, p.stderr
    assert "survived 1" in p.stdout
    log = (tmp_path / "data" / "logs" / "shack-packet.log").read_text()
    assert "CRITICAL crash: unhandled exception" in log and "RuntimeError: boom-in-slot" in log
    assert "Traceback" in log


def test_an_exception_in_a_worker_thread_is_logged(tmp_path):
    p = run(tmp_path, """
        import threading
        def work():
            raise ValueError("boom-in-thread")
        t = threading.Thread(target=work, name="rig-loop"); t.start(); t.join()
    """)
    assert p.returncode == 0, p.stderr
    log = (tmp_path / "data" / "logs" / "shack-packet.log").read_text()
    assert "unhandled exception in thread rig-loop" in log and "ValueError: boom-in-thread" in log


def test_a_hard_crash_leaves_its_stacks_in_fatal_log(tmp_path):
    p = run(tmp_path, """
        import faulthandler
        faulthandler._sigsegv()
    """)
    assert p.returncode != 0
    fatal = (tmp_path / "data" / "logs" / "fatal.log").read_text()
    assert "Fatal Python error" in fatal and "Segmentation fault" in fatal
