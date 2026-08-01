#!/usr/bin/env bash
# Start/stop the model gateway by PID file (never pkill -f: the pattern matches
# the controlling shell's own command line and kills the caller).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDF="$ROOT/.gateway.pid"
case "${1:-}" in
  start)
    UPSTREAM="${2:?upstream model required}"; LOG="${3:-$ROOT/artifacts/gateway.jsonl}"
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "already running $(cat "$PIDF")"; exit 0; fi
    mkdir -p "$(dirname "$LOG")"
    # Kimi K3 rejects any temperature other than 1, so 1.0 is the only value all
    # harnesses can share. Decoding is therefore stochastic by force, not by
    # choice -- see the run-to-run variance section of the README.
    setsid nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/gateway.py" \
      --upstream "$UPSTREAM" --port 4010 --log "$LOG" --max-concurrency 4 \
      --temperature "${TEMPERATURE:-1.0}" \
      --budget-usd "${BUDGET_USD:-11.0}" \
      --budget-per-harness-usd "${BUDGET_PER_HARNESS_USD:-0}" \
      --price-in "${PRICE_IN:-0.95}" --price-cached "${PRICE_CACHED:-0.19}" \
      --price-out "${PRICE_OUT:-4.00}" \
      > "$ROOT/.gateway.out" 2>&1 &
    echo $! > "$PIDF"
    for _ in $(seq 1 40); do
      curl -sS --noproxy '*' -m 2 http://127.0.0.1:4010/health >/dev/null 2>&1 && { echo "up pid=$(cat "$PIDF") upstream=$UPSTREAM"; exit 0; }
      sleep 1
    done
    echo "FAILED to come up"; tail -5 "$ROOT/.gateway.out"; exit 1 ;;
  stop)
    [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"; echo stopped ;;
  status) curl -sS --noproxy '*' -m 3 http://127.0.0.1:4010/health || echo "down" ;;
esac
