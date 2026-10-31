"""Unit + integration tests for query_reranked.py.

Unit tests are pure/deterministic -- no subprocess, no live graph. The
integration tests shell out to the real `graphify` binary against the
BIMpossible backend graph and are skipped (not failed) if either isn't
available on this machine, matching how measure_recall.py/rerank_bm25.py
assume a specific local dev checkout rather than a portable CI fixture.

Run: python -m unittest test_query_reranked -v
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import query_reranked as qr  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))  # the profile clone's tools/
import machine_config  # noqa: E402

BIMPOSSIBLE_BACKEND = Path(machine_config.load()["repos_root"]) / "BIMpossible" / "backend"


HAVE_GRAPHIFY = importlib.util.find_spec("graphify") is not None


def _graphify_stub() -> dict:
    """Stand-ins for the two `graphify` modules the wrapper imports, for an environment without the graphify package (CI
    installs only pytest): `graphify.paths` (one constant, the value the real package defines, checked below) and
    `graphify.querylog` (log_query records its calls on `.calls` instead of writing a log)."""
    pkg = types.ModuleType("graphify")
    pkg.__path__ = []
    paths = types.ModuleType("graphify.paths")
    paths.GRAPHIFY_OUT = "graphify-out"
    querylog = types.ModuleType("graphify.querylog")
    querylog.calls = []
    querylog.log_query = lambda **kwargs: querylog.calls.append(kwargs)
    pkg.paths, pkg.querylog = paths, querylog
    return {"graphify": pkg, "graphify.paths": paths, "graphify.querylog": querylog}


class WithoutGraphifyPackage(unittest.TestCase):
    """Tests of the wrapper's own logic must not need the graphify package: with it absent, the two modules are stubbed;
    with it present, the real ones are used."""

    def setUp(self):
        super().setUp()
        if not HAVE_GRAPHIFY:
            patcher = mock.patch.dict(sys.modules, _graphify_stub())
            patcher.start()
            self.addCleanup(patcher.stop)


class TestGraphifyStub(unittest.TestCase):
    @unittest.skipUnless(HAVE_GRAPHIFY, "the graphify package is not installed here")
    def test_the_stub_constant_is_the_real_one(self):
        from graphify.paths import GRAPHIFY_OUT
        self.assertEqual(_graphify_stub()["graphify.paths"].GRAPHIFY_OUT, GRAPHIFY_OUT)

    @unittest.skipUnless(HAVE_GRAPHIFY, "the graphify package is not installed here")
    def test_the_stub_log_query_takes_the_keywords_the_real_one_takes(self):
        import inspect
        from graphify import querylog
        real = inspect.signature(querylog.log_query)
        wanted = {"kind", "question", "corpus", "result", "mode", "depth", "token_budget", "duration_ms"}
        self.assertTrue(wanted <= set(real.parameters) or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in real.parameters.values()), real)


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

        node_lines = [l for l in out.text.splitlines() if l.startswith("NODE ")]
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
        out_lines = {l for l in out.text.splitlines() if l.startswith("NODE ")}
        self.assertTrue(out_lines.issubset(pool_lines))

    def test_empty_target_short_circuits_without_pool_call(self):
        empty_stdout = "Traversal: BFS depth=2 | Start: [] | 0 nodes found\n"
        calls = {"n": 0}

        def fake_run_query_raw(cwd, question, budget, extra_args):
            calls["n"] += 1
            return empty_stdout

        with mock.patch.object(qr, "run_query_raw", side_effect=fake_run_query_raw):
            out = qr.rerank_query(Path("."), "nonsense query", 2000, [])

        self.assertEqual(out.text, empty_stdout)
        self.assertEqual(out.rerank_ms, 0.0)
        self.assertEqual(calls["n"], 1)  # never fetched a pool for an empty result


class TestArgParsing(unittest.TestCase):
    def _parse(self, argv, gate=""):
        with mock.patch.dict(os.environ, {"GRAPHIFY_RERANK": gate}):
            return qr.parse_args(argv)[0]

    def test_gate_off_defaults_to_passthrough(self):
        args = self._parse(["some question"])
        self.assertFalse(args.rerank)
        self.assertFalse(args.dfs)
        self.assertEqual(args.budget, 2000)

    def test_env_gate_turns_rerank_on(self):
        self.assertTrue(self._parse(["q"], gate="1").rerank)

    def test_no_rerank_overrides_env_gate(self):
        self.assertFalse(self._parse(["q", "--no-rerank"], gate="1").rerank)

    def test_rerank_flag_overrides_gate_off(self):
        self.assertTrue(self._parse(["q", "--rerank"], gate="").rerank)

    def test_unknown_args_pass_through(self):
        _, unknown = qr.parse_args(["q", "--some-future-flag", "v"])
        self.assertEqual(unknown, ["--some-future-flag", "v"])


class TestGraphResolution(WithoutGraphifyPackage):
    def _args(self, graph=None):
        return mock.Mock(graph=graph)

    def test_explicit_graph_is_never_replaced(self):
        args = self._args("custom/graph.json")
        with mock.patch.object(qr.resolve_graph, "resolve") as res:
            qr._resolve_graph_arg(args, Path(tempfile.gettempdir()))
        res.assert_not_called()
        self.assertEqual(args.graph, "custom/graph.json")

    def test_cwd_graph_wins_without_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "graphify-out").mkdir()
            (Path(td) / "graphify-out" / "graph.json").write_text("{}")
            args = self._args()
            with mock.patch.object(qr.resolve_graph, "resolve") as res:
                qr._resolve_graph_arg(args, Path(td))
            res.assert_not_called()
            self.assertIsNone(args.graph)

    def test_resolved_graph_fills_missing_arg(self):
        found = qr.resolve_graph.ResolvedGraph("X:/r/backend/graphify-out/graph.json",
                                               "X:/r/backend", "repo-subdir", "X:/r/backend")
        with tempfile.TemporaryDirectory() as td:
            args = self._args()
            with mock.patch.object(qr.resolve_graph, "resolve", return_value=found):
                qr._resolve_graph_arg(args, Path(td))
            self.assertEqual(args.graph, found.graph)

    def test_no_graph_anywhere_leaves_arg_unset(self):
        with tempfile.TemporaryDirectory() as td:
            args = self._args()
            with mock.patch.object(qr.resolve_graph, "resolve", return_value=None):
                qr._resolve_graph_arg(args, Path(td))
            self.assertIsNone(args.graph)


class TestPassthroughTelemetry(WithoutGraphifyPackage):
    """Passthrough queries log exactly one wrapper record stamped rerank=false."""

    STDOUT = b"Traversal: BFS | 1 nodes found\n\nNODE Foo [src=a.py loc=L1]\n"

    def _run(self, rc, argv):
        completed = subprocess.CompletedProcess([], rc, stdout=self.STDOUT, stderr=b"")
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "q.log"
            graph = Path(td) / "graphify-out" / "graph.json"
            graph.parent.mkdir()
            graph.write_text("{}")
            env = {"GRAPHIFY_QUERY_LOG": str(log), "GRAPHIFY_RERANK": "",
                   "GRAPHIFY_QUERY_LOG_DISABLE": ""}
            with (
                mock.patch.dict(os.environ, env),
                mock.patch.object(qr, "GRAPHIFY", Path(td) / "graphify.exe"),
                mock.patch.object(qr.subprocess, "run", return_value=completed) as run,
                mock.patch.object(qr.Path, "cwd", return_value=Path(td)),
                mock.patch.object(sys, "stdout", io.TextIOWrapper(io.BytesIO())),
                mock.patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO())),
            ):
                code = qr.main(argv)
            records = ([json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
                       if log.exists() else [])
            return code, records, run

    @unittest.skipUnless(HAVE_GRAPHIFY, "reads the record the graphify package's querylog writes")
    def test_success_logs_one_rerank_false_record(self):
        code, records, run = self._run(0, ["find foo"])
        self.assertEqual(code, 0)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertIs(rec["rerank"], False)
        self.assertEqual(rec["via"], "wrapper")
        self.assertEqual(rec["kind"], "query")
        self.assertEqual(rec["mode"], "bfs")
        self.assertEqual(rec["nodes_returned"], 1)
        self.assertEqual(run.call_args.kwargs["env"]["GRAPHIFY_QUERY_LOG_DISABLE"], "1")

    @unittest.skipUnless(HAVE_GRAPHIFY, "reads the record the graphify package's querylog writes")
    def test_dfs_logs_mode_dfs_even_with_gate_forced_on(self):
        code, records, _ = self._run(0, ["find foo", "--dfs", "--rerank"])
        self.assertEqual(code, 0)
        self.assertEqual(records[0]["mode"], "dfs")
        self.assertIs(records[0]["rerank"], False)

    def test_failed_query_logs_nothing(self):
        # rc 3, not 2: 2 is the wrapper's own graphify-not-found exit.
        code, records, _ = self._run(3, ["find foo"])
        self.assertEqual(code, 3)
        self.assertEqual(records, [])


class TestPassthroughTelemetryContract(unittest.TestCase):
    """The wrapper's side of the logging contract, against a stubbed querylog (so it runs without the graphify package):
    exactly one record per query, stamped rerank=false, the mode and question passed through, none for a failed query."""

    STDOUT = b"Traversal: BFS | 1 nodes found\n\nNODE Foo [src=a.py loc=L1]\n"

    def _run(self, rc, argv):
        stub = _graphify_stub()
        completed = subprocess.CompletedProcess([], rc, stdout=self.STDOUT, stderr=b"")
        with tempfile.TemporaryDirectory() as td:
            graph = Path(td) / "graphify-out" / "graph.json"
            graph.parent.mkdir()
            graph.write_text("{}")
            env = {"GRAPHIFY_RERANK": "", "GRAPHIFY_QUERY_LOG_DISABLE": ""}
            with (
                mock.patch.dict(sys.modules, stub),
                mock.patch.dict(os.environ, env),
                mock.patch.object(qr, "GRAPHIFY", Path(td) / "graphify.exe"),
                mock.patch.object(qr.subprocess, "run", return_value=completed),
                mock.patch.object(qr.Path, "cwd", return_value=Path(td)),
                mock.patch.object(sys, "stdout", io.TextIOWrapper(io.BytesIO())),
                mock.patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO())),
            ):
                code = qr.main(argv)
            return code, list(stub["graphify.querylog"].calls)

    def test_success_logs_one_rerank_false_record(self):
        code, calls = self._run(0, ["find foo"])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertIs(call["rerank"], False)
        self.assertEqual((call["via"], call["kind"], call["mode"], call["question"]), ("wrapper", "query", "bfs", "find foo"))
        self.assertTrue(call["corpus"].endswith("graph.json"))

    def test_dfs_logs_mode_dfs_even_with_gate_forced_on(self):
        code, calls = self._run(0, ["find foo", "--dfs", "--rerank"])
        self.assertEqual(code, 0)
        self.assertEqual((calls[0]["mode"], calls[0]["rerank"]), ("dfs", False))

    def test_failed_query_logs_nothing(self):
        code, calls = self._run(3, ["find foo"])
        self.assertEqual(code, 3)
        self.assertEqual(calls, [])


class TestMissingBinary(unittest.TestCase):
    """A missing graphify binary gives one clear stderr line and exit 2, never a traceback."""

    SCRIPT = Path(__file__).resolve().parent / "query_reranked.py"

    def _run_script(self, mode_flag: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no" / "graphify.exe"
            env = dict(os.environ, GRAPHIFY_EXE=str(missing), GRAPHIFY_QUERY_LOG_DISABLE="1")
            return subprocess.run(
                [sys.executable, str(self.SCRIPT), "find foo", mode_flag],
                cwd=td, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )

    def _assert_clear_failure(self, rc: int, stderr: str) -> None:
        self.assertEqual(rc, 2, stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("graphify not found (GRAPHIFY_EXE=", stderr)

    def test_missing_graphify_exe_rerank(self):
        proc = self._run_script("--rerank")
        self._assert_clear_failure(proc.returncode, proc.stderr)

    def test_bare_graphify_exe_with_a_file_of_that_name_in_the_folder_is_the_plain_not_found(self):
        # #65 review N1, end to end: GRAPHIFY_EXE=graphify, graphify not on PATH, and a file called graphify in the
        # current folder is the plain not-found line, never "resolved graphify no longer exists".
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as empty_path_dir:
            (Path(td) / "graphify").write_text("")
            env = dict(os.environ, GRAPHIFY_EXE="graphify", PATH=empty_path_dir, GRAPHIFY_QUERY_LOG_DISABLE="1")
            proc = subprocess.run(
                [sys.executable, str(self.SCRIPT), "find foo", "--no-rerank"],
                cwd=td, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
        self._assert_clear_failure(proc.returncode, proc.stderr)
        self.assertIn("graphify not found (GRAPHIFY_EXE=graphify", proc.stderr)
        self.assertNotIn("no longer exists", proc.stderr)

    def test_missing_graphify_exe_no_rerank(self):
        proc = self._run_script("--no-rerank")
        self._assert_clear_failure(proc.returncode, proc.stderr)

    def _main_with_vanishing_binary(self, argv: list[str]) -> tuple[int, str, Path]:
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            vanished = Path(td) / "graphify.exe"
        with (
            mock.patch.dict(os.environ, {"GRAPHIFY_EXE": ""}),
            mock.patch.object(qr, "GRAPHIFY", vanished),
            mock.patch.object(qr, "_resolve_graph_arg"),
            mock.patch.object(qr.subprocess, "run", side_effect=FileNotFoundError(2, "gone")),
            mock.patch.object(sys, "stdout", io.TextIOWrapper(io.BytesIO())),
            mock.patch.object(sys, "stderr", err),
        ):
            code = qr.main(argv)
        return code, err.getvalue(), vanished

    def test_binary_vanishing_mid_run_rerank(self):
        code, stderr, vanished = self._main_with_vanishing_binary(["find foo", "--rerank"])
        self._assert_clear_failure(code, stderr)
        self.assertIn(str(vanished), stderr)

    def test_binary_vanishing_mid_run_no_rerank(self):
        code, stderr, vanished = self._main_with_vanishing_binary(["find foo", "--no-rerank"])
        self._assert_clear_failure(code, stderr)
        self.assertIn(str(vanished), stderr)


class TestGraphifyExeResolution(unittest.TestCase):
    """GRAPHIFY_EXE accepts a file path, a bare command name on PATH, or (Windows) a
    path without .exe -- resolved like a shell would, never only as a literal file."""

    def _fake_bin_dir(self, td: str) -> Path:
        bin_dir = Path(td) / "bin"
        bin_dir.mkdir()
        for name in ("graphify", "graphify.exe"):
            fake = bin_dir / name
            fake.write_text("")
            fake.chmod(0o755)
        return bin_dir

    def test_bare_name_resolves_through_path(self):
        with tempfile.TemporaryDirectory() as td:
            bin_dir = self._fake_bin_dir(td)
            with mock.patch.dict(os.environ, {"GRAPHIFY_EXE": "graphify", "PATH": str(bin_dir)}):
                found = qr._find_graphify()
            self.assertIsNotNone(found)
            self.assertEqual(found.parent.resolve(), bin_dir.resolve())

    @unittest.skipUnless(os.name == "nt",
                         "PATHEXT completion of an extensionless path is Windows-only")
    def test_extensionless_path_resolves_with_pathext(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "graphify.exe"
            exe.write_text("")
            with mock.patch.dict(os.environ, {"GRAPHIFY_EXE": str(Path(td) / "graphify")}):
                found = qr._find_graphify()
            self.assertIsNotNone(found)
            self.assertEqual(found.resolve(), exe.resolve())

    def test_missing_override_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no" / "graphify.exe"
            with mock.patch.dict(os.environ, {"GRAPHIFY_EXE": str(missing)}):
                self.assertIsNone(qr._find_graphify())

    def _in_folder_with_a_graphify_file(self, override: str, path_dir: str):
        """_find_graphify() for GRAPHIFY_EXE=override, run from a folder that holds a file named `graphify`."""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "graphify").write_text("")
            before = os.getcwd()
            os.chdir(td)
            try:
                with mock.patch.dict(os.environ, {"GRAPHIFY_EXE": override, "PATH": path_dir}):
                    return qr._find_graphify()
            finally:
                os.chdir(before)

    def test_a_bare_name_is_not_taken_as_a_file_in_the_current_folder(self):
        # #65 review N1: `GRAPHIFY_EXE=graphify` names a command, resolved like a shell would (PATH). A file of that
        # name in the current folder is not it; accepting it gave a relative path that "vanished" on the next cd.
        with tempfile.TemporaryDirectory() as empty_path_dir:
            self.assertIsNone(self._in_folder_with_a_graphify_file("graphify", empty_path_dir))

    def test_a_bare_name_still_resolves_on_path_when_a_file_of_that_name_is_in_the_folder(self):
        with tempfile.TemporaryDirectory() as td:
            bin_dir = self._fake_bin_dir(td)
            found = self._in_folder_with_a_graphify_file("graphify", str(bin_dir))
            self.assertIsNotNone(found)
            self.assertEqual(found.parent.resolve(), bin_dir.resolve())

    def test_a_relative_path_with_a_separator_is_still_a_file_check(self):
        # either spelling of the separator: the native one, and the forward slash Windows accepts too
        for spelling in (os.path.join(".", "graphify"), "./graphify"):
            with self.subTest(spelling=spelling):
                found = self._in_folder_with_a_graphify_file(spelling, "")
                self.assertIsNotNone(found)
                self.assertTrue(found.name.startswith("graphify"))


@unittest.skipUnless(
    qr.GRAPHIFY is not None and qr.GRAPHIFY.exists()
    and (BIMPOSSIBLE_BACKEND / "graphify-out" / "graph.json").exists(),
    "graphify binary or BIMpossible backend graph not available on this machine",
)
class TestLiveIntegration(unittest.TestCase):
    """Real subprocess calls against the real BIMpossible backend graph.

    Query logging is forced off for every child so a test run never writes
    records into the real query log.
    """

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GRAPHIFY_QUERY_LOG_DISABLE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    QUESTION = "how does firm hub isolation work"

    def test_passthrough_is_byte_identical_to_bare_graphify(self):
        direct = subprocess.run(
            [str(qr.GRAPHIFY), "query", self.QUESTION, "--budget", "600"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        wrapped = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "query_reranked.py"),
             self.QUESTION, "--budget", "600", "--no-rerank"],
            cwd=str(BIMPOSSIBLE_BACKEND),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(wrapped.stdout, direct.stdout)

    def test_reranked_output_is_subset_of_real_traversal_pool(self):
        reference_pool = subprocess.run(
            # POOL_RETRY_BUDGET, not POOL_BUDGET: at 30000 this query's pool is
            # TRUNCATED and the wrapper retries bigger, so a 30000 reference
            # would miss nodes the wrapper legitimately draws from.
            [str(qr.GRAPHIFY), "query", self.QUESTION, "--budget", str(qr.POOL_RETRY_BUDGET)],
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
