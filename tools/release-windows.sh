#!/usr/bin/env bash
# The Windows step of tools/release.sh: build the signed installer on the
# Windows build VM, prove it installed there, bring it back, clean up.
#
#   tools/release-windows.sh --stage DIR --out DIR [--log DIR] [--firewall-test [--previous SETUP.exe]] [--keep]
#                            [--installer SETUP.exe]
#
# --installer skips the build and tests an installer already built (and
# signed) by an earlier run - for a test step that failed after the build.
#
# --stage is shackpkg.py's stage (tools/release.sh makes it). The VM is
# named in ~/.config/openhamshack/packet-release.conf ($PACKET_RELEASE_CONF:
# WIN_HOST, WIN_JUMP, WIN_SRC, WIN_WORK), never here, because this
# repository's release tarballs are public.
#
# 1. Refuses if WIN_SRC or WIN_WORK already exist on the VM: another run's
#    leftovers, or another session's build in progress (the VM is shared
#    with NB, Radio, DATV and ATV, each in folders of their own).
# 2. Copies this repository's tools\ and packaging\ and the stage across
#    as one tarball, and runs tools\build-windows.ps1 in the foreground
#    (Windows OpenSSH kills a background child when the session closes,
#    and a scheduled task's session died mid-PyInstaller once: HANDOVER).
#    That script signs as 360 Technology through Azure Artifact Signing,
#    verifies every signature, and runs --self-test and --library-check on
#    the frozen console program before it builds the installer.
# 3. Installs the signed installer silently for the current user (without
#    its firewall task: that has a test of its own), and on the INSTALLED
#    program checks the signatures again and runs --version, --self-test,
#    --library-check and --diagnostics-test (tools/windows-install-test.ps1);
#    then uninstalls it and checks nothing is left.
# 4. --firewall-test: tools/windows-firewall-test.ps1, the installer's
#    firewall step over an upgrade from --previous (default: the newest
#    older Windows installer in ~/build/shack-packet/dist).
# 5. Copies the installer and its .sha256 back to --out, checks the hash,
#    and removes WIN_SRC and WIN_WORK from the VM (--keep leaves them, for
#    a failure worth looking at; remove them by hand afterwards).
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
conf="${PACKET_RELEASE_CONF:-$HOME/.config/openhamshack/packet-release.conf}"
die() { echo "release-windows.sh: $*" >&2; exit 1; }
[ -f "$conf" ] || die "no $conf (it names the build hosts)"
# shellcheck disable=SC1090
source "$conf"
: "${WIN_HOST:?$conf has no WIN_HOST}" "${WIN_SRC:?}" "${WIN_WORK:?}"

stage=""; out=""; logs=""; firewall=0; previous=""; keep=0; installer=""
while [ $# -gt 0 ]; do
    case "$1" in
        --stage) stage="$2"; shift 2 ;;
        --out) out="$2"; shift 2 ;;
        --log) logs="$2"; shift 2 ;;
        --firewall-test) firewall=1; shift ;;
        --previous) previous="$2"; shift 2 ;;
        --keep) keep=1; shift ;;
        --installer) installer="$2"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done
[ -f "$stage/app.json" ] || die "--stage $stage is not a shackpkg.py stage"
[ -n "$out" ] || die "--out is needed"
logs="${logs:-$out}"
mkdir -p "$out" "$logs"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$stage/app.json")"
setup="OpenHamShack-Packet-Radio-$version-windows-x64.exe"

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)
[ -n "${WIN_JUMP:-}" ] && ssh_opts+=(-J "$WIN_JUMP")
win() { ssh "${ssh_opts[@]}" "$WIN_HOST" "$@"; }
put() { scp -q "${ssh_opts[@]}" "$1" "$WIN_HOST:$2"; }
get() { scp -q "${ssh_opts[@]}" "$WIN_HOST:$1" "$2"; }
wps() { win "powershell -NoProfile -ExecutionPolicy Bypass $*"; }
fwd() { printf '%s' "$1" | tr '\\' '/'; }      # scp wants C:/x/y

if [ "$firewall" = 1 ] && [ -z "$previous" ]; then
    previous="$(ls "$HOME"/build/shack-packet/dist/OpenHamShack-Packet-Radio-*-windows-x64.exe 2>/dev/null \
        | grep -v -- "-$version-" | sort -V | tail -1 || true)"
    [ -n "$previous" ] || die "--firewall-test needs an earlier installer (--previous)"
fi

# 1. Nobody else's folders.
exists="$(wps "-Command \"(Test-Path '$WIN_SRC') -or (Test-Path '$WIN_WORK')\"" | tr -d '\r')"
[ "$exists" = "False" ] || die "$WIN_SRC or $WIN_WORK already exists on $WIN_HOST: a leftover, or a build in progress"

