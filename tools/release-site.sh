#!/usr/bin/env bash
# Put a Packet Radio release on openhamshack.com's downloads page, after
# tools/publish.sh has put its installers and feed in /desktop/packet/.
#
#   tools/release-site.sh <version> [--dry-run]
#
# The website is not this repository's: its source is ~/openhamshack-site
# on ohs-dev (local-only, no hub), its live build is ~/build/site-dist
# there, and openhamshack-os tools/publish-web.sh uploads it (GUIDE §6:
# each tool touches only its own folders). This script runs that
# repository's tools exactly as the 0.1.1 and 0.1.2 releases did by hand:
#
# 1. The four installers (from ~/build/shack-packet/rel-<v>/dist) go to
#    ohs-dev, with tools/site-downloads.py.
# 2. On ohs-dev, in a worktree of the site's main on a branch
#    feature/packet-<v>: site-downloads.py updates only the Packet entries
#    of data/downloads.json, builds a stage from a copy of the live dist,
#    refuses if anything but this release's pages and files differ from
#    the live dist (diff -r's job, done file by file), and runs
#    check-site.py. The data change is committed on the branch.
# 3. publish-web.sh site --dry-run, then for real, with --remove for each
#    old Packet download and its .sha256; publish-web.sh checks every
#    uploaded file over HTTPS itself.
# 4. The live dist is replaced by the stage (the old one kept beside it as
#    site-dist-pre-packet-<v>), the branch is fast-forwarded into main and
#    removed, and every new download's SHA-256 is checked again here, over
#    HTTPS with a cache-buster, and every removed one must answer 404.
#
# --dry-run stops after the publish dry run, leaving the stage and the
# branch on ohs-dev for a look (the next run refuses until they are gone).
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
conf="${PACKET_RELEASE_CONF:-$HOME/.config/openhamshack/packet-release.conf}"
die() { echo "release-site.sh: $*" >&2; exit 1; }
[ -f "$conf" ] || die "no $conf"
# shellcheck disable=SC1090
source "$conf"
host="${SITE_HOST:-ohs-dev}"
[ $# -ge 1 ] || die "usage: tools/release-site.sh <version> [--dry-run]"
version="$1"; shift
dry=0
[ "${1:-}" = "--dry-run" ] && dry=1
dist="$HOME/build/shack-packet/rel-$version/dist"
logs="$HOME/build/shack-packet/rel-$version/logs"
mkdir -p "$logs"
files=()
for n in linux-x86_64.AppImage linux-all.deb windows-x64.exe macos-arm64.dmg; do
    f="$dist/OpenHamShack-Packet-Radio-$version-$n"
    [ -f "$f" ] && [ -f "$f.sha256" ] || die "no $f (and .sha256): release.sh first"
    (cd "$dist" && sha256sum -c --quiet "$(basename "$f").sha256") || die "$f does not match its .sha256"
    files+=("$f" "$f.sha256")
done

remote="build/packet-site-$version"
wt="wt/site-packet-$version"
branch="feature/packet-$version"
ssh "$host" "test ! -e ~/$remote && test ! -e ~/$wt && ! git -C ~/openhamshack-site rev-parse -q --verify $branch >/dev/null" \
    || die "~/$remote, ~/$wt or the branch $branch already exists on $host (an earlier attempt?)"

# 1.
ssh "$host" "mkdir -p ~/$remote"
rsync -a "${files[@]}" "$root/tools/site-downloads.py" "$host:$remote/"
echo "   copied the four installers to $host:~/$remote"

# 2.
ssh "$host" "set -e; cd ~/openhamshack-site; test -z \"\$(git status --short)\"; git worktree add -q -b $branch ~/$wt main" \
    || die "could not branch the site repository (is ~/openhamshack-site clean?)"
ssh "$host" "python3 ~/$remote/site-downloads.py --version $version --files ~/$remote --site ~/$wt" \
    | tee "$logs/site-downloads.log" || die "site-downloads.py refused (see above); the stage is left on $host"
ssh "$host" "cd ~/$wt && git add data/downloads.json && git commit -q -F - <<'MSG'
Downloads: Packet Radio $version for Linux, Windows and macOS

Written by shack-packet tools/release-site.sh: only the four Packet
entries of data/downloads.json change (version, file, size, SHA-256),
matched by OS and kind; collect-downloads.py was not run, so no other
product moved. The stage was built from a copy of the live dist and
checked to differ from it only in the Packet pages and files.
MSG
git log --oneline -1"
stage="$(sed -n 's/^stage: //p' "$logs/site-downloads.log")"
removes=()
while read -r p; do removes+=(--remove "$p"); done < <(sed -n 's/^remove: //p' "$logs/site-downloads.log")

# 3.
ssh "$host" "cd ~/openhamshack-os && PUBLISH_LOCAL_DIR=$stage tools/publish-web.sh site --docroot-is-ours --dry-run $(printf '%q ' "${removes[@]}")" \
    | tee "$logs/site-publish-dry.log"
if [ "$dry" = 1 ]; then
    echo "release-site.sh: dry run; the stage $stage and branch $branch are on $host"
    exit 0
fi
ssh "$host" "cd ~/openhamshack-os && PUBLISH_LOCAL_DIR=$stage tools/publish-web.sh site --docroot-is-ours $(printf '%q ' "${removes[@]}")" \
    | tee "$logs/site-publish.log"
grep -q "^publish-web.sh: PASS" "$logs/site-publish.log" || die "publish-web.sh did not report PASS"

# 4.
ssh "$host" "set -e; mv ~/build/site-dist ~/build/site-dist-pre-packet-$version; mv $stage ~/build/site-dist
cd ~/openhamshack-site && git merge -q --ff-only $branch && git worktree remove ~/$wt && git branch -q -d $branch
git log --oneline -1; rm -rf ~/$remote"
fail=0
for f in "${files[@]}"; do
    case "$f" in *.sha256) continue ;; esac
    n="$(basename "$f")"
    got="$(curl -sSfL "https://openhamshack.com/downloads/$n?v=$RANDOM$RANDOM" | sha256sum | cut -d' ' -f1)"
    want="$(cut -d' ' -f1 < "$f.sha256")"
    if [ "$got" = "$want" ]; then echo "   live: $n $got"; else echo "   LIVE MISMATCH: $n ($got)"; fail=1; fi
done
for r in "${removes[@]}"; do
    [ "$r" = --remove ] && continue
    code="$(curl -s -o /dev/null -w '%{http_code}' "https://openhamshack.com/$r?v=$RANDOM")"
    if [ "$code" = 404 ]; then echo "   gone: $r"; else echo "   STILL SERVED ($code): $r"; fail=1; fi
done
[ "$fail" = 0 ] || die "the live site does not match; look before telling anyone"
echo "release-site.sh: Packet Radio $version is on https://openhamshack.com/downloads/"
