#!/usr/bin/env bash
# Oracle screen: run each candidate task's OWN reference solution through the
# identical Harbor pipeline, and keep only the tasks whose reference solution
# actually passes here.
#
# Why this exists: every Terminal-Bench 2.0 test.sh curl-installs uv from
# astral.sh and runs the task's pytest suite through uvx. Where that install
# cannot happen, the suite never runs and the script writes a hardcoded 0 to
# reward.txt -- a false zero that is indistinguishable from a real agent failure
# in Harbor's output. Screening with the reference solution proves the grader
# works before any agent is scored. It costs no model tokens, so it is the
# cheapest way to grow a trustworthy scored set.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CA=/ccr-ca.crt
MOUNTS='[{"type":"bind","source":"/root/.ccr/ca-bundle.crt","target":"/ccr-ca.crt","read_only":true},{"type":"bind","source":"/root/.local/bin/uv","target":"/usr/local/bin/uv","read_only":true},{"type":"bind","source":"/root/.local/bin/uvx","target":"/usr/local/bin/uvx","read_only":true}]'
CAND="${1:-$ROOT/artifacts/candidates.json}"
JOB="${2:-oracle-screen}"
NCONC="${3:-4}"
MIN_FREE_GB="${MIN_FREE_GB:-8}"

mapfile -t IDS < <("$ROOT/.venv/bin/python" -c "
import json;print('\n'.join(t['id'] for t in json.load(open('$CAND'))['tasks']))")
mapfile -t IMGS < <("$ROOT/.venv/bin/python" -c "
import json;print('\n'.join(t['docker_image'] for t in json.load(open('$CAND'))['tasks']))")

# Skip tasks already screened by an earlier oracle job. Re-screening wastes time,
# and re-pulling their images can be actively dangerous: one Terminal-Bench image
# in this set is 14.7GB, enough to exhaust the disk on its own.
mapfile -t DONE < <("$ROOT/.venv/bin/python" - <<'PY'
import pathlib
root = pathlib.Path(".")
seen = set()
for job in (root / "jobs").glob("oracle-*"):
    if job.is_dir():
        for d in job.iterdir():
            if d.is_dir() and (d / "verifier" / "reward.txt").exists():
                seen.add(d.name.rsplit("__", 1)[0])
print("\n".join(sorted(seen)))
PY
)
is_done() { local t="$1"; for x in "${DONE[@]:-}"; do [ "$x" = "$t" ] && return 0; done; return 1; }
echo "[screen] already screened: ${#DONE[@]} task(s) - skipping those"

# Pull images, skipping any that will not fit. Disk is the binding constraint
# here, not budget: a benchmark image can be 15GB.
KEEP=()
for i in "${!IDS[@]}"; do
  if is_done "${IDS[$i]}"; then echo "[screen] SKIP ${IDS[$i]} - already screened"; continue; fi
  free=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  if [ "$free" -lt "$MIN_FREE_GB" ]; then
    echo "[screen] SKIP ${IDS[$i]} - only ${free}GB free"; continue
  fi
  if docker image inspect "${IMGS[$i]}" >/dev/null 2>&1 || docker pull -q "${IMGS[$i]}" >/dev/null 2>&1; then
    KEEP+=("${IDS[$i]}")
  else
    echo "[screen] PULL FAILED ${IDS[$i]} (${IMGS[$i]})"
  fi
done
echo "[screen] pullable: ${#KEEP[@]} / ${#IDS[@]}"

INC=(); for t in "${KEEP[@]}"; do INC+=(-i "$t"); done
"$ROOT/.venv/bin/harbor" run -p "$ROOT/terminal-bench" "${INC[@]}" -a oracle \
  -n "$NCONC" -o "$ROOT/jobs" --job-name "$JOB" --yes --quiet \
  --agent-timeout-multiplier 1.0 --mounts "$MOUNTS" \
  --ve SSL_CERT_FILE=$CA --ve REQUESTS_CA_BUNDLE=$CA --ve CURL_CA_BUNDLE=$CA \
  --ve PIP_CERT=$CA --ve UV_NATIVE_TLS=1
echo "[screen] DONE $JOB"
