"""The sound that goes with a notification (notify.py, ui/notifier.py):
the system's beep, a spoken announcement, or a WAV file the operator
chose. Asked for by Brian on 19/10/2026, the spoken one "like the old
AOL alert without being it".

**The voice is bundled (0.3.0).** 0.2.3 spoke with the computer's own
text-to-speech, which sounds different on every machine and robotic on
most. Brian chose Piper's en_GB-cori-high voice (female, British; its
model card says the dataset is public domain, recorded in
packaging/THIRD-PARTY.txt) on 20/10/2026, and the three phrases - "You've
got mail!", "You've got a message!", "New chat!" - were rendered once by
tools/render-voice.sh into sounds/*.wav (22 kHz mono, under a second
each). Piper is not a run-time dependency. "spoken" plays the two-note
chime made here, in code (`chime_wav`: G5 then D6, a soft attack and a
decay), joined to the clip into one WAV so the two never drift apart.
AOL's recording is somebody's voice and somebody's trade mark; none of it
is copied or imitated: another voice, other words for two of the three.

**"system-voice"** keeps 0.2.3's way for whoever prefers it, or a
language the clips are not in: the chime, then the system's own speech
saying the phrase, upbeat and male where the system has one.

**Why the system's tools and not Qt's.** QTextToSpeech and QSoundEffect
are in PyQt6's wheel, but each links Qt Multimedia, which the bundles do
not carry today and which would add its FFmpeg libraries (tens of MB on
each platform) for a two-second sound. Every system already has a way
to speak and to play a WAV:

| | speech | WAV |
|---|---|---|
| macOS | `say` (rate, voice, `[[pbas]]` pitch) | `afplay` |
| Windows | PowerShell's System.Speech (SSML prosody) | `winsound` (Python's own) |
| Linux | `spd-say`, `espeak-ng` or `espeak`, if installed | `paplay`, `pw-play` or `aplay` |

Each is looked for (`shutil.which`) and the first found is used; with no
way to play a WAV the bundled voice falls back to the system's speech,
with no speech the chime alone plays, and with nothing at all, or a
custom file that is missing or not a WAV, it beeps. Commands are argument
lists, never a shell, and the phrase is never anything a station sent:
only the fixed phrases above.

**Never on the UI thread.** `Player.play` returns at once: the beep is
the caller's (QApplication.beep belongs to the GUI thread), everything
else runs one step after another on a thread of its own, with a time
limit on each. A sound already playing is not queued behind: a burst of
messages makes one announcement, not ten.
"""
from __future__ import annotations

import atexit
import base64
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

#: The sound kinds settings.NotificationSettings.sound_kind can name.
KINDS = ("beep", "spoken", "system-voice", "custom")

#: The bundled clips (module docstring), by notice kind (notify.Notice.kind).
#: "mail_status" (0.3.0: mail we sent was delivered, or given up on) has no
#: clip on purpose: "You've got mail!" would say mail came in when it is a
#: receipt for mail that went out. Its phrase is empty too (ui/notifier.py),
#: so "spoken" falls through to the chime alone, and to a beep where there
#: is nothing to play a WAV with.
SOUNDS = Path(__file__).resolve().parent / "sounds"
CLIPS = {"mailbox": "mail", "messages": "message", "chat": "chat"}
#: Silence between the chime and the voice.
CLIP_GAP_S = 0.06

#: The chime: two notes a fifth apart (G5 then D6), each NOTE_S long with a
#: GAP_S between, ATTACK_S rise and an exponential fall. Short and bright
#: enough to be heard over a receiver, quiet enough (LEVEL of full scale)
#: not to startle.
CHIME_HZ = (783.99, 1174.66)
NOTE_S = 0.16
GAP_S = 0.02
ATTACK_S = 0.008
LEVEL = 0.35
CHIME_RATE = 22_050