cleanup() {
    if [ "$keep" = 1 ]; then
        echo "release-windows.sh: --keep: $WIN_SRC and $WIN_WORK left on the VM"
        return
    fi
    wps "-Command \"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '$WIN_SRC','$WIN_WORK'; (Test-Path '$WIN_SRC') -or (Test-Path '$WIN_WORK')\"" \
        | tr -d '\r' | grep -qx False && echo "   cleaned up: $WIN_SRC and $WIN_WORK removed" \
        || echo "release-windows.sh: could not remove $WIN_SRC or $WIN_WORK on the VM; remove them by hand" >&2
}
trap cleanup EXIT

# 2. Build.
payload="$(mktemp -d "$HOME/build/packet-winpayload.XXXXXX")"
mkdir -p "$payload/repo"
cp -a "$root/tools" "$root/packaging" "$root/VERSION" "$root/LICENSE" "$payload/repo/"
rm -rf "$payload/repo/tools/__pycache__"
cp -a "$stage" "$payload/stage"
[ "$firewall" = 1 ] && cp "$previous" "$payload/previous.exe"
tar -czf "$payload.tgz" -C "$payload" .
rm -rf "$payload"
wps "-Command \"New-Item -ItemType Directory $WIN_SRC | Out-Null\""
put "$payload.tgz" "$(fwd "$WIN_SRC")/payload.tgz"
rm -f "$payload.tgz"
# The VM's OpenSSH shell is Windows PowerShell 5.1 (no &&), so each
# remote step is one PowerShell command, checked by what it prints (an
# outer PowerShell would expand any $ in it before the inner one ran).
wps "-Command \"tar -xzf $WIN_SRC\\payload.tgz -C $WIN_SRC; Remove-Item $WIN_SRC\\payload.tgz; Test-Path $WIN_SRC\\stage\\app.json\"" \
    | tr -d '\r' | grep -qx True || die "could not unpack the payload on the VM"
echo "   copied the stage and the build scripts to $WIN_HOST:$WIN_SRC"
if [ -z "$installer" ]; then
    echo "   building (about 10-15 minutes; log: $logs/windows-build.log)"
    wps "-File $WIN_SRC\\repo\\tools\\build-windows.ps1 -Stage $WIN_SRC\\stage -Work $WIN_WORK" \
        > "$logs/windows-build.log" 2>&1 || die "build-windows.ps1 failed (tail of $logs/windows-build.log):$(echo; tail -20 "$logs/windows-build.log")"
    grep -E "self-test: PASS|library-check: PASS|: Valid, signer|^built |^sha256 " "$logs/windows-build.log" | sed 's/^/   /'

    # 5 (first half). Bring it back before anything else can go wrong.
    get "$(fwd "$WIN_WORK")/out/$setup" "$out/$setup"
    get "$(fwd "$WIN_WORK")/out/$setup.sha256" "$out/$setup.sha256"
    (cd "$out" && sha256sum -c --quiet "$setup.sha256") || die "$setup did not arrive intact"
    echo "   $setup: $(cut -d' ' -f1 < "$out/$setup.sha256")"
else
    [ "$(basename "$installer")" = "$setup" ] || die "--installer is not $setup"
    (cd "$(dirname "$installer")" && sha256sum -c --quiet "$setup.sha256") || die "$installer does not match its .sha256"
    wps "-Command \"New-Item -ItemType Directory $WIN_WORK\\out | Out-Null\""
    put "$installer" "$(fwd "$WIN_WORK")/out/$setup"
    echo "   testing the installer already built: $installer"
fi

# 3. Install, prove, uninstall.
wps "-File $WIN_SRC\\repo\\tools\\windows-install-test.ps1 -Setup $WIN_WORK\\out\\$setup -Version $version -Scratch $WIN_WORK\\itest" \
    > "$logs/windows-install-test.log" 2>&1 || die "the install test failed (tail of $logs/windows-install-test.log):$(echo; tail -20 "$logs/windows-install-test.log")"
grep -E "^(PASS|ok|install-test)" "$logs/windows-install-test.log" | sed 's/^/   /'

# 4. The firewall step.
if [ "$firewall" = 1 ]; then
    wps "-File $WIN_SRC\\repo\\tools\\windows-firewall-test.ps1 -Setup $WIN_WORK\\out\\$setup -Previous $WIN_SRC\\previous.exe -Scratch $WIN_WORK\\fwtest" \
        > "$logs/windows-firewall-test.log" 2>&1 || die "the firewall test failed (tail of $logs/windows-firewall-test.log):$(echo; tail -25 "$logs/windows-firewall-test.log")"
    grep -E "^(PASS|ok|firewall-test|note|log )" "$logs/windows-firewall-test.log" | sed 's/^/   /'
fi
echo "release-windows.sh: $out/$setup"
