#!/usr/bin/env bash
# Cut an OpenHamShack Packet Radio release in one command: tests, VERSION,
# the installers for all three platforms, the release folder and its signed
# update feed.
#
#   tools/release.sh <version> [--only linux,windows,mac,feed] [--skip-tests]
#                    [--notes TEXT] [--recommended] [--firewall-test]
#                    [--no-install-test]
#
# Brian, 18/10/2026: "script future builds the same way NB does", so the
# next release is one command and every step that 0.1.x and 0.2.0 did by
# hand is written down where the next session finds it. It is shaped after
# openhamshack-nb's tools/release.sh, but that one builds only the .deb itself
# and takes the Windows installer as a second step; this one also drives
# the Windows and Mac builds remotely, because Packet Radio ships all three
# platforms together.
#
# Steps, in order (each platform step can run alone with --only, so one
# platform can be released by itself, as NB 0.1.19 was for Windows):
#
#   1. Refuses unless both trees are committed: this repository and the
#      ohs-libs checkout ($OHS_LIBS) whose libraries are vendored into
#      every installer. A release is exactly two commits (CLAUDE.md: "cut
#      a release only from a committed tree").
#   2. VERSION: if <version> is not what VERSION says, writes it there -
#      the one place a version lives (src/shack_packet_app/VERSION is a
#      link to it) - and commits "Release Packet Radio <version>". The
#      release notes must already have a "## <version>" section.
#   3. The suites: ohs-libs' shack_rig, shack_packet and shack_pluto, then
#      this app's, with $PYTHON (a venv with ohs-libs installed editable
#      from $OHS_LIBS; the run refuses one that imports them from
#      elsewhere). A green run is remembered per pair of commits, so a
#      second run for one platform does not repeat twenty minutes of tests.
#      --skip-tests is for a run whose code was proved green already.
#   4. linux: tools/build-linux.sh (both installers, self-test, the
#      AppImage's --library-check), then the .deb installed on this
#      machine with dpkg, its --version, --self-test and --library-check,
#      and purged again (--no-install-test skips that).
#   5. windows: tools/release-windows.sh - build, sign as 360 Technology
#      and verify on the Windows build VM, then install the signed
#      installer there and run the installed program's --self-test,
#      --library-check and --diagnostics-test, uninstall, and clean up.
#      --firewall-test adds tools/windows-firewall-test.ps1 (the
#      installer's firewall step, upgrade over the previous release).
#   6. mac: tools/release-macos.sh - build, sign with the Developer ID,
#      notarise, staple, self-test and library-check on the Mac mini, and
#      clean up.
#   7. feed: tools/release-feed.sh gathers the installers into the release
#      folder, writes .htaccess, signs update.json with the OpenHamShack
#      release key (the one NB's and Radio's feeds use) and checks it as a
#      client would; then prints "remove from the remote site:", the old
#      installers the previous feed named, for tools/publish.sh --remove.
#      The AppImage is copied in beside them (it has no feed slot: an
#      AppImage is not updated in place) for the website's downloads page.
#
# After it: tools/publish.sh source (the source first, so no binary is
# ever on offer without it), tools/publish.sh packet (the installers, the
# feed last), then tools/release-site.sh <version> for the website's
# downloads page.
#
# Where things go: everything this run makes is under
# ~/build/shack-packet/rel-<version>/ (stage, dist, logs); the release
# folder is ~/build/shack-packet/release ($RELEASES). The build hosts are
# named in ~/.config/openhamshack/packet-release.conf ($PACKET_RELEASE_CONF),
# not here: this repository's release tarballs are public.
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
ohs="${OHS_LIBS:-$HOME/wt/packet-modem}"
py="${PYTHON:-$HOME/build/venvs/packet-release/bin/python}"
releases="${RELEASES:-$HOME/build/shack-packet/release}"
die() { echo "release.sh: $*" >&2; exit 1; }
say() { echo; echo "== release.sh: $*"; }

[ $# -ge 1 ] || die "usage: tools/release.sh <version> [--only linux,windows,mac,feed] [--skip-tests] [--notes TEXT] [--recommended] [--firewall-test] [--no-install-test]"
version="$1"; shift
only="linux,windows,mac,feed"
tests=1; install_test=1; firewall=(); notes=(); recommended=()
while [ $# -gt 0 ]; do
    case "$1" in
        --only) only="$2"; shift 2 ;;
        --skip-tests) tests=0; shift ;;
        --no-install-test) install_test=0; shift ;;
        --firewall-test) firewall=(--firewall-test); shift ;;
        --notes) notes=(--notes "$2"); shift 2 ;;
        --recommended) recommended=(--recommended); shift ;;
        *) die "unknown argument $1" ;;
    esac
