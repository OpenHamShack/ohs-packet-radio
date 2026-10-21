#!/usr/bin/env bash
# The macOS step of tools/release.sh: build, sign, notarise and staple the
# .dmg on the Mac mini, bring it back, clean up.
#
#   tools/release-macos.sh --stage DIR --out DIR [--log DIR] [--keep]
#
# The Mac is named in ~/.config/openhamshack/packet-release.conf
# ($PACKET_RELEASE_CONF: MAC_HOST, MAC_WORK_FALLBACK), never here.
#
# 1. The work folder is /Volumes/OpenHamShackBuild/shackpacket-release when that
#    drive is attached (the internal disk is small: GUIDE §4), else
#    MAC_WORK_FALLBACK on the internal disk, which needs about 3 GB for the
#    length of the run. Refuses if the folder already exists.
# 2. Copies tools/, packaging/ and the stage across and runs
#    tools/build-macos.sh detached, with nohup and a log, because a build
#    over a plain SSH session died at 255 during pip (HANDOVER). This
#    script then waits for its exit status file, reading the log's last
#    line now and then. build-macos.sh signs with the Developer ID in the
#    Mac's ohs-signing keychain, runs --self-test and --library-check on
#    the signed .app, and notarises, staples and checks the .dmg with
#    Gatekeeper; it fails the build if any of that does not hold.
# 3. Copies the .dmg and its .sha256 back to --out, checks the hash, and
#    removes the work folder (--keep leaves it).
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
conf="${PACKET_RELEASE_CONF:-$HOME/.config/openhamshack/packet-release.conf}"
die() { echo "release-macos.sh: $*" >&2; exit 1; }
[ -f "$conf" ] || die "no $conf (it names the build hosts)"
# shellcheck disable=SC1090
source "$conf"
: "${MAC_HOST:?$conf has no MAC_HOST}" "${MAC_WORK_FALLBACK:?}"

stage=""; out=""; logs=""; keep=0
while [ $# -gt 0 ]; do
    case "$1" in
        --stage) stage="$2"; shift 2 ;;
        --out) out="$2"; shift 2 ;;
        --log) logs="$2"; shift 2 ;;
        --keep) keep=1; shift ;;
        *) die "unknown argument $1" ;;
    esac
done
[ -f "$stage/app.json" ] || die "--stage $stage is not a shackpkg.py stage"
[ -n "$out" ] || die "--out is needed"
logs="${logs:-$out}"
mkdir -p "$out" "$logs"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$stage/app.json")"
dmg="OpenHamShack-Packet-Radio-$version-macos-arm64.dmg"

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)
mac() { ssh "${ssh_opts[@]}" "$MAC_HOST" "$@"; }

# 1. Where.
work="$(mac "if [ -d /Volumes/OpenHamShackBuild ]; then echo /Volumes/OpenHamShackBuild/shackpacket-release; else echo $MAC_WORK_FALLBACK; fi")"
work="$(mac "cd; eval echo $work")"                 # ~ expanded on the Mac
if mac "test -e '$work'"; then die "$work already exists on the Mac: a leftover, or a build in progress"; fi
case "$work" in /Volumes/OpenHamShackBuild/*) ;; *) echo "   note: /Volumes/OpenHamShackBuild is not attached; building on the internal disk in $work" ;; esac

cleanup() {
    if [ "$keep" = 1 ]; then echo "release-macos.sh: --keep: $work left on the Mac"; return; fi
    mac "rm -rf '$work'" && echo "   cleaned up: $work removed from the Mac" \
        || echo "release-macos.sh: could not remove $work on the Mac; remove it by hand" >&2
}
trap cleanup EXIT

# 2. Build, detached.
mac "mkdir -p '$work'"
tar -czf - --exclude=__pycache__ -C "$root" tools packaging VERSION LICENSE \
    | mac "mkdir -p '$work/repo' && tar -xzf - -C '$work/repo'"
tar -czf - -C "$stage" . | mac "mkdir -p '$work/stage' && tar -xzf - -C '$work/stage'"
mac "cd '$work' && nohup bash -c 'bash repo/tools/build-macos.sh --stage '\''$work/stage'\'' --work '\''$work/build'\'' --out '\''$work/out'\'' > build.log 2>&1; echo \$? > build.rc' >/dev/null 2>&1 &"
echo "   building on the Mac in $work (about 15-25 minutes with notarisation; log: $logs/macos-build.log)"
deadline=$(( $(date +%s) + 5400 ))
while ! mac "test -f '$work/build.rc'"; do
    [ "$(date +%s)" -lt "$deadline" ] || die "no result after 90 minutes; the log is $work/build.log on the Mac (--keep to look)"
    sleep 60
done
mac "cat '$work/build.log'" > "$logs/macos-build.log" || true
rc="$(mac "cat '$work/build.rc'" | tr -d '\r\n ')"
[ "$rc" = 0 ] || die "build-macos.sh exited $rc (tail of $logs/macos-build.log):$(echo; tail -25 "$logs/macos-build.log")"
grep -E "self-test: PASS|library-check: PASS|Authority=Developer ID|submission .*: Accepted|ticket stapled|source=Notarized|^built |^sha256 " \
    "$logs/macos-build.log" | sed 's/^/   /'
grep -q "ticket stapled" "$logs/macos-build.log" || die "the .dmg was not notarised and stapled"

# 3. Back, checked.
scp -q "${ssh_opts[@]}" "$MAC_HOST:$work/out/$dmg" "$MAC_HOST:$work/out/$dmg.sha256" "$out/"
(cd "$out" && sha256sum -c --quiet "$dmg.sha256") || die "$dmg did not arrive intact"
echo "   $dmg: $(cut -d' ' -f1 < "$out/$dmg.sha256")"
echo "release-macos.sh: $out/$dmg"
