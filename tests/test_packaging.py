"""The installers' input: shack-app.toml staged by ohs-libs' packaging kit.

Every installer (the .deb, the AppImage, later Windows and macOS) starts
from the kit's stage of shack-app.toml, so a stage that cannot run the app
is a broken installer on every OS. This stages it as tools/build-linux.sh
does (tools/ohslibs.py first, then the kit's shackpkg.py) and runs the
staged program's own launcher, on nothing but the stage: the version it
reports must be the repository's, every library must come from the stage
rather than this venv's editable installs, and `--self-test` must pass.
That last is what found the band-plan dataset missing from the first
stage (shack_bandplan's src/ does not hold it; its wheel build copies it
in, and so now does ohslibs.py).

Skipped where the kit or the ohs-libs checkout is not on this machine.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OHS = Path(os.environ.get("OHS_LIBS", Path.home() / "wt" / "packet-modem"))
KIT = Path(os.environ.get("SHACK_KIT", OHS / "packaging"))
SHACKPKG = KIT / "tools" / "shackpkg.py"
LIBS = ("shack_packet", "shack_rig", "shack_bandplan", "shack_i18n", "shack_pluto", "shack_pad", "shack_client")

pytestmark = pytest.mark.skipif(not SHACKPKG.is_file() or not all((OHS / "python" / lib).is_dir() for lib in LIBS),
                                reason="needs ohs-libs' packaging kit and every library the app carries "
                                       "(shack_pluto: feature/packet-sdr-radio until it merges; set OHS_LIBS)")


@pytest.fixture(scope="module")
def stage(tmp_path_factory):
    out = tmp_path_factory.mktemp("stage") / "stage"
    subprocess.run([sys.executable, str(ROOT / "tools" / "ohslibs.py"), "--from", str(OHS)],
                   check=True, capture_output=True)
    subprocess.run([sys.executable, str(SHACKPKG), "stage", str(ROOT / "shack-app.toml"), str(out)],
                   check=True, capture_output=True, text=True)
    return out


def _run(stage: Path, folders: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(stage / "src"),
               SHACK_PACKET_CONF=str(folders / "conf"), SHACK_PACKET_DATA=str(folders / "data"))
    return subprocess.run([sys.executable, *args], env=env, cwd=folders, capture_output=True, text=True,
                          timeout=180)


def test_the_stage_carries_the_app_and_its_libraries(stage, folders):
    probe = ("import json, shack_packet_app, " + ", ".join(LIBS) + "\n"
             "print(json.dumps({m.__name__: m.__file__ for m in (shack_packet_app, " + ", ".join(LIBS) + ")}))\n"
             "print(shack_packet_app.__version__)")
    r = _run(stage, folders, "-c", probe)
    assert r.returncode == 0, r.stderr
    files, version = r.stdout.splitlines()
    import json
    for name, f in json.loads(files).items():
        assert Path(f).is_relative_to(stage / "src"), f"{name} came from {f}, not the stage"
    assert version == (ROOT / "VERSION").read_text().strip()


def test_the_staged_program_passes_its_self_test(stage, folders):
    r = _run(stage, folders, str(stage / "launcher.py"), "--self-test")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "self-test: PASS"


def test_the_staged_program_saves_diagnostics_from_its_menu(stage, folders):
    # The hidden flag the Windows build is checked with: 0.1.0's Save
    # diagnostics closed the program. Run on the stage, as the frozen
    # program will run it.
    r = _run(stage, folders, str(stage / "launcher.py"), "--diagnostics-test", str(folders / "d.zip"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "diagnostics-test: PASS"


def test_the_library_check_names_every_library_and_proves_what_is_installed(stage, folders):
    # What the Windows and macOS builds run on the frozen program; here on
    # the stage with this venv's packages, which carry cryptography and
    # zeroconf (and may lack the rest: then they must say MISSING, never
    # pass by not being tried).
    r = _run(stage, folders, str(stage / "launcher.py"), "--library-check")
    lines = dict(line.split(": ", 1) for line in r.stdout.strip().splitlines())
    assert set(lines) == {"cryptography", "zeroconf", "hidapi", "Pillow", "librtlsdr", "libiio", "libhackrf",
                          "library-check"}
    assert lines["cryptography"].startswith("ok (Ed25519") and lines["zeroconf"].startswith("ok (zeroconf")
    every = all(v.startswith("ok") for k, v in lines.items() if k != "library-check")
    assert (r.returncode == 0) == every and lines["library-check"] == ("PASS" if every else "FAIL")
