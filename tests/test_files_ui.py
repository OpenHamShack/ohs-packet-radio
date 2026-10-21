"""Files in the window, offscreen, on the simulator: a picture dropped on a
chat tab goes as the size chosen (with its airtime shown first), an offer
from G4SIMF is asked about and lands in the folder with a thumbnail, a
declined one writes nothing, and the Files page's settings and 7PLUS
tools work. Nothing here touches a radio."""
from __future__ import annotations

import random
from pathlib import Path

import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QColor, QDropEvent, QImage
from PyQt6.QtTest import QTest

from conftest import station_settings, wait_for
from shack_packet_app import settings as settings_mod
from shack_packet_app import simfiles
from shack_packet_app.controller import Controller
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.filesend import OfferPrompt, SendFileDialog
from shack_packet_app.ui.window import PAGES, MainWindow


def quiet_band():
    return SimBand(55.95, -3.2, first_beacon_s=(400.0, 500.0), beacon_every_s=600)


@pytest.fixture
def window(qapp):
    made = []

    def make(s=None):
        s = s if s is not None else station_settings()
        ctl = Controller(s, secrets={}, band_factory=quiet_band)
        win = MainWindow(s, {}, controller=ctl, persist=True)
        win.show()
        win.start(first_run_check=False)
        assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
        made.append(win)
        return win
    yield make
    # The prompts and notes are real, non-blocking message boxes (the
    # other window tests replace QMessageBox's static calls instead).
    # Left open past the test they were deleted at interpreter exit after
    # the window that owns them, which crashed the process: closed here.
    from PyQt6.QtWidgets import QApplication, QMessageBox
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMessageBox):
            w.close()
    for w in made:
        w.close()
        w.deleteLater()
    qapp.processEvents()


def _session(win, qapp):
    """Connected to G4SIMF from the Connect / Chat page, armed."""
    win.ctl.arm(5)
    win.nav.setCurrentRow(PAGES.index("chat"))
    page = win.pages["chat"]
    page.call.setText(simfiles.CALL)
    QTest.mouseClick(page.go, Qt.MouseButton.LeftButton)
    view = next(iter(page.views.values()))
    assert wait_for(lambda: (win.tick(), view.sess.connected)[1], 30, app=qapp)
    return page, view


def _photo(path: Path) -> Path:
    """A 3000 x 2000 "photo": smooth, so its small copies are small and the
    simulator (1200 baud at wall-clock speed) sends one in seconds."""
    from PyQt6.QtGui import QLinearGradient, QPainter
    image = QImage(3000, 2000, QImage.Format.Format_RGB32)
    p = QPainter(image)
    g = QLinearGradient(0, 0, 3000, 2000)
    g.setColorAt(0, QColor(30, 90, 160))
    g.setColorAt(1, QColor(220, 200, 120))
    p.fillRect(image.rect(), g)
    p.fillRect(1000, 600, 900, 700, QColor(200, 40, 40))
    p.end()
    assert image.save(str(path), "JPEG", 95)
    return path


