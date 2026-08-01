#!/usr/bin/env python3
"""Turn raw Harbor job output + the gateway meter into the JSON the site reads.

Inputs
  jobs/<job>/result.json                  per-job Harbor summary
  jobs/<job>/<task>__<id>/result.json     per-trial reward + timing
  jobs/<job>/<task>__<id>/agent/*.json    ATIF trajectory (when the harness emits one)
  artifacts/gateway.jsonl                 independent per-call token meter

Output
  artifacts/results.json                  leaderboard + task x harness grid + cost
  artifacts/trajectories/<harness>__<task>.json  trimmed trajectory excerpts

Cost model: cost is taken from the per-call ``cost_usd`` the gateway already
computed, so the write-up, the site, and the budget guard can never disagree
about what was spent. The constants below are only a fallback for older log
lines that predate per-call costing.

Moonshot list price for Kimi K2.7 Code: $0.95 /M uncached input, $0.19 /M cached
input, $4.00 /M output. Cached prompt tokens bill at the discounted rate, which
matters a lot here: several harnesses re-send a long prefix on every step and
would look far more expensive under a flat input price.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
ART = ROOT / "artifacts"

PRICE_IN_UNCACHED = 0.95 / 1_000_000
PRICE_IN_CACHED = 0.19 / 1_000_000
PRICE_OUT = 4.00 / 1_000_000

# Job names encode the whole cell identity:
#   run-<model-slug>--<harness>[--rep<N>]
# "--" is the separator because harness names themselves contain single hyphens
# (mini-swe-agent, terminus-2) and would otherwise be ambiguous.
def parse_job(job_name: str) -> tuple[str, str, int] | None:
    """-> (model_slug, harness, rep) or None if this dir is not a sweep job."""
    if not job_name.startswith("run-"):
        return None
    parts = job_name[len("run-"):].split("--")
    if len(parts) < 2:
        return None
    model, harness = parts[0], parts[1]
    rep = 1
    if len(parts) > 2 and parts[2].startswith("rep"):
        try:
            rep = int(parts[2][3:])
        except ValueError:
            rep = 1
    return model, harness, rep


def cost_usd(p_tok: int, cached: int, c_tok: int) -> float:
    uncached = max(0, p_tok - cached)
    return uncached * PRICE_IN_UNCACHED + cached * PRICE_IN_CACHED + c_tok * PRICE_OUT


def load_gateway_meter() -> dict:
    """Per-harness token totals measured at the gateway (not self-reported)."""
    per = defaultdict(lambda: {"calls": 0, "errors": 0, "retries": 0, "prompt_tokens": 0,
                               "cached_tokens": 0, "completion_tokens": 0,
                               "reasoning_tokens": 0, "latency_s": 0.0,
                               "metered_cost_usd": 0.0, "have_metered_cost": False})
    path = ART / "gateway.jsonl"
    if not path.exists():
        return {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        h = d.get("harness", "unknown")
        if d.get("event") == "retry":
            per[h]["retries"] += 1
            continue
        if d.get("event") != "call":
            continue
        e = per[h]
        e["calls"] += 1
        if d.get("status") != "ok":
            e["errors"] += 1
        for k in ("prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens"):
            e[k] += d.get(k) or 0
        e["latency_s"] += d.get("latency_s") or 0.0
        if d.get("cost_usd") is not None:
            e["metered_cost_usd"] += d["cost_usd"]
            e["have_metered_cost"] = True
    for h, e in per.items():
        # Prefer what the gateway actually billed; recompute only for old lines.
        e["cost_usd"] = round(
            e["metered_cost_usd"] if e["have_metered_cost"]
            else cost_usd(e["prompt_tokens"], e["cached_tokens"], e["completion_tokens"]), 4)
        e["latency_s"] = round(e["latency_s"], 1)
    return dict(per)


def trial_dirs(job_dir: Path):
    for d in sorted(job_dir.iterdir()):
        if d.is_dir() and (d / "result.json").exists():
            yield d


def read_trial(d: Path) -> dict:
    r = json.loads((d / "result.json").read_text())
    task = d.name.rsplit("__", 1)[0]
    reward = r.get("reward")
    if reward is None:
        vr = d / "verifier" / "reward.txt"
        if vr.exists():
            try:
                reward = float(vr.read_text().strip())
            except ValueError:
                reward = None
    exc = None
    if (d / "exception.txt").exists():
        txt = (d / "exception.txt").read_text().strip().splitlines()
        exc = txt[-1][:300] if txt else "unknown"

    # Distinguish "the agent had its shot and failed" from "this cell never ran".
    #
    # AgentTimeoutError means the harness burned its entire wall-clock budget
    # without solving the task. That is a real benchmark outcome -- Terminal-Bench
    # scores it as a failure -- so it counts as an honest 0. It is also often the
    # most interesting failure mode, since thrashing until the clock runs out is
    # exactly the sort of thing a harness, not a model, is responsible for.
    #
    # Anything else -- a 402 from the gateway budget guard, a TLS/network failure,
    # a missing image -- means the cell never got a fair attempt. Those are
    # reported as "not run" and excluded from the denominator, never scored as 0.
    # Count the harness's own response-parsing failures. terminus-2 in particular
    # logs "Extra text detected before/after JSON object" whenever the model's
    # reply does not match the strict shape its parser expects -- each one is a
    # step the harness spends recovering from its own format contract rather than
    # on the task. This is exactly the kind of harness-model interaction that a
    # resolve rate alone hides.
    parser_warnings = 0
    tl = d / "trial.log"
    if tl.exists():
        try:
            parser_warnings = tl.read_text(errors="replace").count("Extra text detected")
        except OSError:
            parser_warnings = 0

    timed_out = bool(exc and "AgentTimeoutError" in exc)
    infra_error = bool(exc) and not timed_out
    started, finished = r.get("started_at"), r.get("finished_at")
    return {
        "task": task, "trial_dir": str(d.relative_to(ROOT)),
        "reward": reward, "resolved": bool(reward and reward > 0),
        "exception": exc, "timed_out": timed_out, "infra_error": infra_error,
        "attempted": not infra_error, "parser_warnings": parser_warnings,
        "started_at": started, "finished_at": finished,
        "n_steps": count_steps(d),
        "wall_s": wall_seconds(r),
    }


def wall_seconds(r: dict) -> float | None:
    for k in ("agent_execution_time_sec", "duration_sec", "elapsed_sec"):
        if r.get(k):
            return round(float(r[k]), 1)
    try:
        from datetime import datetime
        s, f = r.get("started_at"), r.get("finished_at")
        if s and f:
            return round((datetime.fromisoformat(f) - datetime.fromisoformat(s)).total_seconds(), 1)
    except Exception:
        pass
    return None


def count_steps(d: Path) -> int | None:
    """Steps taken, from the ATIF trajectory when the harness emits one."""
    tj = d / "agent" / "trajectory.json"
    if not tj.exists():
        return None
    try:
        data = json.loads(tj.read_text())
    except json.JSONDecodeError:
        return None
    steps = data.get("steps") if isinstance(data, dict) else None
    if isinstance(steps, list):
        return sum(1 for s in steps if (s or {}).get("source") == "agent")
    return None


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + f"\n… [{len(s) - n:,} more chars]"


def _raw_log_excerpt(trial_dir: Path, harness: str, task: str) -> dict | None:
    """Head+tail of a harness's own console transcript, for harnesses with no ATIF."""
    agent_dir = trial_dir / "agent"
    if not agent_dir.is_dir():
        return None
    # Prefer the harness's main transcript; skip the giant per-token stream logs.
    cands = sorted(
        (p for p in agent_dir.glob("*.txt") if p.stat().st_size > 0),
        key=lambda p: p.stat().st_size, reverse=True,
    )
    if not cands:
        return None
    path = cands[0]
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    lines = [ln.rstrip() for ln in text.splitlines()]
    HEAD, TAIL = 30, 70
    steps = []
    if len(lines) > HEAD + TAIL:
        steps.append({"source": "agent", "n": None, "text": "\n".join(lines[:HEAD])})
        steps.append({"source": "note", "n": None,
                      "text": f"… {len(lines) - HEAD - TAIL} lines omitted …"})
        steps.append({"source": "agent", "n": None, "text": "\n".join(lines[-TAIL:])})
    else:
        steps.append({"source": "agent", "n": None, "text": "\n".join(lines)})
    return {"harness": harness, "task": task, "n_agent_steps": None,
            "summary": f"No structured trajectory from this harness — showing its own "
                       f"transcript ({path.name}, {len(lines):,} lines).",
            "steps": steps}


