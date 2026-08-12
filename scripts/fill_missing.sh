#!/usr/bin/env bash
# Re-run only the (harness × task) cells that never got a fair attempt — cells
# lost to a gateway budget cut-off or an infrastructure error. Completed cells are
# never re-run, so a fill is cheap and cannot overwrite a real result.
# Usage: HARNESS=terminus-2 MODEL_SLUG=gemini36 bash scripts/fill_missing.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
H="${HARNESS:?set HARNESS}"; MS="${MODEL_SLUG:?set MODEL_SLUG}"
MULT="${AGENT_TIMEOUT_MULT:-0.4}"; NC="${NCONC:-4}"
JOB="run-${MS}--${H}"; TMP="fill-${H}-$$"
CA=/ccr-ca.crt
MOUNTS='[{"type":"bind","source":"/root/.ccr/ca-bundle.crt","target":"/ccr-ca.crt","read_only":true},{"type":"bind","source":"/root/.local/bin/uv","target":"/usr/local/bin/uv","read_only":true},{"type":"bind","source":"/root/.local/bin/uvx","target":"/usr/local/bin/uvx","read_only":true}]'

mapfile -t MISSING < <("$ROOT/.venv/bin/python" - "$JOB" <<'PY'
import json, pathlib, sys
root = pathlib.Path(".")
scored = [t["id"] for t in json.loads((root/"artifacts/scored_subset.json").read_text())["tasks"]]
job = root / "jobs" / sys.argv[1]
done = set()
if job.is_dir():
    for d in job.iterdir():
        if (d/"verifier"/"reward.txt").exists():
            done.add(d.name.rsplit("__", 1)[0])
print("\n".join(t for t in scored if t not in done))
PY
)
[ "${#MISSING[@]}" -eq 0 ] && { echo "[fill] nothing missing for $H"; exit 0; }
echo "[fill] $H missing ${#MISSING[@]}: ${MISSING[*]}"

INC=(); for t in "${MISSING[@]}"; do [ -n "$t" ] && INC+=(-i "$t"); done
KEY="sk-harness-${H}__${MS}"
MODEL="openai/bench-model"; [ "$H" = "aider" ] && MODEL="openai/openai/bench-model"
EXTRA=(); [ "$H" = "opencode" ] && EXTRA=(--ak 'opencode_config={"provider":{"openai":{"options":{"baseURL":"http://172.17.0.1:4010/v1"}}}}')

OPENAI_API_KEY="$KEY" OPENAI_BASE_URL=http://172.17.0.1:4010/v1 OPENAI_API_BASE=http://172.17.0.1:4010/v1 \
"$ROOT/.venv/bin/harbor" run -p "$ROOT/terminal-bench" "${INC[@]}" -a "$H" -m "$MODEL" \
  -n "$NC" -k 1 -o "$ROOT/jobs" --job-name "$TMP" --yes --quiet \
  --agent-timeout-multiplier "$MULT" --mounts "$MOUNTS" \
  --ae SSL_CERT_FILE=$CA --ae REQUESTS_CA_BUNDLE=$CA --ae CURL_CA_BUNDLE=$CA --ae NODE_EXTRA_CA_CERTS=$CA \
  --ae GIT_SSL_CAINFO=$CA --ae PIP_CERT=$CA --ae CARGO_HTTP_CAINFO=$CA \
  --ae OPENAI_HOST=http://172.17.0.1:4010 --ae OPENAI_BASE_PATH=v1/chat/completions \
  --ae OPENAI_API_KEY="$KEY" --ae OPENAI_BASE_URL=http://172.17.0.1:4010/v1 --ae OPENAI_API_BASE=http://172.17.0.1:4010/v1 \
  --ve SSL_CERT_FILE=$CA --ve REQUESTS_CA_BUNDLE=$CA --ve CURL_CA_BUNDLE=$CA --ve PIP_CERT=$CA --ve UV_NATIVE_TLS=1 \
  "${EXTRA[@]}"

mkdir -p "jobs/$JOB"
for d in jobs/$TMP/*__*/; do
  [ -d "$d" ] || continue
  task=$(basename "$d" | sed 's/__.*//')
  # never clobber a cell that already produced a reward
  if ls "jobs/$JOB"/${task}__*/verifier/reward.txt >/dev/null 2>&1; then rm -rf "$d"; continue; fi
  mv "$d" "jobs/$JOB/" && echo "[fill] merged $task"
done
rm -rf "jobs/$TMP"
echo "[fill] DONE $H"
