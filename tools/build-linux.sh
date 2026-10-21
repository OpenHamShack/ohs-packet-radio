#!/usr/bin/env bash
# Build OpenHamShack Packet Radio's Linux installers - the .deb and the
# AppImage - with ohs-libs' desktop packaging kit, and prove each one by
# running `--self-test` on what it installs.
#
#   tools/build-linux.sh [deb|appimage|all] [--out DIR]     (default: all, ~/build/shack-packet/dist)
#
# $OHS_LIBS is the ohs-libs checkout with the packet branches (default
# ~/wt/packet-modem): tools/ohslibs.py copies the four libraries from it
# into build/ohs-libs, where shack-app.toml's [python] paths finds them.
# $SHACK_KIT is the kit (default $OHS_LIBS/packaging). Only read from
# there: the kit's scripts are run, never changed.
#
# The proofs need no radio, sound card or network (the self-test runs the
# engine on the simulator inside one process):
#   .deb       unpacked with dpkg-deb -x and run by its own /usr/bin
#              launcher on this machine's python3, PyQt6 and numpy - what
#              an install runs, without installing
#   AppImage   run with --appimage-extract-and-run (no FUSE needed); its
#              extraction under /tmp is removed afterwards
# Settings and data go to a scratch folder, never the developer's own.
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
ohs="${OHS_LIBS:-$HOME/wt/packet-modem}"
kit="${SHACK_KIT:-$ohs/packaging}"
what="all"
out="$HOME/build/shack-packet/dist"
while [ $# -gt 0 ]; do
    case "$1" in
        deb|appimage|all) what="$1"; shift ;;
        --out) out="$2"; shift 2 ;;
        *) echo "build-linux.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done
mkdir -p "$out" "$HOME/build"
[ -x "$kit/deb/build-deb.sh" ] || { echo "build-linux.sh: no packaging kit at $kit" >&2; exit 1; }

python3 "$root/tools/ohslibs.py" --from "$ohs" --to "$root/build/ohs-libs"

scratch="$(mktemp -d "$HOME/build/shack-packet-proof.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
export SHACK_PACKET_CONF="$scratch/conf" SHACK_PACKET_DATA="$scratch/data" QT_QPA_PLATFORM=offscreen

if [ "$what" = deb ] || [ "$what" = all ]; then
    echo "== .deb"
    "$kit/deb/build-deb.sh" "$root/shack-app.toml" --out "$out"
    deb="$(ls -t "$out"/OpenHamShack-Packet-Radio-*-linux-all.deb | head -1)"
    dpkg-deb -x "$deb" "$scratch/deb"
    echo "== self-test: $(basename "$deb"), unpacked"
    "$scratch/deb/usr/bin/shack-packet" --self-test
fi

if [ "$what" = appimage ] || [ "$what" = all ]; then
    echo "== AppImage"
    # ADI's libiio Python binding (iio.py) is in no wheel, so pip in the
    # build container cannot bring it: it is fetched here, pinned by
    # sha256, from the v0.26 tag the .deb's python3-libiio and the Windows
    # and macOS bundles also carry (identical bytes to Debian 13's), and
    # put in the stage's vendor/ folder for packaging/qt-app.spec, with its
    # licence. The C library stays the system's (librtlsdr likewise). Only
    # the AppImage gets it: the .deb leaves iio to python3-libiio.
    iio_url="https://raw.githubusercontent.com/analogdevicesinc/libiio/v0.26"
    iio_cache="$HOME/build/shack-packet-vendor/libiio-0.26"
    mkdir -p "$iio_cache"
    fetch() {   # fetch NAME URL SHA256: into the cache once, checked every time
        if [ ! -f "$iio_cache/$1" ]; then
            curl -sSfL --retry 3 -o "$iio_cache/$1.part" "$2"
            mv "$iio_cache/$1.part" "$iio_cache/$1"
        fi
        echo "$3  $iio_cache/$1" | sha256sum -c --quiet - \
            || { echo "build-linux.sh: $1 does not match its pinned sha256" >&2; rm -f "$iio_cache/$1"; exit 1; }
    }
    fetch iio.py "$iio_url/bindings/python/iio.py" 0e7dfa0aa2ddd782c8f01193e24d1d57eb37c5f4c96305de2b8214cb32d9f95d
    fetch libiio-COPYING.txt "$iio_url/COPYING.txt" 102900208eef27b766380135906d431dba87edaa7ec6aa72e6ebd3dd67f3a97b
    stage="$scratch/appimage-stage"
    python3 "$kit/tools/shackpkg.py" stage "$root/shack-app.toml" "$stage" >/dev/null
    mkdir -p "$stage/vendor/py" "$stage/vendor/licences"
    cp "$iio_cache/iio.py" "$stage/vendor/py/"
    cp "$iio_cache/libiio-COPYING.txt" "$stage/vendor/licences/"
    # What the AppImage carries and where its source is (from 0.2.0).
    cp "$root/packaging/THIRD-PARTY.txt" "$stage/vendor/licences/"
    "$kit/appimage/build-appimage.sh" "$stage" --out "$out"
    img="$(ls -t "$out"/OpenHamShack-Packet-Radio-*-linux-x86_64.AppImage | head -1)"
    echo "== self-test: $(basename "$img")"
    # The runtime extracts to /tmp/appimage_extracted_<hash> and keeps it
    # for the next run; /tmp is shared and small, so it goes.
    before="$(ls -d /tmp/appimage_extracted_* 2>/dev/null || true)"
    status=0
    "$img" --appimage-extract-and-run --self-test || status=$?
    for d in /tmp/appimage_extracted_*; do
        if [ -d "$d" ] && ! grep -qxF "$d" <<<"$before"; then rm -rf "$d"; fi
    done
    [ "$status" -eq 0 ] || exit "$status"
    # Every library 0.2 relies on, loaded from inside the AppImage (the
    # Windows and macOS builds make the same check on their bundles).
    echo "== library-check: $(basename "$img")"
    before="$(ls -d /tmp/appimage_extracted_* 2>/dev/null || true)"
    "$img" --appimage-extract-and-run --library-check || status=$?
    for d in /tmp/appimage_extracted_*; do
        if [ -d "$d" ] && ! grep -qxF "$d" <<<"$before"; then rm -rf "$d"; fi
    done
    [ "$status" -eq 0 ] || exit "$status"
fi
echo "== done: $out"
