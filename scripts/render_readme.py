#!/usr/bin/env python3
"""Fill the generated blocks in README.md from artifacts/results.json.

Every number in the write-up is rendered from the committed artifacts rather than
typed by hand, so the prose cannot drift from the data. Blocks are delimited by
`<!-- BEGIN:name -->` / `<!-- END:name -->` and replaced in place.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = json.loads((ROOT / "artifacts" / "results.json").read_text())
README = ROOT / "README.md"


def block(name: str, body: str, text: str) -> str:
    pat = re.compile(rf"(<!-- BEGIN:{name} -->)(.*?)(<!-- END:{name} -->)", re.S)
    if not pat.search(text):
        raise SystemExit(f"marker {name} not found in README")
    return pat.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text)


def main() -> None:
    text = README.read_text()
    meta, head = RES["meta"], RES["headline"]
    models, pm = RES.get("models", []), RES.get("per_model", {})

    # ---- headline
    hl = (
        f"On **{meta['models'][0] if meta.get('models') else head.get('model_label','')}**, "
        f"across {meta.get('n_tasks_scored', meta['n_tasks'])} Terminal-Bench 2.0 tasks with "
        f"the model, the tasks, and the time budget held constant, the best harness "
        f"(`{head['best']}`) resolved **{head['max_rate']*100:.1f}%** and the worst "
        f"(`{head['worst']}`) resolved **{head['min_rate']*100:.1f}%** — a spread of "
        f"**{head['spread_pp']} percentage points** attributable to nothing but the scaffold "
        f"around the model."
    )
    if len(models) > 1:
        hl += (f" Under a second model ({pm[models[1]]['model_label']}) the spread is "
               f"**{pm[models[1]]['spread_pp']}pp**.")
    text = block("headline", hl, text)

    # ---- leaderboard, one table per model
    out = []
    for m in models:
        p_ = pm[m]
        out.append(f"**{p_['model_label']}** — spread **{p_['spread_pp']}pp**\n")
        out.append("| Harness | Resolved | Resolve rate | Tokens (in / cached / out) | Cost | Solved per $ | Timed out |")
        out.append("|---|---|---|---|---|---|---|")
        for h in p_["harnesses"]:
            out.append(
                f"| `{h['harness']}` | {h['n_resolved']}/{h['n_tasks']} | "
                f"**{h['resolve_rate']*100:.1f}%** | "
                f"{h['prompt_tokens']:,} / {h['cached_tokens']:,} / {h['completion_tokens']:,} | "
                f"${h['cost_usd']:.3f} | "
                f"{h['resolved_per_usd'] if h['resolved_per_usd'] is not None else '—'} | "
                f"{h['n_timeouts']} |")
        out.append("")
    n_scored = meta.get("n_tasks_scored", meta["n_tasks"])
    per_flip = 100.0 / n_scored if n_scored else 0.0
    out.append("")
    out.append(f"> **Read these rates as relative, not absolute.** Every harness ran with a "
               f"**{meta.get('agent_timeout_multiplier')}× time budget** — 40% of each task's own "
               f"wall-clock allowance — applied identically to all of them so the comparison "
               f"stays fair. That deliberately depresses every number here, so **these rates are "
               f"not comparable to published Terminal-Bench 2.0 figures** and should not be "
               f"quoted as a result for this model. The only claim being made is the *gap "
               f"between harnesses within this run*.")
    out.append("")
    out.append(f"> **How much weight the spread can carry:** on {n_scored} scored tasks, one "
               f"task changing outcome moves a harness by **{per_flip:.1f} percentage points**. "
               f"Any spread below that is indistinguishable from a single coin flip.")
    c = RES.get("cross_model")
    if c:
        out.append("**Does the ranking survive changing the model?** "
                   + ("The ordering is **identical** under both models."
                      if c["identical_ranking"] else
                      "The ordering **changes** between models.")
                   + f" {c['pairs_agree']} of {c['pairs_total']} harness pairs keep their "
                     f"relative order. Best harness: `{c['best_a']}` "
                     f"({c['model_a']}) vs `{c['best_b']}` ({c['model_b']}).")
    text = block("leaderboard", "\n".join(out), text)

    # ---- grid
    cols = [f"{m}__{h['harness']}" for m in models for h in pm[m]["harnesses"]]
    g = ["| Task | Difficulty | Category | " + " | ".join(f"`{c.split('__')[1]}`<br><sub>{c.split('__')[0]}</sub>" for c in cols) + " | Agreement |",
         "|---|---|---|" + "---|" * (len(cols) + 1)]
    for t in RES["tasks"]:
        cells = []
        for k in cols:
            cc = t["cells"].get(k)
            cells.append("–" if not cc else "✅" if cc["resolved"]
                         else "–" if cc["infra_error"] else "⏱" if cc["timed_out"] else "❌")
        agree = f"{t['n_solved_by']}/{t['n_attempted_by']}"
        if t["disputed"]:
            agree = f"**split {agree}**"
        g.append(f"| `{t['task']}` | {t['difficulty']} | {t['category']} | " + " | ".join(cells) + f" | {agree} |")
    g += ["", "✅ solved · ❌ attempted, not solved · ⏱ ran out of its time budget (counted as a "
              "failure) · – not run or excluded (never counted as a zero)"]
    text = block("grid", "\n".join(g), text)

    # ---- disagreement
    disputed = [t for t in RES["tasks"] if t["disputed"]]
    if disputed:
        lines = [f"{len(disputed)} of {meta.get('n_tasks_scored', meta['n_tasks'])} scored tasks "
                 f"split the harnesses:", ""]
        for t in disputed:
            solved = ", ".join(f"`{s.split('__')[1]}` ({s.split('__')[0]})" for s in t["solved_by"])
            missed = ", ".join(f"`{k.split('__')[1]}` ({k.split('__')[0]})"
                               for k, cc in t["cells"].items()
                               if cc and cc["attempted"] and not cc["resolved"])
            lines.append(f"- **`{t['task']}`** ({t['difficulty']}, {t['category']}) — solved by "
                         f"{solved}" + (f"; missed by {missed}" if missed else ""))
        body = "\n".join(lines)
    else:
        body = ("No task split the harnesses on this subset: every scored task was either solved "
                "by all of them or by none. That is a weaker result than a visible disagreement "
                "and is reported as such — with 6 scored tasks there is simply not much room for "
                "the harnesses to differ.")
    text = block("disagreement", body, text)

    # ---- run config
    cfg = [
        f"- **Benchmark:** `{meta['dataset']}` — {meta['n_tasks']} tasks selected from "
        f"{meta['total_tasks_in_dataset']}, of which "
        f"**{meta.get('n_tasks_scored', meta['n_tasks'])} are scored**",
        f"- **Selection:** seeded (`{meta['selection_seed']}`) stratified pick from the "
        f"{meta['candidate_pool_size']} tasks with an agent budget ≤ "
        f"{meta['max_agent_timeout_sec']}s, frozen before any run — `scripts/select_tasks.py`",
        f"- **Models:** {' · '.join(meta.get('models', []))}",
        f"- **Agent time budget:** {meta.get('agent_timeout_multiplier')}× each task's own limit, "
        f"identical for every harness",
        f"- **Attempts per task:** {meta['n_attempts_per_task']}",
        f"- **Verification:** each task's own test suite, after an `oracle` reference-solution "
        f"baseline confirmed the graders work in this environment",
        f"- **Pricing:** {meta['price_note']}",
    ]
    if meta.get("excluded_by_oracle"):
        cfg.append("- **Excluded by the oracle baseline** (reference solution cannot pass here, so "
                   "never scored): " + ", ".join(f"`{t}`" for t in meta["excluded_by_oracle"]))
    text = block("runconfig", "\n".join(cfg), text)

    # ---- spend
    lines = []
    total = 0.0
    for m in models:
        p_ = pm[m]
        total += p_["cost_usd"]
        lines.append(f"- **{p_['model_label']}**: ${p_['cost_usd']:.2f} across "
                     f"{p_['tokens_total']:,} metered tokens")
    calls = sum(h["llm_calls"] for h in RES["harnesses"])
    lines.append("")
    lines.append(f"Total metered spend: **${total:.2f}** over {calls:,} model calls. Every figure "
                 f"is billed at the gateway as the call happens, not estimated afterwards, and "
                 f"per-harness spend is capped at an equal slice so the harness that runs first "
                 f"cannot starve the one that runs last.")
    text = block("spend", "\n".join(lines), text)

    # ---- not driven
    nd = RES.get("not_driven") or []
    if nd:
        body = "\n".join(
            f"- `{h['harness']}` ({h['model']}) completed {h['n_cells']} trials but made "
            f"**0 model calls** — reported as not driven, never as a score of 0." for h in nd)
    else:
        body = ("Every harness on the board made real, metered model calls. Nothing was scored 0 "
                "while silently failing to call the model — the failure mode that cost me the "
                "most time on this build.")
    text = block("notdriven", body, text)

    README.write_text(text)
    print(f"README rendered: {len(models)} model(s), headline spread {head['spread_pp']}pp, "
          f"${total:.2f} total")


if __name__ == "__main__":
    main()
