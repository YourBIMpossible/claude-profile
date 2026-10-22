"""Drop-in wrapper for `graphify query` that optionally reranks candidates with BM25.

Productionizes the reranker evaluated in
{tools}/graphify/recall/rerank_bm25.py (see RERANK-EXPERIMENT.md):
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
  - The rerank gate is GRAPHIFY_RERANK=1 in the environment; --rerank /
    --no-rerank override it for one call. Gate off (or --dfs) is passthrough:
    `graphify query` runs unchanged and its stdout/stderr/exit code are
    relayed byte-identically.

Graph resolution: when --graph is omitted and `<cwd>/graphify-out/graph.json`
does not exist, resolve_graph.resolve() finds the canonical graph (repo
subfolder, parent, or the main checkout behind a worktree) and passes it as
--graph. Without that, graphify errors from any folder but the graph's own.

Telemetry: every SUCCESSFUL query run through this wrapper writes exactly ONE
query-log record, stamped rerank=true or rerank=false, with via="wrapper" and
wall-clock duration_ms. A failed query (passthrough exit code != 0, or a rerank
RuntimeError) writes none, matching stock graphify, so the log counts successes
only and cannot be used as a failure count. Child `graphify query` calls always
run with logging disabled.
A bare `graphify query` (not through this wrapper) is logged by the package
itself with no rerank field; readers treat a missing field as stock/false.

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
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_graph  # noqa: E402

def _find_graphify() -> Path:
    """GRAPHIFY_EXE overrides; else `graphify` on PATH; else the graphify script installed
    beside this interpreter (scripts dir, then the per-user scripts dir)."""
    if os.environ.get("GRAPHIFY_EXE"):
        return Path(os.environ["GRAPHIFY_EXE"])
    found = shutil.which("graphify")
    if found:
        return Path(found)
    for scheme in (None, f"{os.name}_user"):
        try:
            scripts = Path(sysconfig.get_path("scripts", scheme) if scheme else sysconfig.get_path("scripts"))
        except KeyError:
            continue
        for name in ("graphify.exe", "graphify"):
            if (scripts / name).is_file():
                return scripts / name
    return Path("graphify")


GRAPHIFY = _find_graphify()

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


class RerankResult(NamedTuple):
    """Reranked output plus the two real phases measured around it.

    retrieval_ms times the `graphify query` subprocess calls (target budget +
    candidate pool, including any TRUNCATED retry); rerank_ms times the in-process
    BM25 scoring and output splice. Both are genuine wall-clock spans around real
    work -- never fabricated. The caller adds the end-to-end duration_ms.
    """

    text: str
    retrieval_ms: float
    rerank_ms: float


def rerank_query(cwd: Path, question: str, budget: int, extra_args: list[str]) -> RerankResult:
    _r0 = time.perf_counter()
    target_stdout = run_query_raw(cwd, question, budget, extra_args)
    node_lines, _ = parse_node_lines(target_stdout)
    node_cap = len(node_lines)
    if node_cap == 0:
        # Nothing to rerank: retrieval ran, scoring did not.
        return RerankResult(target_stdout, (time.perf_counter() - _r0) * 1000, 0.0)

    pool_stdout = fetch_pool(cwd, question, extra_args)
    retrieval_ms = (time.perf_counter() - _r0) * 1000

    _s0 = time.perf_counter()
    pool_lines, pool_metas = parse_node_lines(pool_stdout)
    docs = [tokenize(f"{m['label']} {m['src']}") for m in pool_metas]
    bm25 = BM25(docs)
    qtok = tokenize(question)
    order = sorted(range(len(pool_lines)), key=lambda i: -bm25.score(qtok, i))
    top_lines = [pool_lines[i] for i in order[:node_cap]]
    text = rebuild_output(target_stdout, top_lines)
    rerank_ms = (time.perf_counter() - _s0) * 1000

    return RerankResult(text, retrieval_ms, rerank_ms)


_TELEMETRY_WARNED: set[str] = set()


def _warn_telemetry_failure(exc: BaseException) -> None:
    """Surface a reranked-telemetry write failure without breaking the query.

    The user's result has already been written to stdout by the time this runs,
    so a logging failure must never change the exit path -- but it must not be
    swallowed silently either (a signature drift in querylog would otherwise drop
    the entire rerank arm invisibly). Emits ONE concise stderr line per unique
    exception signature per process, so a persistent failure can't flood an
    interactive session. Never calls back into querylog (no recursive telemetry)
    and never prints result content, the corpus, or secrets -- only the exception
    type and message.
    """
    sig = f"{type(exc).__name__}: {exc}"
    if sig in _TELEMETRY_WARNED:
        return
    _TELEMETRY_WARNED.add(sig)
    print(
        f"[graphify] reranked-query telemetry was not recorded:\n{sig}",
        file=sys.stderr,
    )


def _log_wrapped(
    args: argparse.Namespace,
    cwd: Path,
    result: str,
    *,
    reranked: bool,
    duration_ms: float,
    retrieval_ms: float | None = None,
    rerank_ms: float | None = None,
) -> None:
    """Emit exactly ONE query-log record for a query run through this wrapper.

    The wrapper's own `graphify query` children all run with
    GRAPHIFY_QUERY_LOG_DISABLE=1 (see run_query_raw / _passthrough), so the
    package never logs them -- this call is the sole record for the query,
    stamped rerank=True or rerank=False. (A bare `graphify query` outside the
    wrapper is logged by the package with no rerank field at all.)

    corpus is resolved exactly as the stock CLI does (cli.py query path):
    explicit --graph wins, else the GRAPHIFY_OUT-based default, anchored to the
    same cwd the children ran in and .resolve()d -- so stock and reranked records
    for one graph share an identical corpus key and never split into two groups.

    Timing fields are NOT claimed equivalent to the package's traversal-only
    duration_ms. duration_ms here is total wrapper wall time for both arms
    (so rerank=true and rerank=false wrapper records compare like for like);
    retrieval_ms and rerank_ms are the measured reranked sub-phases (see
    RerankResult). Logging is
    non-fatal: on any failure the query result still stands and a single
    rate-limited stderr warning is emitted instead of a silent drop.
    """
    try:
        from graphify import querylog
        from graphify.paths import GRAPHIFY_OUT

        graph_path = args.graph or str(Path(GRAPHIFY_OUT) / "graph.json")
        gp = Path(graph_path)
        if not gp.is_absolute():
            gp = cwd / gp
        corpus = str(gp.resolve())

        extra: dict[str, object] = {"rerank": reranked, "via": "wrapper"}
        if retrieval_ms is not None:
            extra["retrieval_ms"] = round(retrieval_ms, 3)
        if rerank_ms is not None:
            extra["rerank_ms"] = round(rerank_ms, 3)

        querylog.log_query(
            kind="query",
            question=args.question,
            corpus=corpus,
            result=result,
            mode="dfs" if args.dfs else "bfs",
            depth=2,
            token_budget=args.budget,
            duration_ms=duration_ms,
            **extra,
        )
    except Exception as exc:
        _warn_telemetry_failure(exc)


def rerank_gate_on() -> bool:
    return os.environ.get("GRAPHIFY_RERANK", "").strip().lower() in ("1", "true", "yes")


def _resolve_graph_arg(args: argparse.Namespace, cwd: Path) -> None:
    """Fill args.graph from resolve_graph when graphify's cwd default is absent."""
    if args.graph is not None:
        return
    from graphify.paths import GRAPHIFY_OUT

    if (cwd / GRAPHIFY_OUT / "graph.json").is_file():
        return
    found = resolve_graph.resolve(cwd)
    if found is not None:
        args.graph = found.graph


