#!/usr/bin/env python3
"""Build the scored subset: candidates that survived the oracle screen.

The scored set is defined by evidence, not by hand: a task is included only if
Harbor's `oracle` agent -- running that task's own reference solution through the
identical container, verifier and network setup -- actually passed it here. A task
whose reference solution cannot pass is measuring the environment, not the agent,
so scoring an agent 0 on it would be a fabricated failure.

Order is preserved from the seeded candidate selection, so capping to MAX_TASKS
is deterministic and not a second, results-aware choice.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_TASKS = int(os.environ.get("MAX_TASKS", "12"))
SCREEN_JOBS = [d for d in (ROOT / "jobs").glob("oracle-*") if d.is_dir()]

verdict = {}
for job in SCREEN_JOBS:
    for d in sorted(job.iterdir()):
        rf = d / "verifier" / "reward.txt"
        if d.is_dir() and rf.exists():
            task = d.name.rsplit("__", 1)[0]
            passed = rf.read_text().strip() == "1"
            verdict[task] = verdict.get(task, False) or passed

cand = json.loads((ROOT / "artifacts" / "candidates.json").read_text())
qualified, disqualified, unscreened = [], [], []
for t in cand["tasks"]:
    if t["id"] not in verdict:
        unscreened.append(t["id"])
    elif verdict[t["id"]]:
        qualified.append(t)
    else:
        disqualified.append(t["id"])

scored = qualified[:MAX_TASKS]
out = {
    **{k: v for k, v in cand.items() if k != "tasks"},
    "tasks": scored,
    "n_candidates": len(cand["tasks"]),
    "n_qualified": len(qualified),
    "n_scored": len(scored),
    "disqualified_by_oracle": disqualified,
    "unscreened": unscreened,
    "capped_at": MAX_TASKS,
    "oracle_note": (
        "A task is scored only if Harbor's oracle agent -- the task's own reference "
        "solution -- passed it through this exact pipeline. Tasks the reference "
        "solution cannot pass here are excluded rather than scored as agent failures."
    ),
}
(ROOT / "artifacts" / "scored_subset.json").write_text(json.dumps(out, indent=2))
print(f"candidates={len(cand['tasks'])} qualified={len(qualified)} "
      f"scored={len(scored)} disqualified={len(disqualified)} unscreened={len(unscreened)}")
print("scored:", [t["id"] for t in scored])
print("disqualified:", disqualified)
if unscreened:
    print("unscreened:", unscreened, file=sys.stderr)
