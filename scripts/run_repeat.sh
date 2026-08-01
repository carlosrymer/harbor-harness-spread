#!/usr/bin/env bash
# Re-run harnesses over the SAME frozen subset to sample run-to-run variance.
#
# This is the control for the headline number. Decoding is stochastic (Kimi K2.7
# Code only accepts temperature=1), so some tasks will flip outcome between two
# identical runs. If the number of flips is comparable to the harness spread,
# the spread is not a ranking -- and the write-up has to say so.
#
# Usage: REP=2 HARNESSES="terminus-2 aider" bash scripts/run_repeat.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REP="${REP:-2}"
NCONC="${NCONC:-4}"
HARNESSES="${HARNESSES:?set HARNESSES}"

for H in $HARNESSES; do
  JOB="run-${H}--rep${REP}"
  echo "[run_repeat] ===== $H rep$REP starting $(date -u +%H:%M:%S) ====="
  MODEL_OVERRIDE=""
  [ "$H" = "aider" ] && MODEL_OVERRIDE="openai/openai/bench-model"
  export KEY_SUFFIX="--rep${REP}"
  if [ -n "$MODEL_OVERRIDE" ]; then
    MODEL="$MODEL_OVERRIDE" bash "$ROOT/scripts/run_harness.sh" "$H" "$JOB" "$NCONC"
  else
    bash "$ROOT/scripts/run_harness.sh" "$H" "$JOB" "$NCONC"
  fi
  echo "[run_repeat] ===== $H rep$REP finished $(date -u +%H:%M:%S) ====="
  "$ROOT/.venv/bin/python" "$ROOT/scripts/analyze.py" || true
  ( cd "$ROOT" && git add -A artifacts jobs && \
    git commit -q -m "variance: $H repeat run $REP over the same subset" || true )
  docker container prune -f >/dev/null 2>&1 || true
done
echo "[run_repeat] DONE"
