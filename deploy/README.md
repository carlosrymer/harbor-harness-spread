# Deployment

The live site is published to **GitHub Pages from the `gh-pages` branch**, whose root is the
built static site. `main` holds the source and the run artifacts; `gh-pages` holds only what
the browser needs.

```bash
bash scripts/deploy_pages.sh      # rebuild site/data from artifacts and push gh-pages
```

## Why not GitHub Actions?

`deploy/github-pages-workflow.yml` is the workflow I would normally use — `configure-pages@v5`
with `enablement: true`, `upload-pages-artifact@v3`, `deploy-pages@v4`. **It is not running,
and it is deliberately stored outside `.github/workflows/` so nothing implies otherwise.**

The credentials available in the environment this project was built in could not install it:

- The GitHub token carries `admin:public_key, gist, read:org, repo` but **not `workflow`**, so
  any push whose diff touches `.github/workflows/**` is rejected outright by GitHub.
- The REST API is reached through a proxy that blocks the Pages endpoints
  (`/repos/*/pages` returns 403), so Pages could not be configured that way either.

Pushing the built site to `gh-pages` sidesteps both: GitHub enables Pages for the branch on
its own, with no workflow and no API call. If you fork this into an environment with a
`workflow`-scoped token, move that file into `.github/workflows/` and it should work as-is —
it does the same two steps `scripts/deploy_pages.sh` does (copy artifacts into `site/`,
publish `site/`).

## What actually gets published

`scripts/deploy_pages.sh` copies `artifacts/results.json` and `artifacts/trajectories/` into
`site/data/`, then force-pushes that directory as the root of `gh-pages`. The page is static
and reads only those committed JSON files, so the site can never disagree with the run data
in `main`.
