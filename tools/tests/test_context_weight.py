"""Tests for tools/context_weight.py — a missing input must FAIL, not read as 0.

The regression that matters (2026-08-31 slop audit, MEDIUM-1): every measured
path was optional. A missing skills dir returned [], a missing CLAUDE.md was
skipped, a MEMORY.md glob that matched nothing produced no rows — so an estate
with nothing readable totalled 0 bytes, cleared every threshold and printed
"CLEAN — within all budgets" with exit 0. b640675 records exactly that state
persisting for an unknown period.

Every case builds a throwaway estate under tempfile and passes it in; the real
~/.claude and the real CLAUDE.md files are never touched.

Run: python tools/tests/test_context_weight.py   (also collectable by pytest)
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context_weight as cw  # noqa: E402


SKILL_MD = """---
name: demo
description: A short demo description.
---

Body text is free — only the description is standing context.
"""


def build_estate(tmp: Path, *, skills=True, skill_files=True, claude_mds=2,
                 projects=True, memory=True, settings="good", desc_chars=30):
    """Materialise a complete estate, then omit exactly what the case removes."""
    skills_dir = tmp / "skills"
    if skills:
        skills_dir.mkdir()
        if skill_files:
            d = skills_dir / "demo"
            d.mkdir()
            body = SKILL_MD.replace("A short demo description.", "d" * desc_chars)
            (d / "SKILL.md").write_text(body, encoding="utf-8")

    mds = []
    for i in range(2):
        p = tmp / f"CLAUDE{i}.md"
        if i < claude_mds:
            p.write_text("# rules\n", encoding="utf-8")
        mds.append(p)

    projects_dir = tmp / "projects"
    if projects:
        projects_dir.mkdir()
        if memory:
            mem = projects_dir / "proj" / "memory"
            mem.mkdir(parents=True)
            if memory == "unreadable":
                # A directory named MEMORY.md matches the glob but cannot be read
                # as a file — a portable stand-in for a locked/permission-denied file.
                (mem / "MEMORY.md").mkdir()
            else:
                (mem / "MEMORY.md").write_text("# Memory index\n", encoding="utf-8")

    settings_path = tmp / "settings.json"
    if settings == "good":
        settings_path.write_text(json.dumps({"enabledPlugins": {}}), encoding="utf-8")
    elif settings == "malformed":
        settings_path.write_text("{not json", encoding="utf-8")
    # settings == "missing": no file written

    return dict(skills_dir=skills_dir, claude_mds=mds,
                projects_dir=projects_dir, settings_path=settings_path)


def run(**kwargs):
    """-> (exit_code, stdout)."""
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td), **kwargs)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cw.main(**paths)
        return rc, buf.getvalue()


# ------------------------------------------------------------------ baseline

def test_complete_estate_is_clean():
    rc, out = run()
    assert rc == 0, out
    assert "CLEAN" in out
    assert "UNMEASURED" not in out
    assert "skills 1" in out, out


# --------------------------------------------------- missing input != empty input

def test_missing_skills_dir_fails_with_the_path():
    rc, out = run(skills=False)
    assert rc == 2, out
    assert "UNMEASURED: skills directory not found:" in out
    assert "skills" in out
    assert "CLEAN" not in out
    assert "NOT MEASURED" in out


def test_skills_dir_with_no_skill_md_fails():
    # The directory exists but holds nothing measurable — the old code returned
    # [] here too, indistinguishable from "no skills installed".
    rc, out = run(skill_files=False)
    assert rc == 2, out
    assert "no SKILL.md found under" in out
    assert "CLEAN" not in out


def test_missing_claude_md_fails_and_names_the_file():
    rc, out = run(claude_mds=1)
    assert rc == 2, out
    assert "UNMEASURED: CLAUDE.md not found:" in out
    assert "CLAUDE1.md" in out, out
    assert "CLAUDE.md 0 |" not in out, "the CLAUDE.md that DID exist is still counted: " + out
    assert "CLEAN" not in out


def test_memory_glob_matching_nothing_fails():
    rc, out = run(memory=False)
    assert rc == 2, out
    assert "no */memory/MEMORY.md found under" in out
    assert "CLEAN" not in out


def test_unreadable_memory_md_is_unmeasured_not_a_crash():
    # 2026-09-07 slop audit LOW-1: a present-but-unreadable MEMORY.md raised an
    # uncaught OSError while the sibling CLAUDE.md/settings reads reported UNMEASURED.
    rc, out = run(memory="unreadable")
    assert rc == 2, out
    assert "UNMEASURED: could not read" in out and "MEMORY.md" in out, out
    assert "CLEAN" not in out


def test_missing_projects_dir_fails():
    rc, out = run(projects=False)
    assert rc == 2, out
    assert "UNMEASURED: projects directory not found:" in out
    assert "CLEAN" not in out


def test_unreadable_settings_is_unmeasured_not_a_warning():
    # The plugin-allowlist check did not run at all; calling that a WARN (exit 1)
    # implied the allowlist had been compared and only nagged.
    rc, out = run(settings="malformed")
    assert rc == 2, out
    assert "UNMEASURED: could not read" in out
    assert "WARN: could not read" not in out
    assert "CLEAN" not in out


def test_missing_settings_is_unmeasured():
    rc, out = run(settings="missing")
    assert rc == 2, out
    assert "UNMEASURED: could not read" in out


def test_everything_missing_never_reports_clean():
    # The exact b640675 shape: nothing readable, every total 0, every threshold
    # cleared. Must be exit 2 with five unmeasured inputs, never CLEAN/0.
    rc, out = run(skills=False, claude_mds=0, projects=False, settings="missing")
    assert rc == 2, out
    assert "CLEAN" not in out
    assert out.count("UNMEASURED:") == 5, out
    assert "standing context: 0 bytes" in out, "the misleading total is still shown..."
    assert "the totals above are incomplete and no budget verdict is possible" in out


# ------------------------------------------------- real signals still work

def test_oversized_description_still_warns_with_exit_1():
    rc, out = run(desc_chars=cw.DESC_WARN + 5)
    assert rc == 1, out
    assert "WARN: skill 'demo' description" in out
    assert "UNMEASURED" not in out
    assert "CLEAN" not in out


def test_oversized_description_still_fails_with_exit_2():
    rc, out = run(desc_chars=cw.DESC_FAIL + 5)
    assert rc == 2, out
    assert "FAIL: skill 'demo' description" in out
    assert "NOT MEASURED" not in out, "a real budget failure is not an unmeasured input"


def test_unmeasured_input_outranks_a_clean_measurement():
    # Measurable parts are fine; one input is gone. The verdict is "unknown",
    # and the unknown line replaces the all-clear rather than joining it.
    rc, out = run(projects=False)
    assert rc == 2
    assert "NOT MEASURED" in out and "CLEAN" not in out


def test_skill_descriptions_returns_unmeasured_for_a_missing_dir():
    with tempfile.TemporaryDirectory() as td:
        descs, unmeasured = cw.skill_descriptions(Path(td) / "nope")
        assert descs == []
        assert len(unmeasured) == 1
        assert "skills directory not found" in unmeasured[0]


def main() -> int:
    cases = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for fn in cases:
        try:
            fn()
        except AssertionError as e:
            failures.append(f"{fn.__name__}: {e}")
    if failures:
        print(f"FAIL {len(failures)}/{len(cases)}:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"OK - {len(cases)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