def _passthrough(cmd: list[str], cwd: Path) -> tuple[int, bytes]:
    """Run stock `graphify query`, relay its output byte-identically, return (rc, stdout)."""
    env = dict(os.environ)
    env["GRAPHIFY_QUERY_LOG_DISABLE"] = "1"
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True)
    sys.stdout.flush()
    sys.stdout.buffer.write(proc.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.flush()
    sys.stderr.buffer.write(proc.stderr)
    sys.stderr.buffer.flush()
    return proc.returncode, proc.stdout


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse wrapper args; unknown args pass through to `graphify query`.

    args.rerank is resolved to a bool: --rerank / --no-rerank win, otherwise
    the GRAPHIFY_RERANK environment gate decides.
    """
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("question")
    ap.add_argument("--budget", type=int, default=2000)
    ap.add_argument("--dfs", action="store_true")
    ap.add_argument("--context")
    ap.add_argument("--graph")
    gate = ap.add_mutually_exclusive_group()
    gate.add_argument("--rerank", dest="rerank", action="store_true", default=None)
    gate.add_argument("--no-rerank", dest="rerank", action="store_false")
    ap.set_defaults(rerank=None)
    args, unknown = ap.parse_known_args(argv)
    if args.rerank is None:
        args.rerank = rerank_gate_on()
    return args, unknown


def main(argv: list[str] | None = None) -> int:
    args, unknown = parse_args(argv)
    cwd = Path.cwd()
    _resolve_graph_arg(args, cwd)

    extra_args = list(unknown)
    if args.context is not None:
        extra_args += ["--context", args.context]
    if args.graph is not None:
        extra_args += ["--graph", args.graph]
    if args.dfs:
        extra_args.append("--dfs")

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.dfs or not args.rerank:
        _t0 = time.perf_counter()
        rc, out = _passthrough(
            [str(GRAPHIFY), "query", args.question, "--budget", str(args.budget), *extra_args],
            cwd,
        )
        if rc == 0:
            _log_wrapped(
                args,
                cwd,
                out.decode("utf-8", errors="replace"),
                reranked=False,
                duration_ms=(time.perf_counter() - _t0) * 1000,
            )
        return rc

    try:
        _t0 = time.perf_counter()
        rr = rerank_query(cwd, args.question, args.budget, extra_args)
        sys.stdout.write(rr.text)
        _log_wrapped(
            args,
            cwd,
            rr.text,
            reranked=True,
            duration_ms=(time.perf_counter() - _t0) * 1000,
            retrieval_ms=rr.retrieval_ms,
            rerank_ms=rr.rerank_ms,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