#: Longest any one step may run before it is abandoned (a speech engine
#: that hangs must not keep the next notification silent for ever).
STEP_TIMEOUT_S = 10.0

#: Voices preferred for the announcement on macOS, in order, from those
#: Apple ships: male, English, clear. The first installed one is used; with
#: none of them, the system's default voice.
MAC_VOICES = ("Daniel", "Alex", "Fred", "Tom", "Aaron", "Arthur", "Oliver", "Reed (English (UK))",
              "Reed (English (US))", "Rocko (English (US))")


def chime_wav(path: str | os.PathLike, rate: int = CHIME_RATE) -> Path:
    """Write the two-note chime to `path` as 16-bit mono WAV; the path."""
    return _write_wav(path, chime_frames(rate), rate)


def _write_wav(path, frames: bytes, rate: int) -> Path:
    path = Path(path)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def clip_path(kind: str) -> Path | None:
    """The bundled clip for a notice kind, if there is one."""
    name = CLIPS.get(kind)
    p = SOUNDS / f"{name}.wav" if name else None
    return p if p is not None and p.is_file() else None


def announcement_wav(path: str | os.PathLike, clip: Path) -> Path:
    """The chime, a short gap and `clip`, as one WAV at the clip's rate."""
    with wave.open(str(clip)) as w:
        rate = w.getframerate()
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{clip.name}: not 16-bit mono")
        voice = w.readframes(w.getnframes())
    return _write_wav(path, chime_frames(rate) + b"\0\0" * int(CLIP_GAP_S * rate) + voice, rate)


def chime_frames(rate: int = CHIME_RATE) -> bytes:
    frames = bytearray()
    attack = max(1, int(ATTACK_S * rate))
    for i, hz in enumerate(CHIME_HZ):
        n = int(NOTE_S * rate)
        for k in range(n):
            t = k / rate
            env = min(1.0, k / attack) * math.exp(-4.0 * t / NOTE_S)
            # A touch of the octave makes it a chime rather than a test tone.
            x = math.sin(2 * math.pi * hz * t) + 0.25 * math.sin(4 * math.pi * hz * t)
            frames += struct.pack("<h", int(32767 * LEVEL * env * x / 1.25))
        if i < len(CHIME_HZ) - 1:
            frames += b"\0\0" * int(GAP_S * rate)
    return bytes(frames)


def _ps_quote(text: str) -> str:
    """`text` inside a PowerShell single-quoted string."""
    return text.replace("'", "''")


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def windows_speech_script(phrase: str) -> str:
    """The PowerShell that says `phrase` with System.Speech: a male voice
    if one is installed, a little faster and higher than the default (SSML
    prosody, in the chosen voice's own language so it is not swapped)."""
    say = _ps_quote(_xml(phrase))
    return ("Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male) } catch {};"
            "$l = $s.Voice.Culture.Name;"
            "$x = '<speak version=\"1.0\" xmlns=\"http://www.w3.org/2001/10/synthesis\" xml:lang=\"' + $l + "
            "'\"><prosody rate=\"+10%\" pitch=\"+15%\">" + say + "</prosody></speak>';"
            "$s.SpeakSsml($x)")


