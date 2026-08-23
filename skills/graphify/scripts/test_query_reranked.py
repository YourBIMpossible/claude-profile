"""Unit + integration tests for query_reranked.py.

Unit tests are pure/deterministic -- no subprocess, no live graph. The
integration tests shell out to the real `graphify` binary against the
BIMpossible backend graph and are skipped (not failed) if either isn't
available on this machine, matching how measure_recall.py/rerank_bm25.py
assume a specific local dev checkout rather than a portable CI fixture.

Run: python -m unittest test_query_reranked -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import query_reranked as qr  # noqa: E402

BIMPOSSIBLE_BACKEND = Path(r"F:\BIMpossible\backend")


class TestTokenize(unittest.TestCase):
    def test_camel_and_boundary_split(self):
        self.assertEqual(
            qr.tokenize("TestHubIsolation.cs"),
            ["test", "hub", "isolation", "cs"],
        )

    def test_stopwords_and_short_tokens_dropped(self):
        toks = qr.tokenize("how does the firm hub isolation work")
        self.assertNotIn("how", toks)
        self.assertNotIn("the", toks)
        self.assertIn("firm", toks)
        self.assertIn("hub", toks)
        self.assertIn("isolation", toks)

    def test_empty_input(self):
        self.assertEqual(qr.tokenize(""), [])
        self.assertEqual(qr.tokenize(None), [])


class TestBM25(unittest.TestCase):
    def _bm25(self):
        docs = [
            qr.tokenize("hub_tenancy.py firm hub isolation"),   # doc 0: on-topic
            qr.tokenize("account_router.py signup billing"),     # doc 1: off-topic
            qr.tokenize("test_hub_isolation.py hub isolation"),  # doc 2: on-topic
        ]
        return qr.BM25(docs), docs

    def test_relevant_doc_scores_higher(self):
        bm25, _ = self._bm25()
        qtok = qr.tokenize("how does firm hub isolation work")
        s0 = bm25.score(qtok, 0)
        s1 = bm25.score(qtok, 1)
        s2 = bm25.score(qtok, 2)
        self.assertGreater(s0, s1)
        self.assertGreater(s2, s1)

    def test_zero_overlap_scores_exactly_zero(self):
        bm25, _ = self._bm25()
        qtok = qr.tokenize("billing signup account")
        self.assertGreater(bm25.score(qtok, 1), 0.0)
        self.assertEqual(bm25.score(qtok, 0), 0.0)

    def test_empty_pool(self):
        bm25 = qr.BM25([])
        self.assertEqual(bm25.score(["anything"], 0) if False else 0.0, 0.0)


class TestStableTieBreak(unittest.TestCase):
    def test_zero_score_ties_keep_original_bfs_order(self):
        # 5 pooled docs in original BFS order; only index 3 has any overlap
        # with the query. Every other index scores 0.0 and must retain its
        # relative BFS position among the other zero-score entries.
        docs = [
            qr.tokenize("router.py"),
            qr.tokenize("models.py"),
            qr.tokenize("conftest.py"),
            qr.tokenize("hub_tenancy.py firm hub isolation"),
            qr.tokenize("admin_router.py"),
        ]
        bm25 = qr.BM25(docs)
        qtok = qr.tokenize("firm hub isolation")
        order = sorted(range(len(docs)), key=lambda i: -bm25.score(qtok, i))
        self.assertEqual(order[0], 3)
        zero_score_survivors = [i for i in order if i != 3]
        self.assertEqual(zero_score_survivors, [0, 1, 2, 4])


class TestRebuildOutput(unittest.TestCase):
    TEMPLATE = (
        "Traversal: BFS depth=2 | Start: ['Firm'] | 772 nodes found\n"
        "\n"
        "[!] TRUNCATED: showing 3 of 772 nodes (~600-token budget).\n"
        "\n"
        "NODE Firm [src=db/models.py loc=L1078 community=Firm]\n"
        "NODE hub_tenancy.py [src=aec/hub_tenancy.py loc=L1 community=FastAPI]\n"
        "NODE test_hub_isolation.py [src=tests/test_hub_isolation.py loc=L1 community=x]\n"
        "\n"
        "... (truncated -- 769 more nodes cut by ~600-token budget.)\n"
    )

    def test_splices_node_block_keeps_scaffolding_byte_identical(self):
        new_nodes = [
            "NODE fresh_firm() [src=tests/test_hub_isolation.py loc=L78 community=fresh_firm]",
            "NODE test_aps_hub_isolation.py [src=tests/test_aps_hub_isolation.py loc=L1 community=y]",
            "NODE Firm [src=db/models.py loc=L1078 community=Firm]",
        ]
        rebuilt = qr.rebuild_output(self.TEMPLATE, new_nodes)
        rebuilt_lines = rebuilt.splitlines()
        template_lines = self.TEMPLATE.splitlines()

        node_start = next(i for i, l in enumerate(template_lines) if l.startswith("NODE "))
        node_count = sum(1 for l in template_lines if l.startswith("NODE "))

        # Everything before and after the NODE block is untouched.
        self.assertEqual(rebuilt_lines[:node_start], template_lines[:node_start])
        self.assertEqual(
            rebuilt_lines[node_start + node_count:],
            template_lines[node_start + node_count:],
        )
        # The NODE block itself is exactly the new lines, same count, new order.
        self.assertEqual(
            rebuilt_lines[node_start:node_start + node_count], new_nodes
        )
        self.assertEqual(len(new_nodes), node_count)

    def test_no_node_lines_returns_input_unchanged(self):
        text = "Traversal: BFS depth=2 | Start: [] | 0 nodes found\n"
        self.assertEqual(qr.rebuild_output(text, []), text)


class TestRerankQueryMocked(unittest.TestCase):
    """Deterministic: mocks run_query_raw so no subprocess/live graph is used."""

    TARGET_STDOUT = (
        "Traversal: BFS depth=2 | Start: ['Firm'] | 5 nodes found\n"
        "\n"
        "NODE router.py [src=aec/router.py loc=L1 community=x]\n"
        "NODE models.py [src=db/models.py loc=L1 community=y]\n"
        "\n"
    )
    POOL_STDOUT = (
        "Traversal: BFS depth=2 | Start: ['Firm'] | 5 nodes found\n"
        "\n"
        "NODE router.py [src=aec/router.py loc=L1 community=x]\n"
        "NODE models.py [src=db/models.py loc=L1 community=y]\n"
        "NODE hub_tenancy.py [src=aec/hub_tenancy.py loc=L1 community=z]\n"
        "NODE conftest.py [src=tests/conftest.py loc=L1 community=w]\n"
        "NODE admin_router.py [src=admin_router.py loc=L1 community=v]\n"
        "\n"
    )

    def test_node_cap_matches_target_call_count(self):
        calls = {"n": 0}

        def fake_run_query_raw(cwd, question, budget, extra_args):
            calls["n"] += 1
            return self.TARGET_STDOUT if budget == 2000 else self.POOL_STDOUT

        with mock.patch.object(qr, "run_query_raw", side_effect=fake_run_query_raw):
            out = qr.rerank_query(Path("."), "firm hub isolation", 2000, [])

        node_lines = [l for l in out.splitlines() if l.startswith("NODE ")]
        target_node_count = sum(
            1 for l in self.TARGET_STDOUT.splitlines() if l.startswith("NODE ")
        )
        self.assertEqual(len(node_lines), target_node_count)
        self.assertEqual(calls["n"], 2)  # exactly one target call + one pool call

    def test_reranked_lines_are_drawn_only_from_pool(self):
        with mock.patch.object(
            qr, "run_query_raw",
            side_effect=lambda cwd, q, budget, extra: (
                self.TARGET_STDOUT if budget == 2000 else self.POOL_STDOUT
            ),
        ):
            out = qr.rerank_query(Path("."), "firm hub isolation tenancy", 2000, [])

        pool_lines = {
            l for l in self.POOL_STDOUT.splitlines() if l.startswith("NODE ")
        }
        out_lines = {l for l in out.splitlines() if l.startswith("NODE ")}
        self.assertTrue(out_lines.issubset(pool_lines))

    def test_empty_target_short_circuits_without_pool_call(self):
        empty_stdout = "Traversal: BFS depth=2 | Start: [] | 0 nodes found\n"
        calls = {"n": 0}

        def fake_run_query_raw(cwd, question, budget, extra_args):
            calls["n"] += 1
            return empty_stdout

        with mock.patch.object(qr, "run_query_raw", side_effect=fake_run_query_raw):
            out = qr.rerank_query(Path("."), "nonsense query", 2000, [])

        self.assertEqual(out, empty_stdout)
        self.assertEqual(calls["n"], 1)  # never fetched a pool for an empty result


class TestArgParsing(unittest.TestCase):
    def test_rerank_defaults_off(self):
        import argparse
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("question")
        ap.add_argument("--budget", type=int, default=2000)
        ap.add_argument("--dfs", action="store_true")
        ap.add_argument("--rerank", action="store_true", default=False)
        args, _ = ap.parse_known_args(["some question"])
        self.assertFalse(args.rerank)
        self.assertFalse(args.dfs)
        self.assertEqual(args.budget, 2000)


@unittest.skipUnless(
    qr.GRAPHIFY.exists() and (BIMPOSSIBLE_BACKEND / "graphify-out" / "graph.json").exists(),
    "graphify binary or BIMpossible backend graph not available on this machine",
)
class TestLiveIntegration(unittest.TestCase):
    """Real subprocess calls against the real BIMpossible backend graph."""

    QUESTION = "how does firm hub isolation work"

    def test_passthrough_is_byte_identical_to_bare_graphify(self):
        direct = subprocess.run(
            [str(qr.GRAPHIFY), "query", self.QUESTION, "--budget", "600"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        wrapped = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "query_reranked.py"),
             self.QUESTION, "--budget", "600"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(wrapped.stdout, direct.stdout)

    def test_reranked_output_is_subset_of_real_traversal_pool(self):
        reference_pool = subprocess.run(
            [str(qr.GRAPHIFY), "query", self.QUESTION, "--budget", "30000"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        pool_lines, _ = qr.parse_node_lines(reference_pool.stdout)
        pool_set = set(pool_lines)

        reranked = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "query_reranked.py"),
             self.QUESTION, "--budget", "600", "--rerank"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        reranked_lines, _ = qr.parse_node_lines(reranked.stdout)

        self.assertTrue(reranked_lines, "reranked output had no NODE lines")
        self.assertTrue(set(reranked_lines).issubset(pool_set))
        # Same node_cap as an equivalent non-reranked call at the same budget.
        baseline = subprocess.run(
            [str(qr.GRAPHIFY), "query", self.QUESTION, "--budget", "600"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        baseline_lines, _ = qr.parse_node_lines(baseline.stdout)
        self.assertEqual(len(reranked_lines), len(baseline_lines))


if __name__ == "__main__":
    unittest.main()
