#!/usr/bin/env bash
# Upload Packet Radio's release folder to https://openhamshack.com/desktop/packet/.
#
#   tools/publish.sh [packet] [--dry-run] [--releases DIR] [--remove PATH]... [--remove-list FILE]
#   tools/publish.sh source [--dry-run]          (tools/publish-source.sh)
#
# openhamshack-nb tools/publish.sh's nb/ham steps (9f2a3ed), for this app's one
# folder: each tool touches only its own folders on openhamshack.com (GUIDE
# §6), and NB's belongs to the NB session. 20i package PACKAGE_ID,
# user@ssh.example-host, public_html/desktop/packet.
#
# Uploads only what the feed offers: the files update.json names, their
# .sha256, .htaccess, then update.json.sig and update.json LAST, so a
# client never sees a manifest before what it names is there. Then deletes
# exactly what --remove/--remove-list name (release.sh's "remove from the
# remote site:" block, piped through as it is), and finally fetches every
# uploaded file over HTTPS with a cache-buster and checks its SHA-256.
# Before changing anything it backs up what is about to change to
# ~/backups/packet-<date>-pre-<version>.
#
# The key: only the month upload key in ~/.config/openhamshack/20i/
# (openhamshack-os publish-web.sh key-month makes it; valid until the date
# in its meta). NB's publish.sh can also mint a temporary key through a
# lab session on the hosting account; that path is not copied here - it depends on a
# job's scratch file - so with no valid month key this stops and says so.
# A brand-new folder answers 403 for about a minute on 20i (GUIDE §5), so
# the verify step retries for a few minutes before calling it broken.
#
# Not yet run for real (no Packet Radio release has been published to this
# folder): tests/test_release.py runs it with ssh, rsync and curl stubbed.
set -euo pipefail

# tools/publish.sh packet|source (Brian, 18/10/2026: the same two-target
# shape as openhamshack-nb's nb|ham|source). packet is this folder's upload,
# below, and the default; source is tools/publish-source.sh, which must run
# first for a release, so no installer is ever on offer without its source.
case "${1:-}" in
    source) shift; exec "$(dirname "$(readlink -f "$0")")/publish-source.sh" "$@" ;;
    packet) shift ;;
esac

prog=$(basename "$0")
releases=${RELEASES:-$HOME/build/shack-packet/release}
remote_host=${PUBLISH_REMOTE_HOST:user@ssh.example-host}
remote_subdir=${PUBLISH_REMOTE_SUBDIR:-public_html/desktop/packet}
public_base=${PUBLISH_PUBLIC_BASE:-https://openhamshack.com/desktop/packet}
key_dir=${PUBLISH_KEY_DIR:-$HOME/.config/openhamshack/20i}
backup_root=${PUBLISH_BACKUP_ROOT:-$HOME/backups}
verify_tries=${PUBLISH_VERIFY_TRIES:-10}
verify_wait=${PUBLISH_VERIFY_WAIT:-20}
dry_run=0
remove_paths=()
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=1; shift ;;
        --releases) releases=$2; shift 2 ;;
        --remove) remove_paths+=("$2"); shift 2 ;;
        --remove-list)
            [ -f "$2" ] || { echo "$prog: --remove-list $2: no such file" >&2; exit 2; }
            while IFS= read -r line || [ -n "$line" ]; do
                case "$line" in "  "*) remove_paths+=("${line#  }") ;; esac
            done < "$2"
            shift 2 ;;
        *) echo "$prog: unknown argument $1" >&2; exit 2 ;;
    esac
