#!/usr/bin/env bash
# Publish the static site so GitHub Pages serves it.
#
# Pages for this repo is configured as "main branch, /docs folder", and the REST
# Pages endpoints are blocked by this environment's proxy so that setting cannot
# be changed from inside the build. The built site is therefore written to docs/
# on main. gh-pages is kept mirrored as well, so the repo also works if the Pages
# source is ever switched back to that branch.
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

# 2. emit the built site into docs/ on main.
#    This repo's Pages source is "main branch /docs folder"; the REST Pages
#    endpoints are blocked by this environment's proxy so it cannot be changed
#    from inside the build. Publishing anywhere else just makes the Pages build
#    fail with: No such file or directory - /github/workspace/docs
rm -rf docs
mkdir -p docs
cp site/index.html docs/index.html
cp -r site/data docs/data
touch docs/.nojekyll

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
echo "[deploy] site staged in docs/ and mirrored to gh-pages"
echo "[deploy] commit and push main to publish: https://carlosrymer.github.io/harbor-harness-spread/"
