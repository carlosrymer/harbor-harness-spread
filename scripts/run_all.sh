#!/usr/bin/env bash
# Run the full harness sweep over the frozen subset, one harness at a time.
#
# Sequential by harness on purpose: it keeps the number of concurrent containers
# bounded (disk here is tight), keeps the gateway's upstream concurrency
# predictable so no harness is systematically slowed by another's traffic, and
# means a failure part-way through still leaves every completed harness's
# artifacts on disk and committed.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NCONC="${NCONC:-4}"
HARNESSES="${HARNESSES:-terminus-2 mini-swe-agent aider goose}"
MODEL_SLUG="${MODEL_SLUG:?set MODEL_SLUG}"

for H in $HARNESSES; do
  JOB="run-${MODEL_SLUG}--${H}"
  if [ -f "$ROOT/jobs/$JOB/result.json" ] && \
     "$ROOT/.venv/bin/python" -c "
import json,sys
d=json.load(open('$ROOT/jobs/$JOB/result.json'))
sys.exit(0 if d.get('finished_at') else 1)" 2>/dev/null; then
    echo "[run_all] SKIP $H (already finished)"
    continue
  fi

  # Aider's Harbor adapter splits provider off the model name and passes the rest
  # to aider, so it needs the prefix doubled to end up with a provider-qualified
  # model on the inside. Every other harness takes openai/bench-model directly.
  MODEL_OVERRIDE=""
  [ "$H" = "aider" ] && MODEL_OVERRIDE="openai/openai/bench-model"

  # OpenCode's default OpenAI provider package speaks the Responses API, which
  # this gateway does not implement; pinning the openai-compatible package puts
  # it back on /chat/completions like every other harness.
  EXTRA=()
  [ "$H" = "opencode" ] && EXTRA=(--ak 'opencode_config={"provider":{"openai":{"npm":"@ai-sdk/openai-compatible"}}}')

  echo "[run_all] ===== $H starting $(date -u +%H:%M:%S) ====="
  if [ -n "$MODEL_OVERRIDE" ]; then
    MODEL="$MODEL_OVERRIDE" bash "$ROOT/scripts/run_harness.sh" "$H" "$JOB" "$NCONC" "${EXTRA[@]}"
  else
    bash "$ROOT/scripts/run_harness.sh" "$H" "$JOB" "$NCONC" "${EXTRA[@]}"
  fi
  echo "[run_all] ===== $H finished $(date -u +%H:%M:%S) ====="

  # Refresh analysis + commit artifacts after every harness so an expensive run
  # is never lost to a later failure.
  "$ROOT/.venv/bin/python" "$ROOT/scripts/analyze.py" || true
  ( cd "$ROOT" && git add -A artifacts jobs && \
    git commit -q -m "results: $H over the frozen 12-task subset" || true )

  docker container prune -f >/dev/null 2>&1 || true
  df -h / | tail -1
done
echo "[run_all] ALL HARNESSES DONE"
