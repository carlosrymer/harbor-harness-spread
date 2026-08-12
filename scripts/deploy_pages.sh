#!/usr/bin/env bash
# Publish the static site so GitHub Pages serves it.
#
# Pages here builds from `main` (its source was set that way and the REST Pages
# endpoints are blocked by this environment's proxy, so it cannot be changed back
# to gh-pages from inside the build). So the built site is emitted at the repo
# root of `main`: index.html + data/ + .nojekyll. The gh-pages branch is kept in
# sync too, so the repo works under either Pages source setting.
#
# .nojekyll matters: without it Pages runs Jekyll over the whole repository, which
# is what made the main-branch builds fail.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 1. assemble site/data from the committed artifacts
rm -rf site/data
mkdir -p site/data
cp artifacts/results.json site/data/results.json
[ -d artifacts/trajectories ] && cp -r artifacts/trajectories site/data/trajectories
touch site/.nojekyll

# 2. emit the site at the repo root for Pages-from-main
rm -rf data
cp site/index.html index.html
cp -r site/data data
touch .nojekyll

# 3. mirror to gh-pages as well (append, never force: a rewritten history does not
#    reliably retrigger the Pages build)
WORK="$(mktemp -d)"
if git clone -q --branch gh-pages --depth 1 \
     "https://github.com/carlosrymer/harbor-harness-spread.git" "$WORK" 2>/dev/null; then
  find "$WORK" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
else
  rm -rf "$WORK"; mkdir -p "$WORK"; git -C "$WORK" init -q
  git -C "$WORK" checkout -qb gh-pages
  git -C "$WORK" remote add origin "https://github.com/carlosrymer/harbor-harness-spread.git"
fi
cp -r site/. "$WORK/"
git -C "$WORK" config user.email "carlos.rymer@gmail.com"
git -C "$WORK" config user.name "Carlos Rymer"
git -C "$WORK" add -A
if ! git -C "$WORK" diff --cached --quiet; then
  git -C "$WORK" commit -q -m "Publish harness-spread site $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for i in 1 2 3 4; do
    git -C "$WORK" push origin gh-pages && break
    sleep $((i*10))
  done
fi
rm -rf "$WORK"
echo "[deploy] site staged at repo root and mirrored to gh-pages"
echo "[deploy] commit and push main to publish: https://carlosrymer.github.io/harbor-harness-spread/"
