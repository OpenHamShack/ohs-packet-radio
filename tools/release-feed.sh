#!/usr/bin/env bash
# Put a built Packet Radio release into its release folder and sign the
# update feed for it - the step between the builds and tools/publish.sh.
#
#   tools/release-feed.sh [--version V] [--dist DIR] [--releases DIR] [--notes TEXT] [--recommended] [--skip-tests]
#
# tools/release.sh runs this as its last step ("feed"); it was release.sh
# itself until 0.2.0, when release.sh became the one command for the whole
# release (builds included) and this kept the gathering and signing.
#
# Its own script rather than a third app in openhamshack-nb's release.sh, which
# knows only nb|ham and belongs to the NB session (PLAN-0.2 §3.4). Much
# smaller than NB's: the installers are built elsewhere (tools/build-linux.sh
# here, build-windows.ps1 on the Windows VM, build-macos.sh on the Mac) and
# land in --dist (default ~/build/shack-packet/dist); this only gathers,
# proves and signs.
#
# 1. Refuses a dirty tree: a release is exactly a commit, so VERSION names
#    the code that was built (CLAUDE.md: "cut a release only from a
#    committed tree"). Runs the suite unless --skip-tests (for a second run
#    that only adds the Windows or Mac installer to a release already cut).
# 2. Copies each installer of VERSION found in --dist - the .deb, the
#    Windows .exe, the Mac .dmg - with its .sha256 into --releases (default
#    ~/build/shack-packet/release), refusing one whose .sha256 does not
#    match its bytes, and never replacing a file already there under that
#    name with different bytes: a fix is a new version.
# 3. Writes .htaccess (types, and no caching of the feed: a stale
#    update.json at 20i's CDN edge would hide a release for hours).
# 4. Signs the feed (tools/update-feed.py write: the key must be the one
#    the app trusts; a platform with no new installer keeps its previous
#    entry) and verifies the folder as a client would.
# 5. Prints "remove from the remote site:" - the installers the previous
#    feed named that the new one does not - in the form publish.sh
#    --remove-list reads. Nothing is deleted here or there by this script.
#
# Test builds are 0.2.0+betaN (PLAN-0.2 D8), and a feed names releases
# only, so a +label version is refused at step 4: test builds are handed
# out by hand, never through the feed.
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
py="${PYTHON:-python3}"
version="$(tr -d ' \t\r\n' < "$root/VERSION")"
dist="$HOME/build/shack-packet/dist"
releases="${RELEASES:-$HOME/build/shack-packet/release}"
notes=()
recommended=()
tests=1
while [ $# -gt 0 ]; do
    case "$1" in
        --version) version="$2"; shift 2 ;;
        --dist) dist="$2"; shift 2 ;;
        --releases) releases="$2"; shift 2 ;;
        --notes) notes=(--notes "$2"); shift 2 ;;
        --recommended) recommended=(--recommended); shift ;;
        --skip-tests) tests=0; shift ;;
        *) echo "release-feed.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done
die() { echo "release-feed.sh: $*" >&2; exit 1; }

if [ -z "${RELEASE_ALLOW_DIRTY:-}" ]; then           # the tests' way past step 1 only
    [ -z "$(git -C "$root" status --short)" ] || die "the tree has uncommitted changes; commit first"
fi
if [ "$tests" = 1 ]; then
    (cd "$root" && "$py" -m pytest -q -p no:cacheprovider tests/) || die "the suite failed; nothing released"
fi

mkdir -p "$releases"
old_feed="$(mktemp)"
trap 'rm -f "$old_feed"' EXIT
[ -f "$releases/update.json" ] && cp "$releases/update.json" "$old_feed"

found=0
for name in "OpenHamShack-Packet-Radio-$version-linux-all.deb" "OpenHamShack-Packet-Radio-$version-windows-x64.exe" \
            "OpenHamShack-Packet-Radio-$version-macos-arm64.dmg"; do
    src="$dist/$name"
    [ -f "$src" ] || continue
    [ -f "$src.sha256" ] || die "$name has no .sha256 beside it in $dist"
    want="$(cut -d' ' -f1 < "$src.sha256")"
    got="$(sha256sum "$src" | cut -d' ' -f1)"
    [ "$want" = "$got" ] || die "$name does not match its .sha256 (damaged on the way here?)"
    if [ -f "$releases/$name" ]; then
        [ "$(sha256sum "$releases/$name" | cut -d' ' -f1)" = "$got" ] \
            || die "$releases/$name already exists with different bytes; a fix is a new version"
    else
        cp "$src" "$src.sha256" "$releases/"
    fi
    echo "release: $name"
    found=$((found + 1))
done
[ "$found" -gt 0 ] || die "no installer of $version in $dist"

cat > "$releases/.htaccess" <<'HT'
# https://openhamshack.com/desktop/packet/ - written by shack-packet's
# tools/release-feed.sh on every release: edit it there, never on the server.
# The shape of openhamshack-nb's site/htaccess for /desktop/nb/ (Apache on 20i
# behind StackCDN). Only the installers and the feed are here.
Options -Indexes
AddDefaultCharset utf-8
# The last extension wins: x.deb.sha256 must be text, not a Debian package.
AddType application/vnd.debian.binary-package .deb
AddType application/x-apple-diskimage .dmg
AddType text/plain .sha256
AddType application/json .json
AddType application/octet-stream .sig
<IfModule mod_headers.c>
# Installers carry their version in their name and release-feed.sh never puts
# different bytes under a published name: kept for a year.
<FilesMatch "\.(deb|exe|dmg|sha256)$">
  Header set Cache-Control "public, max-age=31536000, immutable"
</FilesMatch>
# The feed changes in place: every client asks again each time, so no
# copy (the CDN edge's included) holds back a release for hours. Clients
# refuse a manifest older than one they have seen, so a stale copy costs a
# delay, never a step back.
<FilesMatch "^update\.json(\.sig)?$">
  Header set Cache-Control "no-cache"
</FilesMatch>
</IfModule>
HT

feed_args=()
[ -n "${RELEASE_UNTRUSTED_KEY_OK:-}" ] && feed_args+=(--untrusted-ok)   # the tests' throw-away key only
"$py" "$root/tools/update-feed.py" write --releases "$releases" --version "$version" "${notes[@]}" \
    "${recommended[@]}" "${feed_args[@]}"
verify_args=()
[ -n "${RELEASE_VERIFY_PUBKEY:-}" ] && verify_args+=(--pubkey "$RELEASE_VERIFY_PUBKEY")
"$py" "$root/tools/update-feed.py" verify --releases "$releases" "${verify_args[@]}"

"$py" - "$old_feed" "$releases/update.json" <<'PY'
import json, sys
def names(p):
    try:
        return {f["url"] for f in json.load(open(p, encoding="utf-8"))["files"]}
    except (OSError, ValueError, KeyError):
        return set()
gone = sorted(names(sys.argv[1]) - names(sys.argv[2]))
if gone:
    print("remove from the remote site:")
    for n in gone:
        print(f"  {n}")
        print(f"  {n}.sha256")
PY
echo "release-feed.sh: $version is in $releases; publish with tools/publish.sh (the feed goes last)"
