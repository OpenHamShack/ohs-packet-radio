#!/bin/sh
# A test virtualenv for one pair of worktrees (this app's and ohs-libs'),
# made by copying a release's venv and pointing its editable installs here.
#
#   tools/devvenv.sh NAME LIBS_WORKTREE [BASE]    # e.g. packet-030-b ~/wt/libs-030-b rel-0.2.3
#   ~/build/venvs/NAME/bin/python -m pytest -q tests/test_bbs.py
#
# Why a copy rather than pip: the release venvs already hold PyQt6, numpy,
# the SDR bindings and every ohs-libs package as editable installs, and
# building that from scratch takes minutes and network. Each release so far
# was made the same way by hand (HANDOVER: "a copy of rel-0.2.2 with the
# app's editable .pth pointed at ..."); this does it the same way every
# time. Run the copy's python with -m: the scripts in bin/ keep the
# shebang of whichever venv was copied first.
set -eu
name=${1:?venv name}
libs=$(cd "${2:?ohs-libs worktree}" && pwd)
base=${3:-rel-0.2.3}
app=$(cd "$(dirname "$0")/.." && pwd)
venvs=$HOME/build/venvs
dest=$venvs/$name
if [ ! -d "$dest" ]; then
    cp -a "$venvs/$base" "$dest"
fi
for pth in "$dest"/lib/python3*/site-packages/__editable__.shack_*.pth; do
    case $(basename "$pth") in
        __editable__.shack_packet_app-*) echo "$app/src" > "$pth" ;;
        *)
            pkg=$(basename "$pth" | sed 's/^__editable__\.\(shack_[a-z0-9_]*\)-.*/\1/')
            echo "$libs/python/$pkg/src" > "$pth" ;;
    esac
done
"$dest/bin/python" -c "import shack_packet, shack_packet_app; print(shack_packet.__file__); print(shack_packet_app.__file__)"