def extract_trajectory(trial_dir: Path, harness: str, task: str) -> dict | None:
    """Trim a full ATIF trajectory into something readable on the site.

    The raw trajectories run to megabytes; the site only needs enough to see *why*
    a run went the way it did. Keeps the opening moves and the ending (where a run
    either lands the fix or gives up), and drops the middle when it is long.
    """
    tj = trial_dir / "agent" / "trajectory.json"
    if not tj.exists():
        # Not every harness emits ATIF. Aider, for one, only leaves its own console
        # transcript, so fall back to that rather than showing the reader nothing.
        return _raw_log_excerpt(trial_dir, harness, task)
    try:
        data = json.loads(tj.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _raw_log_excerpt(trial_dir, harness, task)

    raw = data.get("steps") or []
    agent_steps = [s for s in raw if (s or {}).get("source") == "agent"]
    HEAD, TAIL = 4, 10
    if len(agent_steps) > HEAD + TAIL:
        kept = agent_steps[:HEAD] + agent_steps[-TAIL:]
        gap = len(agent_steps) - HEAD - TAIL
    else:
        kept, gap = agent_steps, 0

    out_steps = []
    for i, s in enumerate(kept):
        if gap and i == HEAD:
            out_steps.append({"source": "note", "n": None,
                              "text": f"… {gap} intermediate steps omitted …"})
        parts = []
        msg = s.get("message") or ""
        if msg:
            parts.append(_clip(msg, 700))
        for tc in (s.get("tool_calls") or [])[:3]:
            fn = (tc or {}).get("function") or {}
            name = fn.get("name") or (tc or {}).get("name") or "tool"
            args = fn.get("arguments") or (tc or {}).get("arguments") or ""
            if isinstance(args, (dict, list)):
                args = json.dumps(args)
            parts.append(f"$ [{name}] {_clip(str(args), 500)}")
        obs = s.get("observation")
        if isinstance(obs, dict):
            obs = obs.get("result") or obs.get("content") or obs.get("output") or ""
        if isinstance(obs, list):
            obs = "\n".join(str(o) for o in obs)
        if obs:
            parts.append(f"→ {_clip(str(obs), 700)}")
        out_steps.append({"source": "agent", "n": s.get("step_id"),
                          "text": "\n".join(p for p in parts if p) or "(empty step)"})

    fm = data.get("final_metrics") or {}
    summary = (f"{len(agent_steps)} agent steps · "
               f"{fm.get('total_prompt_tokens', 0):,} prompt / "
               f"{fm.get('total_completion_tokens', 0):,} completion tokens "
               f"as reported by the harness itself")
    return {"harness": harness, "task": task, "summary": summary,
            "n_agent_steps": len(agent_steps), "steps": out_steps}


MODEL_LABELS = {
    "gemini36": "gemini-3.6-flash (Google AI Studio)",
    "kimik27": "kimi-k2.7-code (Moonshot)",
}


def summarise(model: str, harness: str, primary: dict, meter: dict, reps: list) -> dict:
    n_attempted = sum(1 for t in primary.values() if t["attempted"])
    n_resolved = sum(1 for t in primary.values() if t["resolved"])
    m = meter.get(f"{harness}__{model}", {})
    tot_tok = m.get("prompt_tokens", 0) + m.get("completion_tokens", 0)
    steps = [t["n_steps"] for t in primary.values() if t["n_steps"] is not None]
    parse_warn = sum(t.get("parser_warnings", 0) for t in primary.values())
    walls = [t["wall_s"] for t in primary.values() if t["wall_s"] is not None]
    return {
        "model": model, "model_label": MODEL_LABELS.get(model, model), "harness": harness,
        "n_tasks": n_attempted, "n_cells": len(primary), "n_resolved": n_resolved,
        "resolve_rate": round(n_resolved / n_attempted, 4) if n_attempted else 0.0,
        "n_infra_errors": sum(1 for t in primary.values() if t["infra_error"]),
        "n_timeouts": sum(1 for t in primary.values() if t["timed_out"]),
        "tokens_total": tot_tok,
        "prompt_tokens": m.get("prompt_tokens", 0),
        "cached_tokens": m.get("cached_tokens", 0),
        "completion_tokens": m.get("completion_tokens", 0),
        "llm_calls": m.get("calls", 0), "llm_errors": m.get("errors", 0),
        "llm_retries": m.get("retries", 0), "cost_usd": m.get("cost_usd", 0.0),
        "wall_s_total": round(sum(walls), 1) if walls else None,
        "steps_total": sum(steps) if steps else None,
        "parser_warnings": parse_warn,
        "resolved_per_mtok": round(n_resolved / (tot_tok / 1e6), 3) if tot_tok else None,
        "resolved_per_usd": round(n_resolved / m["cost_usd"], 3) if m.get("cost_usd") else None,
        "reps": len(reps),
    }


def main() -> None:
    sub_path = ART / "scored_subset.json"
    if not sub_path.exists():
        sub_path = ART / "subset.json"
    subset = json.loads(sub_path.read_text())
    task_meta = {t["id"]: t for t in subset["tasks"]}
    task_ids = [t["id"] for t in subset["tasks"]]

    # Oracle baseline: the reference solution run through the same pipeline. Any
    # task the oracle cannot pass is not measuring agent ability in this
    # environment, so it is excluded from every rate rather than scored as 0.
    # Every oracle-* job counts: screening happened in more than one pass, and a
    # task qualifies if its reference solution passed in ANY of them.
    oracle = {}
    for odir in sorted(JOBS.glob("oracle-*")):
        if not odir.is_dir():
            continue
        for d in trial_dirs(odir):
            t = read_trial(d)
            oracle[t["task"]] = oracle.get(t["task"], False) or t["resolved"]

    runs = defaultdict(list)  # (model, harness) -> [{rep, job, trials}]
    if JOBS.exists():
        tdir = ART / "trajectories"
        # Rebuild from scratch: stale files from an earlier naming scheme or a
        # discarded run would otherwise be served by the site as if current.
        if tdir.exists():
            for old in tdir.glob("*.json"):
                old.unlink()
        tdir.mkdir(parents=True, exist_ok=True)
        for job_dir in sorted(JOBS.iterdir()):
            if not job_dir.is_dir():
                continue
            parsed = parse_job(job_dir.name)
            if not parsed:
                continue
            model, harness, rep = parsed
            trials = {}
            for d in trial_dirs(job_dir):
                t = read_trial(d)
                # A task the oracle could not solve here never gets scored.
                if oracle and not oracle.get(t["task"], True):
                    t["attempted"] = False
                    t["infra_error"] = True
                    t["exception"] = (t["exception"] or
                                      "excluded: reference solution also fails in this environment")
                # A task can have more than one trial directory: an interrupted
                # run leaves a CancelledError stub behind, and a later fill run
                # adds a real one. Prefer the trial that actually produced a
                # reward, so a stub can never overwrite a completed cell.
                prev = trials.get(t["task"])
                if prev is not None:
                    def rank(x):
                        return (x["reward"] is not None, x["attempted"], not x["infra_error"])
                    if rank(prev) >= rank(t):
                        continue
                trials[t["task"]] = t
                if rep == 1:
                    ex = extract_trajectory(d, harness, t["task"])
                    if ex:
                        (tdir / f"{model}__{harness}__{t['task']}.json").write_text(
                            json.dumps(ex, indent=1))
            if trials:
                runs[(model, harness)].append({"rep": rep, "job": job_dir.name, "trials": trials})

    meter = load_gateway_meter()

    rows = []
    for (model, harness), reps in sorted(runs.items()):
        primary = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
        rows.append(summarise(model, harness, primary, meter, reps))

    not_driven = [r for r in rows if r["llm_calls"] == 0]
    rows = [r for r in rows if r["llm_calls"] > 0]

    models = sorted({r["model"] for r in rows})
    per_model = {}
    for mdl in models:
        hs = sorted([r for r in rows if r["model"] == mdl],
                    key=lambda x: (-x["resolve_rate"], x["cost_usd"]))
        rates = [h["resolve_rate"] for h in hs]
        per_model[mdl] = {
            "model": mdl, "model_label": MODEL_LABELS.get(mdl, mdl), "harnesses": hs,
            "best": hs[0]["harness"] if hs else None,
            "worst": hs[-1]["harness"] if hs else None,
            "max_rate": max(rates) if rates else 0,
            "min_rate": min(rates) if rates else 0,
            "spread_pp": round((max(rates) - min(rates)) * 100, 1) if rates else 0.0,
            "ranking": [h["harness"] for h in hs],
            "cost_usd": round(sum(h["cost_usd"] for h in hs), 4),
            "tokens_total": sum(h["tokens_total"] for h in hs),
        }

    # Like-for-like comparison. Harnesses can end up with different denominators
    # (a budget cut-off leaves cells unattempted), and comparing 3/9 against 2/12
    # is not a fair spread. So the defensible headline is computed only over the
    # tasks EVERY harness on that model actually attempted.
    common = {}
    for mdl in models:
        hs = [r["harness"] for r in rows if r["model"] == mdl]
        attempted_sets = []
        for h in hs:
            reps = runs[(mdl, h)]
            primary = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
            attempted_sets.append({t for t, v in primary.items() if v["attempted"]})
        shared = set.intersection(*attempted_sets) if attempted_sets else set()
        per_h = []
        for h in hs:
            reps = runs[(mdl, h)]
            primary = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
            k = sum(1 for t in shared if primary[t]["resolved"])
            per_h.append({"harness": h, "n": len(shared), "resolved": k,
                          "rate": round(k / len(shared), 4) if shared else 0.0})
        per_h.sort(key=lambda x: -x["rate"])
        rates_c = [x["rate"] for x in per_h]
        common[mdl] = {
            "model": mdl, "n_common_tasks": len(shared),
            "tasks": sorted(shared), "harnesses": per_h,
            "spread_pp": round((max(rates_c) - min(rates_c)) * 100, 1) if rates_c else 0.0,
            "best": per_h[0]["harness"] if per_h else None,
            "worst": per_h[-1]["harness"] if per_h else None,
            "pp_per_task": round(100.0 / len(shared), 1) if shared else None,
        }

    # Does the harness ranking survive a change of model? This is the stronger
    # question: a spread that reorders under a different model is an interaction
    # with that model, not a property of the harness.
    cross = None
    if len(models) >= 2:
        a, b = models[0], models[1]
        ra, rb = per_model[a]["ranking"], per_model[b]["ranking"]
        common = [h for h in ra if h in rb]
        agree = [h for h in common if ra.index(h) == rb.index(h)]
        pairs_total = pairs_agree = 0
        for i in range(len(common)):
            for j in range(i + 1, len(common)):
                x, y = common[i], common[j]
                pairs_total += 1
                if (ra.index(x) < ra.index(y)) == (rb.index(x) < rb.index(y)):
                    pairs_agree += 1
        cross = {
            "model_a": a, "model_b": b,
            "ranking_a": ra, "ranking_b": rb,
            "identical_ranking": ra == rb,
            "same_position": agree,
            "pairwise_agreement": (round(pairs_agree / pairs_total, 3) if pairs_total else None),
            "pairs_total": pairs_total, "pairs_agree": pairs_agree,
            "best_a": per_model[a]["best"], "best_b": per_model[b]["best"],
            "spread_a_pp": per_model[a]["spread_pp"], "spread_b_pp": per_model[b]["spread_pp"],
        }

    # task x (model, harness) grid
    grid = []
    for tid in task_ids:
        row = {"task": tid,
               **{k: task_meta[tid][k] for k in
                  ("difficulty", "category", "agent_timeout_sec", "expert_time_estimate_min")},
               "oracle_ok": oracle.get(tid), "cells": {}}
        for (model, harness), reps in runs.items():
            primary = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
            t = primary.get(tid)
            row["cells"][f"{model}__{harness}"] = None if not t else {
                "model": model, "harness": harness,
                "resolved": t["resolved"], "reward": t["reward"],
                "exception": t["exception"], "timed_out": t["timed_out"],
                "infra_error": t["infra_error"], "attempted": t["attempted"],
                "n_steps": t["n_steps"], "wall_s": t["wall_s"],
                "parser_warnings": t.get("parser_warnings", 0),
            }
        solved = [k for k, c in row["cells"].items() if c and c["resolved"]]
        att = [k for k, c in row["cells"].items() if c and c["attempted"]]
        row["n_solved_by"], row["n_attempted_by"] = len(solved), len(att)
        row["solved_by"] = sorted(solved)
        row["disputed"] = 0 < len(solved) < len(att)
        grid.append(row)

    variance = []
    for (model, harness), reps in runs.items():
        if len(reps) < 2:
            continue
        per_rep, ordered = [], sorted(reps, key=lambda r: r["rep"])
        for r in ordered:
            n = sum(1 for t in r["trials"].values() if t["attempted"])
            k = sum(1 for t in r["trials"].values() if t["resolved"])
            per_rep.append({"rep": r["rep"], "job": r["job"], "n": n, "resolved": k,
                            "rate": round(k / n, 4) if n else 0.0})
        flips = []
        base = ordered[0]["trials"]
        for other in ordered[1:]:
            for tid, t in other["trials"].items():
                b = base.get(tid)
                if b and b["attempted"] and t["attempted"] and b["resolved"] != t["resolved"]:
                    flips.append({"task": tid, "rep1": b["resolved"], "rep2": t["resolved"]})
        variance.append({"model": model, "harness": harness,
                         "model_label": MODEL_LABELS.get(model, model),
                         "reps": per_rep, "flips": flips,
                         "n_flips": len(flips),
                         "rate_range_pp": round((max(r["rate"] for r in per_rep) -
                                                 min(r["rate"] for r in per_rep)) * 100, 1)})

    headline_model = models[0] if models else None
    head = per_model.get(headline_model, {}) if headline_model else {}
    excluded = [t for t in task_ids if oracle and not oracle.get(t, True)]

    # All-in spend across every meter log, including discarded runs, so the
    # write-up can state what the build actually cost rather than only what the
    # published numbers cost.
    all_in = {}
    for f in sorted(ART.glob("gateway.jsonl")) + sorted((ART / "compat").glob("*.jsonl")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        sub, n, ups = 0.0, 0, set()
        for ln in lines:
            try:
                dd = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if dd.get("event") != "call":
                continue
            n += 1
            ups.add(dd.get("upstream", "?"))
            c = dd.get("cost_usd")
            if c is None:
                pt, ct = dd.get("prompt_tokens") or 0, dd.get("completion_tokens") or 0
                c = (pt * 3 + ct * 15) / 1e6 if "k3" in str(dd.get("upstream")) else (pt * 0.95 + ct * 4) / 1e6
            sub += c
        prov = "Google AI Studio (Gemini)" if any("gemini" in u for u in ups) else "Moonshot (Kimi)"
        e = all_in.setdefault(prov, {"cost_usd": 0.0, "calls": 0})
        e["cost_usd"] += sub
        e["calls"] += n
    for v in all_in.values():
        v["cost_usd"] = round(v["cost_usd"], 2)

    out = {
        "all_in_spend": all_in,
        "meta": {
            "dataset": subset["dataset"],
            "n_tasks": len(task_ids),
            "n_tasks_scored": len(task_ids) - len(excluded),
            "excluded_by_oracle": excluded,
            "total_tasks_in_dataset": subset["total_tasks_in_dataset"],
            "candidate_pool_size": subset["candidate_pool_size"],
            "selection_seed": subset["seed"],
            "max_agent_timeout_sec": subset["max_agent_timeout_sec"],
            "agent_timeout_multiplier": float(os.environ.get("AGENT_TIMEOUT_MULT", "0.4")),
            "models": [MODEL_LABELS.get(m, m) for m in models],
            "temperature": "1.0 (Kimi) / provider default (Gemini)",
            "n_attempts_per_task": 1,
            "price_note": "Gemini 3.6 Flash list price $1.50/M input, $0.375/M cached input, "
                          "$7.50/M output, applied to gateway-metered tokens",
            "n_candidates": subset.get("n_candidates"),
            "n_qualified": subset.get("n_qualified"),
            "disqualified_by_oracle": subset.get("disqualified_by_oracle", []),
            "unscreened": subset.get("unscreened", []),
        },
        # The headline is the like-for-like number: same tasks for every harness.
        "headline": (lambda c: {
            "model": headline_model,
            "model_label": MODEL_LABELS.get(headline_model, headline_model),
            "basis": "common attempted subset",
            "n_tasks": c.get("n_common_tasks", 0),
            "pp_per_task": c.get("pp_per_task"),
            "best": c.get("best"), "worst": c.get("worst"),
            "max_rate": (c["harnesses"][0]["rate"] if c.get("harnesses") else 0),
            "min_rate": (c["harnesses"][-1]["rate"] if c.get("harnesses") else 0),
            "spread_pp": c.get("spread_pp", 0.0),
            "ranking": [h["harness"] for h in c.get("harnesses", [])],
        })(common.get(headline_model, {})),
        "models": models,
        "per_model": per_model,
        "common_subset": common,
        "cross_model": cross,
        "harnesses": rows,
        "not_driven": not_driven,
        "tasks": grid,
        "variance": variance,
        "oracle": oracle,
        "task_meta": task_meta,
    }
    (ART / "results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out["headline"], indent=2))
    if excluded:
        print("excluded by oracle baseline:", excluded)
    for mdl in models:
        pm = per_model[mdl]
        print(f"\n== {pm['model_label']}  spread {pm['spread_pp']}pp  ${pm['cost_usd']:.2f}")
        for h in pm["harnesses"]:
            print(f"   {h['harness']:16s} {h['n_resolved']:2d}/{h['n_tasks']:2d} "
                  f"= {h['resolve_rate']*100:5.1f}%  tok={h['tokens_total']:>9,}  "
                  f"${h['cost_usd']:.3f}  timeouts={h['n_timeouts']}  not-run={h['n_infra_errors']}")
    for mdl, c in common.items():
        print(f"\n== like-for-like on the {c['n_common_tasks']} tasks every harness attempted "
              f"({mdl}): spread {c['spread_pp']}pp, 1 task = {c['pp_per_task']}pp")
        for h in c["harnesses"]:
            print(f"   {h['harness']:16s} {h['resolved']}/{h['n']} = {h['rate']*100:5.1f}%")
    if cross:
        print(f"\ncross-model pairwise ranking agreement: "
              f"{cross['pairs_agree']}/{cross['pairs_total']}")


if __name__ == "__main__":
    main()