done
log() { echo "$prog: $*"; }
for p in "${remove_paths[@]}"; do
    case "$p" in */*|.*|"") log "refusing to remove '$p': only a file name in the folder"; exit 2 ;; esac
done

[ -f "$releases/update.json" ] && [ -f "$releases/update.json.sig" ] \
    || { log "$releases has no signed feed (tools/release.sh writes it)"; exit 1; }
key="$key_dir/id_ed25519"
expires=$(sed -n 's/^expires=//p' "$key_dir/meta" 2>/dev/null || true)
if [ ! -f "$key" ] || [ "${expires:-0}" -le $(( $(date +%s) + 3600 )) ] 2>/dev/null; then
    log "no month upload key valid for another hour in $key_dir; renew it (openhamshack-os publish-web.sh key-month)"
    exit 1
fi
ssh_cmd=(ssh -i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20)

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# What the feed offers, in upload order, the feed last.
python3 - "$releases" > "$tmp/offer" <<'PY'
import json, os, sys
d = sys.argv[1]
doc = json.load(open(os.path.join(d, "update.json"), encoding="utf-8"))
if doc.get("app") != "packet":
    sys.exit(f"update.json is for {doc.get('app')!r}, not packet")
for f in doc["files"]:
    n = f["url"]
    if "/" in n or n.startswith("."):
        sys.exit(f"update.json names {n!r}, not a file beside it")
    for x in (n, n + ".sha256"):
        if not os.path.isfile(os.path.join(d, x)):
            sys.exit(f"{x} is named by the feed but not in {d}")
        print(x)
if os.path.isfile(os.path.join(d, ".htaccess")):
    print(".htaccess")
print("update.json.sig")
print("update.json")
PY
version=$(python3 -c 'import json,sys; print(max(f["version"] for f in json.load(open(sys.argv[1]))["files"]))' \
    "$releases/update.json")
mapfile -t files < "$tmp/offer"

if [ "$dry_run" = 1 ]; then
    for f in "${files[@]}"; do log "would upload: $f"; done
    for p in "${remove_paths[@]}"; do log "would remove: $p"; done
    log "dry run: nothing uploaded, removed or verified"
    exit 0
fi

"${ssh_cmd[@]}" "$remote_host" mkdir -p "$remote_subdir"
backup="$backup_root/packet-$(date -u +%F)-pre-$version"
mkdir -p "$backup"
printf '%s\n' "${files[@]}" "${remove_paths[@]}" | sort -u > "$tmp/backup-list"
rsync -az --ignore-missing-args --files-from="$tmp/backup-list" -e "${ssh_cmd[*]}" \
    "$remote_host:$remote_subdir/" "$backup/"
log "backed up what is about to change to $backup"

# The payload (never --delete), then the feed's signature, then the feed.
for f in "${files[@]}"; do
    case "$f" in update.json|update.json.sig) continue ;; esac
    rsync -z --chmod=F644 -e "${ssh_cmd[*]}" "$releases/$f" "$remote_host:$remote_subdir/$f"
done
for f in update.json.sig update.json; do
    rsync -z --chmod=F644 -e "${ssh_cmd[*]}" "$releases/$f" "$remote_host:$remote_subdir/$f"
done
log "uploaded ${#files[@]} files, the feed last"
for p in "${remove_paths[@]}"; do
    "${ssh_cmd[@]}" "$remote_host" rm -f -- "$remote_subdir/$p"
    log "removed $p from the remote site"
done

fail=0
for f in "${files[@]}"; do
    case "$f" in .*) continue ;; esac          # .htaccess is never served: a 403 is the server working
    want=$(sha256sum "$releases/$f" | cut -d' ' -f1)
    ok=0
    for _ in $(seq 1 "$verify_tries"); do
        got=$(curl -sS -f "$public_base/$f?v=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')" 2>/dev/null \
              | sha256sum | cut -d' ' -f1)
        [ "$got" = "$want" ] && { ok=1; break; }
        sleep "$verify_wait"
    done
    [ "$ok" = 1 ] || { log "verify FAILED: $public_base/$f does not serve what was uploaded"; fail=1; }
done
[ "$fail" = 0 ] || { log "check the site before telling anyone it is live"; exit 1; }
log "published $version to $public_base/ (every file verified over HTTPS)"
