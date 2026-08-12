# harbor-harness-spread

**Try it live: [https://carlosrymer.github.io/harbor-harness-spread/](https://carlosrymer.github.io/harbor-harness-spread/)**

I held one model fixed, swapped only the agent harness around it, and measured how far
apart the benchmark results landed.

<!-- BEGIN:headline -->
On **gemini-3.6-flash (Google AI Studio)**, over the **8 Terminal-Bench 2.0 tasks every harness attempted**, with the model, the tasks and the time budget held constant, the best harness (`terminus-2`) resolved **62.5%** and the worst (`aider`) resolved **0.0%** — a spread of **62.5 percentage points** attributable to nothing but the scaffold around the model. Ranking: `terminus-2` › `mini-swe-agent` › `goose` › `aider`.
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
is the right tool to check it, because running several different agent harnesses against the
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
**gemini-3.6-flash (Google AI Studio)** — spread **63.6pp**

| Harness | Resolved | Resolve rate | Tokens (in / cached / out) | Cost | Solved per $ | Timed out |
|---|---|---|---|---|---|---|
| `terminus-2` | 7/11 | **63.6%** | 9,511,005 / 6,634,822 / 233,908 | $8.557 | 0.818 | 4 |
| `mini-swe-agent` | 3/9 | **33.3%** | 3,898,263 / 2,305,436 / 193,178 | $4.703 | 0.638 | 6 |
| `goose` | 2/12 | **16.7%** | 2,026,738 / 793,664 / 74,663 | $2.707 | 0.739 | 2 |
| `aider` | 0/12 | **0.0%** | 63,227 / 0 / 179,411 | $1.440 | 0.0 | 0 |


> **Read these rates as relative, not absolute.** Every harness ran with a **0.4× time budget** — that fraction of each task's own wall-clock allowance — applied identically to all of them so the comparison stays fair. That deliberately depresses every number here, so **these rates are not comparable to published Terminal-Bench 2.0 figures** and should not be quoted as a result for this model. The only claim being made is the *gap between harnesses within this run*.

> **How much weight the spread can carry:** on the 8 tasks every harness attempted, one task changing outcome moves a harness by **12.5 percentage points**. The measured spread of 62.5pp is therefore about 5 task(s) wide. Any spread of one task or less is indistinguishable from a coin flip. A repeat pass measured the actual run-to-run swing — see the variance section, which is where this spread should be judged.
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
| Task | Difficulty | Category | `terminus-2`<br><sub>gemini36</sub> | `mini-swe-agent`<br><sub>gemini36</sub> | `goose`<br><sub>gemini36</sub> | `aider`<br><sub>gemini36</sub> | Agreement |
|---|---|---|---|---|---|---|---|
| `fix-git` | easy | software-engineering | ✅ | ✅ | ❌ | ❌ | **split 2/4** |
| `overfull-hbox` | easy | debugging | ✅ | ⏱ | ❌ | ❌ | **split 1/4** |
| `configure-git-webserver` | hard | system-administration | ❌ | – | ❌ | ❌ | 0/3 |
| `gpt2-codegolf` | hard | software-engineering | – | ⏱ | ❌ | ❌ | 0/3 |
| `model-extraction-relu-logits` | hard | mathematics | ❌ | ⏱ | ⏱ | ❌ | 0/4 |
| `password-recovery` | hard | security | ✅ | – | ❌ | ❌ | **split 1/3** |
| `sparql-university` | hard | data-querying | ✅ | ⏱ | ✅ | ❌ | **split 2/4** |
| `torch-pipeline-parallelism` | hard | software-engineering | ⏱ | ⏱ | ❌ | ❌ | 0/4 |
| `adaptive-rejection-sampler` | medium | scientific-computing | ✅ | ⏱ | ❌ | ❌ | **split 1/4** |
| `db-wal-recovery` | medium | file-operations | ⏱ | ✅ | ⏱ | ❌ | **split 1/4** |
| `log-summary-date-ranges` | medium | data-processing | ✅ | ✅ | ✅ | ❌ | **split 3/4** |
| `sqlite-with-gcov` | medium | system-administration | ✅ | – | ❌ | ❌ | **split 1/3** |

✅ solved · ❌ attempted, not solved · ⏱ ran out of its time budget (counted as a failure) · – not run or excluded (never counted as a zero)
<!-- END:grid -->

<!-- BEGIN:disagreement -->
8 of 12 scored tasks split the harnesses:

- **`fix-git`** (easy, software-engineering) — solved by `mini-swe-agent` (gemini36), `terminus-2` (gemini36); missed by `aider` (gemini36), `goose` (gemini36)
- **`overfull-hbox`** (easy, debugging) — solved by `terminus-2` (gemini36); missed by `aider` (gemini36), `goose` (gemini36), `mini-swe-agent` (gemini36)
- **`password-recovery`** (hard, security) — solved by `terminus-2` (gemini36); missed by `aider` (gemini36), `goose` (gemini36)
- **`sparql-university`** (hard, data-querying) — solved by `goose` (gemini36), `terminus-2` (gemini36); missed by `aider` (gemini36), `mini-swe-agent` (gemini36)
- **`adaptive-rejection-sampler`** (medium, scientific-computing) — solved by `terminus-2` (gemini36); missed by `aider` (gemini36), `goose` (gemini36), `mini-swe-agent` (gemini36)
- **`db-wal-recovery`** (medium, file-operations) — solved by `mini-swe-agent` (gemini36); missed by `aider` (gemini36), `goose` (gemini36), `terminus-2` (gemini36)
- **`log-summary-date-ranges`** (medium, data-processing) — solved by `goose` (gemini36), `mini-swe-agent` (gemini36), `terminus-2` (gemini36); missed by `aider` (gemini36)
- **`sqlite-with-gcov`** (medium, system-administration) — solved by `terminus-2` (gemini36); missed by `aider` (gemini36), `goose` (gemini36)
<!-- END:disagreement -->

The [live site](https://carlosrymer.github.io/harbor-harness-spread/) lets you click any cell
in that grid and read the trajectory for that run.

### Run configuration

<!-- BEGIN:runconfig -->
- **Benchmark:** `terminal-bench@2.0` (89 tasks). 24 candidates were selected and oracle-screened; **13 qualified** and **12 were scored** (capped in seeded order)
- **Excluded — grader failure, not agent failure:** 6 task(s) whose own reference solution could not pass here `chess-best-move`, `count-dataset-tokens`, `crack-7z-hash`, `gcode-to-text`, `largest-eigenval`, `pytorch-model-recovery`. A further 5 candidate(s) could not be screened at all because their images would not pull in this environment.
- **Selection:** seeded (`20260801`) stratified pick from the 50 tasks with an agent budget ≤ 900s, frozen before any run — `scripts/select_tasks.py`
- **Models:** gemini-3.6-flash (Google AI Studio)
- **Agent time budget:** 0.4× each task's own limit, identical for every harness
- **Attempts per task:** 1
- **Verification:** each task's own test suite, after an `oracle` reference-solution baseline confirmed the graders work in this environment
- **Pricing:** Gemini 3.6 Flash list price $1.50/M input, $0.375/M cached input, $7.50/M output, applied to gateway-metered tokens
<!-- END:runconfig -->

Held constant across harnesses: the task list, the model (pinned at the gateway, not merely
requested), the per-task wall-clock budget, the container image, the CA/network setup, and
one attempt per task. What varied is the harness — *including* its own default step limits,
prompt templates, and context management. That's deliberate: those defaults **are** the
harness. This measures "harness as shipped", not "scaffold shape with all else equal".

<!-- BEGIN:spend -->
- **gemini-3.6-flash (Google AI Studio)**: $17.41 across 16,180,393 metered tokens

That is the spend behind the numbers published above: **$17.41** over 843 model calls, billed at the gateway as each call happened rather than estimated afterwards.

**All-in cost of the build, including work that produced nothing publishable** — probes, the invalidated false-zero sweep, and a Gemini run discarded when the task set was widened:

- **Google AI Studio (Gemini)**: $23.96 over 1,161 calls
- **Moonshot (Kimi)**: $1.70 over 115 calls
- **OpenAI**: $0.00 — a key arrived late in the build and was used only to enumerate available models. No scored run used an OpenAI model, because doing so would have varied the model and the harness together.

The gap between those two figures is the honest price of finding the verifier bug: a large share of the total bought discarded results.
<!-- END:spend -->

## Honest limitations

### Variance: how much of the spread is real

<!-- BEGIN:variance -->
I re-ran **`goose`** over the same tasks, same model, same limits, changing nothing: **2/12 (16.7%) → 4/11 (36.4%)** — a swing of **19.7 percentage points** between identical runs, with 2 task(s) changing outcome (`configure-git-webserver`, `fix-git`).

**What that means for the headline.** The measured spread is 62.5pp and the observed run-to-run swing on a single harness is 19.7pp. So the *extremes* of the ranking are separable — `terminus-2` at 62.5% versus `aider` at 0.0% is a gap several times larger than the noise I measured. The *middle* of the ranking is not: adjacent harnesses separated by less than 19.7pp cannot be ordered from this data, and I do not claim they can.

This is one repeated harness, not all four, so it is a floor on the variance rather than a full characterisation. Decoding is stochastic — Gemini is left at its provider default and Kimi K2.7 Code accepts only `temperature=1` — so some flipping between identical runs is expected. Anyone quoting a single-run agent benchmark number, mine included, should assume a swing of this order.
<!-- END:variance -->

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

Two of the six harnesses I tried are absent from the board, and one runs but is deliberately
not scored. None was silently dropped.

- **`opencode` — now runs on the pinned model, but is not scored.** Its build hard-requires
  the OpenAI **Responses** API; pointed at a chat-completions endpoint it fails with
  `AI_APICallError: Not Found`, and pinning `@ai-sdk/openai-compatible` instead fails deeper
  with `Z.responses is not a function`. I implemented a Responses-to-chat-completions shim in
  the gateway specifically so OpenCode could run on the **same pinned model as every other
  harness** rather than being pointed at a different provider — which would have turned it into
  a different experiment. It now connects and does real metered work (it sends its 10 tools and
  gets real completions back), but it halts after a single step, and I could not establish
  within budget whether that is the harness, the model's tool-calling under translation, or a
  gap in my shim. Scoring it would measure my shim, not OpenCode, so it stays off the board.

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
Every harness on the board made real, metered model calls. Nothing was scored 0 while silently failing to call the model — the failure mode that cost me the most time on this build.
<!-- END:notdriven -->

**Claude Code is absent** because this build had no Anthropic credentials. **Codex CLI was not
run**: an OpenAI key did become available late in the build, but Codex CLI drives OpenAI models,
and running it that way would vary the model and the harness together — a different experiment
that cannot be folded into a same-model spread. With the time remaining I judged a measured
variance figure worth more than a fifth harness on its own denominator, so I ran the repeat
pass instead. That is a real limitation on how far these results generalise: the harnesses here
are the ones I could drive **on one pinned model**.

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