def _drop(win, widget, path: Path):
    """A file dropped on `widget`. Qt delivers no drop event without a real
    drag under way (sendEvent of a QDropEvent reaches no handler or
    filter, checked 20/10/2026), so the window's drop filter is handed the
    events Qt would hand it: enter, then drop."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    accepted = []
    for kind in (QDropEvent.Type.DragEnter, QDropEvent.Type.Drop):
        if kind == QDropEvent.Type.DragEnter:
            from PyQt6.QtGui import QDragEnterEvent
            e = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier)
        else:
            e = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)
        accepted.append(win.files_ui.drops.eventFilter(widget, e))
    return accepted


def test_a_picture_dropped_on_a_chat_tab_goes_at_the_size_chosen(window, qapp, tmp_path):
    win = window()
    page, view = _session(win, qapp)
    shown = []
    bar = win.statusBar()
    real = bar.showMessage
    bar.showMessage = lambda text, ms=0: (shown.append(text), real(text, ms))
    photo = _photo(tmp_path / "shack photo.jpg")
    assert _drop(win, view.line, photo) == [True, True]          # on the typing line, even
    dlg = win.files_ui.dialog
    assert isinstance(dlg, SendFileDialog) and dlg.session_id == view.sess.id and dlg.is_picture
    keys = [dlg.preset.itemData(i) for i in range(dlg.preset.count())]
    assert keys == ["original", "large", "medium", "small", "tiny"]
    assert dlg.preset.currentData() == "medium"                     # the default
    labels = [dlg.preset.itemText(i) for i in range(dlg.preset.count())]
    assert "3000 × 2000" in labels[0] and "1024 × 682" in labels[2] and labels[0].endswith("at AFSK 1200")
    sizes = [n for _, n, _ in dlg.options]
    assert sizes[4] < sizes[3] < sizes[2] < sizes[1]
    dlg.preset.setCurrentIndex(keys.index("tiny"))
    dlg.send_btn.click()
    t = dlg.transfer
    assert t is not None and t.name == "shack photo.jpg" and t.size == sizes[4] and not t.compress
    assert wait_for(lambda: (win.tick(), t.state == "done")[1], 120, app=qapp), (t.state, t.reason)
    assert win.settings.files.picture_preset == "tiny"
    assert f"Sent shack photo.jpg to {simfiles.CALL}." in shown
    win.nav.setCurrentRow(PAGES.index("files"))
    files = win.pages["files"]
    files.refresh(force=True)
    assert files.table.rowCount() == 1
    row = [files.table.item(0, j).text() for j in range(files.table.columnCount())]
    assert row[1] == "↑" and row[2] == simfiles.CALL and row[4] == "100 %" and row[7] == "Done"
    # Its details say how long it took, with the table for it at every mode.
    files.table.selectRow(0)
    assert "Took " in files.details.text()
    assert files.estimates.table.isVisibleTo(files) and files.estimates.table.rowCount() == 9
    assert [r.mode for r in files.estimates.rows if r.current] == ["afsk1200"]


def _table(dlg):
    t = dlg.estimates.table
    return [[t.item(i, j) for j in range(t.columnCount())] for i in range(t.rowCount())]


def test_the_send_dialog_shows_the_time_at_every_mode_for_the_size_chosen(window, qapp, tmp_path):
    from shack_packet_app import airtime
    win = window()
    page, view = _session(win, qapp)
    # A link test with G4SIMF at 1200 that ran twice as long as the model.
    params, timing = airtime.link_params(win.ctl.settings, "afsk1200")
    win.ctl.files.measured.add(airtime.Measurement(
        peer=simfiles.CALL, mode="afsk1200", nbytes=5000, kind="linktest", protocol="raw",
        seconds=2 * airtime.transfer_s(5000, "afsk1200", params, timing, acks="internet", handshake=False,
                                       protocol="raw")))
    photo = _photo(tmp_path / "shack photo.jpg")
    dlg = win.files_ui.send_file(str(photo), prefer_front=True)
    rows = _table(dlg)
    assert [r[0].text() for r in rows] == ["AFSK 1200", "G3RUH 9600", "G3RUH 19 200", "G3RUH 38 400",
                                           "GFSK 76 800", "GFSK 153 600", "4-FSK 38 400 (76.8 kbit/s)",
                                           "4-FSK 76 800 (153.6 kbit/s)", "23 cm DD 128 kbit/s"]
    first = rows[0]
    assert first[0].font().bold() and first[0].flags() & Qt.ItemFlag.ItemIsEnabled
    assert first[3].text().startswith(f"this station's mode; measured with {simfiles.CALL}: about ")
    assert "(link test" in first[3].text()
    for r in rows[1:]:
        assert not r[0].flags() & Qt.ItemFlag.ItemIsEnabled and not r[0].font().bold()
        assert r[3].text().startswith("not available on the simulator")
    assert rows[8][3].text().endswith("estimate, unmeasured")
    assert all(r[1].text().startswith("about ") and r[2].text().startswith("about ") for r in rows)
    # The size chosen is what the table reckons with: tiny is quicker than
    # original, and the list's own line says the measured time.
    medium = dlg.estimates.row("afsk1200").best_s
    dlg.preset.setCurrentIndex(dlg.preset.findData("tiny"))
    tiny = dlg.estimates.row("afsk1200").best_s
    dlg.preset.setCurrentIndex(dlg.preset.findData("original"))
    original = dlg.estimates.row("afsk1200").best_s
    assert tiny < medium < original
    row = dlg.estimates.row("afsk1200")
    assert row.best_s == pytest.approx(2 * row.good_s, rel=0.1)
    dlg.reject()


def test_the_estimate_greys_what_an_ic9700_cannot_send_and_counts_compression(window, qapp, tmp_path):
    win = window()
    win.ctl.settings.radio.kind = "ic-9700"         # read by the estimate only; nothing is opened
    log = tmp_path / "log.txt"
    log.write_bytes(b"de GM4BRI the packet station is QRV on 70cm, readable 59, thanks\n" * 2000)
    dlg = SendFileDialog(win, 0, str(log))
    rows = _table(dlg)
    assert [bool(r[0].flags() & Qt.ItemFlag.ItemIsEnabled) for r in rows] == [True] + [False] * 8
    assert rows[1][3].text() == "not available on IC-9700"
    assert rows[8][3].text().startswith("not available on IC-9700 until 23 cm DD is turned on")
    assert dlg.on_air.text().endswith("after compression")
    compressed = dlg.estimates.row("afsk1200").good_s
    dlg.protocol.setCurrentIndex(dlg.protocol.findData("yapp"))         # YAPP: no compression
    assert not dlg.on_air.text().endswith("after compression")
    assert dlg.estimates.row("afsk1200").good_s > 3 * compressed
    win.ctl.settings.radio.kind = "ic-9700-dd"
    dlg2 = SendFileDialog(win, 0, str(log))
    assert [r.mode for r in dlg2.estimates.rows if r.available and r.current] == ["dd128k"]
    assert _table(dlg2)[0][3].text() == "not available while 23 cm DD is on"
    win.ctl.settings.radio.kind = "simulator"
    dlg.reject()
    dlg2.reject()


@pytest.mark.parametrize("seconds, text", [(42, "about 42 s"), (130, "about 2 min 10 s"), (121, "about 2 min"),
                                           (1500, "about 25 min"), (4800, "about 1 h 20 min")])
def test_durations_read_as_estimates(qapp, seconds, text):
    from shack_packet_app.ui.filesend import duration_text
    assert duration_text(seconds) == text


def test_an_offer_is_asked_about_and_lands_in_the_folder_with_a_thumbnail(window, qapp, tmp_path):
    win = window(station_settings(files__folder=str(tmp_path / "inbox")))
    page, view = _session(win, qapp)
    win.ctl.send(view.sess.id, "PIC")
    assert wait_for(lambda: (win.tick(), win.files_ui.prompts)[1], 30, app=qapp)
    prompt = win.files_ui.prompts[-1]
    assert isinstance(prompt, OfferPrompt)
    assert "G4SIMF offers you g4sim-test-card.png" in prompt.text()
    assert "not opened" in prompt.text() and str(tmp_path / "inbox") in prompt.text()
    assert not (tmp_path / "inbox").exists()
    prompt.accept_btn.click()
    t = prompt.t
    assert wait_for(lambda: (win.tick(), t.state == "done")[1], 60, app=qapp), (t.state, t.reason)
    assert (tmp_path / "inbox" / "g4sim-test-card.png").read_bytes() == simfiles.test_card()
    win.nav.setCurrentRow(PAGES.index("files"))
    files = win.pages["files"]
    files.refresh(force=True)
    files.table.selectRow(0)
    assert files.preview.pixmap() is not None and not files.preview.pixmap().isNull()
    assert "Saved as" in files.details.text()


def test_a_declined_offer_writes_nothing(window, qapp, tmp_path):
    win = window(station_settings(files__folder=str(tmp_path / "inbox")))
    page, view = _session(win, qapp)
    win.ctl.send(view.sess.id, "TXT")
    assert wait_for(lambda: (win.tick(), win.files_ui.prompts)[1], 30, app=qapp)
    prompt = win.files_ui.prompts[-1]
    prompt.decline_btn.click()
    assert prompt.t.state == "declined"
    wait_for(lambda: (win.tick(), False)[1], 1.5, app=qapp)
    assert not (tmp_path / "inbox").exists() or not list((tmp_path / "inbox").glob("*.*"))


def test_send_file_with_no_session_says_connect_first(window, qapp, tmp_path):
    win = window()
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert win.files_ui.send_file(str(f)) is None
    assert "Connect to a station first" in win.last_note


def test_the_files_pages_settings_are_saved(window, qapp, tmp_path):
    win = window()
    files = win.pages["files"]
    files.max_mb.setValue(7)
    files.compress.setChecked(False)
    files.protocol.setCurrentIndex(files.protocol.findData("yapp"))
    files.protocol.activated.emit(files.protocol.currentIndex())
    files.set_folder(str(tmp_path / "elsewhere"))
    saved = settings_mod.load()
    assert (saved.files.max_mb, saved.files.compress, saved.files.protocol) == (7, False, "yapp")
    assert saved.files.folder == str(tmp_path / "elsewhere")
    assert win.ctl.files.service.policy.max_size == 7 * 1024 * 1024
    assert win.ctl.files.service.inbox.folder == tmp_path / "elsewhere"


def test_sevenplus_from_the_files_page(window, qapp, tmp_path):
    win = window(station_settings(files__folder=str(tmp_path / "inbox")))
    files = win.pages["files"]
    files.lines.setValue(20)
    src = tmp_path / "log.adi"
    src.write_bytes(random.Random(3).randbytes(4000))
    parts = files._encode7(str(src), str(tmp_path / "parts"))
    assert len(parts) == 4 and "4 parts" in files.last_message
    files._decode7_files([str(p) for p in parts[:3]])
    assert "not complete yet" in files.last_message and "parts 4" in files.last_message
    pasted = "\r".join(p.read_bytes().decode("latin-1") for p in parts)
    files._decode7_paste(pasted)
    assert "Saved log.adi" in files.last_message
    assert (tmp_path / "inbox" / "log.adi").read_bytes() == src.read_bytes()


def test_a_new_session_tab_is_watched_for_drops_once_it_is_whole(window, qapp):
    # The drop filter sits on the chat page's widgets, not the whole
    # application (ui/filesend.py _ChatDrops says why); a tab opened later
    # must still take a drop, so its widgets are watched when polished.
    win = window()
    watched = []
    real = win.files_ui.drops.watch
    win.files_ui.drops.watch = lambda w: (watched.append(w), real(w))
    page, view = _session(win, qapp)
    qapp.processEvents()
    assert any(w is view or w.isAncestorOf(view) or view.isAncestorOf(w) for w in watched)


def test_the_chat_page_has_the_send_button_and_takes_drops(window, qapp):
    win = window()
    chat = win.pages["chat"]
    assert chat.tabs.cornerWidget(Qt.Corner.TopRightCorner) is win.files_ui.chat_button
    assert chat.acceptDrops()
    # A drop somewhere else in the window is not the chat page's.
    f = Path(__file__)
    assert _drop(win, win.pages["monitor"], f) == [False, False] and win.files_ui.dialog is None
