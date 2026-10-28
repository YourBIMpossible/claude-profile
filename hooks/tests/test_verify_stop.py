"""Tests for verify_stop.py: the advisory Stop hook that flags a completion claim
made after file changes with no verification run since the last change.

Run: python hooks/tests/test_verify_stop.py  (from the repo root or anywhere)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "verify_stop.py"


def prompt(text: str) -> dict:
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


def tool(name: str, **inp: object) -> dict:
    return {"type": "assistant", "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": name, "input": inp}]}}


def result() -> dict:
    return {"type": "user", "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def say(text: str) -> dict:
    return {"type": "assistant", "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


EDIT = tool("Edit", file_path="a.py", old_string="x", new_string="y")
TEST = tool("Bash", command="python -m pytest hooks/tests -q")
CLAIM = "**Done** - fixed the parser in a.py."
SILENT = (0, "", "")


class VerifyStop(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_hook(self, entries: list[dict] | None, *, payload: dict | None = None,
                 raw: bytes | None = None, env: dict | None = None) -> tuple[int, str, str]:
        data = dict(payload or {})
        if entries is not None:
            tp = Path(self.tmp.name) / "t.jsonl"
            tp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
            data.setdefault("transcript_path", str(tp))
        data.setdefault("hook_event_name", "Stop")
        data.setdefault("stop_hook_active", False)
        full_env = {k: v for k, v in os.environ.items() if k != "VERIFY_STOP_HOOK"}
        full_env.update(env or {})
        stdin = raw if raw is not None else json.dumps(data).encode()
        proc = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, timeout=30, env=full_env)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def assert_warns(self, out: str) -> None:
        msg = json.loads(out)["systemMessage"]
        self.assertIn("verify-stop", msg)
        self.assertIn("not verified", msg)

    # --- the one case it exists for -------------------------------------------------
    def test_edit_then_claim_without_check_warns(self) -> None:
        code, out, _ = self.run_hook([prompt("fix it"), EDIT, result(), say(CLAIM)])
        self.assertEqual(code, 0)
        self.assert_warns(out)

    def test_bash_write_counts_as_a_change(self) -> None:
        for cmd in ("sed -i 's/a/b/' x.py", "cat > x.py <<'EOF'\nprint(1)\nEOF", "echo x=1 >> config.ini",
                    "Set-Content -Path x.ps1 -Value 1", "git apply fix.patch"):
            with self.subTest(cmd=cmd):
                code, out, _ = self.run_hook([prompt("go"), tool("Bash", command=cmd), result(), say(CLAIM)])
                self.assertEqual(code, 0)
                self.assert_warns(out)

    def test_last_assistant_message_in_payload_is_used(self) -> None:
        code, out, _ = self.run_hook([prompt("go"), EDIT, result(), say("Working on it.")],
                                     payload={"last_assistant_message": "All tests pass. Done."})
        self.assertEqual(code, 0)
        self.assert_warns(out)

    # --- proportional: silent whenever a check ran or nothing needs one --------------
    def test_check_after_last_edit_is_silent(self) -> None:
        self.assertEqual(self.run_hook([prompt("go"), EDIT, result(), TEST, result(), say(CLAIM)]), SILENT)

    def test_check_before_a_later_edit_does_not_count(self) -> None:
        _, out, _ = self.run_hook([prompt("go"), EDIT, result(), TEST, result(), EDIT, result(), say(CLAIM)])
        self.assert_warns(out)

    def test_recognised_check_commands(self) -> None:
        for cmd in ("npm test", "pnpm run test", "dotnet build Foo.sln", "dotnet test", "go test ./...",
                    "cargo check", "npx tsc --noEmit", "ruff check .", "python hooks/tests/test_x.py",
                    "Invoke-Pester", "python -m unittest", "python -m py_compile a.py"):
            with self.subTest(cmd=cmd):
                rc = self.run_hook([prompt("go"), EDIT, result(), tool("PowerShell", command=cmd), result(), say(CLAIM)])
                self.assertEqual(rc, SILENT)

    def test_no_change_this_turn_is_silent(self) -> None:
        self.assertEqual(self.run_hook([prompt("what is x?"), tool("Bash", command="grep -n x a.py"), result(),
                                        say("Done: x is defined at a.py:3.")]), SILENT)

    def test_redirects_to_null_and_arrows_are_not_writes(self) -> None:
        cmd = "git status 2>/dev/null; python - <<'EOF'\ndef f() -> None: pass\nEOF\nls > /dev/null; dir > $null"
        self.assertEqual(self.run_hook([prompt("go"), tool("Bash", command=cmd), result(), say(CLAIM)]), SILENT)

    def test_doc_only_changes_are_silent(self) -> None:
        for entry in (tool("Write", file_path="F:/reports/2026-10-27-audit.md", content="x"),
                      tool("Edit", file_path="notes/handover.MD", old_string="a", new_string="b"),
                      tool("Bash", command='cat "$SP/r7.md" >> RESULTS.md && echo posted'),
                      tool("Bash", command="echo '| row |' >> /f/reports/stage-a.md; grep -n x notes.txt"),
                      tool("Edit", file_path="F:/Claude-Tools/state/queue.yaml", old_string="a", new_string="b"),
                      tool("Write", file_path="C:/Temp/claude/s1/scratchpad/closeq.py", content="x"),
                      tool("Bash", command="sed -i 's/a/b/' C:/Temp/claude/s1/scratchpad/fix.py")):
            with self.subTest(entry=entry):
                self.assertEqual(self.run_hook([prompt("go"), entry, result(), say(CLAIM)]), SILENT)

    def test_shell_writes_without_a_named_code_file_are_silent(self) -> None:
        for cmd in ("mkdir -p /f/reports/run-1 && ls",
                    "sed -i 's/a/b/' notes.md; python -c \"import sys; sys.path.insert(0, 'x'); s.replace('a', 'b')\"",
                    'cp "$K/prod.css" "$K/check.css"', "git bundle create F:/backups/x.bundle --all; cp a.bundle b.bundle"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.run_hook([prompt("go"), tool("Bash", command=cmd), result(), say(CLAIM)]), SILENT)

    def test_doc_and_code_change_together_still_counts(self) -> None:
        _, out, _ = self.run_hook([prompt("go"), tool("Bash", command="python gen.py > out.md && sed -i 's/a/b/' a.py"),
                                   result(), say(CLAIM)])
        self.assert_warns(out)

    def test_redirecting_a_scripts_output_is_not_a_code_change(self) -> None:
        for cmd in ('python state/next.py 7 > "C:/Temp/out.txt"', 'cat a.md >> "$R"; grep -n x "$R"',
                    "dotnet --info > info.log 2>&1"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.run_hook([prompt("go"), tool("Bash", command=cmd), result(), say(CLAIM)]), SILENT)

    def test_angle_brackets_in_text_are_not_redirects(self) -> None:
        cmd = ("git commit -m \"$(cat <<'EOF'\nfix\n\nCo-Authored-By: Bot <noreply@example.com>\nEOF\n)\"; "
               "git log --oneline -2")
        self.assertEqual(self.run_hook([prompt("go"), tool("Bash", command=cmd), result(), say(CLAIM)]), SILENT)

    def test_edits_from_an_earlier_turn_are_ignored(self) -> None:
        self.assertEqual(self.run_hook([prompt("one"), EDIT, result(), say("ok"), prompt("status?"),
                                        say("Done, the change is in.")]), SILENT)

    def test_no_completion_claim_is_silent(self) -> None:
        self.assertEqual(self.run_hook([prompt("go"), EDIT, result(), say("Edited a.py; next I will run the tests.")]),
                         SILENT)

    def test_honest_disclosure_is_silent(self) -> None:
        for text in ("Done. Not verified: no test suite here.", "Fixed; untested.", "Done (not re-verified)."):
            with self.subTest(text=text):
                self.assertEqual(self.run_hook([prompt("go"), EDIT, result(), say(text)]), SILENT)

    def test_subagent_entries_do_not_count(self) -> None:
        side = dict(EDIT, isSidechain=True)
        self.assertEqual(self.run_hook([prompt("go"), side, say(CLAIM)]), SILENT)

    # --- never loops, never blocks, opt-out --------------------------------------------
    def test_stop_hook_active_is_silent(self) -> None:
        rc = self.run_hook([prompt("go"), EDIT, result(), say(CLAIM)], payload={"stop_hook_active": True})
        self.assertEqual(rc, SILENT)

    def test_opt_out_env(self) -> None:
        rc = self.run_hook([prompt("go"), EDIT, result(), say(CLAIM)], env={"VERIFY_STOP_HOOK": "0"})
        self.assertEqual(rc, SILENT)

    def test_never_emits_a_block_decision(self) -> None:
        _, out, _ = self.run_hook([prompt("go"), EDIT, result(), say(CLAIM)])
        self.assertNotIn("decision", json.loads(out))

    def test_fail_open_on_bad_input(self) -> None:
        for raw in (b"", b"not json", b"[]", json.dumps({"transcript_path": "Z:/nope/missing.jsonl"}).encode()):
            with self.subTest(raw=raw):
                code, out, _ = self.run_hook(None, raw=raw)
                self.assertEqual((code, out), (0, ""))

    def test_malformed_transcript_lines_are_skipped(self) -> None:
        tp = Path(self.tmp.name) / "bad.jsonl"
        lines = [json.dumps(prompt("go")), "{broken", json.dumps(EDIT), json.dumps(say(CLAIM))]
        tp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, out, _ = self.run_hook(None, raw=json.dumps({"transcript_path": str(tp)}).encode())
        self.assertEqual(code, 0)
        self.assert_warns(out)


if __name__ == "__main__":
    unittest.main()
