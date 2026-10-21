#!/usr/bin/env bash
# Publish a Packet Radio release's source on https://openhamshack.com/source/
# - tools/publish.sh source runs this. Before any installer of the release
# is put on offer: the GPL's source must be there first, and stay for as
# long as the installer is offered.
#
#   tools/publish.sh source [--dry-run] [--version V]
#
# The shared source site is openhamshack-nb's (docs/source-site.md there, served
# as HOW-TO-PUBLISH.txt): one folder on shack, /srv/source-site, with a
# manifest per project; its tool, tools/source-site.py, is called by path
# from a checkout of openhamshack-nb ($NB_REPO, default ~/openhamshack-nb), as that
# document asks other projects to. Packet Radio's manifest is
# openhamshack-packet, anchored #openhamshack-packet, which THIRD-PARTY.txt
# in every installer names. Three things go in, in this order:
#
#   1. The third-party list, packaging/sources.txt (source-site.py publish:
#      fetches what the site lacks, checks every file's SHA-256, and only
#      then writes the manifest).
#   2. This release's own source: the release commit's tree, made
#      reproducibly by source-site.py tarball (HANDOVER.md and the scrub
#      list are export-ignore; packaging/source-scrub.txt is what it hides),
#      as openhamshack-packet-<v>-src.tar.gz.
#   3. The OpenHamShack libraries inside the installers: exactly the
#      vendored folder build/ohs-libs that tools/ohslibs.py copied from
#      the ohs-libs commit the release names (OHS-LIBS.txt), tarred the
#      same way - sorted, owner 0, every member stamped with that commit's
#      time, gzip -n - so the same commit always gives the same bytes, as
#      openhamshack-packet-<v>-ohs-libs-src.tar.gz. Only what ships: the
#      ohs-libs repository holds other products' code and test tools that
#      name internal hosts.
#
# Then the site folder is uploaded with openhamshack-nb's tools/publish.sh
# source (whose own run backs up, uploads only what changed, and checks
# every file over HTTPS), and the result is checked from outside with
# source-site.py check --live. Nothing is ever removed here: a release's
# source stays for as long as any of its binaries is offered.
#
# --dry-run makes and checks the two tarballs and stops before the shared
# site is touched.
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
nb="${NB_REPO:-$HOME/openhamshack-nb}"
site_tool="$nb/tools/source-site.py"
project=openhamshack-packet
title="OpenHamShack Packet Radio"
home=https://openhamshack.com/apps/packet/
die() { echo "publish.sh source: $*" >&2; exit 1; }
dry=0
version="$(tr -d ' \t\r\n' < "$root/VERSION")"
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry=1; shift ;;
        --version) version="$2"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done
[ -f "$site_tool" ] || die "no $site_tool (set NB_REPO to a openhamshack-nb checkout)"
[ -z "$(git -C "$root" status --short)" ] || die "$root has uncommitted changes"
[ "$(tr -d ' \t\r\n' < "$root/VERSION")" = "$version" ] || die "VERSION is not $version"

work="$HOME/build/shack-packet/rel-$version"
commits="$work/commits.txt"
[ -f "$commits" ] || die "no $commits: run tools/release.sh $version first"
app_sha="$(awk '$1 == "app" {print $2}' "$commits")"
libs_sha="$(awk '$1 == "ohs-libs" {print $2}' "$commits")"
git -C "$root" merge-base --is-ancestor "$app_sha" HEAD || die "the release commit $app_sha is not in this branch"
vend="$root/build/ohs-libs"
grep -q "ohs-libs $libs_sha\|ohs-libs ${libs_sha:0:7}" "$vend/OHS-LIBS.txt" 2>/dev/null \
    || die "$vend is not the vendored copy of ohs-libs $libs_sha (run tools/ohslibs.py from that commit)"

out="$work/source"
rm -rf "$out" && mkdir -p "$out"

# 2. The app's own source.
own="$(python3 "$site_tool" tarball "$root" "$app_sha" "$project-$version" --out "$out" | tail -1)"
[ -f "$own" ] || die "source-site.py tarball made no file ($own)"
echo "   $(basename "$own"): $(sha256sum "$own" | cut -d' ' -f1)"

# 3. The vendored libraries, reproducibly.
libs="$out/$project-$version-ohs-libs-src.tar.gz"
stamp="$(git -C "${OHS_LIBS:-$HOME/wt/packet-modem}" log -1 --format=%ct "$libs_sha" 2>/dev/null \
    || git -C "$root" log -1 --format=%ct "$app_sha")"
# OHS-LIBS.txt says which checkout it was copied from - a path in
# somebody's home - so the tarball's copy names the commit alone.
tree="$out/ohs-libs-tree"
rm -rf "$tree" && cp -a "$vend" "$tree"
printf 'ohs-libs %s\n' "$libs_sha" > "$tree/OHS-LIBS.txt"
touch -d "@$stamp" "$tree/OHS-LIBS.txt"
tar --sort=name --mtime="@$stamp" --owner=0 --group=0 --numeric-owner --mode='u+rwX,go+rX,go-w' \
    --exclude=__pycache__ --exclude='*.pyc' --transform "s|^\.|$project-$version-ohs-libs|" \
    -C "$tree" -cf - . | gzip -n -9 > "$libs"
rm -rf "$tree"
echo "   $(basename "$libs"): $(sha256sum "$libs" | cut -d' ' -f1)"

# Nothing public may name the build hosts or accounts: the scrub list's
# @forbid patterns, applied here to the libraries' tarball too, which no
# scrub list reads. (Read from the list, not written here, or this very
# file would trip the app tarball's check.)
forbid="$(sed -n 's/^@forbid (?i)//p' "$root/packaging/source-scrub.txt" | paste -sd'|')"
if [ -n "$forbid" ] && tar -xzOf "$libs" | grep -aiqE "$forbid"; then
    die "$(basename "$libs") names an internal host or account; not publishing it"
fi

if [ "$dry" = 1 ]; then
    echo "publish.sh source: dry run: both tarballs made in $out; the source site is untouched"
    exit 0
fi

# 1-3 onto the site folder, the list first.
python3 "$site_tool" publish "$project" "$root/packaging/sources.txt" --title "$title" --home "$home"
python3 "$site_tool" publish-own "$project" "$own" --licence GPL-3.0-or-later \
    --carried "Packet $version: every installer of it" \
    --covers "OpenHamShack Packet Radio $version itself - the whole repository at its release commit $app_sha" \
    --title "$title" --home "$home"
python3 "$site_tool" publish-own "$project" "$libs" --licence GPL-3.0-or-later \
    --carried "Packet $version: every installer of it" \
    --covers "the OpenHamShack libraries inside Packet Radio $version (shack_packet, shack_rig, shack_pluto, shack_client, shack_pad, shack_bandplan, shack_i18n), exactly as vendored from ohs-libs $libs_sha" \
    --title "$title" --home "$home"
python3 "$site_tool" check "$project"

# Up, by openhamshack-nb's own upload for this folder, then checked from outside.
"$nb/tools/publish.sh" source
python3 "$site_tool" check "$project" --live https://openhamshack.com/source/
echo "publish.sh source: $project $version live at https://openhamshack.com/source/#$project"
