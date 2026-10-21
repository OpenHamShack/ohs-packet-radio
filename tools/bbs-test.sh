#!/usr/bin/env bash
# The controller BBS's tests (docs/BBS-DESIGN.md), against this worktree
# and an ohs-libs worktree, without building a venv for them.
#
#   tools/bbs-test.sh [--libs <ohs-libs worktree>] [--venv <venv>] [libs|app|all] [pytest args...]
#
# libs: ohs-libs shack_packet's BBS, forwarding, room and ID tests;
# app: this repo's store-app tests (tests/test_box_*.py); all: both.
# PYTHONPATH comes before the venv's editable .pth entries, so naming the
# worktrees here hides the release venv's own copies. Prints the last
# lines of each run; the whole output is in the log.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
libs=${BBS_TEST_LIBS:-$HOME/wt/libs-030-c}
venv=${BBS_TEST_VENV:-$HOME/build/venvs/rel-0.2.3}
while [ $# -gt 0 ]; do
    case "$1" in
        --libs) libs=$2; shift 2 ;;
        --venv) venv=$2; shift 2 ;;
        *) break ;;
    esac
done
what=${1:-all}; [ $# -gt 0 ] && shift
pp="$here/src"
for d in "$libs"/python/*/src; do pp="$pp:$d"; done
log=$HOME/build/bbs-test-$(basename "$here").log
base=$HOME/build/pytest-bbs-$$
mkdir -p "$base"; trap 'rm -rf "$base"' EXIT
: >"$log"
rc=0
run() {
    local name=$1; shift
    echo "== $name" >>"$log"
    set +e
    (cd "$1" && shift && OPENBLAS_NUM_THREADS=1 QT_QPA_PLATFORM=offscreen PYTHONPATH="$pp" \
        "$venv/bin/python" -m pytest -q -p no:cacheprovider --basetemp="$base/$name" "$@") >>"$log" 2>&1
    local r=$?
    set -e
    echo "$name: $(grep -E '^[0-9]+ (passed|failed)|passed|failed|error' "$log" | tail -n 1) (exit $r)"
    [ $r -eq 0 ] || rc=$r
}
lt="$libs/python/shack_packet/tests"
case "$what" in
    libs) run libs "$lt" test_bbs.py test_fbb.py "$@" ;;
    app) run app "$here" tests/test_box_*.py "$@" ;;
    all) run libs "$lt" test_bbs.py test_fbb.py "$@"; run app "$here" tests/test_box_*.py "$@" ;;
    *) echo "libs, app or all" >&2; exit 2 ;;
esac
echo "bbs-test: exit $rc (full output: $log)"
exit $rc
