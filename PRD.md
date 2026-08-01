# PRD — Harbor harness spread

## Problem statement

Agent benchmark results are almost always reported as a property of a *model*: "model X
scores N% on Terminal-Bench". But the number that gets quoted is produced by a model
running inside a **harness** — a scaffold that decides how the task is framed, which tools
are exposed, how terminal output is fed back, when to stop, and what to do after a failed
command. The harness is rarely named and almost never controlled for.

If swapping the harness alone moves the score materially, then a large part of what the
industry reports as model progress is harness progress, and any comparison of two models
evaluated under different scaffolds is confounded.

The claim under test, and the reason this project exists:

> The harness is a first-class variable in agent performance. Holding the model constant
> and swapping only the harness moves benchmark results substantially — reportedly on the
> order of 5+ percentage points, sometimes far more.

This project tests that claim directly rather than taking it on faith, and publishes the
per-task evidence so a reader can check whether the gap is real or noise.

## Target user

Someone who has to *make a decision* that depends on agent benchmark numbers:

- an engineer choosing a coding agent for their team, deciding how much of a published
  leaderboard gap is attributable to the model they'd actually be paying for;
- someone building an eval and deciding whether harness choice needs to be reported as
  part of their methodology;
- a reader of agent benchmark claims who wants to know how much weight a headline number
  can carry.

## Goals

- Measure the resolve-rate spread across 3–5 agent harnesses with the model, tasks, and
  time budget held constant.
- Report the spread as the headline number, with the exact run configuration next to it.
- Make cost visible alongside score, so a harness that wins by burning far more tokens is
  not confused with one that wins efficiently.
- Surface per-task disagreement — the tasks one harness solved and another missed — because
  that is what distinguishes a real effect from noise.
- Be explicit about what the sample size and single-run design can and cannot support.
- Ship auditable artifacts: every number on the site traceable to committed run data.

## Non-goals

- **Not** a model comparison. One model is held fixed; this says nothing about which model
  is better.
- **Not** a leaderboard claim about which harness is best in general. It is one model, one
  benchmark, 12 tasks, one attempt each.
- **Not** a reproduction of published Terminal-Bench numbers. The subset, the model, and
  the time budget all differ, so absolute rates are not comparable to any public leaderboard.
- **Not** an attempt to tune any harness. Every harness runs at its shipped defaults.
- **Not** a statistically powered study. 12 tasks cannot deliver tight confidence intervals.

## Scope (MVP)

**In scope**

- A frozen, deterministically selected 12-task subset of Terminal-Bench 2.0, chosen by a
  committed seeded script before any harness ran.
- 5 Harbor harnesses attempted; those that could not be driven are reported as such rather
  than silently dropped.
- One shared OpenAI-compatible gateway that pins the model and meters tokens per harness.
- One attempt per harness × task, plus a repeat pass on a subset to sample variance.
- Committed artifacts: per-trial rewards, per-call token meter, trimmed trajectories.
- A static site: headline spread, resolve-rate leaderboard, cost-adjusted ranking,
  task × harness grid, and a trajectory drill-down.

**Out of scope**

- Multiple models, multiple benchmarks, or the full 89-task Terminal-Bench 2.0.
- Statistical significance testing.
- Any hosted backend — the site is static and reads committed JSON.

## User stories

- As an engineer choosing a coding agent, I want to see the same model scored under
  several harnesses, so that I can tell how much of a published gap is the scaffold rather
  than the model.
- As an eval author, I want to see whether harness choice moves results more than the
  differences I'm trying to measure, so that I know whether to control for it.
- As a skeptical reader, I want to drill into a task that one harness solved and another
  missed and read both trajectories, so that I can judge whether the difference is
  meaningful or a coin flip.
- As someone reproducing this, I want the exact task ids, model, limits, and selection
  script committed, so that I can rerun it without guessing.

## Success criteria

The technology being trialed is **Harbor**, and the claim being trialed is that the harness
is a first-class variable. Success means:

1. **Harbor actually ran a multi-harness sweep.** At least three harnesses driven to
   completion over the identical task subset, with rewards produced by the benchmark's own
   verifiers — not a build log.
2. **The spread is reported honestly**, with the exact subset, model, and limits stated next
   to it, and with no implication of a larger sweep than was run.
3. **Per-task disagreement is visible**, with real trajectory excerpts explaining at least
   one case where harnesses diverged.
4. **The variance question is answered plainly** — either with repeat-run data, or with an
   explicit statement that single-run results cannot separate spread from noise.
5. **Every published number is traceable** to committed artifacts.

Verdict on the claim, and on Harbor as a tool for testing it, is recorded in `README.md`
including where it fell short.

## Risks / open questions

| Risk | How it was handled |
|---|---|
| A harness cannot be driven by the available model | Report it explicitly as dropped, with the reason, rather than substituting silently |
| Fixed prepaid balance is exhausted mid-sweep | Hard spend cap in the gateway that fails closed; cost measured per call as it accrues |
| A hung agent burns the whole budget | Per-request upstream timeout, bounded concurrency, and the benchmark's own per-task wall-clock limit |
| 12 tasks is too small to separate signal from noise | Repeat pass to sample variance; the limitation is stated on the site itself rather than buried |
| Subset could be chosen to flatter a result | Selection is a seeded committed script, run and frozen before any harness ran |
| Disk exhaustion from benchmark images | Images pruned between phases; artifacts committed as they are produced |

**Open questions this project does not answer:** whether the ranking holds for a different
model; whether it holds on the full 89-task benchmark; how much of each harness's advantage
is prompt engineering versus control flow versus context management.

## Timeline

Single build session: research and Harbor setup → environment adaptation (container TLS,
gateway) → harness compatibility probes → frozen subset → sweep → analysis → site → deploy.
