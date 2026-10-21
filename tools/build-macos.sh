#!/bin/bash
# Build "OpenHamShack Packet Radio.app" and its signed, notarised .dmg on
# the Mac mini (Apple Silicon).
#
#   tools/build-macos.sh --stage DIR [--work DIR] [--out DIR] [--no-sign] [--no-notarize]
#
# --stage is the folder packaging/tools/shackpkg.py (ohs-libs' packaging
# kit) made on Linux from shack-app.toml, copied here with scp: this
# machine has no brand kit and no Python new enough until this script
# fetches one. The same stage a Windows build copies elsewhere
# (tools/build-windows.ps1): one `shackpkg.py stage` serves both.
# --work (default $SHACK_MAC_WORK, else ~/shackpacket-mac; the Mac mini's
# internal disk is small, so point it at /Volumes/OpenHamShackBuild/... if that
# volume exists) holds everything downloaded or built: a pinned Python, a
# virtualenv, PortAudio (for PyAudio), PyInstaller's folders. Nothing is
# installed outside it and nothing needs an administrator or Homebrew.
#
# Steps:
#   1. Python 3.13.16 (python-build-standalone, openhamshack-nb's pin), a venv,
#      and packaging/macos/requirements.txt.
#   2. PortAudio v19.7.0 built from source (static) and PyAudio 0.2.14
#      built against it from its sdist: PyPI's PyAudio release carries no
#      macOS wheel. openhamshack-nb's packaging/macos/build-dmg.sh method,
#      proved there; this app needs no libiio, libad9361 or codec2, so
#      everything else that script builds is left out.
#   2b. From 0.2, the two C libraries the SDR monitor needs, built from
#      pinned source into <work>/vendor and copied into
#      Contents/Frameworks/<name>/ after PyInstaller (host.py says why a
#      folder each; signed with the rest):
#        libiio 0.26 (network backend only, as openhamshack-nb's Mac build: a
#        Pluto on a Mac is reached at its USB network address), with that
#        release's own iio.py on the import path - a Pluto;
#        libusb 1.0.27 and rtl-sdr-blog V1.4.0's librtlsdr, compiled
#        directly with clang (its CMake wants pkg-config, which a Mac
#        without Homebrew lacks; the library is six C files) - an RTL-SDR.
#        libhackrf 2026.01.3, one C file compiled the same way, with its
#        own copy of that libusb - a HackRF One (from 0.2.0).
#      cryptography, zeroconf, hidapi and Pillow come as wheels from
#      packaging/macos/requirements.txt.
#   3. The .icns from the staged brand-kit PNGs (iconutil), then the .app
#      from this app's own spec (packaging/macos/shack-packet.spec,
#      staged as app.spec): the kit's own packaging/macos/build-dmg.sh
#      freezes with the kit's shared spec, which excludes Qt
#      (HANDOVER.md), so this app keeps its own macOS pipeline, as NB,
#      HAM, DATV and ATV do.
#   4. Signed with the Developer ID Application identity in the Mac
#      mini's ohs-signing keychain when it is there (code-signing
#      reference: Mac mini note) - hardened runtime, secure timestamp,
#      every Mach-O inside first, then the bundle - unless --no-sign.
#      Ad-hoc signed (an arm64 Mac runs nothing else) when there is none,
#      or when --no-sign is given.
#   5. --self-test on the signed .app's own executable (stdout is not
#      lost here, unlike the windowed .exe on Windows), then
#      --library-check: every optional library loads from the bundle.
#   6. The .dmg (app + Applications link), signed, notarised with
#      `notarytool --keychain-profile ohs-notary --wait`, stapled and
#      checked by Gatekeeper (spctl) - skipped by --no-notarize or when
#      the build is unsigned.
#
# Output in --out (default <work>/out): OpenHamShack-Packet-Radio-<v>-macos-arm64.dmg
# and its .sha256. arm64 only.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
stage_in="" work="${SHACK_MAC_WORK:-$HOME/shackpacket-mac}" out="" sign=1 notarize=1
while [ $# -gt 0 ]; do
    case "$1" in
        --stage) stage_in="$2"; shift 2 ;;
        --work) work="$2"; shift 2 ;;
        --out) out="$2"; shift 2 ;;
        --no-sign) sign=0; shift ;;
        --no-notarize) notarize=0; shift ;;
        *) echo "build-macos.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done
