#!/bin/sh
# Stage (and with --image, build) the store form of Packet Radio: the
# controller's BBS (store/packet/module.yaml, docs/BBS-DESIGN.md).
#
#   tools/build-store.sh [--image] [--allow-dirty]
#
# Environment:
#   OHS_LIBS      the ohs-libs checkout to carry (default ~/wt/libs-030-c)
#   SHACK_STAGE   where the build context is staged (default ~/build/shack-packet/store-stage;
#                 never /tmp, a small shared tmpfs)
#
# The context is cut from HEAD with git archive (a package is exactly a
# commit; a dirty tree is refused unless --allow-dirty, and even then only
# committed files go in): store/packet/ at the top, shack_packet_box and
# shack_packet_app beside it, and ohs-libs/ from tools/ohslibs.py.
# --image builds it with podman as localhost/shack-packet:<commit> and
# runs the image's own self-check; nothing is installed, signed or
# published. A signed .d360mod is made by openhamshack-os
# tools/package-app.sh from this context, as every OpenHamShack app's is
# (QO-100 NB's tools/build-store.sh shows the call), at release time.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=0; ALLOW_DIRTY=0
for a in "$@"; do
    case "$a" in
        --image) IMAGE=1 ;;
        --allow-dirty) ALLOW_DIRTY=1 ;;
        *) echo "usage: $0 [--image] [--allow-dirty]" >&2; exit 2 ;;
    esac
done
STAGE=${SHACK_STAGE:-$HOME/build/shack-packet/store-stage}
OHS_LIBS=${OHS_LIBS:-$HOME/wt/libs-030-c}
cd "$ROOT"
if [ "$ALLOW_DIRTY" = 0 ] && [ -n "$(git status --porcelain)" ]; then
    echo "The tree has uncommitted changes; commit them (or --allow-dirty to stage HEAD anyway)." >&2
    exit 1
fi
COMMIT=$(git rev-parse --short=12 HEAD)
rm -rf "$STAGE"
mkdir -p "$STAGE"
git archive HEAD store/packet | tar -x -C "$STAGE"
mv "$STAGE/store/packet/"* "$STAGE/"
rm -rf "$STAGE/store"
git archive HEAD src/shack_packet_box src/shack_packet_app LICENSE | tar -x -C "$STAGE"
mv "$STAGE/src/shack_packet_box" "$STAGE/src/shack_packet_app" "$STAGE/"
rm -rf "$STAGE/src" "$STAGE/shack_packet_app/ui" "$STAGE/shack_packet_app/resources"
rm -f "$STAGE/shack_packet_app/VERSION"; cp VERSION "$STAGE/shack_packet_app/VERSION"   # a symlink in the repo
python3 tools/ohslibs.py --from "$OHS_LIBS" --to "$STAGE/ohs-libs" >/dev/null
echo "staged $COMMIT in $STAGE (ohs-libs $(cat "$STAGE/ohs-libs/OHS-LIBS.txt" 2>/dev/null | head -1))"
if [ "$IMAGE" = 1 ]; then
    log=$HOME/build/shack-packet/store-image-$COMMIT.log
    if ! podman build -q -t "localhost/shack-packet:$COMMIT" -f "$STAGE/Containerfile" "$STAGE" >"$log" 2>&1; then
        tail -n 20 "$log"; echo "image build FAILED (log $log)"; exit 1
    fi
    echo "image localhost/shack-packet:$COMMIT built (log $log)"
fi
