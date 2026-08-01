#!/usr/bin/env bash
# Publish site/ (plus the committed run artifacts it reads) to the gh-pages branch.
#
# GitHub Pages auto-enables for a repo when a gh-pages branch appears, which is the
# only route available here: the token has no `workflow` scope (so an Actions
# workflow cannot be pushed) and the Pages REST endpoints are blocked by the proxy.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rm -rf site/data
mkdir -p site/data
cp artifacts/results.json site/data/results.json
[ -d artifacts/trajectories ] && cp -r artifacts/trajectories site/data/trajectories
touch site/.nojekyll   # keep Pages from running Jekyll over the static output

WORK="$(mktemp -d)"
cp -r site/. "$WORK/"
cd "$WORK"
git init -q
git config user.email "carlos.rymer@gmail.com"
git config user.name "Carlos Rymer"
git checkout -qb gh-pages
git add -A
git commit -q -m "Publish harness-spread site $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git remote add origin "https://github.com/carlosrymer/harbor-harness-spread.git"

for i in 1 2 3 4; do
  if git push -f origin gh-pages; then echo "[deploy] pushed gh-pages (attempt $i)"; ok=1; break; fi
  echo "[deploy] push attempt $i failed"; sleep $((i*10))
done
cd "$ROOT"; rm -rf "$WORK"
[ "${ok:-0}" = "1" ] || { echo "[deploy] FAILED"; exit 1; }
echo "[deploy] site pushed. Pages URL: https://carlosrymer.github.io/harbor-harness-spread/"
