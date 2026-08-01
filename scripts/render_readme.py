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
    H, meta, head = RES["harnesses"], RES["meta"], RES["headline"]

    # ---- leaderboard table
    rows = ["| Harness | Resolved | Resolve rate | Tokens (in / cached / out) | Cost | Solved per $ |",
            "|---|---|---|---|---|---|"]
    for h in H:
        rows.append(
            f"| `{h['harness']}` | {h['n_resolved']}/{h['n_tasks']} | "
            f"**{h['resolve_rate']*100:.1f}%** | "
            f"{h['prompt_tokens']:,} / {h['cached_tokens']:,} / {h['completion_tokens']:,} | "
            f"${h['cost_usd']:.3f} | "
            f"{h['resolved_per_usd'] if h['resolved_per_usd'] is not None else '—'} |"
        )
    text = block("leaderboard", "\n".join(rows), text)

    # ---- headline sentence
    spread = head["spread_pp"]
    hl = (
        f"Across {meta['n_tasks']} Terminal-Bench 2.0 tasks with the model, the tasks, and the "
        f"time budget held constant, the best harness (`{head['best']}`) resolved "
        f"**{head['max_rate']*100:.1f}%** and the worst (`{head['worst']}`) resolved "
        f"**{head['min_rate']*100:.1f}%** — a spread of **{spread} percentage points** "
        f"attributable to nothing but the scaffold around the model."
    )
    text = block("headline", hl, text)

    # ---- task x harness grid
    names = [h["harness"] for h in H]
    g = ["| Task | Difficulty | Category | " + " | ".join(f"`{n}`" for n in names) + " | Agreement |",
         "|---|---|---|" + "---|" * (len(names) + 1)]
    for t in RES["tasks"]:
        cells = []
        for n in names:
            c = t["cells"].get(n)
            cells.append("–" if not c else "✅" if c["resolved"] else ("⚠️" if c["exception"] else "❌"))
        agree = f"{t['n_solved_by']}/{len([c for c in t['cells'].values() if c])}"
        if t["disputed"]:
            agree = f"**split {agree}**"
        g.append(f"| `{t['task']}` | {t['difficulty']} | {t['category']} | " + " | ".join(cells) + f" | {agree} |")
    g.append("")
    g.append("✅ solved · ❌ attempted, not solved · ⚠️ infrastructure error, not counted as a "
             "model failure · – not run")
    text = block("grid", "\n".join(g), text)

    # ---- disagreement summary
    disputed = [t for t in RES["tasks"] if t["disputed"]]
    if disputed:
        lines = [f"{len(disputed)} of {meta['n_tasks']} tasks split the harnesses:", ""]
        for t in disputed:
            solved = ", ".join(f"`{s}`" for s in t["solved_by"])
            missed = ", ".join(
                f"`{n}`" for n in names
                if t["cells"].get(n) and not t["cells"][n]["resolved"] and not t["cells"][n]["exception"]
            )
            lines.append(f"- **`{t['task']}`** ({t['difficulty']}, {t['category']}) — solved by {solved}"
                         + (f"; missed by {missed}" if missed else ""))
        body = "\n".join(lines)
    else:
        body = ("No task split the harnesses: every task was either solved by all of them or by "
                "none. On this subset the spread comes from overall counts rather than from "
                "identifiable per-task disagreement, which is a weaker result and is reported as such.")
    text = block("disagreement", body, text)

    # ---- run config
    cfg = [
        f"- **Benchmark:** `{meta['dataset']}` — {meta['n_tasks']} of "
        f"{meta['total_tasks_in_dataset']} tasks",
        f"- **Selection:** seeded (`{meta['selection_seed']}`) stratified pick from the "
        f"{meta['candidate_pool_size']} tasks with an agent budget ≤ {meta['max_agent_timeout_sec']}s, "
        f"frozen before any run — see `scripts/select_tasks.py`",
        f"- **Model:** {meta['model']}",
        f"- **Temperature:** {meta['temperature']} (Kimi K2.7 Code rejects any other value)",
        f"- **Attempts per task:** {meta['n_attempts_per_task']}",
        f"- **Pricing:** {meta['price_note']}",
    ]
    text = block("runconfig", "\n".join(cfg), text)

    # ---- spend
    total = sum(h["cost_usd"] for h in H)
    tok = sum(h["tokens_total"] for h in H)
    calls = sum(h["llm_calls"] for h in H)
    spend = (
        f"The whole experiment cost **${total:.2f}** of Moonshot credit — {tok:,} tokens "
        f"across {calls:,} model calls, metered at the gateway rather than estimated. "
        f"Per-harness spend was capped at an equal slice so that whichever harness ran first "
        f"could not starve the ones that ran last."
    )
    text = block("spend", spend, text)

    # ---- not driven
    nd = RES.get("not_driven") or []
    if nd:
        body = "\n".join(
            f"- `{h['harness']}` completed {h['n_tasks']} trials but made **0 model calls** — "
            f"reported as not driven, not as a score of 0." for h in nd)
    else:
        body = ("Every harness on the board made real, metered model calls; none were counted "
                "as scoring 0 while silently failing to call the model.")
    text = block("notdriven", body, text)

    README.write_text(text)
    print(f"README rendered: {len(H)} harnesses, spread {spread}pp, ${total:.2f}")


if __name__ == "__main__":
    main()
