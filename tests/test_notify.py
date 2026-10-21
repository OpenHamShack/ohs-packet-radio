"""Notifications (0.2.3): which arrivals make one (notify.py), the sound and
its fall-backs with every outside tool faked (notifysound.py), and the
window on the simulator with a fake tray and player (ui/notifier.py): a
message, a chat line and mail left in the mailbox each notify once, with
an unread count in the sidebar until the page is opened, and nothing when
the operator is already looking."""
from __future__ import annotations

import base64
import threading
import wave

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from conftest import station_settings, wait_for
from shack_packet_app import notify, notifysound
from shack_packet_app.controller import AppEvent, Controller
from shack_packet_app.simband import SimBand
from shack_packet_app.ui.window import PAGES, MainWindow

# -- what notifies -----------------------------------------------------------------------


def test_a_message_to_us_notifies_and_our_own_heard_back_does_not():
    e = AppEvent("aprs", "message", {"peer": "G4SIM", "text": "Got it:   hello\nthere", "addressee": "GM4BRI-1"})
    assert notify.notice_for(e, "GM4BRI-1") == notify.Notice("messages", "G4SIM", "Got it: hello there")
    own = AppEvent("aprs", "message", {"peer": "GM4BRI-1", "text": "to myself"})
    assert notify.notice_for(own, "GM4BRI-1") is None
    # Another SSID of ours is another station.
    assert notify.notice_for(AppEvent("aprs", "message", {"peer": "GM4BRI-7", "text": "x"}), "GM4BRI-1")
    assert notify.notice_for(AppEvent("aprs", "message", {"peer": "GM4BRI-0", "text": "x"}), "GM4BRI") is None


def test_chat_notifies_only_for_a_whole_line_with_words():
    def chat(**d):
        return notify.notice_for(AppEvent("chat", "data", {"session": 1, "remote": "GB7SIM", **d}), "GM4BRI")
    assert chat(lines=0, text="") is None                       # a prompt still arriving
    assert chat(lines=1, text="   ") is None
    assert chat(lines=2, text="Welcome to GB7SIM") == notify.Notice("chat", "GB7SIM", "Welcome to GB7SIM")


def test_only_mail_left_for_the_operator_notifies():
    def mail(**d):
        base = {"sender": "G4SIM", "to": "GM4BRI", "subject": "Hello", "body": "from G4SIM", "for_me": True}
        return notify.notice_for(AppEvent("mail", "mail-received", {**base, **d}), "GM4BRI")
    assert mail() == notify.Notice("mailbox", "G4SIM", "Hello: from G4SIM")
    assert mail(for_me=False, to="G0ABC") is None               # third-party mail waiting for G0ABC
    assert mail(subject="") == notify.Notice("mailbox", "G4SIM", "from G4SIM")


def test_the_preview_is_one_line_cut_at_a_word():
    text = "word " * 40
    p = notify.preview(text)
    assert len(p) <= notify.PREVIEW_CHARS and p.endswith("…") and "  " not in p and "\n" not in p
    assert notify.preview("short") == "short"


def test_the_switches_and_whether_the_operator_is_looking():
    from shack_packet_app.settings import NotificationSettings
    n = notify.Notice("chat", "GB7SIM", "hi")
    assert notify.wanted(n, NotificationSettings())
    assert not notify.wanted(n, NotificationSettings(chat=False))
    assert not notify.wanted(n, NotificationSettings(enabled=False))
    assert notify.interrupts(n, window_active=False, page="chat")
    assert notify.interrupts(n, window_active=True, page="monitor")
    assert not notify.interrupts(n, window_active=True, page="chat")


def test_settings_from_before_0_2_3_get_notifications_on():
    from shack_packet_app import settings as settings_mod
    old = settings_mod.to_dict(station_settings())
    del old["notify"]
    s = settings_mod.from_dict(old)
    assert s.notify.enabled and s.notify.sound and s.notify.sound_kind == "beep"
    assert s.notify.messages and s.notify.chat and s.notify.mailbox
    s.notify.sound_kind, s.notify.chat = "spoken", False
    back = settings_mod.from_dict(settings_mod.to_dict(s))
    assert back.notify.sound_kind == "spoken" and not back.notify.chat


