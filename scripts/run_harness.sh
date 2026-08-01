#!/usr/bin/env bash
# Run ONE harness over the frozen 12-task Terminal-Bench 2.0 subset.
#
# Everything that is not the harness is held constant here: the same task list,
# the same model (pinned by the gateway, not by this flag), the same per-task
# wall-clock budget from the task definition, one attempt per task, and the same
# container trust/network setup.
#
# Usage: scripts/run_harness.sh <harness> <job-name> [n_concurrent] [extra harbor args...]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="${1:?harness required}"
JOB="${2:?job name required}"
NCONC="${3:-2}"
shift 3 2>/dev/null || shift $#

TASKS_FILE="$ROOT/artifacts/subset.json"
CA=/ccr-ca.crt
GW_HOST="http://172.17.0.1:4010/v1"       # host-side agents (terminus-*)
GW_CONTAINER="http://172.17.0.1:4010/v1"  # installed agents run in the container
# Same URL both sides on purpose: the OpenCode adapter reads OPENAI_BASE_URL from
# the *host* env and bakes it into the in-container config, so a 127.0.0.1 host URL
# would be written into a container that cannot reach it.
# Repeat runs get their own meter identity (KEY_SUFFIX) so their tokens are not
# added to the primary run's cost. Without this a harness that was repeated would
# look proportionally more expensive than one that was not.
KEY="sk-harness-${HARNESS}${KEY_SUFFIX:-}"

# CA bundle: this environment terminates TLS at an egress proxy, so containers
# must trust its CA. uv is bind-mounted because astral.sh is not reachable from
# inside the containers here (503 through the transparent proxy), and several
# harnesses bootstrap themselves with it.
MOUNTS='[{"type":"bind","source":"/root/.ccr/ca-bundle.crt","target":"/ccr-ca.crt","read_only":true},{"type":"bind","source":"/root/.local/bin/uv","target":"/usr/local/bin/uv","read_only":true}]'

INC=()
while read -r t; do INC+=(-i "$t"); done < <("$ROOT/.venv/bin/python" -c "
import json;print('\n'.join(t['id'] for t in json.load(open('$TASKS_FILE'))['tasks']))")

echo "[run_harness] harness=$HARNESS job=$JOB tasks=${#INC[@]} n_concurrent=$NCONC"

OPENAI_API_KEY="$KEY" OPENAI_BASE_URL="$GW_HOST" OPENAI_API_BASE="$GW_HOST" \
"$ROOT/.venv/bin/harbor" run \
  -p "$ROOT/terminal-bench" "${INC[@]}" \
  -a "$HARNESS" -m "${MODEL:-openai/bench-model}" \
  -n "$NCONC" -k 1 \
  --agent-timeout-multiplier "${AGENT_TIMEOUT_MULT:-1.0}" \
  -o "$ROOT/jobs" --job-name "$JOB" --yes --quiet \
  --mounts "$MOUNTS" \
  --ae SSL_CERT_FILE=$CA --ae REQUESTS_CA_BUNDLE=$CA --ae CURL_CA_BUNDLE=$CA \
  --ae NODE_EXTRA_CA_CERTS=$CA --ae GIT_SSL_CAINFO=$CA --ae PIP_CERT=$CA \
  --ae CARGO_HTTP_CAINFO=$CA \
  --ae OPENAI_API_KEY="$KEY" \
  --ae OPENAI_BASE_URL="$GW_CONTAINER" --ae OPENAI_API_BASE="$GW_CONTAINER" \
  --ae OPENAI_HOST="http://172.17.0.1:4010" --ae OPENAI_BASE_PATH="v1/chat/completions" \
  "$@"
echo "[run_harness] EXIT=$? harness=$HARNESS job=$JOB"
