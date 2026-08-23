"""Per-query latency of query_reranked.py: passthrough vs --rerank.

Runs every query in benchmark set v1 through the wrapper at a fixed budget,
timing wall-clock subprocess duration for both modes, and reports
min/median/p90/max so a real, small explicit ceiling can be set before
--rerank is ever turned on by default. Passthrough is one subprocess call;
--rerank is two internally (target-budget call + pool call), so its latency
is expected to be higher -- the question this answers is by how much, on
real queries against the real Add-Ins graph, not in the abstract.

Usage:
  python measure_latency.py --budget 2000
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
RECALL_DIR = Path(r"F:\Claude-Tools\graphify\recall")
sys.path.insert(0, str(RECALL_DIR))

import measure_recall as mr  # noqa: E402

WRAPPER = SCRIPTS_DIR / "query_reranked.py"


def timed_call(repo: Path, question: str, budget: int, rerank: bool) -> float:
    cmd = [sys.executable, str(WRAPPER), question, "--budget", str(budget)]
    if rerank:
        cmd.append("--rerank")
    env = dict(os.environ)
    env["GRAPHIFY_QUERY_LOG_DISABLE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd, cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"query_reranked.py failed for {question!r}: {proc.stderr[:400]}")
    return elapsed


def stats(label: str, samples: list[float]) -> dict:
    s = sorted(samples)
    n = len(s)
    p90 = s[min(n - 1, int(round(0.9 * (n - 1))))]
    row = {
        "label": label,
        "n": n,
        "min": round(s[0], 3),
        "median": round(statistics.median(s), 3),
        "p90": round(p90, 3),
        "max": round(s[-1], 3),
        "mean": round(statistics.mean(s), 3),
    }
    print(
        f"  {label:<12} n={row['n']:<3} min={row['min']:.3f}s  "
        f"median={row['median']:.3f}s  p90={row['p90']:.3f}s  max={row['max']:.3f}s"
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=SCRIPTS_DIR / "latency-report.json")
    args = ap.parse_args()

    spec = mr.load_spec()
    repo = Path(spec["repo"])

    passthrough_times, rerank_times, per_query = [], [], []
    for q in spec["queries"]:
        pt = timed_call(repo, q["question"], args.budget, rerank=False)
        rt = timed_call(repo, q["question"], args.budget, rerank=True)
        passthrough_times.append(pt)
        rerank_times.append(rt)
        per_query.append({"id": q["id"], "passthrough_s": round(pt, 3), "rerank_s": round(rt, 3),
                           "overhead_s": round(rt - pt, 3)})
        print(f"  {q['id']:<5} passthrough={pt:.3f}s  rerank={rt:.3f}s  overhead={rt - pt:+.3f}s")

    print(f"\n=== latency summary (budget={args.budget}, {len(per_query)} queries) ===")
    passthrough_stats = stats("passthrough", passthrough_times)
    rerank_stats = stats("rerank", rerank_times)
    overheads = [r["overhead_s"] for r in per_query]
    overhead_stats = stats("overhead", overheads)

    report = {
        "budget": args.budget,
        "queries": len(per_query),
        "passthrough": passthrough_stats,
        "rerank": rerank_stats,
        "overhead": overhead_stats,
        "per_query": per_query,
    }
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
