#!/usr/bin/env bash
# Kill in-flight `harbor run` processes WITHOUT matching the calling shell.
# `pgrep -f harbor` is unusable here: the repo path itself contains "harbor",
# so any pattern mentioning it also matches this script's own command line and
# the caller kills itself (exit 143/144). Match on the venv python + the harbor
# entrypoint instead, and explicitly skip self and ancestors.
set -uo pipefail
SELF=$$
for p in $(ps -eo pid=,args= | awk '$0 ~ /\.venv\/bin\/harbor/ && $0 !~ /kill_runs/ {print $1}'); do
  [ "$p" = "$SELF" ] && continue
  [ "$p" = "$PPID" ] && continue
  kill "$p" 2>/dev/null && echo "killed $p"
done
exit 0
