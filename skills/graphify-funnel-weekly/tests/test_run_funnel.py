"""Fixture-only tests for the graphify-funnel-weekly wrapper.

Needs the real graphify_funnel.py: set GRAPHIFY_FUNNEL_SCRIPT, else these tests skip.
All inputs (--projects, --log, --config, stub resolver) are synthetic, under tmp_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "run_funnel.py"
SCRIPT = os.environ.get("GRAPHIFY_FUNNEL_SCRIPT")
pytestmark = pytest.mark.skipif(not SCRIPT or not Path(SCRIPT).is_file(),
                                reason="GRAPHIFY_FUNNEL_SCRIPT not set to graphify_funnel.py")
UNTIL = "2026-10-10T12:00:00+00:00"

STUB_RESOLVER = textwrap.dedent('''
    """SYNTHETIC resolver stub: a graph exists only under <ROOT>/repo/backend."""
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent

    def _git_toplevel(start):
        for d in (start, *start.parents):
            if (d / ".git").exists():
                return d
        return None

    def _main_checkout(top):
        return None

    def resolve(cwd):
        repo = ROOT / "repo"
        here = Path(cwd).resolve()
        if here == repo or repo in here.parents:
            return {"graph": str(repo / "backend" / "graphify-out" / "graph.json"),
                    "graph_root": str(repo / "backend"), "source": "repo-subdir",
                    "local_root": str(repo / "backend")}
        return None
''')


@pytest.fixture()
def fx(tmp_path: Path) -> dict[str, Path]:
    inputs = tmp_path / "inputs"
    repo = inputs / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "backend" / "graphify-out").mkdir(parents=True)
    (repo / "backend" / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    (inputs / "resolve_graph.py").write_text(STUB_RESOLVER, encoding="utf-8")
    log = inputs / "queries.log"
    log.write_text("", encoding="utf-8")
    projects = inputs / "projects" / "p"
    projects.mkdir(parents=True)
    lines = [{"type": "user", "timestamp": "2026-10-09T10:00:00+00:00", "cwd": str(repo),
              "sessionId": "s-fixture", "message": {"role": "user", "content": "hello"}}]
    (projects / "s-fixture.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                                              encoding="utf-8")
    cfg = inputs / "graphify.local.json"
    cfg.write_text(json.dumps({"skill_scripts": str(inputs), "query_log": str(log),
                               "targets": [{"repo": str(repo)}],
                               "funnel": {"exclude_sessions": {}, "exclude_temp_cwd": False}}),
                   encoding="utf-8")
    out_dir = tmp_path / "out"
    return {"root": tmp_path, "inputs": inputs, "cfg": cfg, "log": log,
            "projects": inputs / "projects", "out": out_dir / "r-1.md",
            "json": out_dir / "r-1.json"}


def run(fx: dict[str, Path], *extra: str, cfg: Path | None = None,
        out: Path | None = None) -> subprocess.CompletedProcess[str]:
    out = out or fx["out"]
    cmd = [sys.executable, str(WRAPPER), "--script", str(SCRIPT),
           "--config", str(cfg or fx["cfg"]), "--projects", str(fx["projects"]),
           "--log", str(fx["log"]), "--days", "7", "--until", UNTIL,
           "--out", str(out), "--json", str(fx["json"]), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def snapshot(root: Path) -> dict[str, int]:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_exit_0_writes_report_and_json(fx):
    r = run(fx)
    assert r.returncode == 0, r.stderr
    md = fx["out"].read_text(encoding="utf-8")
    for row in ("1a Graph resolution", "1b Hint delivery", "2 Routing", "3 Execution", "4 Measurement"):
        assert row in md
    assert isinstance(json.loads(fx["json"].read_text(encoding="utf-8")), dict)


def test_exit_2_on_bad_config(fx):
    bad = fx["inputs"] / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    r = run(fx, cfg=bad)
    assert r.returncode == 2
    assert "missing skill_scripts" in r.stderr
    assert not fx["out"].exists() and not fx["json"].exists()


def test_exit_2_on_unparseable_config(fx):
    bad = fx["inputs"] / "garbage.json"
    bad.write_text("not json", encoding="utf-8")
    assert run(fx, cfg=bad).returncode == 2


def test_exit_2_on_bad_until(fx):
    r = subprocess.run([sys.executable, str(WRAPPER), "--script", str(SCRIPT),
                        "--config", str(fx["cfg"]), "--projects", str(fx["projects"]),
                        "--log", str(fx["log"]), "--until", "not-a-date",
                        "--out", str(fx["out"]), "--json", str(fx["json"])],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 2
    assert "bad --until" in r.stderr


def test_writes_only_out_and_json(fx):
    before = snapshot(fx["inputs"])
    assert run(fx).returncode == 0
    assert snapshot(fx["inputs"]) == before, "inputs were modified"
    written = sorted(str(p.relative_to(fx["root"])) for p in fx["root"].rglob("*")
                     if p.is_file() and not str(p).startswith(str(fx["inputs"])))
    assert written == sorted([str(fx["json"].relative_to(fx["root"])),
                              str(fx["out"].relative_to(fx["root"]))])


def test_refuses_out_inside_git_tree(fx):
    inside = fx["inputs"] / "repo" / "reports" / "r-1.md"
    r = run(fx, out=inside)
    assert r.returncode == 3
    assert "inside git tree" in r.stderr
    assert not inside.exists() and not fx["json"].exists()


def test_allows_git_ignored_folder_inside_git_tree(fx):
    repo = fx["root"] / "ignoring-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("/reports/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    out = repo / "reports" / "r-1.md"
    r = run(fx, out=out)
    assert r.returncode == 0, r.stderr
    assert out.is_file()


def test_missing_script_is_exit_4(fx):
    r = subprocess.run([sys.executable, str(WRAPPER), "--script", str(fx["root"] / "nope.py"),
                        "--out", str(fx["out"]), "--json", str(fx["json"])],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 4
    assert "audit tooling FAIL" in r.stderr
