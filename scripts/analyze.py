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

Cost model: Moonshot list price for Kimi K3 -- $3.00 /M uncached input,
$0.30 /M cached input, $15.00 /M output. Cached prompt tokens are billed at the
discounted rate, which matters a lot: several harnesses re-send a long prefix on
every step and would look far more expensive under a flat input price.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
ART = ROOT / "artifacts"

PRICE_IN_UNCACHED = 3.00 / 1_000_000
PRICE_IN_CACHED = 0.30 / 1_000_000
PRICE_OUT = 15.00 / 1_000_000

# job-name prefix -> harness label shown on the site
def job_harness(job_name: str) -> str | None:
    m = re.match(r"^run-(.+?)(?:--rep(\d+))?$", job_name)
    return m.group(1) if m else None


def job_rep(job_name: str) -> int:
    m = re.match(r"^run-(.+?)--rep(\d+)$", job_name)
    return int(m.group(2)) if m else 1


def cost_usd(p_tok: int, cached: int, c_tok: int) -> float:
    uncached = max(0, p_tok - cached)
    return uncached * PRICE_IN_UNCACHED + cached * PRICE_IN_CACHED + c_tok * PRICE_OUT


def load_gateway_meter() -> dict:
    """Per-harness token totals measured at the gateway (not self-reported)."""
    per = defaultdict(lambda: {"calls": 0, "errors": 0, "retries": 0, "prompt_tokens": 0,
                               "cached_tokens": 0, "completion_tokens": 0,
                               "reasoning_tokens": 0, "latency_s": 0.0})
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
    for h, e in per.items():
        e["cost_usd"] = round(cost_usd(e["prompt_tokens"], e["cached_tokens"], e["completion_tokens"]), 4)
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
    started, finished = r.get("started_at"), r.get("finished_at")
    return {
        "task": task, "trial_dir": str(d.relative_to(ROOT)),
        "reward": reward, "resolved": bool(reward and reward > 0),
        "exception": exc, "started_at": started, "finished_at": finished,
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


def main() -> None:
    subset = json.loads((ART / "subset.json").read_text())
    task_meta = {t["id"]: t for t in subset["tasks"]}
    task_ids = [t["id"] for t in subset["tasks"]]

    runs = defaultdict(list)  # harness -> list of (rep, {task: trial})
    if JOBS.exists():
        for job_dir in sorted(JOBS.iterdir()):
            if not job_dir.is_dir():
                continue
            h = job_harness(job_dir.name)
            if not h:
                continue
            trials = {}
            tdir = ART / "trajectories"
            tdir.mkdir(parents=True, exist_ok=True)
            for d in trial_dirs(job_dir):
                t = read_trial(d)
                trials[t["task"]] = t
                if job_rep(job_dir.name) == 1:  # only the primary run feeds the site
                    ex = extract_trajectory(d, h, t["task"])
                    if ex:
                        (tdir / f"{h}__{t['task']}.json").write_text(json.dumps(ex, indent=1))
            if trials:
                runs[h].append({"rep": job_rep(job_dir.name), "job": job_dir.name, "trials": trials})

    meter = load_gateway_meter()

    harnesses = []
    for h, reps in sorted(runs.items()):
        primary = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
        n_attempted = len(primary)
        n_resolved = sum(1 for t in primary.values() if t["resolved"])
        n_errors = sum(1 for t in primary.values() if t["exception"])
        m = meter.get(h, {})
        tot_tok = m.get("prompt_tokens", 0) + m.get("completion_tokens", 0)
        steps = [t["n_steps"] for t in primary.values() if t["n_steps"] is not None]
        walls = [t["wall_s"] for t in primary.values() if t["wall_s"] is not None]
        harnesses.append({
            "harness": h,
            "n_tasks": n_attempted,
            "n_resolved": n_resolved,
            "resolve_rate": round(n_resolved / n_attempted, 4) if n_attempted else 0.0,
            "n_infra_errors": n_errors,
            "tokens_total": tot_tok,
            "prompt_tokens": m.get("prompt_tokens", 0),
            "cached_tokens": m.get("cached_tokens", 0),
            "completion_tokens": m.get("completion_tokens", 0),
            "reasoning_tokens": m.get("reasoning_tokens", 0),
            "llm_calls": m.get("calls", 0),
            "llm_errors": m.get("errors", 0),
            "llm_retries": m.get("retries", 0),
            "cost_usd": m.get("cost_usd", 0.0),
            "wall_s_total": round(sum(walls), 1) if walls else None,
            "steps_total": sum(steps) if steps else None,
            "steps_median": (sorted(steps)[len(steps) // 2] if steps else None),
            # Cost-adjusted: resolved tasks per million tokens and per dollar.
            "resolved_per_mtok": round(n_resolved / (tot_tok / 1e6), 3) if tot_tok else None,
            "resolved_per_usd": round(n_resolved / m["cost_usd"], 3) if m.get("cost_usd") else None,
            "reps": len(reps),
        })

    # A harness that finished every trial without ever calling the model did not
    # "score 0" -- it never ran. Aider did exactly this at first (Harbor strips the
    # provider prefix, so litellm inside aider got a bare model name and refused),
    # and Harbor still reported a clean trial with reward 0. Anything with no
    # metered LLM calls is reported as not-driven, never as a zero on the board.
    not_driven = [h for h in harnesses if h["llm_calls"] == 0]
    harnesses = [h for h in harnesses if h["llm_calls"] > 0]

    harnesses.sort(key=lambda x: (-x["resolve_rate"], x["cost_usd"]))
    rates = [h["resolve_rate"] for h in harnesses]
    spread = round(max(rates) - min(rates), 4) if rates else 0.0

    # task x harness grid
    grid = []
    for tid in task_ids:
        row = {"task": tid, **{k: task_meta[tid][k] for k in
                               ("difficulty", "category", "agent_timeout_sec", "expert_time_estimate_min")},
               "cells": {}}
        for h, reps in runs.items():
            primary = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
            t = primary.get(tid)
            row["cells"][h] = None if not t else {
                "resolved": t["resolved"], "reward": t["reward"],
                "exception": t["exception"], "n_steps": t["n_steps"], "wall_s": t["wall_s"],
            }
        solved_by = [h for h, c in row["cells"].items() if c and c["resolved"]]
        row["n_solved_by"] = len(solved_by)
        row["solved_by"] = sorted(solved_by)
        row["disputed"] = 0 < len(solved_by) < len([c for c in row["cells"].values() if c])
        grid.append(row)

    # repeat-run variance (only harnesses run more than once)
    variance = []
    for h, reps in runs.items():
        if len(reps) < 2:
            continue
        per_rep = []
        for r in sorted(reps, key=lambda r: r["rep"]):
            n = len(r["trials"])
            k = sum(1 for t in r["trials"].values() if t["resolved"])
            per_rep.append({"rep": r["rep"], "job": r["job"], "n": n, "resolved": k,
                            "rate": round(k / n, 4) if n else 0.0})
        flips = []
        base = sorted(reps, key=lambda r: r["rep"])[0]["trials"]
        for other in sorted(reps, key=lambda r: r["rep"])[1:]:
            for tid, t in other["trials"].items():
                b = base.get(tid)
                if b and b["resolved"] != t["resolved"]:
                    flips.append({"task": tid, "rep1": b["resolved"], "rep2": t["resolved"]})
        variance.append({"harness": h, "reps": per_rep, "flips": flips})

    out = {
        "meta": {
            "dataset": subset["dataset"],
            "n_tasks": len(task_ids),
            "total_tasks_in_dataset": subset["total_tasks_in_dataset"],
            "candidate_pool_size": subset["candidate_pool_size"],
            "selection_seed": subset["seed"],
            "max_agent_timeout_sec": subset["max_agent_timeout_sec"],
            "model": "moonshotai/kimi-k3 (Moonshot API, pinned by the gateway)",
            "temperature": 1.0,
            "n_attempts_per_task": 1,
            "price_note": "Moonshot list price: $3.00/M input, $0.30/M cached input, $15.00/M output",
        },
        "headline": {
            "best": harnesses[0]["harness"] if harnesses else None,
            "worst": harnesses[-1]["harness"] if harnesses else None,
            "max_rate": max(rates) if rates else 0,
            "min_rate": min(rates) if rates else 0,
            "spread_pp": round(spread * 100, 1),
        },
        "harnesses": harnesses,
        "not_driven": not_driven,
        "tasks": grid,
        "variance": variance,
        "task_meta": task_meta,
    }
    (ART / "results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out["headline"], indent=2))
    for h in harnesses:
        print(f"  {h['harness']:16s} {h['n_resolved']:2d}/{h['n_tasks']:2d} "
              f"= {h['resolve_rate']*100:5.1f}%  tok={h['tokens_total']:>9,}  "
              f"${h['cost_usd']:.2f}  err={h['n_infra_errors']}")


if __name__ == "__main__":
    main()