# -- the sound ---------------------------------------------------------------------------


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _player(platform, *present, **kw):
    return notifysound.Player(platform=platform, which=_which(*present), threaded=False, **kw)


def test_the_chime_is_two_notes_rising_a_fifth_g5_to_d6(tmp_path):
    path = notifysound.chime_wav(tmp_path / "c.wav")
    with wave.open(str(path)) as w:
        rate, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype(float)
    assert 0.25 < n / rate < 0.4 and np.abs(x).max() < 32767 * 0.5

    def peak(seg):
        f = np.fft.rfftfreq(len(seg), 1 / rate)
        return f[np.argmax(np.abs(np.fft.rfft(seg * np.hanning(len(seg)))))]
    half = int(notifysound.NOTE_S * rate)
    a, b = peak(x[:half]), peak(x[-half:])
    assert abs(a - 783.99) < 15 and abs(b - 1174.66) < 15 and b / a == pytest.approx(1.5, rel=0.02)


def test_the_system_voice_on_a_mac_is_the_chime_then_say_upbeat_with_a_male_voice(tmp_path):
    p = _player("darwin", "say", "afplay", tmpdir=str(tmp_path), mac_voices=lambda: ["Samantha", "Daniel"])
    steps = p.plan("system-voice", "You've got mail!")
    assert steps[0] == ("run", ["afplay", str(p.chime_path())])
    argv = steps[1][1]
    assert argv[0] == "say" and argv[argv.index("-v") + 1] == "Daniel" and int(argv[argv.index("-r") + 1]) > 175
    assert argv[-1].endswith("You've got mail!") and "pbas +" in argv[-1]
    # No male voice installed: the system's own.
    q = _player("darwin", "say", "afplay", tmpdir=str(tmp_path), mac_voices=lambda: ["Samantha"])
    assert "-v" not in q.plan("system-voice", "New chat!")[1][1]