class Player:
    """Plays a notification's sound (module docstring). Everything it
    reaches outside Python is injectable for the tests: `platform`
    (sys.platform), `which` (shutil.which), `run(argv, timeout)` (runs a
    command to its end), `winsound` (the module) and `beep` (called on the
    caller's thread). `threaded` False runs the steps before returning."""

    def __init__(self, *, platform: str | None = None, which: Callable[[str], str | None] | None = None,
                 run: Callable[[list[str], float], int] | None = None, winsound=None,
                 beep: Callable[[], None] | None = None, threaded: bool = True, tmpdir: str | None = None,
                 mac_voices: Callable[[], list[str]] | None = None):
        self.platform = platform or sys.platform
        self.which = which or shutil.which
        self.run = run or _run
        self.winsound = winsound
        self.beep = beep or (lambda: None)
        self.threaded = threaded
        self.tmpdir = tmpdir
        self._mac_voices = mac_voices
        self._voice: str | None = None
        self._voice_known = False
        self._chime: Path | None = None
        self._announcements: dict[str, Path] = {}
        #: Where the clips are (the tests point it elsewhere).
        self.sounds = SOUNDS
        self._busy = threading.Lock()
        #: What the last `play` did, for the tests and the log: "beep",
        #: "voice" (the bundled clip), "spoken" (the system's speech),
        #: "chime", "custom", or "busy" (one already playing).
        self.last = ""

    # -- what would be done -------------------------------------------------------------

    def plan(self, kind: str, phrase: str = "", path: str = "", notice: str = "") -> list[tuple]:
        """The steps for `kind` ("beep", "spoken", "system-voice",
        "custom"): each ("beep",), ("run", argv) or ("winsound", path).
        `notice` is the notice's kind (CLIPS), for the bundled voice.
        Falls back to [("beep",)]."""
        if kind == "custom":
            p = Path(path) if path else None
            if p is None or p.suffix.lower() != ".wav" or not p.is_file():
                return [("beep",)]
            step = self._wav(p)
            return [step] if step else [("beep",)]
        if kind == "spoken":
            clip = self._clip(notice)
            if clip is not None and self._wav_player_exists():
                # Marked, so play() can say the bundled voice was used.
                return [(*self._wav(self.announcement_path(notice, clip)), "voice")]
            # No clip, or nothing to play a WAV with: the system's speech.
            kind = "system-voice"
        if kind == "system-voice":
            steps = []
            chime = self._wav(self.chime_path()) if self._wav_player_exists() else None
            if chime:
                steps.append(chime)
            speech = self._speech(phrase) if phrase else None
            if speech:
                steps.append(("run", speech))
            return steps or [("beep",)]
        return [("beep",)]

    def _wav_player_exists(self) -> bool:
        return self._wav(Path("x.wav")) is not None

    def _wav(self, path: Path):
        if self.platform == "win32":
            return ("winsound", str(path)) if self._winsound() is not None else None
        if self.platform == "darwin":
            return ("run", ["afplay", str(path)]) if self.which("afplay") else None
        for tool, args in (("paplay", []), ("pw-play", []), ("aplay", ["-q"])):
            exe = self.which(tool)
            if exe:
                return ("run", [exe, *args, str(path)])
        return None

    def _speech(self, phrase: str) -> list[str] | None:
        if self.platform == "darwin":
            if not self.which("say"):
                return None
            voice = self._mac_voice()
            # [[pbas +6]] raises the voice's pitch base a little; 205 words a
            # minute is brisk without gabbling (macOS's default is about 175).
            return ["say", "-r", "205", *(["-v", voice] if voice else []), f"[[pbas +6]] {phrase}"]
        if self.platform == "win32":
            ps = self.which("powershell") or self.which("powershell.exe")
            if not ps:
                return None
            encoded = base64.b64encode(windows_speech_script(phrase).encode("utf-16-le")).decode("ascii")
            return [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        exe = self.which("spd-say")
        if exe:
            # -w: wait until it has been said, so the next sound does not
            # talk over it and the step's time limit means something.
            return [exe, "-w", "-t", "male1", "-r", "15", "-p", "20", phrase]
        for tool in ("espeak-ng", "espeak"):
            exe = self.which(tool)
            if exe:
                return [exe, "-v", "en+m3", "-s", "190", "-p", "60", phrase]
        return None

    def _mac_voice(self) -> str | None:
        if not self._voice_known:
            self._voice_known = True
            try:
                have = set(self._mac_voices() if self._mac_voices else _say_voices())
            except Exception:                       # noqa: BLE001 - any failure: the default voice
                have = set()
            self._voice = next((v for v in MAC_VOICES if v in have), None)
        return self._voice

    def _winsound(self):
        if self.winsound is None and self.platform == "win32":
            try:
                import winsound                     # noqa: PLC0415 - Windows only
                self.winsound = winsound
            except ImportError:
                return None
        return self.winsound

    def _clip(self, notice: str) -> Path | None:
        name = CLIPS.get(notice)
        p = Path(self.sounds) / f"{name}.wav" if name else None
        return p if p is not None and p.is_file() else None

    def announcement_path(self, notice: str, clip: Path) -> Path:
        """The chime and the clip in one file, made once per run of the app."""
        p = self._announcements.get(notice)
        if p is None or not p.exists():
            fd, name = tempfile.mkstemp(prefix=f"ohs-packet-{CLIPS[notice]}-", suffix=".wav", dir=self.tmpdir)
            os.close(fd)
            p = self._announcements[notice] = announcement_wav(name, clip)
            atexit.register(lambda p=p: p.unlink(missing_ok=True))
        return p

    def chime_path(self) -> Path:
        """The chime, written once per run of the app to a temporary file."""
        if self._chime is None or not self._chime.exists():
            fd, name = tempfile.mkstemp(prefix="ohs-packet-chime-", suffix=".wav", dir=self.tmpdir)
            os.close(fd)
            self._chime = chime_wav(name)
            # Gone when the app quits, not left in the temp folder per run.
            atexit.register(lambda p=self._chime: p.unlink(missing_ok=True))
        return self._chime

    # -- doing it ------------------------------------------------------------------------

    def play(self, kind: str, phrase: str = "", path: str = "", notice: str = "") -> str:
        """Start the sound for `kind`; returns at once (module docstring)."""
        if not self._busy.acquire(blocking=False):
            self.last = "busy"
            return self.last
        try:
            steps = self.plan(kind, phrase, path, notice)
        except Exception:                           # noqa: BLE001 - a sound is never worth an error
            log.exception("notification sound")
            steps = [("beep",)]
        if steps == [("beep",)]:
            self._busy.release()
            self.beep()
            self.last = "beep"
            return self.last
        if kind == "custom":
            self.last = "custom"
        elif any(len(st) > 2 and st[2] == "voice" for st in steps):
            self.last = "voice"
        else:
            spoken = any(s[0] == "run" and not _is_player(s[1]) for s in steps)
            self.last = "spoken" if spoken else "chime"
        if self.threaded:
            threading.Thread(target=self._steps, args=(steps,), name="notify-sound", daemon=True).start()
        else:
            self._steps(steps)
        return self.last

    def _steps(self, steps) -> None:
        try:
            for step in steps:
                try:
                    if step[0] == "run":
                        self.run(step[1], STEP_TIMEOUT_S)
                    elif step[0] == "winsound":
                        ws = self._winsound()
                        ws.PlaySound(step[1], ws.SND_FILENAME | ws.SND_NODEFAULT)
                except Exception as e:              # noqa: BLE001 - the next step, or nothing
                    log.warning("notification sound: %s failed: %s", step[0], e)
        finally:
            self._busy.release()


def _is_player(argv: list[str]) -> bool:
    return Path(argv[0]).name in ("afplay", "paplay", "pw-play", "aplay")


def _run(argv: list[str], timeout: float) -> int:
    """Run `argv` to its end (no console window on Windows), killed after
    `timeout` seconds."""
    flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
    try:
        return p.wait(timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(2)
        return -1


def _say_voices() -> list[str]:
    """The voices `say -v ?` lists: a name, then its language and a sample."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    names = []
    for line in out.splitlines():
        head = line.split("#", 1)[0].rstrip()
        # "Daniel              en_GB" / "Reed (English (UK)) en_GB": the name
        # is everything before the last run of spaces and the locale.
        parts = head.rsplit(None, 1)
        if len(parts) == 2:
            names.append(parts[0].strip())
    return names