done
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "$version is not a release version (x.y.z); test builds are handed out by hand"
for s in ${only//,/ }; do
    case "$s" in linux|windows|mac|feed) ;; *) die "--only: no step called $s" ;; esac
done
want() { [[ ",$only," == *",$1,"* ]]; }

work="$HOME/build/shack-packet/rel-$version"
dist="$work/dist"
mkdir -p "$work/logs" "$dist"
exec > >(tee -a "$work/logs/release.log") 2>&1
echo "release.sh $version, $(date -u +%FT%TZ), steps: $only"

# ---------------------------------------------------------------- 1. committed
[ -z "$(git -C "$root" status --short)" ] || die "$root has uncommitted changes; commit first"
[ -d "$ohs/python/shack_rig" ] || die "no ohs-libs checkout at $ohs (set OHS_LIBS)"
[ -z "$(git -C "$ohs" status --short)" ] || die "$ohs (ohs-libs) has uncommitted changes; commit first"
libs_sha="$(git -C "$ohs" rev-parse HEAD)"

# ---------------------------------------------------------------- 2. VERSION
have="$(tr -d ' \t\r\n' < "$root/VERSION")"
grep -Eq "^## $version( |\$)" "$root/docs/RELEASE-NOTES.md" \
    || die "docs/RELEASE-NOTES.md has no '## $version' section; write the notes first"
if [ "$have" != "$version" ]; then
    say "VERSION $have -> $version"
    printf '%s\n' "$version" > "$root/VERSION"
    git -C "$root" commit -q -m "Release Packet Radio $version" -- VERSION
fi
app_sha="$(git -C "$root" rev-parse HEAD)"
echo "app $app_sha, ohs-libs $libs_sha"
printf 'app %s\nohs-libs %s\n' "$app_sha" "$libs_sha" > "$work/commits.txt"

