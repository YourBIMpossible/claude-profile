"""Tests for resolve_graph.py against synthetic checkouts (no git binary needed).

Run: python -m unittest test_resolve_graph -v
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_graph as rg  # noqa: E402


def make_graph(root: Path) -> Path:
    g = root / "graphify-out" / "graph.json"
    g.parent.mkdir(parents=True, exist_ok=True)
    g.write_text("{}", encoding="utf-8")
    return g


def make_repo(root: Path) -> Path:
    (root / ".git" / "worktrees").mkdir(parents=True, exist_ok=True)
    return root


def make_worktree(main: Path, wt: Path, name: str) -> Path:
    """Mimic `git worktree add`: <wt>/.git file -> <main>/.git/worktrees/<name>."""
    admin = main / ".git" / "worktrees" / name
    admin.mkdir(parents=True, exist_ok=True)
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    wt.mkdir(parents=True, exist_ok=True)
    (wt / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    return wt


class ResolveGraphTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()

    def test_graph_beside_cwd(self):
        repo = make_repo(self.tmp / "addins")
        g = make_graph(repo)
        r = rg.resolve(repo)
        self.assertEqual((r.graph, r.source, r.local_root), (str(g), "cwd-walk", str(repo)))

    def test_nested_cwd_walks_up_to_repo_graph(self):
        repo = make_repo(self.tmp / "addins")
        g = make_graph(repo)
        deep = repo / "src" / "Tools"
        deep.mkdir(parents=True)
        self.assertEqual(rg.resolve(deep).graph, str(g))

    def test_repo_root_uses_backend_subdir_graph(self):
        repo = make_repo(self.tmp / "web")
        g = make_graph(repo / "backend")
        r = rg.resolve(repo)
        self.assertEqual((r.graph, r.source), (str(g), "repo-subdir"))
        self.assertEqual(r.graph_root, str(repo / "backend"))

    def test_sibling_folder_uses_backend_subdir_graph(self):
        repo = make_repo(self.tmp / "web")
        g = make_graph(repo / "backend")
        (repo / "frontend").mkdir()
        self.assertEqual(rg.resolve(repo / "frontend").graph, str(g))

    def test_worktree_maps_to_main_checkout_graph(self):
        main = make_repo(self.tmp / "web")
        g = make_graph(main / "backend")
        wt = make_worktree(main, main / ".claude" / "worktrees" / "lane", "lane")
        r = rg.resolve(wt)
        self.assertEqual((r.graph, r.source), (str(g), "main-checkout"))
        self.assertEqual(r.graph_root, str(main / "backend"))
        self.assertEqual(r.local_root, str(wt / "backend"))

    def test_worktree_subfolder_maps_through(self):
        main = make_repo(self.tmp / "addins")
        g = make_graph(main)
        wt = make_worktree(main, self.tmp / "elsewhere" / "lane", "lane")
        (wt / "src").mkdir()
        r = rg.resolve(wt / "src")
        self.assertEqual((r.graph, r.local_root), (str(g), str(wt)))

    def test_worktree_own_graph_wins_over_main(self):
        main = make_repo(self.tmp / "addins")
        make_graph(main)
        wt = make_worktree(main, main / ".claude" / "worktrees" / "lane", "lane")
        own = make_graph(wt)
        r = rg.resolve(wt)
        self.assertEqual((r.graph, r.source), (str(own), "cwd-walk"))

    def test_foreign_worktree_inside_graphed_repo_does_not_borrow_its_graph(self):
        repo_a = make_repo(self.tmp / "a")
        make_graph(repo_a / "backend")
        repo_b = make_repo(self.tmp / "b")
        wt = make_worktree(repo_b, repo_a / ".claude" / "worktrees" / "b-lane", "b-lane")
        self.assertIsNone(rg.resolve(wt))

    def test_repo_without_graph_resolves_none(self):
        repo = make_repo(self.tmp / "dashboard")
        (repo / "src").mkdir()
        self.assertIsNone(rg.resolve(repo / "src"))

    def test_worktree_of_graphless_repo_resolves_none(self):
        main = make_repo(self.tmp / "dashboard")
        wt = make_worktree(main, main / ".claude" / "worktrees" / "lane", "lane")
        self.assertIsNone(rg.resolve(wt))

    def test_non_git_folder_walks_limited_levels(self):
        base = self.tmp / "loose"
        g = make_graph(base)
        near = base / "a" / "b"
        near.mkdir(parents=True)
        self.assertEqual(rg.resolve(near).graph, str(g))
        far = base.joinpath(*["x"] * (rg.MAX_NON_GIT_LEVELS + 1))
        far.mkdir(parents=True)
        self.assertIsNone(rg.resolve(far))

    def test_unusual_commondir_is_not_treated_as_main_checkout(self):
        main = make_repo(self.tmp / "web")
        make_graph(main / "backend")
        wt = make_worktree(main, self.tmp / "lane", "lane")
        admin = main / ".git" / "worktrees" / "lane"
        (admin / "commondir").write_text(str(self.tmp / "bare.git"), encoding="utf-8")
        self.assertIsNone(rg.resolve(wt))

    def test_cli_exit_codes(self):
        repo = make_repo(self.tmp / "web")
        self.assertEqual(rg.main(["--cwd", str(repo)]), 1)
        make_graph(repo / "backend")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rg.main(["--cwd", str(repo), "--json"]), 0)
        self.assertIn("repo-subdir", out.getvalue())


if __name__ == "__main__":
    unittest.main()
