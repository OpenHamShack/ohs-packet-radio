#!/usr/bin/env bash
# Run tests against a pair of worktrees without building a venv for them.
#
#   tools/wt-test.sh [--libs <ohs-libs worktree>] [--app <app worktree>] [--venv <venv>] <pytest args...>
#
# Each release has a venv (~/build/venvs/rel-<version>) whose editable .pth
# files point at that release's worktrees. Copying and re-pointing one per
# feature branch (as every earlier release did) costs minutes and leaves
# venvs behind; PYTHONPATH comes before .pth entries on sys.path, so naming
# the worktrees here makes the venv's own copies invisible. Pytest args are
# relative to wherever you run from; with no --app, this script's own tree.
# Prints pytest's last lines only, and keeps the whole output in the log.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
app=$here; libs=""; venv=${WT_TEST_VENV:-$HOME/build/venvs/rel-0.2.3}
while [ $# -gt 0 ]; do
    case "$1" in
        --libs) libs=$2; shift 2 ;;
        --app) app=$2; shift 2 ;;
        --venv) venv=$2; shift 2 ;;
        *) break ;;
    esac
done
pp="$app/src"
if [ -n "$libs" ]; then
    for d in "$libs"/python/*/src; do pp="$pp:$d"; done
fi
log=${WT_TEST_LOG:-$HOME/build/wt-test-$(basename "$app").log}
mkdir -p "$(dirname "$log")"
set +e
QT_QPA_PLATFORM=offscreen PYTHONPATH="$pp" "$venv/bin/python" -m pytest -q -p no:cacheprovider "$@" >"$log" 2>&1
rc=$?
set -e
tail -n 15 "$log"
echo "wt-test: exit $rc (full output: $log)"
exit $rc
