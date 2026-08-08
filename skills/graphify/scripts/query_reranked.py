"""Drop-in wrapper for `graphify query` that optionally reranks candidates with BM25.

Productionizes the reranker evaluated in
F:\\AI-Dev\\.tools\\graphify\\recall\\rerank_bm25.py (see RERANK-EXPERIMENT.md):
BM25 k1=1.5 b=0.75 over tokenize(label + source_file), stable sort so a
0.0-vs-0.0 tie keeps graphify's own BFS order, truncated to the same node
count graphify's own budget would have shown. That method is reused here
verbatim -- same constants, same tokenizer, same tie behavior.

Scope, matching the evaluated method exactly:
  - Only BFS queries are reranked. `--dfs` traces a specific path by
    construction; the experiment never touched it, so it always passes
    through untouched here too, regardless of --rerank.
  - Seeding and traversal are never altered. Reranking runs AFTER graphify
    has already built its candidate pool and BEFORE the pool is truncated
    to the caller's token budget -- it only changes which prefix of the
    pool survives truncation and in what order, never what BFS discovers.
  - Default is passthrough. With --rerank omitted (or false), this script
    execs `graphify query` unchanged and streams its stdout/stderr/exit
    code through -- byte-identical to calling graphify directly.

How reranking works: graphify fuses candidate-selection and truncation into
one budget-parameterized call, so there's no internal seam to hook. Instead
(exactly as the experiment did) this makes two calls with the same seeds:
one at the caller's real budget to learn node_cap (how many NODE lines
graphify itself would show) and the exact template text to reuse verbatim,
and one at a much larger pool budget to get the (near-)full candidate pool
in original BFS order. Every pooled node is BM25-scored against the
question, stable-sorted, and the top node_cap raw NODE lines splice into
the template in place of the original block -- header, truncation notice,
and footer stay byte-identical to what graphify itself produced for that
query and budget, since only the node membership/order within the cap can
change, never the surrounding scaffolding or the reported counts.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
from pathlib import Path

GRAPHIFY = Path(
    r"C:\Users\Zeria\AppData\Local\Python\pythoncore-3.14-64\Scripts\graphify.exe"
)

# Same ladder as rerank_bm25.py: ask for a lot, retry bigger only if still
# truncated. Total pool size varies wildly by query (hundreds to thousands
# of nodes at BFS depth=2), so this is more robust than estimating a
# tokens-per-node ratio.
POOL_BUDGET = 30000
POOL_RETRY_BUDGET = 150000

NODE_RE = re.compile(r"^NODE\s+(?P<label>.*?)\s+\[src=(?P<src>[^\s\]]+)")

_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "for", "and", "or", "that", "this", "these", "those",
    "it", "its", "with", "as", "at", "by", "from", "how", "what", "when",
    "where", "which", "who", "whom", "does", "do", "did", "every", "each",
    "into", "out", "up", "down", "not", "no", "so", "than", "then", "there",
    "here", "if", "just",
}
_BOUNDARY = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    toks = []
    for part in _BOUNDARY.split(text):
        if not part:
            continue
        for sub in _CAMEL.split(part):
            if sub:
                toks.append(sub.lower())
    return [t for t in toks if len(t) > 1 and t not in _STOP]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.n = len(docs)
        self.k1 = k1
        self.b = b
        self.doc_len = [len(d) for d in docs]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf = [{} for _ in docs]
        df: dict[str, int] = {}
        for i, d in enumerate(docs):
            for t in d:
                self.tf[i][t] = self.tf[i].get(t, 0) + 1
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        self.idf = {
            t: math.log((self.n - n + 0.5) / (n + 0.5) + 1) for t, n in df.items()
        }

    def score(self, query_tokens: list[str], i: int) -> float:
        tf = self.tf[i]
        if not tf:
            return 0.0
        dl = self.doc_len[i]
        s = 0.0
        for t in query_tokens:
            f = tf.get(t)
            if not f:
                continue
            idf = self.idf.get(t, 0.0)
            s += idf * (f * (self.k1 + 1)) / (
                f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            )
        return s


def run_query_raw(cwd: Path, question: str, budget: int, extra_args: list[str]) -> str:
    """Run `graphify query` and return its raw stdout. Raises on nonzero exit."""
    env = dict(os.environ)
    env["GRAPHIFY_QUERY_LOG_DISABLE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.run(
        [str(GRAPHIFY), "query", question, "--budget", str(budget), *extra_args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"graphify query failed ({proc.returncode}) for {question!r}: "
            f"{proc.stderr.strip()[:400]}"
        )
    return proc.stdout


def parse_node_lines(stdout: str) -> tuple[list[str], list[dict]]:
    """Return (raw NODE lines in order, [{'label','src'} per line])."""
    lines, metas = [], []
    for line in stdout.splitlines():
        stripped = line.strip()
        m = NODE_RE.match(stripped)
        if m:
            lines.append(stripped)
            metas.append({"label": m.group("label"), "src": m.group("src")})
    return lines, metas


def fetch_pool(cwd: Path, question: str, extra_args: list[str]) -> str:
    stdout = run_query_raw(cwd, question, POOL_BUDGET, extra_args)
    if "TRUNCATED" in stdout:
        stdout = run_query_raw(cwd, question, POOL_RETRY_BUDGET, extra_args)
    return stdout


def rebuild_output(target_stdout: str, reranked_node_lines: list[str]) -> str:
    """Splice a new NODE-line block into target_stdout's own template.

    Every other line (header, blank lines, truncation notice, footer) is
    left byte-identical to what graphify itself produced for this exact
    query and budget -- only the contiguous NODE-line run is replaced.
    """
    lines = target_stdout.splitlines()
    first_i = next((i for i, l in enumerate(lines) if l.startswith("NODE ")), None)
    if first_i is None:
        return target_stdout
    last_i = first_i
    for i in range(first_i, len(lines)):
        if lines[i].startswith("NODE "):
            last_i = i
        elif lines[i].strip() == "":
            break
    new_lines = lines[:first_i] + reranked_node_lines + lines[last_i + 1:]
    text = "\n".join(new_lines)
    return text + "\n" if target_stdout.endswith("\n") else text


def rerank_query(cwd: Path, question: str, budget: int, extra_args: list[str]) -> str:
    target_stdout = run_query_raw(cwd, question, budget, extra_args)
    node_lines, _ = parse_node_lines(target_stdout)
    node_cap = len(node_lines)
    if node_cap == 0:
        return target_stdout

    pool_stdout = fetch_pool(cwd, question, extra_args)
    pool_lines, pool_metas = parse_node_lines(pool_stdout)

    docs = [tokenize(f"{m['label']} {m['src']}") for m in pool_metas]
    bm25 = BM25(docs)
    qtok = tokenize(question)
    order = sorted(range(len(pool_lines)), key=lambda i: -bm25.score(qtok, i))
    top_lines = [pool_lines[i] for i in order[:node_cap]]

    return rebuild_output(target_stdout, top_lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("question")
    ap.add_argument("--budget", type=int, default=2000)
    ap.add_argument("--dfs", action="store_true")
    ap.add_argument("--context")
    ap.add_argument("--graph")
    ap.add_argument("--rerank", action="store_true", default=False)
    args, unknown = ap.parse_known_args(argv)

    extra_args = list(unknown)
    if args.context is not None:
        extra_args += ["--context", args.context]
    if args.graph is not None:
        extra_args += ["--graph", args.graph]
    if args.dfs:
        extra_args.append("--dfs")

    cwd = Path.cwd()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.dfs or not args.rerank:
        proc = subprocess.run(
            [str(GRAPHIFY), "query", args.question, "--budget", str(args.budget), *extra_args],
            cwd=str(cwd),
        )
        return proc.returncode

    try:
        sys.stdout.write(rerank_query(cwd, args.question, args.budget, extra_args))
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