# ---------------------------------------------------------------- 3. tests
green="$HOME/build/shack-packet/tests-green/$app_sha-$libs_sha"
if [ "$tests" = 1 ] && [ ! -f "$green" ]; then
    say "the suites (ohs-libs at $libs_sha, app at $app_sha)"
    where="$("$py" -c 'import shack_rig, os; print(os.path.realpath(shack_rig.__file__))' 2>/dev/null)" \
        || die "$py cannot import shack_rig; make a venv with ohs-libs installed editable from $OHS_LIBS (PYTHON=...)"
    [[ $where == "$(readlink -f "$ohs")"/* ]] || die "$py imports shack_rig from $where, not from $ohs"
    export QT_QPA_PLATFORM=offscreen
    for p in shack_rig shack_packet shack_pluto; do
        log="$work/logs/tests-$p.log"
        (cd "$ohs/python/$p" && timeout 1800 "$py" -m pytest -q -p no:cacheprovider tests > "$log" 2>&1) \
            || die "ohs-libs $p failed (tail of $log):$(echo; tail -5 "$log")"
        echo "   $p: $(tail -1 "$log")"
    done
    log="$work/logs/tests-app.log"
    (cd "$root" && OHS_LIBS="$ohs" timeout 2400 "$py" -m pytest -q -p no:cacheprovider tests > "$log" 2>&1) \
        || die "the app suite failed (tail of $log):$(echo; tail -5 "$log")"
    echo "   app: $(tail -1 "$log")"
    mkdir -p "$(dirname "$green")" && date -u +%FT%TZ > "$green"
elif [ "$tests" = 1 ]; then
    echo "suites already green for these two commits ($(cat "$green"))"
else
    echo "suites skipped (--skip-tests)"
fi

# ---------------------------------------------------------------- the stage
if want windows || want mac; then
    say "stage for the Windows and Mac builds"
    python3 "$root/tools/ohslibs.py" --from "$ohs" --to "$root/build/ohs-libs"
    rm -rf "$work/stage"
    python3 "$ohs/packaging/tools/shackpkg.py" stage "$root/shack-app.toml" "$work/stage" >/dev/null
    grep -q "\"version\": \"$version\"" "$work/stage/app.json" || die "the stage does not say $version"
fi

# ---------------------------------------------------------------- 4. linux
if want linux; then
    say "Linux: .deb and AppImage"
    mkdir -p "$work/tmp"
    TMPDIR="$work/tmp" OHS_LIBS="$ohs" "$root/tools/build-linux.sh" all --out "$dist" > "$work/logs/linux.log" 2>&1 \
        || die "the Linux build failed (tail of $work/logs/linux.log):$(echo; tail -15 "$work/logs/linux.log")"
    grep -E "self-test: PASS|library-check: PASS" "$work/logs/linux.log" | sed 's/^/   /'
    deb="$dist/OpenHamShack-Packet-Radio-$version-linux-all.deb"
    [ -f "$deb" ] || die "no $deb"
    if [ "$install_test" = 1 ]; then
        say "Linux: install the .deb here, run it, purge it"
        # Leave this machine as it was: only if the package is not already
        # installed, and always purged again.
        ! dpkg -s openhamshack-packet >/dev/null 2>&1 || die "openhamshack-packet is installed here already; not touching it"
        scratch="$(mktemp -d "$work/tmp/install.XXXXXX")"
        trap 'sudo dpkg -P openhamshack-packet >/dev/null 2>&1 || true; rm -rf "$scratch"' EXIT
        sudo dpkg -i "$deb" > "$work/logs/linux-install.log" 2>&1 || die "dpkg -i failed: $(tail -3 "$work/logs/linux-install.log")"
        got="$(shack-packet --version)"
        [[ $got == *"$version"* ]] || die "the installed program says '$got', not $version"
        echo "   --version: $got"
        SHACK_PACKET_CONF="$scratch/conf" SHACK_PACKET_DATA="$scratch/data" QT_QPA_PLATFORM=offscreen \
            shack-packet --self-test > "$work/logs/linux-installed-self-test.log" 2>&1 \
            || die "the installed program's self-test failed"
        echo "   $(tail -1 "$work/logs/linux-installed-self-test.log")"
        # Recommends are not installed by dpkg -i, so a missing optional
        # library here is this machine's, not the package's: shown, not fatal.
        SHACK_PACKET_CONF="$scratch/conf" SHACK_PACKET_DATA="$scratch/data" QT_QPA_PLATFORM=offscreen \
            shack-packet --library-check 2>&1 | sed 's/^/   /' || true
        sudo dpkg -P openhamshack-packet >/dev/null
        trap - EXIT
        rm -rf "$scratch"
        ! dpkg -s openhamshack-packet >/dev/null 2>&1 || die "the package did not purge"
        echo "   purged"
    fi
    rm -rf "$work/tmp"
fi

# ---------------------------------------------------------------- 5. windows
if want windows; then
    say "Windows: build, sign, install-test on the build VM"
    "$root/tools/release-windows.sh" --stage "$work/stage" --out "$dist" --log "$work/logs" "${firewall[@]}" \
        || die "the Windows step failed (logs in $work/logs)"
fi

# ---------------------------------------------------------------- 6. mac
if want mac; then
    say "macOS: build, sign, notarise on the Mac mini"
    "$root/tools/release-macos.sh" --stage "$work/stage" --out "$dist" --log "$work/logs" \
        || die "the macOS step failed (logs in $work/logs)"
fi

# ---------------------------------------------------------------- 7. feed
if want feed; then
    say "release folder and the signed update feed"
    PYTHON=python3 "$root/tools/release-feed.sh" --version "$version" --dist "$dist" --releases "$releases" \
        --skip-tests "${notes[@]}" "${recommended[@]}" | tee "$work/logs/feed.log"
    sed -n '/^remove from the remote site:/,$p' "$work/logs/feed.log" | grep -v '^release-feed.sh:' > "$work/remove-list.txt" || true
    img="OpenHamShack-Packet-Radio-$version-linux-x86_64.AppImage"
    if [ -f "$dist/$img" ]; then
        if [ -f "$releases/$img" ] && ! cmp -s "$dist/$img" "$releases/$img"; then
            die "$releases/$img already exists with different bytes; a fix is a new version"
        fi
        cp "$dist/$img" "$dist/$img.sha256" "$releases/"
    fi
fi

say "done"
echo "artefacts in $dist:"
(cd "$dist" && for f in OpenHamShack-Packet-Radio-"$version"-*; do
    case "$f" in *.sha256) ;; *) printf '   %s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f" ;; esac
done)
if [ -s "$work/remove-list.txt" ]; then
    cat "$work/remove-list.txt"
    echo "(saved as $work/remove-list.txt for tools/publish.sh packet --remove-list)"
fi
echo "next: tools/publish.sh source, tools/publish.sh packet --remove-list $work/remove-list.txt, tools/release-site.sh $version"
