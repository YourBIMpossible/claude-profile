"""Re-run benchmark set v1 through the production query_reranked.py wrapper.

Verifies two things the standalone rerank_bm25.py experiment couldn't:
  1. The wrapper's default (no --rerank) path is a true no-op: measuring
     through it reproduces measure_recall.py's own bare-graphify baseline
     exactly, query for query.
  2. The wrapper's --rerank path, run through the actual production script
     (not the experiment's standalone copy of the algorithm), reproduces
     the previously measured improvement -- same queries, same budget,
     same graph.

Imports load_spec/graph_source_files/aggregate/measure/fmt/now_iso from
measure_recall.py rather than reimplementing them; only the "how do I run
one query" seam is swapped to go through query_reranked.py.

Usage:
  python bench_reranker.py --budget 2000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
RECALL_DIR = Path(r"F:\AI-Dev\.tools\graphify\recall")
sys.path.insert(0, str(RECALL_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

import measure_recall as mr  # noqa: E402
import query_reranked as qr  # noqa: E402

WRAPPER = SCRIPTS_DIR / "query_reranked.py"


def run_query_via_wrapper(repo: Path, question: str, budget: int, rerank: bool) -> list[str]:
    """Same contract as measure_recall.run_query, routed through the wrapper."""
    cmd = [sys.executable, str(WRAPPER), question, "--budget", str(budget)]
    if rerank:
        cmd.append("--rerank")
    env = dict(os.environ)
    env["GRAPHIFY_QUERY_LOG_DISABLE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"query_reranked.py failed ({proc.returncode}) for {question!r}: "
            f"{proc.stderr.strip()[:400]}"
        )
    _, metas = qr.parse_node_lines(proc.stdout)
    return mr.dedup_files([{"src": m["src"]} for m in metas])


def measure_via_wrapper(spec: dict, budget: int, rerank: bool) -> dict:
    repo = Path(spec["repo"])
    graph = repo / spec["graph"]

    known = mr.graph_source_files(graph)
    bad = sorted({p for q in spec["queries"] for p in q["expect"] if p not in known})
    if bad:
        raise SystemExit(
            "Ground truth references files that are not in the graph:\n  " + "\n  ".join(bad)
        )

    results = []
    for q in spec["queries"]:
        got = run_query_via_wrapper(repo, q["question"], budget, rerank)
        pos = {f: i + 1 for i, f in enumerate(got)}
        hits = [f for f in q["expect"] if f in pos]
        ranks = sorted(pos[f] for f in hits)
        results.append(
            {
                "id": q["id"],
                "phrasing": q["phrasing"],
                "question": q["question"],
                "expect": q["expect"],
                "returned": len(got),
                "hit": bool(hits),
                "rank": ranks[0] if ranks else None,
                "found": len(hits),
                "expected": len(q["expect"]),
                "missed": [f for f in q["expect"] if f not in pos],
            }
        )

    graph_doc = json.loads(graph.read_text(encoding="utf-8"))
    return {
        "measured_at": mr.now_iso(),
        "set_version": spec["set_version"],
        "budget": budget,
        "rerank": rerank,
        "repo": str(repo),
        "graph_nodes": len(graph_doc["nodes"]),
        "graph_built_at_commit": graph_doc.get("built_at_commit"),
        "overall": mr.aggregate(results),
        "by_phrasing": {
            tag: mr.aggregate([r for r in results if r["phrasing"] == tag])
            for tag in sorted({r["phrasing"] for r in results})
        },
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=2000)
    ap.add_argument("--out-dir", type=Path, default=SCRIPTS_DIR)
    args = ap.parse_args()

    spec = mr.load_spec()

    print("=== baseline: bare graphify (measure_recall.py's own path) ===")
    baseline = mr.measure(spec, args.budget)
    print(f"  overall  {mr.fmt(baseline['overall'])}  ({baseline['overall']['queries']} queries)")
    for tag, a in baseline["by_phrasing"].items():
        print(f"  {tag:<8} {mr.fmt(a)}  ({a['queries']} queries)")

    print("\n=== wrapper passthrough (--rerank omitted) ===")
    passthrough = measure_via_wrapper(spec, args.budget, rerank=False)
    print(f"  overall  {mr.fmt(passthrough['overall'])}  ({passthrough['overall']['queries']} queries)")
    for tag, a in passthrough["by_phrasing"].items():
        print(f"  {tag:<8} {mr.fmt(a)}  ({a['queries']} queries)")

    mismatches = [
        (b["id"], b["rank"], p["rank"])
        for b, p in zip(baseline["results"], passthrough["results"])
        if b["rank"] != p["rank"] or b["hit"] != p["hit"]
    ]
    if mismatches:
        print(f"\n  !! passthrough diverges from bare-graphify baseline on {len(mismatches)} queries:")
        for qid, brank, prank in mismatches:
            print(f"       {qid}: baseline rank={brank}  passthrough rank={prank}")
    else:
        print("\n  passthrough matches bare-graphify baseline exactly (hit + rank, all queries).")

    print("\n=== wrapper reranked (--rerank) ===")
    reranked = measure_via_wrapper(spec, args.budget, rerank=True)
    print(f"  overall  {mr.fmt(reranked['overall'])}  ({reranked['overall']['queries']} queries)")
    for tag, a in reranked["by_phrasing"].items():
        print(f"  {tag:<8} {mr.fmt(a)}  ({a['queries']} queries)")

    print(f"\nvs wrapper passthrough (budget={args.budget}):")
    for scope in ("overall", *passthrough["by_phrasing"]):
        o = passthrough["overall"] if scope == "overall" else passthrough["by_phrasing"].get(scope)
        n = reranked["overall"] if scope == "overall" else reranked["by_phrasing"][scope]
        if not o:
            continue
        for k in ("hit_rate", "file_recall", "mrr"):
            d = n[k] - o[k]
            print(f"  {scope:<8} {k:<11} {o[k]:.3f} -> {n[k]:.3f}  {d:+.3f}")

    regressions = [
        r_row["id"]
        for p_row, r_row in zip(passthrough["results"], reranked["results"])
        if p_row["hit"] and not r_row["hit"]
    ]
    if regressions:
        print(f"\n  !! REGRESSIONS (hit under passthrough, miss under rerank): {regressions}")
    else:
        print("\n  no regressions: every passthrough hit is still a hit under rerank.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (
        ("bench-baseline", baseline),
        ("bench-passthrough", passthrough),
        ("bench-reranked", reranked),
    ):
        out = args.out_dir / f"{name}-budget{args.budget}.json"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    return 1 if (mismatches or regressions) else 0


if __name__ == "__main__":
    sys.exit(main())
