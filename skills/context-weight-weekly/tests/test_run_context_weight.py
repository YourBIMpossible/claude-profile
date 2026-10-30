"""Fixture tests for the context-weight-weekly wrapper.

Set CONTEXT_WEIGHT_TOOLS_DIR (or rely on <repo>/tools) to the real tools dir.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("run_context_weight", HERE.parent / "scripts" / "run_context_weight.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

TOOLS = W.resolve_tools_dir(None)
CANONICAL = TOOLS / "context_weight.history.jsonl"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "ABSENT"


def estate(tmp: Path, *, global_md: bool = True, mem_lines: int = 1) -> dict:
    (tmp / "skills" / "demo").mkdir(parents=True)
    (tmp / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: ddd\n---\nbody\n", encoding="utf-8", newline="")
    for n in ("wrapper_CLAUDE.md", "bim_CLAUDE.md", "bim_MEMORY.md"):
        (tmp / n).write_text("# x\n", encoding="utf-8", newline="")
    if global_md:
        (tmp / "global_CLAUDE.md").write_text("# g\n", encoding="utf-8", newline="")
    (tmp / "global_MEMORY.md").write_text("- l\n" * mem_lines, encoding="utf-8", newline="")
    (tmp / "settings.json").write_text(json.dumps({"enabledPlugins": {}}), encoding="utf-8")
    (tmp / "plugins").mkdir()
    return dict(skills_dir=tmp / "skills", wrapper_claude_md=tmp / "wrapper_CLAUDE.md",
                global_claude_md=tmp / "global_CLAUDE.md", bimpossible_claude_md=tmp / "bim_CLAUDE.md",
                global_memory_md=tmp / "global_MEMORY.md", bimpossible_memory_md=tmp / "bim_MEMORY.md",
                settings_path=tmp / "settings.json", plugins_dir=tmp / "plugins", repo_dir=tmp)


def test_exit0_clean(tmp_path, capsys):
    h = tmp_path / "h.jsonl"
    assert W.run(["--tools-dir", str(TOOLS), "--history-path", str(h)], **estate(tmp_path)) == 0
    assert "VERDICT: CLEAN" in capsys.readouterr().out
    assert len(h.read_text(encoding="utf-8").splitlines()) == 1


def test_exit2_missing_required_source(tmp_path, capsys):
    assert W.run(["--tools-dir", str(TOOLS), "--no-history-write"], **estate(tmp_path, global_md=False)) == 2
    assert "VERDICT: FAIL or NOT MEASURED" in capsys.readouterr().out


def test_exit2_memory_line_ceiling(tmp_path, capsys):
    assert W.run(["--tools-dir", str(TOOLS), "--no-history-write"], **estate(tmp_path, mem_lines=201)) == 2
    assert "VERDICT: FAIL" in capsys.readouterr().out


def test_canonical_history_untouched(tmp_path):
    before = _sha(CANONICAL)
    h = tmp_path / "h.jsonl"
    W.run(["--tools-dir", str(TOOLS), "--history-path", str(h)], **estate(tmp_path / "a"))
    W.run(["--tools-dir", str(TOOLS), "--no-history-write"], **estate(tmp_path / "b"))
    assert _sha(CANONICAL) == before
    assert h.exists()


def test_no_history_write_writes_nothing(tmp_path):
    h = tmp_path / "h.jsonl"
    W.run(["--tools-dir", str(TOOLS), "--history-path", str(h), "--no-history-write"], **estate(tmp_path))
    assert not h.exists()


def test_implicit_history_refused_in_fake_tools_dir(tmp_path):
    fake = tmp_path / "tools"
    fake.mkdir()
    (fake / "context_weight.py").write_text((TOOLS / "context_weight.py").read_text(encoding="utf-8"), encoding="utf-8")
    canon = fake / "context_weight.history.jsonl"
    canon.write_text('{"seed": 1}\n', encoding="utf-8")
    before = _sha(canon)
    assert W.run(["--tools-dir", str(fake)], **estate(tmp_path / "e")) == W.EXIT_USAGE
    assert _sha(canon) == before


@pytest.mark.parametrize("argv", [["--bogus"], ["--history-path"], ["--tools-dir", "Z:/nope", "--no-history-write"], []])
def test_bad_args_exit_64(argv, capsys):
    assert W.run(argv) == W.EXIT_USAGE
    assert "usage error" in capsys.readouterr().err
