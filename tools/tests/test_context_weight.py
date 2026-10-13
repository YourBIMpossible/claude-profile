"""Tests for tools/context_weight.py (schema v2, scenario-aware).

Every case builds a throwaway estate under tempfile and passes it in — the
real ~/.claude, F:\\Claude-Profile and F:\\BIMpossible are never touched.

Carries forward the v1 regression this tool exists to prevent (2026-08-31
slop audit, MEDIUM-1; b640675): a missing/unreadable required input must
never be silently read as 0 bytes and must never let the run report clean.
v2 adds: MEMORY.md is a direct per-project measurement (never derived by
subtraction), global vs BIMpossible scenarios are independent, and a
comparable prior history record drives trend warnings.

Run: python tools/tests/test_context_weight.py   (also collectable by pytest)
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context_weight as cw  # noqa: E402


NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

SKILL_MD = """---
name: {name}
description: {desc}
---

Body text is free — only the description is standing context.
"""


def _write_text(path: Path, text: str) -> None:
    # newline="" disables Windows' text-mode \n -> \r\n translation so a
    # fixture's byte count is the same on every platform.
    path.write_text(text, encoding="utf-8", newline="")


def _write_skill(skills_dir: Path, name: str, desc: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    _write_text(d / "SKILL.md", SKILL_MD.format(name=name, desc=desc))


def build_estate(tmp: Path, *,
                  skills=True, skill_files=True, desc_chars=30,
                  wrapper=True, global_md=True, bim_md=True,
                  global_mem=True, bim_mem=True,
                  settings="good", enabled_plugins=None,
                  plugins_dir=True,
                  history=None) -> dict:
    """Materialise a complete, passing estate, then omit/corrupt exactly
    what the case asks for. Returns kwargs ready for cw.run_scan(**kwargs)."""
    skills_dir = tmp / "skills"
    if skills:
        skills_dir.mkdir()
        if skill_files:
            _write_skill(skills_dir, "demo", "d" * desc_chars)

    wrapper_path = tmp / "wrapper_CLAUDE.md"
    if wrapper:
        _write_text(wrapper_path, "# wrapper\n")

    global_md_path = tmp / "global_CLAUDE.md"
    if global_md:
        _write_text(global_md_path, "# global rules\n")

    bim_md_path = tmp / "bim_CLAUDE.md"
    if bim_md:
        _write_text(bim_md_path, "# bimpossible rules\n")

    global_mem_path = tmp / "global_MEMORY.md"
    if global_mem == "unreadable":
        global_mem_path.mkdir()
    elif global_mem:
        _write_text(global_mem_path, "# global memory index\n")

    bim_mem_path = tmp / "bim_MEMORY.md"
    if bim_mem == "unreadable":
        bim_mem_path.mkdir()
    elif bim_mem:
        _write_text(bim_mem_path, "# bimpossible memory index\n")

    settings_path = tmp / "settings.json"
    plugins = {k: True for k in (enabled_plugins or [])}
    if settings == "good":
        settings_path.write_text(json.dumps({"enabledPlugins": plugins}), encoding="utf-8")
    elif settings == "malformed":
        settings_path.write_text("{not json", encoding="utf-8")
    # settings == "missing": no file written

    plugins_dir_path = tmp / "plugins"
    if plugins_dir:
        plugins_dir_path.mkdir()

    history_path = tmp / "history.jsonl"
    if history:
        with history_path.open("w", encoding="utf-8") as fh:
            for rec in history:
                fh.write(json.dumps(rec) + "\n")

    return dict(
        skills_dir=skills_dir,
        wrapper_claude_md=wrapper_path,
        global_claude_md=global_md_path,
        bimpossible_claude_md=bim_md_path,
        global_memory_md=global_mem_path,
        bimpossible_memory_md=bim_mem_path,
        settings_path=settings_path,
        plugins_dir=plugins_dir_path,
        history_path=history_path,
        repo_dir=tmp,
        now=NOW,
        write_history=False,
    )


def run(**kwargs):
    """-> (result:dict, report:str)."""
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td), **kwargs)
        result = cw.run_scan(**paths)
        report = cw.render_report(result)
        return result, report


def run_cli(**kwargs):
    """-> (exit_code, stdout) via main(), for the printed-output-shape tests."""
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td), **kwargs)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cw.main(**paths)
        return rc, buf.getvalue()


# ------------------------------------------------------------------ baseline

def test_complete_estate_is_clean():
    result, report = run()
    assert result["exit_code"] == 0, report
    assert result["missing_n"] == 0
    assert "VERDICT: CLEAN" in report


# --------------------------------------------------- missing input != empty input

def test_missing_skills_dir_is_missing_not_zero():
    result, report = run(skills=False)
    assert result["exit_code"] == 2, report
    src = next(s for s in result["sources"] if s.key == "personal_skill_description_bytes")
    assert src.state == "MISSING"
    assert src.bytes is None
    assert "CLEAN" not in report


def test_skills_dir_with_no_skill_md_is_missing():
    result, report = run(skill_files=False)
    assert result["exit_code"] == 2, report
    src = next(s for s in result["sources"] if s.key == "personal_skill_description_bytes")
    assert src.state == "MISSING"
    assert "no SKILL.md found under" in src.reason


def test_missing_global_claude_md_names_the_file():
    result, report = run(global_md=False)
    assert result["exit_code"] == 2, report
    src = next(s for s in result["sources"] if s.key == "global_claude_md_bytes")
    assert src.state == "MISSING"
    assert "global_CLAUDE.md" in src.reason


def test_missing_bimpossible_claude_md_only_gates_bimpossible_scenario():
    result, report = run(bim_md=False)
    assert result["exit_code"] == 2, report
    assert result["scenario_results"][cw.BIMPOSSIBLE]["total"] is None
    # global_session does not depend on the BIMpossible-only file.
    assert result["scenario_results"][cw.GLOBAL]["total"] is not None


def test_unreadable_global_memory_is_unmeasured_not_a_crash():
    result, report = run(global_mem="unreadable")
    assert result["exit_code"] == 2, report
    src = next(s for s in result["sources"] if s.key == "global_memory_bytes")
    assert src.state == "UNMEASURED"
    assert "could not read" in src.reason


def test_missing_settings_makes_plugin_measurement_unmeasured():
    result, report = run(settings="missing")
    assert result["exit_code"] == 2, report
    src = next(s for s in result["sources"] if s.key == "plugin_skill_description_bytes")
    assert src.state == "UNMEASURED"


def test_malformed_settings_is_unmeasured_not_silently_clean():
    result, report = run(settings="malformed")
    assert result["exit_code"] == 2, report
    src = next(s for s in result["sources"] if s.key == "plugin_skill_description_bytes")
    assert src.state == "UNMEASURED"
    assert "CLEAN" not in report


# ------------------------------------------------- required test suite (Phase 1 spec)

def test_1_memory_is_direct_not_subtraction():
    # global_memory_bytes must equal the actual file's byte count, not a
    # figure derived from (total - everything else).
    result, _ = run()
    src = next(s for s in result["sources"] if s.key == "global_memory_bytes")
    assert src.bytes == len(b"# global memory index\n")


def test_2_global_session_excludes_bimpossible_only_instructions():
    result, _ = run()
    # Shrink nothing else, just make the BIMpossible file huge — global must
    # not move.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        paths["bimpossible_claude_md"].write_text("x" * 50_000, encoding="utf-8")
        big = cw.run_scan(**paths)
    assert big["scenario_results"][cw.GLOBAL]["total"] == result["scenario_results"][cw.GLOBAL]["total"]
    assert big["scenario_results"][cw.BIMPOSSIBLE]["total"] > result["scenario_results"][cw.BIMPOSSIBLE]["total"]


def test_3_bimpossible_session_includes_bimpossible_instructions():
    result, _ = run()
    g = result["scenario_results"][cw.GLOBAL]["total"]
    b = result["scenario_results"][cw.BIMPOSSIBLE]["total"]
    assert b > g  # bimpossible_session = global_session + project-only sources


def test_4_unreadable_optional_source_is_unmeasured_never_zero():
    result, _ = run(global_mem="unreadable")
    src = next(s for s in result["sources"] if s.key == "global_memory_bytes")
    assert src.state == "UNMEASURED"
    assert src.bytes is None  # never coerced to 0


def test_5_missing_required_source_fails():
    result, _ = run(global_md=False)
    assert result["exit_code"] == 2
    assert result["missing_n"] >= 1


def test_6_source_at_90pct_of_ceiling_warns():
    result, report = run()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        # ceiling for a source with both warn_at and fail_at is fail_at
        # (Source.ceiling); 90% of that is what "near its ceiling" means.
        near = int(cw.MEMORY_FAIL_BYTES * 0.92)
        paths["global_memory_md"].write_text("x" * near, encoding="utf-8")
        r = cw.run_scan(**paths)
    rep = cw.render_report(r)
    assert "NEAR-LIMIT" in rep or "WARN" in rep


def test_7_scenario_past_45kb_is_info_not_a_failure():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        # 44_000 alone undershoots SCENARIO_WARN_BYTES once the estate's other
        # small required global sources are summed in; pad past it.
        paths["global_claude_md"].write_text("x" * 46_000, encoding="utf-8")
        r = cw.run_scan(**paths)
    # Aggregate scenario size is telemetry: labelled, never gating.
    assert r["scenario_results"][cw.GLOBAL]["status"] == "info"
    assert r["scenario_results"][cw.GLOBAL]["verdict"] == "WARN"
    assert r["exit_code"] == 0


def test_8_scenario_past_56kb_is_info_not_a_failure():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        paths["global_claude_md"].write_text("x" * 56_000, encoding="utf-8")
        r = cw.run_scan(**paths)
    assert r["scenario_results"][cw.GLOBAL]["status"] == "info"
    assert r["scenario_results"][cw.GLOBAL]["verdict"] == "FAIL"
    assert r["exit_code"] == 0
    assert "status=INFO" in cw.render_report(r)


def test_9_comparable_history_over_10pct_scenario_growth_warns():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        baseline = cw.run_scan(**paths)
        prior_bytes = baseline["scenario_results"][cw.GLOBAL]["total"]
        prior = dict(
            timestamp="2026-09-01T00:00:00+00:00", schema_version=cw.SCHEMA_VERSION,
            global_session_bytes=prior_bytes, bimpossible_session_bytes=prior_bytes,
        )
        with paths["history_path"].open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(prior) + "\n")
        # grow global by >10%
        paths["global_claude_md"].write_text("x" * int(prior_bytes * 2), encoding="utf-8")
        r = cw.run_scan(**paths)
    assert any("grew" in ln and cw.GLOBAL in ln for ln in r["trend_lines"]), r["trend_lines"]


def test_10_comparable_history_over_10pct_principal_source_growth_warns():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        baseline = cw.run_scan(**paths)
        src = next(s for s in baseline["sources"] if s.key == "global_claude_md_bytes")
        prior = {
            "timestamp": "2026-09-01T00:00:00+00:00",
            "schema_version": cw.SCHEMA_VERSION,
            "global_session_bytes": baseline["scenario_results"][cw.GLOBAL]["total"],
            "bimpossible_session_bytes": baseline["scenario_results"][cw.BIMPOSSIBLE]["total"],
            "global_claude_md_bytes": src.bytes,
        }
        with paths["history_path"].open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(prior) + "\n")
        paths["global_claude_md"].write_text("x" * int(src.bytes * 3), encoding="utf-8")
        r = cw.run_scan(**paths)
    assert any("global_claude_md_bytes grew" in ln for ln in r["trend_lines"]), r["trend_lines"]


def test_11_schema_mismatch_reports_reset_not_a_bad_trend_claim():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        prior = {"timestamp": "x", "schema_version": cw.SCHEMA_VERSION - 1,
                 "global_session_bytes": 1}
        with paths["history_path"].open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(prior) + "\n")
        r = cw.run_scan(**paths)
    assert r["trend_reset"] is True
    assert any("TREND BASELINE RESET" in ln for ln in r["trend_lines"])


def test_12_backing_artifact_excluded_when_preview_known():
    result, _ = run()
    src = next(s for s in result["sources"] if s.key == "session_start_backing_artifact_bytes")
    assert src.scenarios == ()  # never contributes to any scenario total
    for scen_name, r in result["scenario_results"].items():
        # No scenario total silently includes a 40-50KB artifact bump.
        assert r["total"] is None or r["total"] < 40_000


def test_13_output_includes_top_contributors_and_coverage_when_clean():
    result, report = run()
    assert result["exit_code"] == 0
    assert "Top three measured contributors:" in report
    assert "Coverage: measured=" in report
    assert "Unmeasured/discovered sources:" in report


def test_14_jsonl_history_append_does_not_overwrite_earlier_entries():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        paths = build_estate(tmp)
        paths["write_history"] = True
        cw.run_scan(**paths)
        cw.run_scan(**paths)
        lines = paths["history_path"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)  # each line still valid, independent JSON
    assert json.loads(lines[0])["status"] == "clean"


def test_14b_advisory_run_is_recorded_as_advisory_not_clean():
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td))
        paths["write_history"] = True
        base = cw.run_scan(**{**paths, "write_history": False})["scenario_results"][cw.GLOBAL]["total"]
        paths["history_path"].write_text(json.dumps(dict(
            timestamp="2026-09-01T00:00:00+00:00", schema_version=cw.SCHEMA_VERSION,
            global_session_bytes=base // 2, bimpossible_session_bytes=base // 2,
        )) + "\n", encoding="utf-8")
        r = cw.run_scan(**paths)
        last = json.loads(paths["history_path"].read_text(encoding="utf-8").splitlines()[-1])
    assert r["exit_code"] == 0 and r["advisory"] is True
    assert last["status"] == "advisory"


# ------------------------------------------------- real signals still work

def test_oversized_description_warns_as_advisory_exit_0():
    result, report = run(desc_chars=cw.DESC_WARN + 5)
    assert result["exit_code"] == 0, report
    assert result["advisory"] is True
    assert "VERDICT: ADVISORY" in report
    assert any(k == "WARN" for _, _, k in result["per_skill_issues"])


def test_oversized_description_still_fails():
    result, report = run(desc_chars=cw.DESC_FAIL + 5)
    assert result["exit_code"] == 2, report
    assert any(k == "FAIL" for _, _, k in result["per_skill_issues"])


def test_plugin_allowlist_flags_unlisted_enabled_plugin():
    result, report = run(enabled_plugins=["some-new-plugin@some-marketplace"])
    assert "some-new-plugin@some-marketplace" in result["plugin_allowlist_warns"]
    assert result["exit_code"] == 0
    assert result["advisory"] is True


def test_plugin_with_no_skill_is_not_applicable_not_unmeasured():
    result, _ = run(enabled_plugins=["typescript-lsp@claude-plugins-official"])
    # No SKILL.md exists for it under the fixture plugins dir -> its
    # contribution is genuinely zero-cost, not "could not check".
    src = next(s for s in result["sources"] if s.key == "plugin_skill_description_bytes")
    assert src.state == "MEASURED"
    assert src.bytes == 0


def test_hosted_unmeasurable_sources_never_gate_or_count():
    result, _ = run()
    hosted_keys = {k for k, _ in cw.HOSTED_UNMEASURABLE}
    hosted = [s for s in result["sources"] if s.key in hosted_keys]
    assert len(hosted) == len(cw.HOSTED_UNMEASURABLE)
    assert all(s.state == "UNMEASURED" and not s.required for s in hosted)
    assert result["exit_code"] == 0  # optional/hosted gaps don't block a clean verdict


def test_skill_descriptions_returns_unmeasured_for_a_missing_dir():
    with tempfile.TemporaryDirectory() as td:
        descs, unmeasured = cw.skill_descriptions(Path(td) / "nope")
        assert descs == []
        assert len(unmeasured) == 1
        assert "skills directory not found" in unmeasured[0]


# ------------------------------------ Claude Code MEMORY.md truncation still fails

def test_memory_index_at_25kb_truncation_fails():
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td))
        paths["bimpossible_memory_md"].write_text("x" * cw.MEMORY_FAIL_BYTES, encoding="utf-8")
        r = cw.run_scan(**paths)
    assert r["exit_code"] == 2, cw.render_report(r)


def test_memory_index_at_200_lines_truncation_fails():
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td))
        paths["global_memory_md"].write_text("- x\n" * cw.MEMORY_FAIL_LINES, encoding="utf-8")
        r = cw.run_scan(**paths)
    assert r["exit_code"] == 2, cw.render_report(r)


def test_advisory_trend_growth_does_not_change_exit_code():
    with tempfile.TemporaryDirectory() as td:
        paths = build_estate(Path(td))
        base = cw.run_scan(**paths)["scenario_results"][cw.GLOBAL]["total"]
        paths["history_path"].write_text(json.dumps(dict(
            timestamp="2026-09-01T00:00:00+00:00", schema_version=cw.SCHEMA_VERSION,
            global_session_bytes=base // 2, bimpossible_session_bytes=base // 2,
        )) + "\n", encoding="utf-8")
        r = cw.run_scan(**paths)
    assert any(t.startswith("WARN") for t in r["trend_lines"])
    assert r["exit_code"] == 0
    assert r["advisory"] is True


def main() -> int:
    cases = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for fn in cases:
        try:
            fn()
        except AssertionError as e:
            failures.append(f"{fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - surface unexpected errors as failures too
            failures.append(f"{fn.__name__}: {type(e).__name__}: {e}")
    if failures:
        print(f"FAIL {len(failures)}/{len(cases)}:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"OK - {len(cases)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