def test_the_system_voice_on_windows_is_winsound_then_system_speech(tmp_path):
    class WinSound:
        SND_FILENAME, SND_NODEFAULT = 0x20000, 0x2
        played = []

        def PlaySound(self, path, flags):
            self.played.append(path)
    ran = []
    p = _player("win32", "powershell", tmpdir=str(tmp_path), winsound=WinSound(), run=lambda a, t: ran.append(a))
    assert p.play("system-voice", "You've got mail!") == "spoken"
    assert WinSound.played == [str(p.chime_path())]
    argv = ran[0]
    assert argv[:4] == ["/usr/bin/powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(argv[4]).decode("utf-16-le")
    assert "You''ve got mail!" in script and "VoiceGender]::Male" in script and "pitch=" in script


def test_the_system_voice_on_linux_uses_what_is_installed_and_beeps_with_nothing(tmp_path):
    p = _player("linux", "spd-say", "paplay", tmpdir=str(tmp_path))
    steps = p.plan("system-voice", "New chat!")
    assert steps[0][1][0] == "/usr/bin/paplay" and steps[1][1][0] == "/usr/bin/spd-say"
    assert steps[1][1][-1] == "New chat!" and "-w" in steps[1][1]
    # espeak and no player: the voice alone.
    p = _player("linux", "espeak-ng", tmpdir=str(tmp_path))
    assert [s[1][0] for s in p.plan("system-voice", "x")] == ["/usr/bin/espeak-ng"]
    # A player and no voice: the chime alone.
    p = _player("linux", "aplay", tmpdir=str(tmp_path), run=lambda a, t: 0)
    assert p.play("system-voice", "x") == "chime"
    beeps = []
    p = _player("linux", tmpdir=str(tmp_path), beep=lambda: beeps.append(1))
    assert p.play("system-voice", "x") == "beep" and beeps == [1]


def test_a_custom_file_plays_and_a_missing_or_wrong_one_beeps(tmp_path):
    wav = notifysound.chime_wav(tmp_path / "mine.wav")
    beeps, ran = [], []
    p = _player("linux", "pw-play", beep=lambda: beeps.append(1), run=lambda a, t: ran.append(a))
    assert p.play("custom", "", str(wav)) == "custom" and ran == [["/usr/bin/pw-play", str(wav)]]
    (tmp_path / "song.mp3").write_bytes(b"ID3")
    for path in ("", str(tmp_path / "gone.wav"), str(tmp_path / "song.mp3")):
        assert p.play("custom", "", path) == "beep"
    assert len(beeps) == 3 and len(ran) == 1


def test_a_sound_already_playing_is_not_queued_behind(tmp_path):
    release = threading.Event()
    ran = []

    def slow(argv, timeout):
        ran.append(argv)
        release.wait(5)
    p = notifysound.Player(platform="linux", which=_which("aplay", "espeak"), run=slow, tmpdir=str(tmp_path))
    assert p.play("system-voice", "one") == "spoken"
    assert p.play("system-voice", "two") == "busy"
    release.set()
    assert wait_for(lambda: p._busy.acquire(blocking=False), 5)
    p._busy.release()
    assert p.play("system-voice", "three") == "spoken"
    release.set()


def test_a_tool_that_fails_does_not_stop_the_next_or_the_app(tmp_path):
    ran = []

    def run(argv, timeout):
        ran.append(argv[0])
        if "aplay" in argv[0]:
            raise OSError("no sound device")
    p = _player("linux", "aplay", "espeak", tmpdir=str(tmp_path), run=run)
    assert p.play("system-voice", "x") == "spoken" and ran == ["/usr/bin/aplay", "/usr/bin/espeak"]


def _clips(tmp_path):
    """Stand-in clips: a 0.5 s tone each, 22050 Hz mono, as the bundled ones."""
    d = tmp_path / "sounds"
    d.mkdir()
    t = np.arange(int(0.5 * 22050)) / 22050
    for name in ("mail", "message", "chat"):
        with wave.open(str(d / f"{name}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes((np.sin(2 * np.pi * 440 * t) * 8000).astype("<i2").tobytes())
    return d


def test_the_bundled_clips_are_short_22khz_mono_and_one_per_kind():
    for kind in ("mailbox", "messages", "chat"):
        p = notifysound.clip_path(kind)
        assert p is not None, kind
        with wave.open(str(p)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22050)
            assert 0.2 < w.getnframes() / 22050 < 1.5
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        assert np.abs(x).max() > 3000                     # a voice, not silence
    assert notifysound.clip_path("nothing") is None


@pytest.mark.parametrize("platform,tools", [("linux", ("paplay",)), ("darwin", ("afplay",)), ("win32", ())])
def test_spoken_is_one_wav_the_chime_then_the_bundled_voice(tmp_path, platform, tools):
    class WinSound:
        SND_FILENAME, SND_NODEFAULT = 0x20000, 0x2

        def __init__(self):
            self.played = []

        def PlaySound(self, path, flags):
            self.played.append(path)
    ws, ran = WinSound(), []
    p = _player(platform, *tools, tmpdir=str(tmp_path), winsound=ws, run=lambda a, t: ran.append(a))
    p.sounds = _clips(tmp_path)
    assert p.play("spoken", "You've got mail!", notice="mailbox") == "voice"
    played = ws.played[0] if platform == "win32" else ran[0][-1]
    assert len(ran) <= 1                                   # no speech engine run at all
    with wave.open(played) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    chime = np.frombuffer(notifysound.chime_frames(rate), dtype="<i2")
    with wave.open(str(p.sounds / "mail.wav")) as w:
        voice = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    gap = int(notifysound.CLIP_GAP_S * rate)
    assert np.array_equal(x[:len(chime)], chime) and not x[len(chime):len(chime) + gap].any()
    assert np.array_equal(x[len(chime) + gap:], voice)
    # Made once, played again from the same file.
    p.play("spoken", "You've got mail!", notice="mailbox")
    assert (ws.played if platform == "win32" else [r[-1] for r in ran])[1] == played


def test_spoken_without_a_clip_or_a_wav_player_falls_back_to_the_system_voice(tmp_path):
    p = _player("linux", "espeak-ng", "aplay", tmpdir=str(tmp_path))
    p.sounds = tmp_path / "none"
    steps = p.plan("spoken", "New chat!", notice="chat")
    assert [s[1][0] for s in steps] == ["/usr/bin/aplay", "/usr/bin/espeak-ng"]
    q = _player("linux", "espeak-ng", tmpdir=str(tmp_path))
    q.sounds = _clips(tmp_path)
    assert [s[1][0] for s in q.plan("spoken", "New chat!", notice="chat")] == ["/usr/bin/espeak-ng"]


def test_a_mail_receipt_is_the_chime_alone_never_the_mail_voice(tmp_path):
    # 0.3.0 merge: E's delivered/failed notices reach A's bundled voice.
    # They must not announce "You've got mail!" for mail that went out.
    from shack_packet_app.ui.notifier import phrase
    p = _player("linux", "espeak-ng", "paplay", tmpdir=str(tmp_path))
    p.sounds = _clips(tmp_path)
    steps = p.plan("spoken", phrase("mail_status"), notice="mail_status")
    assert len(steps) == 1 and steps[0][0] == "run" and steps[0][1][-1] == str(p.chime_path())
    assert p.plan("beep", phrase("mail_status"), notice="mail_status") == [("beep",)]
    quiet = _player("linux", "espeak-ng", tmpdir=str(tmp_path))
    quiet.sounds = p.sounds
    assert quiet.plan("spoken", phrase("mail_status"), notice="mail_status") == [("beep",)]


# -- the window --------------------------------------------------------------------------


class FakeTray:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, text, icon, ms):
        self.messages.append((title, text))

    def hide(self):
        pass


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, kind, phrase="", path="", notice=""):
        self.played.append((kind, phrase, path))
        self.notices = getattr(self, "notices", []) + [notice]
        return kind


@pytest.fixture
def notified(qapp):
    made = []

    def make(s=None, active=False):
        s = s if s is not None else station_settings()
        tray, player, looking = FakeTray(), FakePlayer(), {"active": active}
        ctl = Controller(s, secrets={}, band_factory=lambda: SimBand(55.95, -3.2, first_beacon_s=(40.0, 50.0),
                                                                     beacon_every_s=60))
        win = MainWindow(s, {}, controller=ctl, persist=False,
                         notifier=dict(tray=lambda: tray, player=player, active=lambda: looking["active"],
                                       tray_available=lambda: True))
        win.show()
        win.tray, win.player, win.looking = tray, player, looking
        made.append(win)
        win.start(first_run_check=False)
        assert wait_for(lambda: win.ctl.link_state == "open", 5, app=qapp)
        win.ctl.arm(5)
        return win
    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def _nav(win, page):
    item = win.nav.item(PAGES.index(page))
    return item.text(), item.font().bold()


def test_a_message_answered_on_the_air_notifies_once_with_an_unread_count(notified, qapp):
    win = notified()
    win.nav.setCurrentRow(PAGES.index("monitor"))
    win.ctl.send_message("G4SIM", "testing notifications")

    def ticked():
        win.tick()
        return win.notifier.shown
    assert wait_for(lambda: ticked(), 30, app=qapp)
    wait_for(lambda: False, 2, app=qapp)                # retries and acks: still one
    win.tick()
    assert win.notifier.shown == [notify.Notice("messages", "G4SIM", "Got it: testing notifications")]
    assert win.tray.messages == [("APRS message from G4SIM", "Got it: testing notifications")]
    assert win.player.played == [("beep", "You've got a message!", "")]
    assert _nav(win, "messages") == ("Messages (1)", True)
    # Clicking the notification: the window comes forward on Messages and
    # the count goes.
    win.notifier.clicked()
    assert win.current_key() == "messages" and _nav(win, "messages") == ("Messages", False)


def test_nothing_interrupts_the_operator_already_on_the_page(notified, qapp):
    win = notified(active=True)
    win.nav.setCurrentRow(PAGES.index("messages"))
    win.ctl.send_message("G4SIM", "I am watching")

    def heard():
        win.tick()
        return any(m.direction == "in" for m in win.ctl.messages)
    assert wait_for(heard, 30, app=qapp)
    win.tick()
    assert win.notifier.shown == [] and win.player.played == [] and _nav(win, "messages") == ("Messages", False)


def test_chat_lines_and_mailbox_mail_notify_and_the_switches_are_obeyed(notified, qapp):
    s = station_settings(mailbox__enabled=True)
    s.notify.sound_kind = "spoken"
    s.notify.chat = False
    win = notified(s)
    win.nav.setCurrentRow(PAGES.index("monitor"))
    # Chat switched off: counted, not shown.
    win.ctl.connect("GB7SIM")

    def chat_counted():
        win.tick()
        return win.notifier.unread.count("chat") > 0
    assert wait_for(chat_counted, 30, app=qapp)
    assert win.notifier.shown == [] and _nav(win, "chat")[1]
    # Mail left for the operator: shown, spoken.
    assert win.ctl.simulated_mail("Hello", "From the simulator")

    def mail():
        win.tick()
        return [n for n in win.notifier.shown if n.kind == "mailbox"]
    assert wait_for(mail, 30, app=qapp)
    assert mail()[0].sender == "G4SIM" and mail()[0].text == "Hello: From the simulator"
    assert ("spoken", "You've got mail!", "") in win.player.played
    assert "mailbox" in win.player.notices                 # the bundled clip is chosen by the notice
    assert _nav(win, "mailbox") == ("Mailbox (1)", True)
    # Opening a page (with the window in front) clears its count only.
    win.looking["active"] = True
    win.nav.setCurrentRow(PAGES.index("mailbox"))
    assert _nav(win, "mailbox") == ("Mailbox", False) and _nav(win, "chat")[1]


def test_without_a_tray_the_window_asks_for_attention(notified, qapp, monkeypatch):
    win = notified()
    win.notifier.tray, win.notifier._tray_available = None, lambda: False
    alerts = []
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication.alert", lambda w, ms=0: alerts.append(w))
    win._event(AppEvent("aprs", "message", {"peer": "G0ABC", "text": "hello"}))
    assert alerts == [win] and "G0ABC" in win.statusBar().currentMessage()


def test_settings_has_a_notifications_tab_that_saves(qapp):
    from shack_packet_app.ui.settingsdlg import SettingsDialog
    d = SettingsDialog(station_settings(), None, "notify")
    assert d.tabs.currentIndex() == SettingsDialog.TABS.index("notify")
    assert d.n_on.isChecked() and d.n_kind.currentData() == "beep" and not d.n_file.isEnabled()
    labels = [d.n_kind.itemText(i) for i in range(d.n_kind.count())]
    assert "Spoken: 'You've got mail!' (retro)" in labels
    d.n_kind.setCurrentIndex(d.n_kind.findData("custom"))
    assert d.n_file.isEnabled()
    d.n_file.setText("/home/op/ding.wav")
    d.n_chat.setChecked(False)
    out = d.result_settings().notify
    assert out.sound_kind == "custom" and out.sound_file == "/home/op/ding.wav" and not out.chat and out.messages
    d.n_on.setChecked(False)
    assert not d.n_kind.isEnabled() and not d.n_chat.isEnabled()
    assert d.result_settings().notify.enabled is False
