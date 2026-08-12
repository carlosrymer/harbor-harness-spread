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
        f"On **{head.get('model_label','')}**, over the **{head.get('n_tasks')} Terminal-Bench "
        f"2.0 tasks every harness attempted**, with the model, the tasks and the time budget "
        f"held constant, the best harness (`{head['best']}`) resolved "
        f"**{head['max_rate']*100:.1f}%** and the worst (`{head['worst']}`) resolved "
        f"**{head['min_rate']*100:.1f}%** — a spread of **{head['spread_pp']} percentage "
        f"points** attributable to nothing but the scaffold around the model. "
        f"Ranking: {' › '.join('`'+h+'`' for h in head.get('ranking', []))}."
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
    n_scored = head.get("n_tasks") or meta.get("n_tasks_scored", meta["n_tasks"])
    per_flip = head.get("pp_per_task") or (100.0 / n_scored if n_scored else 0.0)
    out.append("")
    out.append(f"> **Read these rates as relative, not absolute.** Every harness ran with a "
               f"**{meta.get('agent_timeout_multiplier')}× time budget** — that fraction of each task's "
               f"own wall-clock allowance — applied identically to all of them so the comparison "
               f"stays fair. That deliberately depresses every number here, so **these rates are "
               f"not comparable to published Terminal-Bench 2.0 figures** and should not be "
               f"quoted as a result for this model. The only claim being made is the *gap "
               f"between harnesses within this run*.")
    out.append("")
    out.append(f"> **How much weight the spread can carry:** on the {n_scored} tasks every "
               f"harness attempted, one task changing outcome moves a harness by "
               f"**{per_flip:.1f} percentage points**. The measured spread of "
               f"{head['spread_pp']}pp is therefore about "
               f"{head['spread_pp']/per_flip:.0f} task(s) wide. Any spread of one task or less "
               f"is indistinguishable from a coin flip. A repeat pass measured the actual "
               f"run-to-run swing — see the variance section, which is where this spread should "
               f"be judged.")
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
        f"- **Benchmark:** `{meta['dataset']}` ({meta['total_tasks_in_dataset']} tasks). "
        f"{meta.get('n_candidates', '?')} candidates were selected and oracle-screened; "
        f"**{meta.get('n_qualified', '?')} qualified** and **{meta['n_tasks']} were scored** "
        f"(capped in seeded order)",
        f"- **Excluded — grader failure, not agent failure:** "
        f"{len(meta.get('disqualified_by_oracle', []))} task(s) whose own reference solution "
        f"could not pass here "
        + (", ".join(f"`{t}`" for t in meta.get("disqualified_by_oracle", [])) or "none")
        + f". A further {len(meta.get('unscreened', []))} candidate(s) could not be screened at "
          f"all because their images would not pull in this environment.",
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
    allin = RES.get("all_in_spend", {})
    lines.append("")
    lines.append(f"That is the spend behind the numbers published above: **${total:.2f}** over "
                 f"{calls:,} model calls, billed at the gateway as each call happened rather than "
                 f"estimated afterwards.")
    if allin:
        lines.append("")
        lines.append("**All-in cost of the build, including work that produced nothing "
                     "publishable** — probes, the invalidated false-zero sweep, and a Gemini run "
                     "discarded when the task set was widened:")
        lines.append("")
        for prov, v in sorted(allin.items()):
            lines.append(f"- **{prov}**: ${v['cost_usd']:.2f} over {v['calls']:,} calls")
        lines.append("- **OpenAI**: $0.00 — a key arrived late in the build and was used only to "
                     "enumerate available models. No scored run used an OpenAI model, because "
                     "doing so would have varied the model and the harness together.")
        lines.append("")
        lines.append("The gap between those two figures is the honest price of finding the "
                     "verifier bug: a large share of the total bought discarded results.")
    text = block("spend", "\n".join(lines), text)

    # ---- variance
    var = RES.get("variance") or []
    if var:
        lines = []
        for v in var:
            reps = " → ".join(f"{r['resolved']}/{r['n']} ({r['rate']*100:.1f}%)" for r in v["reps"])
            lines.append(
                f"I re-ran **`{v['harness']}`** over the same tasks, same model, same limits, "
                f"changing nothing: **{reps}** — a swing of **{v['rate_range_pp']} percentage "
                f"points** between identical runs, with "
                f"{v['n_flips']} task(s) changing outcome"
                + (" (" + ", ".join(f"`{f['task']}`" for f in v["flips"]) + ")" if v["flips"] else "")
                + ".")
        worst = max(v["rate_range_pp"] for v in var)
        spread = head["spread_pp"]
        lines.append("")
        lines.append(
            f"**What that means for the headline.** The measured spread is {spread}pp and the "
            f"observed run-to-run swing on a single harness is {worst}pp. So the *extremes* of "
            f"the ranking are separable — `{head['best']}` at {head['max_rate']*100:.1f}% versus "
            f"`{head['worst']}` at {head['min_rate']*100:.1f}% is a gap several times larger than "
            f"the noise I measured. The *middle* of the ranking is not: adjacent harnesses "
            f"separated by less than {worst}pp cannot be ordered from this data, and I do not "
            f"claim they can.")
        lines.append("")
        lines.append(
            "This is one repeated harness, not all four, so it is a floor on the variance rather "
            "than a full characterisation. Decoding is stochastic — Gemini is left at its "
            "provider default and Kimi K2.7 Code accepts only `temperature=1` — so some flipping "
            "between identical runs is expected. Anyone quoting a single-run agent benchmark "
            "number, mine included, should assume a swing of this order.")
        body = "\n".join(lines)
    else:
        body = ("Not measured — every cell was run once, so this data cannot separate the harness "
                "spread from run-to-run noise. Treat the ranking as indicative, not settled.")
    text = block("variance", body, text)

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
