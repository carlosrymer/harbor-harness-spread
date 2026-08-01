# harbor-harness-spread

**Try it live: [https://carlosrymer.github.io/harbor-harness-spread/](https://carlosrymer.github.io/harbor-harness-spread/)**

I held one model fixed, swapped only the agent harness around it, and measured how far
apart the benchmark results landed.

<!-- BEGIN:headline -->
<!-- END:headline -->

> ### ⚠️ If you run Terminal-Bench 2.0 on a restricted network, check this first
>
> Every Terminal-Bench 2.0 task's `tests/test.sh` begins by `curl`-installing `uv` from
> `astral.sh`, then runs the task's pytest suite through `uvx`. If that install cannot happen —
> a proxy, an egress allowlist, an air-gapped runner — the suite never executes, and the
> script's closing `if [ $? -eq 0 ]` branch writes a **hardcoded `0`** into `reward.txt`.
>
> Harbor then reports a completed trial, reward 0.0, no exception. **That zero is
> indistinguishable from a real agent failure**, and it will quietly become "the model scored
> 0%" in your write-up. It nearly became mine: I had a full 8-task sweep of clean-looking
> zeros before I read a verifier log instead of trusting a reward.
>
> The fix is two flags and a bind-mount (`--ve` for the verifier phase's CA, `uvx` mounted onto
> `PATH`). The safeguard is better: run Harbor's `oracle` agent — which executes each task's own
> reference solution — through the identical pipeline first, and refuse to score any task whose
> reference solution can't pass. It costs no model tokens. Details in
> [Verification: the zeros were fake](#verification-the-zeros-were-fake).

## What this showcases

**Technology:** [Harbor](https://github.com/laude-institute/harbor) — the agent-evaluation
framework from the Laude Institute team behind Terminal-Bench. It runs agent evals at scale
in containers, with ~40 built-in agent adapters, 20+ benchmarks, several execution backends,
and Terminal-Bench 2.0 distributed through its registry.

The claim I wanted to test is one that gets repeated a lot but rarely gets shown:

> The harness is a first-class variable in agent performance. Holding the model constant and
> swapping only the harness moves benchmark results substantially — reportedly on the order
> of 5+ percentage points, sometimes far more.

If that's true, then a large share of what gets reported as *model* progress is *harness*
progress, and comparing two models evaluated under different scaffolds is confounded. Harbor
is the right tool to check it, because running four different agent harnesses against the
same containerised benchmark is exactly what it's built for — and reimplementing even two of
those adapters faithfully would have been the whole project.

### What surprised me

<h3 id="verification-the-zeros-were-fake">Verification: the zeros were fake</h3>

**The benchmark scored every task 0 for hours, and the agents were never the reason.**
This is the finding I'd most want another person running Harbor to know. Every
Terminal-Bench 2.0 task ships a `tests/test.sh` that begins by `curl`-installing `uv` from
`astral.sh` and then runs the task's pytest suite through `uvx`. In this build environment
`astral.sh` is unreachable, so that install failed, `uvx` was missing, the suite never ran —
and the script's closing `if [ $? -eq 0 ]` branch wrote a hardcoded `0` into `reward.txt`.
Harbor faithfully reported a completed trial with reward 0.0 and no exception. Those zeros
are **indistinguishable from real agent failures** in the job output, and I very nearly
published them as a result.

What caught it was reading a verifier log rather than trusting a reward. The fix was to
bind-mount `uvx` into the container and pass the proxy CA to the *verifier* phase
(`--ve`, which is separate from the agent phase's `--ae`). The safeguard I added afterwards
matters more than the fix: I now run Harbor's `oracle` agent — which executes each task's own
reference solution — through the identical pipeline first. Any task where the reference
solution can't pass is not measuring agent ability, so it's excluded from every rate instead
of being scored as a zero. Two of my eight tasks were excluded that way.

**The harnesses are not interchangeable clients of a model.** I assumed "hold the model
constant" meant passing the same `--model` string four times. It doesn't. Harbor's Aider
adapter hard-codes `openai`/`anthropic` as the only providers; Goose maps a provider name and
never sets a base URL; OpenCode reads `OPENAI_BASE_URL` from the *host* process and bakes it
into config written inside the container. Making the model genuinely constant took a gateway,
not a flag.

**A harness can "pass" a benchmark without ever calling the model.** Aider's first run came
back from Harbor as a completed trial with reward 0.0 and zero exceptions — a clean-looking
data point. It had actually died instantly: Harbor's adapter splits the provider off the
model name and hands the remainder to Aider, so Aider received a bare `bench-model` and its
own litellm refused it. That failure is indistinguishable from "the agent tried and failed"
unless you are metering the model calls. It is the single most important reason this project
put a gateway in the middle, and why the analysis now refuses to score any harness that made
zero model calls.

**Reasoning-token bursts, not context length, drive cost.** While sizing the budget I measured
a single Kimi K3 call that emitted 10,537 completion tokens and took 309 seconds — one partial
task cost $0.71. Agent-eval cost is dominated by a handful of enormous bursts, not by steady
context growth, which is why the gateway meters and caps per call rather than per run.

## The use case

This is a real methodology question, not a toy. Anyone choosing an agent off a leaderboard is
implicitly trusting that the number reflects the model they'll be paying for. If the scaffold
moves the number as much as the model does, that inference is unsound — and the only way to
find out is to hold one side fixed and vary the other.

I picked Terminal-Bench 2.0 because it's the benchmark Harbor's authors built, its tasks are
real terminal work (compile this, recover that database, fix this build) with their own
verifiers, and it isn't a benchmark where a harness can luck into a pass.

### Results

<!-- BEGIN:leaderboard -->
<!-- END:leaderboard -->

Cost-adjusted matters here: a harness that wins by burning several times the tokens is a
different result than one that wins for free, so the site ranks by solved-per-dollar as well
as by raw rate.

**Excluded tasks are grader failures, not agent failures.** Where a task appears as excluded
below, it means that task's *own reference solution* could not pass in this environment — the
oracle screen caught it. Scoring an agent 0 on such a task would invent a failure that never
happened, so those tasks are removed from every rate and every denominator rather than counted
against anyone.

### Where the harnesses disagreed

<!-- BEGIN:grid -->
<!-- END:grid -->

<!-- BEGIN:disagreement -->
<!-- END:disagreement -->

The [live site](https://carlosrymer.github.io/harbor-harness-spread/) lets you click any cell
in that grid and read the trajectory for that run.

### Run configuration

<!-- BEGIN:runconfig -->
<!-- END:runconfig -->

Held constant across harnesses: the task list, the model (pinned at the gateway, not merely
requested), the per-task wall-clock budget, the container image, the CA/network setup, and
one attempt per task. What varied is the harness — *including* its own default step limits,
prompt templates, and context management. That's deliberate: those defaults **are** the
harness. This measures "harness as shipped", not "scaffold shape with all else equal".

<!-- BEGIN:spend -->
<!-- END:spend -->

## Honest limitations

**A single run per cell cannot fully separate spread from variance.** I want this stated
plainly rather than buried. With one attempt per harness × task on a 6-task scored subset,
one flipped task moves a harness by 16.7 percentage points — so a spread smaller than that is
within touching distance of noise. Decoding is stochastic (Kimi K2.7 Code accepts only
`temperature=1`; Gemini is left at its provider default), so some flipping between identical
runs is expected. Where repeat runs were affordable they are reported in the variance section
below and on the site; where they were not, the ranking should be read as *indicative of an
effect worth controlling for*, not as a settled ordering of these harnesses.

**The sample is small.** Each solved task moves a harness's rate by a large fraction, so
confidence intervals are wide. The subset is stratified for coverage, not sized for power. It
was cut from 12 tasks to 8 when the shared model budget for this build was reduced — before any
recorded run, and by changing only the task count in the selection script, never the rule — and
2 of those 8 were then excluded by the oracle baseline, leaving 6 scored. Shrinking tasks rather
than harnesses was deliberate: the harness spread *is* the experiment.

**Cost is list price**, computed from each provider's published rates applied to
gateway-metered tokens, not read off an invoice.

**Absolute rates are low and the time budget is most of why.** Each task ran with a fraction of
its own wall-clock allowance (see the run configuration for the exact multiplier) — a
cost-control decision forced by a shared, fixed model budget and applied identically to every
harness. Running out of the clock counts as a failure rather than "not run", because burning the
budget without converging is a real outcome and a harness-attributable one. But it depresses
every rate, and it is the main reason **these numbers must not be quoted as a result for this
model or compared to any published Terminal-Bench 2.0 leaderboard**. The comparison that
survives is the one between harnesses inside this run, where the budget was identical.

**Absolute rates are not comparable to published Terminal-Bench numbers.** Different subset,
different model, and a reduced per-task time budget. Only the *spread between harnesses within
this run* is the result.

## Harnesses I could not run, and why

Two of the six harnesses I tried are absent from the board. Neither was silently dropped.

- **`opencode`** — its build requires the OpenAI **Responses** API. Pointed at a
  chat-completions endpoint it fails with `AI_APICallError: Not Found`; pinning the
  `@ai-sdk/openai-compatible` provider package instead fails deeper inside its bundle with
  `Z.responses is not a function`. Supporting it would have meant implementing the Responses
  API in the gateway, which I judged out of scope.
- **`swe-agent`** — broken in Harbor 0.20.0 for any task without a `/testbed` directory. The
  adapter builds its command as
  `$(if [ -d /testbed ]; then …; else echo '--env.repo.path=$(pwd)'; fi)`; the inner `$(pwd)`
  is inside single quotes, so the literal string `$(pwd)` reaches sweagent and it dies with
  `NoSuchPathError: /app/$(pwd)`. Terminal-Bench tasks use `/app`, so it fails on all of them.
  This is an upstream bug, not a configuration problem on my side.

Also excluded before selection: the `qemu-alpine-ssh` task, whose prebuilt image returns 503
from the registry CDN in this environment across repeated attempts. It is recorded as an
exclusion in `scripts/select_tasks.py` rather than dropped after the fact.

<!-- BEGIN:notdriven -->
<!-- END:notdriven -->

**Claude Code and Codex CLI — the two agents most people would want in this comparison — are
absent** because this build had no Anthropic or OpenAI credentials. That is a real limitation
on how far these results generalise: the harnesses here are the ones I could actually drive.

## Did Harbor deliver?

Yes, with caveats worth knowing before you rely on it.

**What worked:** `harbor download terminal-bench@2.0` and `harbor run -a <agent>` did what the
docs say. Per-trial artifacts — rewards, ATIF trajectories, verifier output — are
well-structured enough that my analysis is mostly a join. `--mounts`, `--ae`, and
`--install-only` were exactly the escape hatches I needed to adapt to a restricted network,
and `--install-only` in particular turned a slow compatibility question into a fast one.

**What cost me time:** the agent adapters vary a lot in maturity. One is outright broken on
this benchmark (`swe-agent`), one silently no-ops on a model name Harbor itself constructed
(`aider`), and the provider/base-URL handling differs enough between adapters that "the same
model everywhere" is not something you get by default. None of this is visible from the CLI
surface — you find it by metering the traffic. If you're doing cross-harness comparisons with
Harbor, meter the model calls independently; don't trust a completed trial to mean the agent
ran.

**On the claim itself:** see the headline number and the caveat above it. The honest summary
is that swapping only the harness moved the result on this subset, and that the effect is
large enough to be worth controlling for in any cross-model comparison — but that a
single-run 8-task experiment cannot tell you how much of the specific gap is noise. I'd want
several repeats over a much larger subset before treating the ordering as real.

## Docs

- [Architecture](ARCHITECTURE.md) — system design, components, data flow, deployment
- [PRD](PRD.md) — problem statement, scope, success criteria

## Running locally

```bash
# Harbor needs Python 3.12+; Docker must be running.
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 1. Fetch the benchmark (89 tasks) from the Harbor registry
.venv/bin/harbor download terminal-bench@2.0

# 2. Freeze the task subset (seeded — reproduces the exact 8 tasks used here)
.venv/bin/python scripts/select_tasks.py > artifacts/subset.json

# 3. Start the metering gateway (pins the model, meters per harness, caps spend)
export GEMINI_API_KEY=...
BUDGET_USD=8.0 BUDGET_PER_HARNESS_USD=2.0 \
  PRICE_IN=1.50 PRICE_CACHED=0.375 PRICE_OUT=7.50 TEMPERATURE=-1 \
  bash scripts/gw_ctl.sh start gemini/gemini-3.6-flash

# 4. Prove the graders work here before scoring anything (no model calls, no cost)
.venv/bin/harbor run -p terminal-bench -a oracle -o jobs --job-name oracle-baseline ...

# 5. Run the sweep, one harness at a time
MODEL_SLUG=gemini36 AGENT_TIMEOUT_MULT=0.6 \
  HARNESSES="terminus-2 mini-swe-agent aider goose" NCONC=3 \
  bash scripts/run_all.sh

# 6. Rebuild results + the write-up's numbers from the artifacts
.venv/bin/python scripts/analyze.py
.venv/bin/python scripts/render_readme.py

# 7. Serve the site locally, or publish it
cp artifacts/results.json site/data/ && cp -r artifacts/trajectories site/data/
python3 -m http.server -d site 8000
bash scripts/deploy_pages.sh   # publishes site/ to the gh-pages branch
```

Note: in a network that terminates TLS at an inspecting proxy, task containers must be given
its CA. `scripts/run_harness.sh` bind-mounts a CA bundle and the host `uv` binary into every
container for that reason; on an unrestricted network those mounts are unnecessary.

## Stack

- **Harbor 0.20.0** — benchmark distribution, container orchestration, agent adapters, verification
- **Terminal-Bench 2.0** — tasks and verifiers, via the Harbor registry
- **Gemini 3.6 Flash** (Google AI Studio) — the fixed model under test
- **FastAPI + uvicorn + litellm** — the model gateway (~280 lines)
- **Python 3.13** — selection and analysis
- **Docker** — one container per trial
- **Vanilla HTML/CSS/JS** — the site; no build step, no external requests

## Deployed via

GitHub Pages, served from the `gh-pages` branch (`bash scripts/deploy_pages.sh`). There is no
Actions workflow: the token in the build environment lacks the `workflow` scope and the Pages
REST endpoints are proxy-blocked, so the workflow I would have used sits in
[`deploy/`](deploy/README.md) rather than pretending to run. The site is static and reads the
same JSON committed under `artifacts/`, so the page can never disagree with the run data.

---
Part of an ongoing series of small, real-world builds trialing frontier AI models, frameworks,
and tools as they ship.
