"""Tests for sync_finish.py. Every mutation runs on a tmp copy of the source state dir.

Source state dir: env NEXT_SYNC_TEST_SOURCE (tests skip without it). The source is only read
(copytree) and its queue.yaml/QUEUE.md sha256 is asserted unchanged at module teardown.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_finish.py"
SRC = os.environ.get("NEXT_SYNC_TEST_SOURCE")
pytestmark = pytest.mark.skipif(not SRC, reason="NEXT_SYNC_TEST_SOURCE not set")
WATCHED = ("queue.yaml", "QUEUE.md")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def source() -> Iterator[Path]:
    src = Path(SRC or "")
    before = {n: _sha(src / n) for n in WATCHED}
    yield src
    assert {n: _sha(src / n) for n in WATCHED} == before, "canonical source state dir was modified"


@pytest.fixture()
def copy(source: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "state"
    shutil.copytree(source, dst, ignore=shutil.ignore_patterns("__pycache__", "_backups", ".canonical"))
    return dst


def _run(*args: str, canonical_env: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "NEXT_CANONICAL_STATE_DIR"}
    if canonical_env:
        env["NEXT_CANONICAL_STATE_DIR"] = canonical_env
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def _generated_on(state: Path) -> str:
    for line in (state / "QUEUE.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("Generated "):
            return line.split()[1]
    raise AssertionError("no Generated line in QUEUE.md")


def test_refuses_canonical_without_live(source: Path) -> None:
    r = _run("--state-dir", str(source), "--canonical-state-dir", str(source))
    assert r.returncode == 2 and "REFUSED" in r.stderr


def test_refuses_canonical_via_env(source: Path) -> None:
    r = _run("--state-dir", str(source), canonical_env=str(source))
    assert r.returncode == 2 and "REFUSED" in r.stderr


def test_refuses_marker_file(copy: Path) -> None:
    (copy / ".canonical").write_text("", encoding="utf-8")
    assert _run("--state-dir", str(copy)).returncode == 2


def test_copy_ok_and_check_reproduces(copy: Path, source: Path) -> None:
    date = _generated_on(copy)
    args = ("--state-dir", str(copy), "--canonical-state-dir", str(source), "--generated-on", date)
    r = _run(*args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[validate] exit=0" in r.stdout and "[render] exit=0" in r.stdout and "[check] exit=0" in r.stdout
    assert "watermark delta: none" in r.stdout
    md_first = _sha(copy / "QUEUE.md")
    assert _run(*args).returncode == 0
    assert _sha(copy / "QUEUE.md") == md_first  # deterministic: re-render is byte-identical


def test_validate_failure_exit_1_stops_before_render(copy: Path, source: Path) -> None:
    q = copy / "queue.yaml"
    q.write_text("schema_version: [not, valid\n" + q.read_text(encoding="utf-8"), encoding="utf-8")
    r = _run("--state-dir", str(copy), "--canonical-state-dir", str(source), "--baseline", str(source / "queue.yaml"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[validate] exit=1" in r.stdout and "[render]" not in r.stdout


def test_check_rejects_tampered_view(copy: Path) -> None:
    (copy / "QUEUE.md").write_text("tampered\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(copy / "render_queue.py"), "--state-dir", str(copy), "--generated-on", "2026-10-16", "--check"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0


def test_watermark_delta_reported(copy: Path, source: Path) -> None:
    base = copy.parent / "baseline.yaml"
    shutil.copy2(copy / "queue.yaml", base)
    q = copy / "queue.yaml"
    text = q.read_text(encoding="utf-8")
    # The watermark value moves with live state; read it instead of pinning one PR number.
    m = re.search(r"reconciled:\n  bimpossible:\n(?:    .*\n)*?    pr: (\d+)\n", text)
    assert m, "bimpossible watermark pr not found in queue.yaml"
    old = int(m.group(1))
    new = old + 1
    q.write_text(text[:m.start(1)] + str(new) + text[m.end(1):], encoding="utf-8")
    r = _run("--state-dir", str(copy), "--canonical-state-dir", str(source), "--baseline", str(base))
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"bimpossible"' in r.stdout and str(new) in r.stdout
