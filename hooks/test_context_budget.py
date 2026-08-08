"""Unit + integration tests for context_budget.py.

Unit tests exercise the pure helpers (_resolve_text, _read_if_safe, ...)
directly against synthetic payload shapes captured from the real harness
2026-08-07 (see the module docstring's TOOL RESULT SHAPES section for the
measurement trail). Integration tests run the real hook as a subprocess
against its actual stdin/stdout contract - the thing that matters here is
the process boundary, not just the Python functions behind it.

Run: python -m unittest test_context_budget -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import context_budget as cb  # noqa: E402

HOOK_PATH = Path(__file__).resolve().parent / "context_budget.py"


# --- unit tests: pure helpers -------------------------------------------------


class TestAsText(unittest.TestCase):
    def test_none(self):
        self.assertEqual(cb._as_text(None), "")

    def test_string_passthrough(self):
        self.assertEqual(cb._as_text("hello"), "hello")

    def test_dict_json_dumped(self):
        self.assertEqual(cb._as_text({"a": 1}), '{"a": 1}')

    def test_list_json_dumped(self):
        self.assertEqual(cb._as_text([1, 2]), "[1, 2]")


class TestLimitFor(unittest.TestCase):
    def test_known_override(self):
        self.assertEqual(cb._limit_for("Read"), 12000)
        self.assertEqual(cb._limit_for("ToolSearch"), 6000)
        self.assertEqual(cb._limit_for("Grep"), 10000)

    def test_unknown_uses_default(self):
        self.assertEqual(cb._limit_for("Bash"), cb.DEFAULT_LIMIT)
        self.assertEqual(cb._limit_for("SomeRandomTool"), cb.DEFAULT_LIMIT)


class TestShouldConsider(unittest.TestCase):
    def test_spill_tools(self):
        for name in cb.SPILL_TOOLS:
            self.assertTrue(cb._should_consider(name))

    def test_mcp_prefix(self):
        self.assertTrue(cb._should_consider("mcp__ccd_session__spawn_task"))

    def test_control_tools_excluded(self):
        for name in ("TaskCreate", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion"):
            self.assertFalse(cb._should_consider(name))


class TestClipLines(unittest.TestCase):
    def test_joins_under_budget(self):
        self.assertEqual(cb._clip_lines(["a", "b", "c"], 100), "a\nb\nc")

    def test_truncates_when_over_budget(self):
        lines = [f"line{i}" for i in range(1000)]
        out = cb._clip_lines(lines, 50)
        self.assertIn("...[excerpt truncated - see FULL OUTPUT]", out)

    def test_clips_single_over_long_line(self):
        out = cb._clip_lines(["x" * 1000], 10000)
        self.assertIn("...[+", out)
        self.assertLess(len(out), 1000)


class TestSlug(unittest.TestCase):
    def test_sanitizes_and_truncates(self):
        self.assertEqual(cb._slug("mcp__ccd_session__spawn_task"), "mcp__ccd_session__spawn_task")
        self.assertEqual(cb._slug("weird/tool:name*here"), "weird_tool_name_here")
        self.assertEqual(len(cb._slug("x" * 100)), 40)


class TestDescribeInput(unittest.TestCase):
    def test_non_dict(self):
        self.assertEqual(cb._describe_input("not a dict"), "")
        self.assertEqual(cb._describe_input(None), "")

    def test_extracts_command(self):
        self.assertEqual(cb._describe_input({"command": "ls -la"}), "command: ls -la")

    def test_extracts_file_path(self):
        self.assertEqual(cb._describe_input({"file_path": "F:/foo.py"}), "file_path: F:/foo.py")

    def test_no_known_keys(self):
        self.assertEqual(cb._describe_input({"other": "x"}), "")


# --- unit tests: persisted-output resolution ----------------------------------


class TestReadIfSafe(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cb_test_"))
        self._orig_root = cb._TOOL_RESULTS_ROOT
        cb._TOOL_RESULTS_ROOT = self._tmp

    def tearDown(self):
        cb._TOOL_RESULTS_ROOT = self._orig_root
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_result_file(self, name: str, content: str) -> Path:
        d = self._tmp / "proj" / "tool-results"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_reads_file_under_safe_root(self):
        p = self._make_result_file("abc.txt", "full content here")
        self.assertEqual(cb._read_if_safe(str(p)), "full content here")

    def test_rejects_path_outside_safe_root(self):
        outside = self._tmp.parent / "evil.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            self.assertIsNone(cb._read_if_safe(str(outside)))
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_path_not_directly_in_tool_results(self):
        d = self._tmp / "proj" / "tool-results" / "nested"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "abc.txt"
        p.write_text("content", encoding="utf-8")
        self.assertIsNone(cb._read_if_safe(str(p)))

    def test_rejects_missing_file(self):
        d = self._tmp / "proj" / "tool-results"
        d.mkdir(parents=True, exist_ok=True)
        self.assertIsNone(cb._read_if_safe(str(d / "does-not-exist.txt")))

    def test_rejects_dotdot_traversal_resolving_outside_root(self):
        self._make_result_file("real.txt", "real")
        outside = self._tmp.parent / "outside.txt"
        outside.write_text("leaked", encoding="utf-8")
        try:
            traversal = str(self._tmp / "proj" / "tool-results" / ".." / ".." / ".." / "outside.txt")
            self.assertIsNone(cb._read_if_safe(traversal))
        finally:
            outside.unlink(missing_ok=True)


class TestResolveText(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cb_test_"))
        self._orig_root = cb._TOOL_RESULTS_ROOT
        cb._TOOL_RESULTS_ROOT = self._tmp

    def tearDown(self):
        cb._TOOL_RESULTS_ROOT = self._orig_root
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_result_file(self, content: str) -> Path:
        d = self._tmp / "proj" / "tool-results"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "persisted.txt"
        p.write_text(content, encoding="utf-8")
        return p

    def test_non_dict_string(self):
        self.assertEqual(cb._resolve_text("plain string"), "plain string")

    def test_non_dict_none(self):
        self.assertEqual(cb._resolve_text(None), "")

    def test_bash_shape_no_persistence(self):
        out = {"stdout": "hello\nworld", "stderr": "", "interrupted": False, "isImage": False}
        self.assertEqual(cb._resolve_text(out), "hello\nworld")

    def test_bash_shape_with_stderr(self):
        out = {"stdout": "out", "stderr": "warn"}
        self.assertEqual(cb._resolve_text(out), "out\nwarn")

    def test_bash_shape_stdout_only_empty_stderr_no_trailing_junk(self):
        out = {"stdout": "just this", "stderr": ""}
        self.assertEqual(cb._resolve_text(out), "just this")

    def test_bash_shape_resolves_persisted_path(self):
        full = "A" * 50000
        p = self._make_result_file(full)
        out = {
            "stdout": "X" * 29998,  # truncated stub, as the harness actually sends
            "stderr": "",
            "persistedOutputPath": str(p),
            "persistedOutputSize": 50000,
        }
        self.assertEqual(cb._resolve_text(out), full)

    def test_bash_shape_unsafe_persisted_path_falls_back_to_stdout(self):
        outside = self._tmp.parent / "forged.txt"
        outside.write_text("attacker content", encoding="utf-8")
        try:
            out = {"stdout": "truncated stub", "stderr": "", "persistedOutputPath": str(outside)}
            self.assertEqual(cb._resolve_text(out), "truncated stub")
        finally:
            outside.unlink(missing_ok=True)

    def test_bash_shape_missing_persisted_file_falls_back_to_stdout(self):
        out = {
            "stdout": "truncated stub",
            "stderr": "",
            "persistedOutputPath": str(self._tmp / "proj" / "tool-results" / "gone.txt"),
        }
        self.assertEqual(cb._resolve_text(out), "truncated stub")

    def test_read_shape_extracts_nested_content(self):
        out = {
            "file": {
                "filePath": "F:/x.csv",
                "content": "line1\nline2\nline3",
                "numLines": 3,
                "startLine": 1,
                "totalLines": 3,
            },
            "type": "text",
        }
        self.assertEqual(cb._resolve_text(out), "line1\nline2\nline3")

    def test_glob_shape_joins_filenames(self):
        out = {
            "filenames": ["a.py", "b.py", "c.py"],
            "numFiles": 3,
            "totalMatches": 3,
            "truncated": False,
            "countIsComplete": True,
            "durationMs": 12,
        }
        self.assertEqual(cb._resolve_text(out), "a.py\nb.py\nc.py")

    def test_glob_shape_empty_filenames_falls_back(self):
        out = {"filenames": [], "numFiles": 0, "totalMatches": 0, "truncated": False}
        # Nothing to extract from an empty list; falls back to the whole-dict
        # dump, which is harmless since there is nothing worth spilling anyway.
        self.assertEqual(cb._resolve_text(out), cb._as_text(out))

    def test_grep_shape_returns_content_field(self):
        out = {
            "content": "match1\nmatch2",
            "filenames": [],
            "mode": "content",
            "numFiles": 0,
            "numLines": 2,
            "totalLines": 2,
        }
        self.assertEqual(cb._resolve_text(out), "match1\nmatch2")

    def test_files_with_matches_shape_prefers_filenames_over_content(self):
        # Synthetic: Grep's files_with_matches mode was not directly captured
        # against a large payload, but when content is empty while filenames
        # is populated, the filenames list is the real answer.
        out = {"content": "", "filenames": ["a.py", "b.py"], "mode": "files_with_matches"}
        self.assertEqual(cb._resolve_text(out), "a.py\nb.py")

    def test_unrecognised_shape_falls_back_to_json_dump(self):
        out = {"results": [{"title": "x", "url": "y"}], "query": "z"}
        self.assertEqual(cb._resolve_text(out), cb._as_text(out))

    def test_read_shape_preserves_real_newlines_not_json_escaped(self):
        # Regression guard: json.dumps() turns real newlines into the two
        # character sequence \n, which breaks text.splitlines() downstream.
        out = {"file": {"content": "a\nb\nc"}, "type": "text"}
        resolved = cb._resolve_text(out)
        self.assertEqual(resolved.count("\n"), 2)
        self.assertNotIn("\\n", resolved)


# --- integration tests: real hook process, real stdin/stdout contract --------


def _run_hook(payload: dict, env: dict | None = None, timeout: float = 15) -> tuple[int, str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=full_env,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


class TestHookIntegration(unittest.TestCase):
    def setUp(self):
        self._cwd = Path(tempfile.mkdtemp(prefix="cb_it_"))

    def tearDown(self):
        shutil.rmtree(self._cwd, ignore_errors=True)

    def test_empty_stdin_passthrough(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="",
            capture_output=True,
            text=True,
            env=dict(os.environ),
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_malformed_json_fails_open(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="{not valid json",
            capture_output=True,
            text=True,
            env=dict(os.environ),
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_non_spill_tool_passthrough_even_if_huge(self):
        code, out = _run_hook(
            {"tool_name": "TaskCreate", "tool_output": "x" * 100000, "cwd": str(self._cwd)}
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_small_bash_output_passthrough(self):
        code, out = _run_hook(
            {
                "tool_name": "Bash",
                "tool_response": {"stdout": "hi", "stderr": "", "interrupted": False, "isImage": False},
                "cwd": str(self._cwd),
            }
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_large_plain_string_output_spills(self):
        big = "line\n" * 3000  # well over DEFAULT_LIMIT (8000 chars)
        code, out = _run_hook({"tool_name": "Bash", "tool_output": big, "cwd": str(self._cwd)})
        self.assertEqual(code, 0)
        self.assertTrue(out)
        payload = json.loads(out)
        replacement = payload["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn(cb.MARKER, replacement)
        self.assertIn("FULL OUTPUT:", replacement)
        spill_path = Path(replacement.split("FULL OUTPUT: ", 1)[1].splitlines()[0])
        self.assertTrue(spill_path.is_file())
        self.assertEqual(spill_path.read_text(encoding="utf-8"), big)

    def test_large_dict_response_without_persistence_spills_true_content(self):
        big_stdout = "A" * 9000  # over DEFAULT_LIMIT, under the 30000 native cap
        code, out = _run_hook(
            {
                "tool_name": "Bash",
                "tool_response": {"stdout": big_stdout, "stderr": "", "interrupted": False, "isImage": False},
                "cwd": str(self._cwd),
            }
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        replacement = payload["hookSpecificOutput"]["updatedToolOutput"]
        spill_path = Path(replacement.split("FULL OUTPUT: ", 1)[1].splitlines()[0])
        spilled = spill_path.read_text(encoding="utf-8")
        # Pre-fix this was a json.dumps() of the whole dict (quotes, keys,
        # escapes) instead of the raw 9000-char stdout string.
        self.assertEqual(spilled, big_stdout)

    def test_persisted_output_path_resolved_end_to_end(self):
        fake_home = self._cwd / "fakehome"
        results_dir = fake_home / ".claude" / "projects" / "someproj" / "tool-results"
        results_dir.mkdir(parents=True, exist_ok=True)
        full_content = "REAL-" + ("B" * 60000)
        persisted_file = results_dir / "abc123.txt"
        persisted_file.write_text(full_content, encoding="utf-8")

        code, out = _run_hook(
            {
                "tool_name": "Bash",
                "tool_response": {
                    "stdout": "X" * 29998,
                    "stderr": "",
                    "interrupted": False,
                    "isImage": False,
                    "persistedOutputPath": str(persisted_file),
                    "persistedOutputSize": len(full_content.encode("utf-8")),
                },
                "cwd": str(self._cwd),
            },
            env={"USERPROFILE": str(fake_home), "HOME": str(fake_home)},
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        replacement = payload["hookSpecificOutput"]["updatedToolOutput"]
        spill_path = Path(replacement.split("FULL OUTPUT: ", 1)[1].splitlines()[0])
        spilled = spill_path.read_text(encoding="utf-8")
        self.assertEqual(spilled, full_content)
        self.assertNotIn("X" * 100, spilled)  # not the truncated stdout stub

    def test_idempotence_marker_short_circuits(self):
        already = cb.MARKER + " already processed " + ("z" * 20000)
        code, out = _run_hook({"tool_name": "Bash", "tool_output": already, "cwd": str(self._cwd)})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_error_flag_noted_in_spill_record(self):
        big = "boom\n" * 3000
        code, out = _run_hook(
            {
                "tool_name": "Bash",
                "tool_output": big,
                "tool_output_is_error": True,
                "cwd": str(self._cwd),
            }
        )
        payload = json.loads(out)
        replacement = payload["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("ERROR", replacement)

    def test_mcp_tool_emits_both_output_keys(self):
        big = "z" * 20000
        code, out = _run_hook(
            {"tool_name": "mcp__someserver__sometool", "tool_output": big, "cwd": str(self._cwd)}
        )
        payload = json.loads(out)
        hso = payload["hookSpecificOutput"]
        self.assertIn("updatedToolOutput", hso)
        self.assertIn("updatedMCPToolOutput", hso)
        self.assertEqual(hso["updatedToolOutput"], hso["updatedMCPToolOutput"])

    def test_per_tool_limit_read_passes_through_below_its_own_threshold(self):
        # 9000 chars is over DEFAULT_LIMIT (8000) but under Read's own 12000 -
        # only correct if the size check runs against the true resolved
        # content length, not a json.dumps()-wrapped length.
        content = "y" * 9000
        code, out = _run_hook(
            {
                "tool_name": "Read",
                "tool_response": {
                    "file": {"filePath": "f.txt", "content": content, "numLines": 1, "startLine": 1, "totalLines": 1},
                    "type": "text",
                },
                "cwd": str(self._cwd),
            }
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_per_tool_limit_bash_same_size_spills(self):
        # Same 9000 chars, but Bash's limit is the 8000 default -> spills.
        content = "y" * 9000
        code, out = _run_hook(
            {
                "tool_name": "Bash",
                "tool_response": {"stdout": content, "stderr": "", "interrupted": False, "isImage": False},
                "cwd": str(self._cwd),
            }
        )
        self.assertEqual(code, 0)
        self.assertTrue(out)

    def test_read_shape_excerpt_has_real_lines_not_one_json_blob(self):
        # Fixed-width rows (20 chars incl. newline) x 2000 = ~40000 chars,
        # comfortably over Read's 12000-char limit with margin to spare.
        lines = [f"row-{i:04d},value{i:04d}" for i in range(2000)]
        content = "\n".join(lines)
        code, out = _run_hook(
            {
                "tool_name": "Read",
                "tool_response": {
                    "file": {"filePath": "f.csv", "content": content, "numLines": 2000, "startLine": 1, "totalLines": 2000},
                    "type": "text",
                },
                "cwd": str(self._cwd),
            }
        )
        payload = json.loads(out)
        replacement = payload["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("row-0000,value0000", replacement)
        self.assertIn("row-1999,value1999", replacement)
        self.assertNotIn("\\n", replacement)  # not JSON-escaped newlines
        self.assertIn("2,000 lines", replacement)  # line count computed from real content


if __name__ == "__main__":
    unittest.main()