[ -n "$stage_in" ] && [ -f "$stage_in/app.json" ] || { echo "build-macos.sh: --stage DIR (with app.json) is required" >&2; exit 2; }
out="${out:-$work/out}"
die() { echo "build-macos.sh: $*" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || die "runs on an Apple Silicon Mac"
for t in codesign hdiutil iconutil xcrun shasum plutil ditto curl file otool; do
    command -v "$t" >/dev/null || die "needs $t (Xcode Command Line Tools)"
done

IDENTITY="Developer ID Application: Brian Henderson (GTVYVL6ZY7)"
KEYCHAIN="$HOME/Library/Keychains/ohs-signing.keychain-db"
PASSFILE="$HOME/.config/ohs-signing/keychain-pass"
PROFILE="ohs-notary"
if ((sign)) && { [[ ! -f $KEYCHAIN ]] || [[ ! -r $PASSFILE ]]; }; then
    echo "   no Developer ID keychain at $KEYCHAIN: building ad-hoc signed instead"
    sign=0
fi

mkdir -p "$work/cache" "$out"
stage="$work/stage"
rm -rf "$stage"
ditto "$stage_in" "$stage"
j() { plutil -extract "$1" raw -o - "$stage/app.json"; }
name="$(j name)"; version="$(j version)"; exe="$(j executable)"; file_name="$(j file_name)"
echo "$name $version ($(j id))"

step "Python 3.13.16 (python-build-standalone)"
py_tar="cpython-3.13.16+20261003-aarch64-apple-darwin-install_only.tar.gz"
py_sha="d8975d7df4f08f7b1c7aafcdfacbddcec3d366415f2c1a72b2466b6850815933"
py_url="https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.13.16%2B20261003-aarch64-apple-darwin-install_only.tar.gz"
if ! echo "$py_sha  $work/cache/$py_tar" | shasum -a 256 -c --status 2>/dev/null; then
    echo "   fetching $py_url"
    curl -fsSL -o "$work/cache/$py_tar.part" "$py_url"
    echo "$py_sha  $work/cache/$py_tar.part" | shasum -a 256 -c --status \
        || { rm -f "$work/cache/$py_tar.part"; die "$py_tar does not match its pinned SHA-256"; }
    mv "$work/cache/$py_tar.part" "$work/cache/$py_tar"
fi
if [ ! -x "$work/python/bin/python3" ]; then
    mkdir -p "$work/python"
    tar -xzf "$work/cache/$py_tar" -C "$work/python" --strip-components 1
fi
[ -x "$work/venv/bin/python" ] || "$work/python/bin/python3" -m venv "$work/venv"
VPY="$work/venv/bin/python"
"$work/venv/bin/pip" install --quiet --disable-pip-version-check --cache-dir "$work/cache/pip" \
    -r "$repo/packaging/macos/requirements.txt"
CMAKE="$work/venv/bin/cmake"

step "PortAudio v19.7.0 (static) and PyAudio 0.2.14"
# openhamshack-nb's pins and method: PyAudio's PyPI release has no macOS wheel,
# so it is built from its sdist against a PortAudio built here, statically
# (a folder holding only libportaudio.a, so PyAudio's setup.py cannot find
# a dylib to link dynamically instead - it has no static switch of its
# own on macOS, so these flags are it).
pa_tar="pa_stable_v190700_20210406.tgz"
pa_sha="47efbf42c77c19a05d22e627d42873e991ec0c1357219c0d74ce6a2948cb2def"
pa_url="http://files.portaudio.com/archives/pa_stable_v190700_20210406.tgz"
pyaudio_tar="PyAudio-0.2.14.tar.gz"
pyaudio_sha="78dfff3879b4994d1f4fc6485646a57755c6ee3c19647a491f790a0895bd2f87"
pyaudio_url="https://files.pythonhosted.org/packages/26/1d/8878c7752febb0f6716a7e1a52cb92ac98871c5aa522cba181878091607c/PyAudio-0.2.14.tar.gz"
fetch_pinned() {   # fetch_pinned NAME SHA URL -> prints the cached path
    local dest="$work/cache/$1"
    if [[ -f $dest ]] && echo "$2  $dest" | shasum -a 256 -c --status 2>/dev/null; then echo "$dest"; return; fi
    echo "   fetching $3" >&2
    curl -fsSL -A "Wget/1.21" -o "$dest.part" "$3"
    echo "$2  $dest.part" | shasum -a 256 -c --status || { rm -f "$dest.part"; die "$1: SHA-256 mismatch; upstream changed the file"; }
    mv "$dest.part" "$dest"
    echo "$dest"
}
export MACOSX_DEPLOYMENT_TARGET=12.0
LIC="$work/licences"; mkdir -p "$LIC"
if ! "$VPY" -c 'import pyaudio, sys; sys.exit(0 if "V19.7.0" in pyaudio.get_portaudio_version_text() else 1)' 2>/dev/null; then
    pa_src=$(fetch_pinned "$pa_tar" "$pa_sha" "$pa_url")
    rm -rf "$work/build/portaudio"; mkdir -p "$work/build/portaudio"
    tar -xzf "$pa_src" -C "$work/build/portaudio" --strip-components 1
    "$CMAKE" -S "$work/build/portaudio" -B "$work/build/portaudio/b" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DCMAKE_INSTALL_PREFIX="$work/vendor/portaudio" >/dev/null
    "$CMAKE" --build "$work/build/portaudio/b" -j 8 --target portaudio_static >/dev/null
    mkdir -p "$work/vendor/portaudio/static" "$work/vendor/portaudio/include"
    cp "$work/build/portaudio/b/libportaudio.a" "$work/vendor/portaudio/static/"
    cp "$work/build/portaudio/include/"*.h "$work/vendor/portaudio/include/"
    pyaudio_src=$(fetch_pinned "$pyaudio_tar" "$pyaudio_sha" "$pyaudio_url")
    rm -rf "$work/build/pyaudio"; mkdir -p "$work/build/pyaudio"
    tar -xzf "$pyaudio_src" -C "$work/build/pyaudio" --strip-components 1
    CFLAGS="-I$work/vendor/portaudio/include" \
    LDFLAGS="-L$work/vendor/portaudio/static -framework CoreAudio -framework AudioToolbox -framework AudioUnit -framework CoreFoundation -framework CoreServices" \
        "$work/venv/bin/pip" install --quiet --no-build-isolation --no-cache "$work/build/pyaudio"
fi
"$VPY" -c 'import pyaudio; print("   PyAudio", pyaudio.__version__, "with", pyaudio.get_portaudio_version_text())'
if otool -L "$("$VPY" -c 'import pyaudio._portaudio as m; print(m.__file__)')" | tail -n +2 | grep -q libportaudio; then
    die "PyAudio links a PortAudio dylib; it must have it linked in statically"
fi
cp "$work/build/portaudio/LICENSE.txt" "$LIC/portaudio-LICENSE.txt" 2>/dev/null || true

step "libiio 0.26 (network only), libusb 1.0.27 and librtlsdr (rtl-sdr-blog V1.4.0), from source"
VENDOR="$work/vendor"
mkdir -p "$VENDOR/py" "$VENDOR/libiio" "$VENDOR/rtlsdr" "$VENDOR/licences"
iio_src=$(fetch_pinned libiio-0.26.tar.gz fb445fb860ef1248759f45d4273a4eff360534480ec87af64c6b8db3b99be7e5 \
    https://github.com/analogdevicesinc/libiio/archive/refs/tags/v0.26.tar.gz)
if [[ ! -f $VENDOR/libiio/libiio.dylib || ! -f $VENDOR/py/iio.py ]]; then
    rm -rf "$work/build/libiio"; mkdir -p "$work/build/libiio"
    tar -xzf "$iio_src" -C "$work/build/libiio" --strip-components 1
    # openhamshack-nb's flags: no USB or serial backend (libusb would have to be
    # signed and shipped for nothing a Mac uses), no docs, no bindings build.
    "$CMAKE" -S "$work/build/libiio" -B "$work/build/libiio/b" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_INSTALL_PREFIX="$work/build/libiio/inst" \
        -DOSX_FRAMEWORK=OFF -DOSX_PACKAGE=OFF -DWITH_USB_BACKEND=OFF -DWITH_SERIAL_BACKEND=OFF -DWITH_TESTS=OFF \
        -DWITH_DOC=OFF -DWITH_MAN=OFF -DPYTHON_BINDINGS=OFF >/dev/null
    "$CMAKE" --build "$work/build/libiio/b" -j 8 >/dev/null
    "$CMAKE" --install "$work/build/libiio/b" >/dev/null
    cp "$work/build/libiio/inst/lib/libiio.0.26.dylib" "$VENDOR/libiio/libiio.dylib"
    install_name_tool -id @rpath/libiio.dylib "$VENDOR/libiio/libiio.dylib"
    cp "$work/build/libiio/bindings/python/iio.py" "$VENDOR/py/iio.py"
fi
tar -xzf "$iio_src" -O libiio-0.26/COPYING.txt > "$VENDOR/licences/libiio-COPYING.txt"
usb_src=$(fetch_pinned libusb-1.0.27.tar.bz2 ffaa41d741a8a3bee244ac8e54a72ea05bf2879663c098c82fc5757853441575 \
    https://github.com/libusb/libusb/releases/download/v1.0.27/libusb-1.0.27.tar.bz2)
rtl_src=$(fetch_pinned rtl-sdr-blog-1.4.0.tar.gz 89520433e63b1a1371b45d8deea198cad9dbd1f0d55cc3a706aafb7b187bd325 \
    https://github.com/rtlsdrblog/rtl-sdr-blog/archive/refs/tags/V1.4.0.tar.gz)
if [[ ! -f $VENDOR/rtlsdr/librtlsdr.dylib || ! -f $VENDOR/rtlsdr/libusb-1.0.dylib ]]; then
    rm -rf "$work/build/libusb" "$work/build/rtlsdr"; mkdir -p "$work/build/libusb" "$work/build/rtlsdr"
    tar -xjf "$usb_src" -C "$work/build/libusb" --strip-components 1
    (cd "$work/build/libusb" && CFLAGS="-arch arm64 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET -O2" \
        ./configure --quiet --disable-static --prefix="$work/build/libusb/inst" >/dev/null && make -s -j 8 >/dev/null \
        && make -s install >/dev/null)
    cp "$work/build/libusb/inst/lib/libusb-1.0.0.dylib" "$VENDOR/rtlsdr/libusb-1.0.dylib"
    install_name_tool -id @loader_path/libusb-1.0.dylib "$VENDOR/rtlsdr/libusb-1.0.dylib"
    tar -xzf "$rtl_src" -C "$work/build/rtlsdr" --strip-components 1
    (cd "$work/build/rtlsdr" && cc -dynamiclib -O2 -arch arm64 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET \
        -Drtlsdr_EXPORTS -I include -I "$work/build/libusb/inst/include/libusb-1.0" src/librtlsdr.c src/tuner_*.c \
        "$VENDOR/rtlsdr/libusb-1.0.dylib" -install_name @rpath/librtlsdr.dylib -o "$VENDOR/rtlsdr/librtlsdr.dylib")
fi
cp "$work/build/libusb/COPYING" "$VENDOR/licences/libusb-COPYING" 2>/dev/null \
    || tar -xjf "$usb_src" -O libusb-1.0.27/COPYING > "$VENDOR/licences/libusb-COPYING"
tar -xzf "$rtl_src" -O rtl-sdr-blog-1.4.0/COPYING > "$VENDOR/licences/rtl-sdr-COPYING"
otool -L "$VENDOR/rtlsdr/librtlsdr.dylib" | grep -q "@loader_path/libusb-1.0.dylib" \
    || die "librtlsdr.dylib does not take libusb from beside itself"
# libhackrf 2026.01.3 (from 0.2.0): the source release OpenHamShack ATV's
# Windows build pins, compiled directly with clang as librtlsdr is - the
# library is one C file - into hackrf/ with its own copy of the libusb
# built above (@loader_path), so the two folders never depend on each other.
hk_src=$(fetch_pinned hackrf-2026.01.3.tar.xz d2b76a1115d9b4df648c29efb2f3c8e80009b7cf9a8adf37abbfdba72101b086 \
    https://github.com/greatscottgadgets/hackrf/releases/download/v2026.01.3/hackrf-2026.01.3.tar.xz)
mkdir -p "$VENDOR/hackrf"
if [[ ! -f $VENDOR/hackrf/libhackrf.dylib ]]; then
    rm -rf "$work/build/hackrf"; mkdir -p "$work/build/hackrf"
    tar -xJf "$hk_src" -C "$work/build/hackrf" --strip-components 1
    cp "$VENDOR/rtlsdr/libusb-1.0.dylib" "$VENDOR/hackrf/libusb-1.0.dylib"
    (cd "$work/build/hackrf/host/libhackrf/src" && cc -dynamiclib -O2 -arch arm64 \
        -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET -DLIBRARY_VERSION='"2026.01.3"' -DLIBRARY_RELEASE='"2026.01.3"' \
        -I . -I "$work/build/libusb/inst/include/libusb-1.0" hackrf.c "$VENDOR/hackrf/libusb-1.0.dylib" \
        -install_name @rpath/libhackrf.dylib -o "$VENDOR/hackrf/libhackrf.dylib")
fi
tar -xJf "$hk_src" -O hackrf-2026.01.3/COPYING > "$VENDOR/licences/hackrf-COPYING"
# The bundle's notice: what it carries and where its source is (from 0.2.0).
cp "$repo/packaging/THIRD-PARTY.txt" "$VENDOR/licences/"
otool -L "$VENDOR/hackrf/libhackrf.dylib" | grep -q "@loader_path/libusb-1.0.dylib" \
    || die "libhackrf.dylib does not take libusb from beside itself"
"$VPY" -c 'import ctypes, sys; l = ctypes.CDLL(sys.argv[1]); assert l.hackrf_init() == 0, "hackrf_init failed"; l.hackrf_exit(); print("   libhackrf", sys.argv[1])' \
    "$VENDOR/hackrf/libhackrf.dylib"
DYLD_LIBRARY_PATH="$VENDOR/libiio" "$VPY" -c 'import sys, os; sys.path.insert(0, sys.argv[1]); import iio
d = os.path.dirname(iio._lib._name); print("   libiio", ".".join(map(str, iio.version[:2])), "from", d)
assert iio.version[:2] == (0, 26), iio.version; assert os.path.samefile(d, sys.argv[2]), d' "$VENDOR/py" "$VENDOR/libiio"
"$VPY" -c 'import ctypes, sys; l = ctypes.CDLL(sys.argv[1]); print("   librtlsdr", sys.argv[1], l.rtlsdr_get_device_count(), "dongle(s)")' \
    "$VENDOR/rtlsdr/librtlsdr.dylib"

step "Icon and app bundle"
iconset="$work/app.iconset"
rm -rf "$iconset" && mkdir "$iconset"
for s in 16 32 128 256 512; do
    cp "$stage/icons/$s.png" "$iconset/icon_${s}x${s}.png"
    cp "$stage/icons/$((s * 2)).png" "$iconset/icon_${s}x${s}@2x.png"
done
iconutil -c icns -o "$stage/icons/app.icns" "$iconset"
SHACK_STAGE="$stage" SHACK_VENDOR="$VENDOR" "$work/venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
    --distpath "$work/dist" --workpath "$work/pyi" "$repo/packaging/macos/shack-packet.spec"
app="$work/dist/$name.app"
[ -d "$app" ] || die "PyInstaller made no $app"
if find "$app" -name 'QtPdf*' -o -name 'libqpdf.dylib' | grep -q .; then
    die "Qt PDF is in the bundle (nothing imports it; the Linux spec drops it too)"
fi
# Into Contents/Frameworks (sys._MEIPASS), a folder each, before signing.
for lib in libiio rtlsdr hackrf; do
    rm -rf "$app/Contents/Frameworks/$lib"
    ditto "$VENDOR/$lib" "$app/Contents/Frameworks/$lib"
done

if ((sign)); then
    step "Sign ($IDENTITY): hardened runtime, inside out"
    security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
    sign_file() {
        codesign --force --options runtime --timestamp --keychain "$KEYCHAIN" -s "$IDENTITY" "$@" 2>&1 \
            | grep -v "replacing existing signature" || true
    }
    find "$app/Contents" -type f ! -path "$app/Contents/MacOS/$exe" -print0 \
        | xargs -0 file -h | awk -F': ' '/Mach-O/ {print $1}' \
        | awk -F/ '{print NF "\t" $0}' | sort -rn | cut -f2- \
        | while IFS= read -r f; do sign_file "$f"; done
    sign_file --entitlements "$repo/packaging/macos/entitlements.plist" "$app"
else
    step "Sign (ad hoc, no Developer ID): for this Mac only"
    sign_file() { codesign --force --sign - "$@" 2>&1 | grep -v "replacing existing signature" || true; }
    sign_file "$app"
fi
codesign --verify --deep --strict --verbose=1 "$app" 2>&1 | sed 's/^/   /'
codesign -dv "$app" 2>&1 | grep -E '^(Identifier|Authority=Developer|TeamIdentifier|Runtime|Timestamp)' | sed 's/^/   /' || true

intel=$(find "$app" -type f -print0 | xargs -0 file -h \
        | awk -F': ' '/Mach-O/ && !/arm64/ && !/\(for architecture / {print $1}' | sed "s|^$app/||")
[[ -z $intel ]] || die "Mach-O files with no arm64 slice (would need Rosetta 2):"$'\n'"$intel"
echo "   every Mach-O in the bundle has an arm64 slice"

step "Self-test on the signed bundle (simulator only)"
smoke="$work/smoke"
rm -rf "$smoke"; mkdir -p "$smoke"
SHACK_PACKET_CONF="$smoke/conf" SHACK_PACKET_DATA="$smoke/data" \
    "$app/Contents/MacOS/$exe" --self-test | tee "$work/self-test.log"
grep -q "self-test: PASS" "$work/self-test.log" || die "self-test did not report PASS"
step "Library check on the signed bundle"
SHACK_PACKET_CONF="$smoke/conf" SHACK_PACKET_DATA="$smoke/data" \
    "$app/Contents/MacOS/$exe" --library-check | tee "$work/library-check.log" || true
grep -q "library-check: PASS" "$work/library-check.log" || die "library-check did not report PASS"
grep -q "libiio 0.26 from .*/Contents/Frameworks/libiio" "$work/library-check.log" \
    || die "libiio was not loaded from the bundle's own copy"
grep -q "Contents/Frameworks/rtlsdr/librtlsdr.dylib" "$work/library-check.log" \
    || die "librtlsdr was not loaded from the bundle's own copy"
grep -q "Contents/Frameworks/hackrf/libhackrf.dylib" "$work/library-check.log" \
    || die "libhackrf was not loaded from the bundle's own copy"

step "Disk image"
dmg="$out/$file_name-macos-arm64.dmg"
dmgroot="$work/dmgroot"
rm -rf "$dmgroot" "$dmg" && mkdir "$dmgroot"
ditto "$app" "$dmgroot/$name.app"
ln -s /Applications "$dmgroot/Applications"
hdiutil create -quiet -ov -fs HFS+ -format UDZO -volname "$name" -srcfolder "$dmgroot" "$dmg"
if ((sign)); then
    security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
    codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp "$dmg"
    if ((notarize)); then
        step "Notarise (notarytool, profile $PROFILE)"
        security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
        xcrun notarytool submit "$dmg" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --wait \
            --output-format plist > "$work/notary.plist"
        status="$(plutil -extract status raw -o - "$work/notary.plist")"
        sub="$(plutil -extract id raw -o - "$work/notary.plist")"
        echo "   submission $sub: $status"
        if [ "$status" != "Accepted" ]; then
            xcrun notarytool log "$sub" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" || true
            die "notarisation was not accepted"
        fi
        xcrun stapler staple -q "$dmg"
        xcrun stapler validate -q "$dmg" && echo "   ticket stapled"
        spctl -a -t open --context context:primary-signature -vv "$dmg" 2>&1 | sed 's/^/   /'
    else
        echo "signed but NOT NOTARISED (--no-notarize): Gatekeeper will refuse it on another Mac"
    fi
else
    echo "UNSIGNED (ad hoc): Gatekeeper will ask the user to confirm it"
fi

(cd "$out" && shasum -a 256 "$(basename "$dmg")" > "$(basename "$dmg").sha256")
echo
echo "built $dmg ($(du -h "$dmg" | cut -f1))"
echo "sha256 $(cut -d' ' -f1 < "$dmg.sha256")"
