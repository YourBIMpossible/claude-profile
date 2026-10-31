"""Fixture tests for the weekly-full-audit / slop-audit-sweep run-control scripts.

Every test works in a fresh temporary directory with throw-away git
repositories. Nothing reads or writes a live audit root, a live scheduled task,
the live watchdog folder or a real ledger.

Run:  python -m unittest discover -s <skill>/tests -v

Optional integration tests need AUDIT_TEST_WORKSPACE_ROOT pointing at a
BIMpossible-Workspace checkout. They only READ scripts from it and run them
against temporary fixtures:
  * the HYG-13 header checker in --file mode against a rendered report
  * the audit watchdog (pwsh) against a temporary StatusRoot
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import re
import shlex
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Iterator

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_lib as L  # noqa: E402
import audit_run as R  # noqa: E402
import heartbeat as HB  # noqa: E402
import hyg8_drift as H8  # noqa: E402
import startup_context as SC  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def sh(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def make_repo(base: Path, name: str, origin: str | None = None) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "commit.gpgsign", "false")
    sh(repo, "config", "core.autocrlf", "false")
    if origin:
        sh(repo, "remote", "add", "origin", origin)
    commit(repo, "README.md", "seed\n")
    return repo


def commit(repo: Path, rel: str, text: str, msg: str = "c") -> str:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", msg)
    return sh(repo, "rev-parse", "HEAD")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def attest(method: Path) -> str:
    """What a dispatched child writes after loading a method: the file hash, plus a line hash for
    every non-blank line (so the fixture is valid whichever random line k dispatch picked)."""
    lines = [f"method-sha256: {method.as_posix()} {L.file_sha256(method)}\n"]
    for k, ln in enumerate(L.method_lines(method), start=1):
        if ln.strip():
            lines.append(f"method-line: {method.as_posix()} {k} {L.line_sha256(ln)}\n")
    return "".join(lines)


BRIEF_TEXT = "- Scope: repo r\n- Boundaries: inspection tier\n- Done means: AC-1\n- Authority: report only\n"


def ensure_brief(base: list[str]) -> None:
    """Record a filled-brief ledger once per run (dispatch refuses until `brief-done`)."""
    rid, sd = base[base.index("--run-id") + 1], Path(base[base.index("--state-dir") + 1])
    rec = L.load_record(sd / L.parse_run_id(rid).task_id / f"{rid}.json")
    if rec.get("briefs"):
        return
    led = sd / "fixture-ledgers" / f"{rid}.md"
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text(BRIEF_TEXT, encoding="utf-8")
    for argv in (("record", *base, "--ledger", str(led)), ("brief-done", *base, "--ledger", str(led))):
        code, _, err = run_cli(*argv)
        if code != 0:
            raise AssertionError(err)


def lib_brief(rec: dict[str, Any], d: Path) -> None:
    led = d / "brief.ledger.md"
    led.write_text(BRIEF_TEXT, encoding="utf-8")
    rec["ledgers"].append(str(led))
    L.brief_done(rec, str(led))


def dispatch(base: list[str], key: str, shard: str, method: Path, *outputs: Path) -> tuple[int, str, str]:
    ensure_brief(base)
    outs = [x for o in outputs for x in ("--output", str(o))]
    return run_cli("dispatch", *base, "--key", key, "--shard", shard, "--method", str(method), *outs)


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = R.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-fixture-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        self.wd = self.tmp / "watchdog"
        self.tools = self.tmp / "tools"
        (self.tools / "ctxcheck").mkdir(parents=True)
        (self.tools / "ctxcheck" / "ctxcheck.py").write_text("# fixture\n", encoding="utf-8")
        (self.tools / "local-audit").mkdir(parents=True)
        (self.tools / "local-audit" / "slop_prepass.py").write_text("# fixture\n", encoding="utf-8")
        self.grrp = self.tmp / "grrp.py"
        self.grrp.write_text("# fixture\n", encoding="utf-8")

    def repo(self, name: str, origin: str | None = None) -> Path:
        return make_repo(self.tmp, name, origin)

    def workspace(self) -> Path:
        ws = self.repo("ws", "https://github.com/Example/Workspace.git")
        for c in R.WEEKLY_CHECKERS:
            commit(ws, f"scripts/{c}", "// fixture\n")
        commit(ws, R.CHECKLIST_REL.as_posix(), "checklist")
        return ws

    def weekly_args(self, repo: Path, ws: Path, *extra: str) -> list[str]:
        return ["--task", "weekly-full-audit", "--root", f"repo={repo}", "--root", f"workspace={ws}",
                "--tools-root", str(self.tools), "--grrp", str(self.grrp), *extra]


# --------------------------------------------------------------------------- identities (rule 5)


class IdentityTests(unittest.TestCase):
    def test_run_id_shape_and_uniqueness(self) -> None:
        now = dt.datetime(2026, 10, 19, 6, 0, tzinfo=dt.timezone.utc)
        ids = {L.mint_run_id("weekly-full-audit", now) for _ in range(200)}
        self.assertEqual(len(ids), 200)
        for rid in ids:
            p = L.parse_run_id(rid)
            self.assertEqual(p.task_id, "weekly-full-audit")
            self.assertEqual(p.stamp, now)

    def test_unknown_task_and_malformed_id_refused(self) -> None:
        with self.assertRaises(L.AuditError):
            L.mint_run_id("weekly")
        for bad in ("", "weekly-full-audit-20261019T060000Z", "other-20261019T060000Z-abcdef",
                    "weekly-full-audit-20261019T060000Z-ABCDEF"):
            with self.assertRaises(L.AuditError):
                L.parse_run_id(bad)

    def test_slug_is_stable_for_a_run_and_prefix_checked(self) -> None:
        rid = L.mint_run_id("slop-audit-sweep")
        self.assertEqual(L.grrp_slug(rid), L.grrp_slug(rid))
        self.assertTrue(L.grrp_slug(rid).endswith(L.parse_run_id(rid).short))
        self.assertTrue(L.grrp_slug(rid, "slop-audit-claude-tools").startswith("slop-audit-claude-tools-"))
        with self.assertRaises(L.AuditError):
            L.grrp_slug(rid, "Bad Prefix")

    def test_exclusive_create_takes_next_free_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a = L.create_exclusive(Path(d), "weekly-full-audit_2026-10-19", ".md", "one")
            b = L.create_exclusive(Path(d), "weekly-full-audit_2026-10-19", ".md", "two")
            c = L.create_exclusive(Path(d), "weekly-full-audit_2026-10-19", ".md", "three")
            self.assertEqual([a.name, b.name, c.name], ["weekly-full-audit_2026-10-19.md",
                                                        "weekly-full-audit_2026-10-19-2.md",
                                                        "weekly-full-audit_2026-10-19-3.md"])
            self.assertEqual(a.read_text(encoding="utf-8"), "one")  # never overwritten

    def test_remote_identity_normalized(self) -> None:
        for url in ("https://github.com/Owner/Repo.git", "git@github.com:Owner/Repo.git",
                    "https://user:tok@GitHub.com/Owner/Repo/", "ssh://git@github.com/Owner/Repo"):
            self.assertEqual(L.normalize_remote(url), "github.com/Owner/Repo")


# --------------------------------------------------------------------------- protected paths (rule 1)


class ProtectedPathTests(unittest.TestCase):
    def test_protected_paths(self) -> None:
        for rel in (".env", "backend/.env.local", "backend/guard.py", "99_Archive/x.md",
                    "a/_backups/b.txt", "backend/alembic/versions/0001.py"):
            self.assertIsNotNone(L.protected_reason(rel), rel)
        for rel in ("backend/guardian.py", "docs/env.md", "alembic/env.py"):
            self.assertIsNone(L.protected_reason(rel), rel)

    def test_resolve_inside_refuses_escape_and_protected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(L.resolve_inside(root, "a/b.txt"), (root / "a" / "b.txt").resolve())
            for rel in ("../outside.txt", ".env", "backend/guard.py"):
                with self.assertRaises(L.AuditError):
                    L.resolve_inside(root, rel)


# --------------------------------------------------------------------------- baselines (rule 3)


class BaselineTests(Fixture):
    def report(self, d: Path, task: str, name: str, info: L.RootInfo, rev: str, stem: str,
               base: str | None = None, run_id: str | None = None, outcome: str | None = "ok") -> Path:
        rid = run_id or L.mint_run_id(task)
        if outcome is not None:
            rec = L.new_record(task, rid, {"deliver": "none"})
            rec["run"] = outcome
            L.save_record(L.record_path(self.state, task, rid), rec)
        entry = {"identity": info.identity, "path_arg": info.path_arg, "baseline_rev": base,
                 "head_rev": rev, "window": "full"}
        return L.create_exclusive(d, stem, ".md", L.render_front_matter(task, rid, {name: entry}) + "body\n")

    def test_no_prior_report_is_full_scope(self) -> None:
        repo = self.repo("r")
        bl = L.select_baseline(L.root_info("repo", repo), "weekly-full-audit", [self.tmp / "out"], "*.md")
        self.assertEqual((bl.window, bl.baseline_rev), ("full", None))
        self.assertIn("no baseline", bl.notes)

    def test_newest_by_ancestry_not_by_date_or_name(self) -> None:
        repo = self.repo("r")
        info0 = L.root_info("repo", repo)
        r1 = info0.head_rev
        r2 = commit(repo, "a.txt", "2\n")
        r3 = commit(repo, "a.txt", "3\n")
        out = self.tmp / "out"
        old_stamp = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        new_stamp = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
        # The report for r2 has an OLDER run_id and an alphabetically-earlier name; it still wins.
        self.report(out, "weekly-full-audit", "repo", info0, r2, "a",
                    run_id=L.mint_run_id("weekly-full-audit", old_stamp))
        self.report(out, "weekly-full-audit", "repo", info0, r1, "z",
                    run_id=L.mint_run_id("weekly-full-audit", new_stamp))
        info = L.root_info("repo", repo)
        bl = L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state)
        self.assertEqual(bl.baseline_rev, r2)
        self.assertEqual(bl.window, f"{r2}..{r3}")

    def test_unchanged_window_and_identity_filter(self) -> None:
        repo = self.repo("r", "https://github.com/O/R.git")
        info = L.root_info("repo", repo)
        out = self.tmp / "out"
        other = L.RootInfo("repo", info.path, info.toplevel, "github.com/O/Other", ".", info.head_rev)
        self.report(out, "weekly-full-audit", "repo", other, info.head_rev, "x")
        self.assertEqual(L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state).window, "full")
        self.report(out, "slop-audit-sweep", "repo", info, info.head_rev, "y")  # other task: ignored
        self.assertEqual(L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state).window, "full")
        self.report(out, "weekly-full-audit", "repo", info, info.head_rev, "w")
        self.assertEqual(L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state).window, "unchanged")

    def test_failed_run_and_unknown_commit_skipped(self) -> None:
        repo = self.repo("r")
        info = L.root_info("repo", repo)
        out = self.tmp / "out"
        rid = L.mint_run_id("weekly-full-audit")
        self.report(out, "weekly-full-audit", "repo", info, info.head_rev, "f", run_id=rid, outcome="failed")
        self.report(out, "weekly-full-audit", "repo", info, "0" * 40, "u")
        bl = L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state)
        self.assertEqual(bl.window, "full")
        self.assertTrue(any("run failed" in n for n in bl.notes))
        self.assertTrue(any("commit unknown" in n for n in bl.notes))

    def test_override_and_non_ancestor_uses_merge_base(self) -> None:
        repo = self.repo("r")
        base = L.head_rev(repo)
        sh(repo, "checkout", "-q", "-b", "side")
        side = commit(repo, "s.txt", "s\n")
        sh(repo, "checkout", "-q", "main")
        head = commit(repo, "m.txt", "m\n")
        info = L.root_info("repo", repo)
        bl = L.select_baseline(info, "weekly-full-audit", [], "*.md", override=side)
        self.assertEqual(bl.window, f"{base}..{head}")
        self.assertTrue(any("merge-base" in n for n in bl.notes))
        with self.assertRaises(L.AuditError):
            L.select_baseline(info, "weekly-full-audit", [], "*.md", override="deadbeef")

    def test_legacy_or_unrecorded_report_bootstraps_full_scope(self) -> None:
        # Regression (1a.iv): a prior report without a successful run record is never a baseline;
        # nothing is inferred from its date. The run inspects full scope as a bootstrap.
        repo = self.repo("r")
        info = L.root_info("repo", repo)
        out = self.tmp / "out"
        self.report(out, "weekly-full-audit", "repo", info, info.head_rev, "legacy", outcome=None)
        bl = L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state)
        self.assertEqual((bl.window, bl.baseline_rev, bl.trust), ("full", None, "bootstrap"))
        self.assertTrue(any("no run record" in n for n in bl.notes))
        self.assertTrue(any(n.startswith("bootstrap:") for n in bl.notes))
        no_state = L.select_baseline(info, "weekly-full-audit", [out], "*.md")
        self.assertEqual((no_state.window, no_state.trust), ("full", "bootstrap"))
        rid = L.mint_run_id("weekly-full-audit")
        self.report(out, "weekly-full-audit", "repo", info, info.head_rev, "unfinished", run_id=rid, outcome=None)
        rec = L.new_record("weekly-full-audit", rid, {"deliver": "none"})
        L.save_record(L.record_path(self.state, "weekly-full-audit", rid), rec)  # run never completed
        bl = L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state)
        self.assertEqual(bl.trust, "bootstrap")
        self.assertTrue(any("run not successful" in n for n in bl.notes))
        self.report(out, "weekly-full-audit", "repo", info, info.head_rev, "good", outcome="degraded")
        bl = L.select_baseline(info, "weekly-full-audit", [out], "*.md", state_dir=self.state)
        self.assertEqual((bl.window, bl.trust), ("unchanged", "recorded"))

    def test_override_is_labelled_not_established(self) -> None:
        repo = self.repo("r")
        base = L.head_rev(repo)
        commit(repo, "a.txt", "2\n")
        bl = L.select_baseline(L.root_info("repo", repo), "weekly-full-audit", [], "*.md", override=base)
        self.assertEqual(bl.trust, "override")
        self.assertTrue(any("not established by a successful run record" in n for n in bl.notes))


# --------------------------------------------------------------------------- run record states (rules 6, 7)


class StateMachineTests(unittest.TestCase):
    def rec(self, **flags: Any) -> dict[str, Any]:
        return L.new_record("weekly-full-audit", L.mint_run_id("weekly-full-audit"), {"deliver": "none", **flags})

    def test_degrading_coverage_cannot_be_softened(self) -> None:
        r = self.rec()
        L.set_inspection(r, "running")
        L.set_coverage(r, "handoff", "handoff-pending", "HANDOFF PENDING: --handoff not given")
        L.set_coverage(r, "sec", "lost", "lens did not return")
        for key in ("handoff", "sec"):
            with self.assertRaises(L.AuditError):
                L.set_coverage(r, key, "complete")
        L.set_coverage(r, "sec", "lost", "still lost, new note")  # degrading -> degrading is allowed
        L.set_inspection(r, "complete")
        with self.assertRaises(L.AuditError):
            L.set_run(r, "ok")
        L.set_run(r, "degraded")

    def test_separate_states_happy_path(self) -> None:
        r = self.rec()
        L.set_inspection(r, "running")
        L.set_coverage(r, "lens-1", "complete")
        L.set_coverage(r, "addins", "not-run-optional", "NOT RUN (addins root not present)")
        L.set_inspection(r, "complete")
        L.set_run(r, "ok")  # declared gaps leave the run ok
        L.set_gate(r, {"verdict": "PASS"})
        L.publish(r)
        L.set_delivery(r, "skipped")
        self.assertFalse(L.start_remediation(r, []))
        st = r["states"]
        self.assertEqual((r["run"], st["publication"], st["delivery"]["state"], st["remediation"],
                          st["remediation_reason"]), ("ok", "published", "skipped", "not-run", "flag off"))

    def test_run_never_softer_than_coverage(self) -> None:
        r = self.rec()
        L.set_inspection(r, "running")
        L.set_coverage(r, "handoff", "handoff-pending", "HANDOFF PENDING")
        L.set_inspection(r, "complete")
        with self.assertRaises(L.AuditError):
            L.set_run(r, "ok")
        L.set_run(r, "degraded")
        with self.assertRaises(L.AuditError):
            L.set_run(r, "failed")  # declared once

    def test_ordering_guards(self) -> None:
        r = self.rec()
        with self.assertRaises(L.AuditError):
            L.set_coverage(r, "x", "complete")  # not running
        L.set_inspection(r, "running")
        with self.assertRaises(L.AuditError):
            L.set_run(r, "ok")  # inspection not ended
        with self.assertRaises(L.AuditError):
            L.set_gate(r, {})
        L.set_inspection(r, "complete")
        L.set_run(r, "ok")
        with self.assertRaises(L.AuditError):
            L.publish(r)  # no gate (rule 6)
        with self.assertRaises(L.AuditError):
            L.set_delivery(r, "skipped")  # before publication
        L.set_gate(r, {"verdict": "PASS"})
        L.publish(r)
        with self.assertRaises(L.AuditError):
            L.set_delivery(r, "delivered")  # mode none never delivers
        with self.assertRaises(L.AuditError):
            L.set_coverage(r, "late", "complete")  # coverage frozen after inspection

    def test_failed_run_cannot_publish(self) -> None:
        r = self.rec()
        L.set_inspection(r, "running")
        L.set_inspection(r, "failed")
        L.set_run(r, "failed")
        L.set_gate  # gate cannot even be recorded: inspection is not complete
        with self.assertRaises(L.AuditError):
            L.publish(r)

    def test_finalize_failed_before_and_after_publication(self) -> None:
        r = self.rec()
        L.set_inspection(r, "running")
        L.finalize_failed(r, "lens agent crashed")
        st = r["states"]
        self.assertEqual((st["inspection"], r["run"], st["publication"], st["delivery"]["state"]),
                         ("failed", "failed", "not-published", "skipped"))
        self.assertEqual(r["failure_reason"], "lens agent crashed")

        r2 = self.rec()
        L.set_inspection(r2, "running")
        L.set_inspection(r2, "complete")
        L.set_run(r2, "ok")
        L.set_gate(r2, {})
        L.publish(r2)
        L.set_delivery(r2, "skipped")
        r2["states"]["remediation"] = "running"  # as start_remediation would after D1
        L.finalize_failed(r2, "fix crashed")
        self.assertEqual((r2["run"], r2["states"]["publication"], r2["states"]["remediation"]),
                         ("ok", "published", "failed"))  # published outcome kept; remediation failed

    def test_autofix_requires_activation(self) -> None:
        r = self.rec(autofix=True)
        L.set_inspection(r, "running")
        L.set_inspection(r, "complete")
        L.set_run(r, "ok")
        L.set_gate(r, {})
        L.publish(r)
        L.set_delivery(r, "skipped")
        self.assertFalse(L.ACTIVATION["autofix"])
        with self.assertRaises(L.AuditError):
            L.start_remediation(r, [])

    def test_dirty_tree_and_ordering_when_activated(self) -> None:
        r = self.rec(autofix=True)
        orig = dict(L.ACTIVATION)
        self.addCleanup(L.ACTIVATION.update, orig)
        L.ACTIVATION["autofix"] = True
        L.set_inspection(r, "running")
        L.set_inspection(r, "complete")
        L.set_run(r, "ok")
        with self.assertRaises(L.AuditError):
            L.start_remediation(r, [])  # before publication
        L.set_gate(r, {})
        L.publish(r)
        L.set_delivery(r, "skipped")
        self.assertFalse(L.start_remediation(r, [" M a.txt"]))
        self.assertEqual(r["states"]["remediation_reason"], "dirty tree")


# --------------------------------------------------------------------------- rollback (rule 8, AC-9)


class RollbackTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.repo("target")
        commit(self.root, "src/a.py", "print('pre')\n")
        commit(self.root, "src/b.py", "B = 1\n")
        self.fdir = self.tmp / "fixes"

    def apply_fix(self, fix_id: str) -> None:
        L.fix_snapshot(self.root, self.fdir, fix_id, ["src/a.py", "src/b.py", "src/new.py"])
        (self.root / "src" / "a.py").write_text("print('post')\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("B = 2\n", encoding="utf-8")
        (self.root / "src" / "new.py").write_text("NEW = True\n", encoding="utf-8")
        L.fix_post(self.root, self.fdir, fix_id)

    def test_rollback_restores_when_post_state_unchanged(self) -> None:
        self.apply_fix("f1")
        entry = L.fix_rollback(self.root, self.fdir, "f1")
        self.assertEqual(entry["status"], "reverted")
        self.assertEqual((self.root / "src" / "a.py").read_text(encoding="utf-8"), "print('pre')\n")
        self.assertFalse((self.root / "src" / "new.py").exists())
        self.assertEqual(L.dirty(self.root), [])
        self.assertEqual(L.remediation_outcome([entry]), "reverted")

    def test_rollback_refused_on_concurrent_change_is_failure(self) -> None:
        self.apply_fix("f2")
        (self.root / "src" / "b.py").write_text("B = 3  # someone else\n", encoding="utf-8")
        entry = L.fix_rollback(self.root, self.fdir, "f2")
        self.assertEqual(entry["status"], "rollback-refused (concurrent change)")
        self.assertEqual(entry["files"], ["src/b.py"])
        # all-or-nothing: no file restored, concurrent edit preserved
        self.assertEqual((self.root / "src" / "a.py").read_text(encoding="utf-8"), "print('post')\n")
        self.assertEqual((self.root / "src" / "b.py").read_text(encoding="utf-8"), "B = 3  # someone else\n")
        self.assertTrue((self.root / "src" / "new.py").exists())
        self.assertEqual(L.read_json(self.fdir / "f2" / "manifest.json")["state"], "rollback-refused")
        # a refused rollback is a remediation FAILURE, never ok or reverted
        self.assertEqual(L.remediation_outcome([{"status": "reverted"}, entry]), "failed")
        with self.assertRaises(L.AuditError):
            L.fix_rollback(self.root, self.fdir, "f2")  # no second attempt on a refused fix

    def test_snapshot_refuses_protected_and_escaping_paths(self) -> None:
        for bad in (".env", "backend/guard.py", "../escape.txt"):
            with self.assertRaises(L.AuditError):
                L.fix_snapshot(self.root, self.fdir, f"p{abs(hash(bad)) % 1000}", [bad])
        with self.assertRaises(L.AuditError):
            L.fix_snapshot(self.root, self.fdir, "../evil", ["src/a.py"])

    def test_cli_rollback_refusal_marks_remediation_failed(self) -> None:
        ws = self.workspace()
        orig = dict(L.ACTIVATION)
        self.addCleanup(L.ACTIVATION.update, orig)
        L.ACTIVATION["autofix"] = True  # simulated D1 activation, fixture only
        code, out, err = run_cli("start", *self.weekly_args(self.root, ws, "--deliver", "none", "--autofix",
                                                            "--handoff"),
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        # --handoff needs audit-resolution in the repo; the fixture repo lacks it -> preflight fails
        self.assertEqual(code, 2, out + err)
        code, out, err = run_cli("start", *self.weekly_args(self.root, ws, "--deliver", "none", "--autofix"),
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, out + err)
        rid = json.loads(out)["run_id"]
        base = ["--run-id", rid, "--state-dir", str(self.state)]
        self.assertEqual(run_cli("record", *base, "--inspection", "complete")[0], 0)
        self.assertEqual(run_cli("record", *base, "--run", "degraded")[0], 0)
        gate = self.tmp / "gate.json"
        gate.write_text('{"verdict": "PASS"}', encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--gate-file", str(gate))[0], 0)
        self.assertEqual(run_cli("publish", *base)[0], 0)
        self.assertEqual(run_cli("deliver", *base, "--state", "skipped")[0], 0)
        self.assertEqual(run_cli("finish", *base, "--watchdog-dir", str(self.wd))[0], 0)
        code, out, _ = run_cli("remediation", *base, "start", "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, out)
        self.assertEqual(run_cli("fix", *base, "snapshot", "--fix-id", "x1", "--file", "src/a.py")[0], 0)
        (self.root / "src" / "a.py").write_text("print('fixed')\n", encoding="utf-8")
        self.assertEqual(run_cli("fix", *base, "post", "--fix-id", "x1")[0], 0)
        (self.root / "src" / "a.py").write_text("print('concurrent')\n", encoding="utf-8")
        code, out, _ = run_cli("fix", *base, "rollback", "--fix-id", "x1")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "rollback-refused (concurrent change)")
        code, out, _ = run_cli("remediation", *base, "finish", "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["remediation"], "failed")
        hb = L.read_json(HB.heartbeat_path(self.wd, "weekly-full-audit", rid))
        self.assertEqual((hb["outcome"], hb["remediation"]), ("degraded", "failed"))
        self.assertEqual((self.root / "src" / "a.py").read_text(encoding="utf-8"), "print('concurrent')\n")


# --------------------------------------------------------------------------- slop reuse (rule 4, AC-8)


class ReuseTests(Fixture):
    def sweep(self, repo: Path, *, base: str | None = None, run: str = "ok", cov: str = "complete",
              head: str | None = None, extra_root: bool = False, record: bool = True) -> Path:
        info = L.root_info(repo.name, repo)
        rid = L.mint_run_id("slop-audit-sweep")
        roots = {repo.name: L.root_entry(info, base, "full")}
        if head:
            roots[repo.name]["head_rev"] = head
        if extra_root:
            roots["other"] = L.root_entry(info, base, "full")
        path = L.create_exclusive(repo / "audits", f"{L.run_date(rid)}__slop-audit", ".md",
                                  L.render_front_matter("slop-audit-sweep", rid, roots) + "report\n")
        if record:
            rec = L.new_record("slop-audit-sweep", rid, {"deliver": "none"})
            rec["run"] = run
            rec["coverage"][repo.name] = {"status": cov, "note": ""}
            L.save_record(L.record_path(self.state, "slop-audit-sweep", rid), rec)
        return path

    def check(self, report: Path, repo: Path, weekly_base: str | None) -> tuple[bool, list[str]]:
        return L.reuse_check(report, self.state, L.root_info(repo.name, repo), weekly_base)

    def test_full_scope_complete_sweep_is_reused(self) -> None:
        repo = self.repo("claude-tools")
        ok, reasons = self.check(self.sweep(repo), repo, L.head_rev(repo))
        self.assertTrue(ok, reasons)

    def test_each_mismatch_refuses_reuse(self) -> None:
        repo = self.repo("claude-tools")
        r0 = L.head_rev(repo)
        r1 = commit(repo, "x.txt", "1\n")
        cases = {
            "head_rev differs": self.sweep(repo, head=r0),
            "run record says run: degraded": self.sweep(repo, run="degraded"),
            "root coverage not complete": self.sweep(repo, cov="lost"),
            "scope differs": self.sweep(repo, extra_root=True),
            "run record missing": self.sweep(repo, record=False),
            "window narrower: sweep is incremental": self.sweep(repo, base=r1),
        }
        for expect, report in cases.items():
            ok, reasons = self.check(report, repo, None if "incremental" in expect else r0)
            self.assertFalse(ok, expect)
            self.assertTrue(any(expect in r for r in reasons), (expect, reasons))

    def test_incremental_sweep_window_must_cover_weekly_window(self) -> None:
        repo = self.repo("claude-tools")
        r0 = L.head_rev(repo)
        r1 = commit(repo, "x.txt", "1\n")
        commit(repo, "x.txt", "2\n")
        self.assertTrue(self.check(self.sweep(repo, base=r0), repo, r1)[0])  # sweep starts earlier: covers
        ok, reasons = self.check(self.sweep(repo, base=r1), repo, r0)  # sweep starts later: narrower
        self.assertFalse(ok)
        self.assertTrue(any("window narrower" in r for r in reasons))

    def test_cli_exit_codes(self) -> None:
        repo = self.repo("claude-tools")
        good = self.sweep(repo)
        code, out, _ = run_cli("reuse-check", "--sweep-report", str(good), "--state-dir", str(self.state),
                               "--root", f"claude-tools={repo}", "--weekly-baseline", L.head_rev(repo))
        self.assertEqual((code, json.loads(out)["action"]), (0, "reuse"))
        bad = self.sweep(repo, cov="lost")
        code, out, _ = run_cli("reuse-check", "--sweep-report", str(bad), "--state-dir", str(self.state),
                               "--root", f"claude-tools={repo}")
        self.assertEqual((code, json.loads(out)["action"]), (1, "run /slop-audit nested"))


# --------------------------------------------------------------------------- preflight (rule 2) + D1/D2


class PreflightTests(Fixture):
    def pf(self, *argv: str) -> tuple[int, dict[str, Any]]:
        code, out, _ = run_cli("preflight", *argv)
        return code, json.loads(out)

    def test_weekly_minimal_passes_and_reports_not_run(self) -> None:
        repo, ws = self.repo("app"), self.workspace()
        code, res = self.pf(*self.weekly_args(repo, ws, "--deliver", "none"))
        self.assertEqual(code, 0, res["failures"])
        self.assertEqual(res["optional"]["addins-root"]["note"], "NOT RUN (addins-root not present)")
        self.assertFalse(res["optional"]["breach-chain"]["present"])
        self.assertIn("security-engineer", res["personas"])

    def test_required_roots_and_tools(self) -> None:
        repo = self.repo("app")
        code, res = self.pf("--task", "weekly-full-audit", "--root", f"repo={repo}", "--deliver", "none",
                            "--tools-root", str(self.tmp / "nowhere"), "--grrp", str(self.tmp / "no.py"))
        self.assertEqual(code, 2)
        joined = " | ".join(res["failures"])
        for frag in ("required root 'workspace'", "ctxcheck/ctxcheck.py not found", "grrp.py not found"):
            self.assertIn(frag, joined)

    def test_non_git_root_is_hard_failure(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        code, res = self.pf("--task", "slop-audit-sweep", "--root", f"plain={plain}", "--deliver", "none",
                            "--tools-root", str(self.tools), "--grrp", str(self.grrp))
        self.assertEqual(code, 2)
        self.assertTrue(any("not a git checkout" in f for f in res["failures"]))

    def test_deliver_required_file_activated_gmail_refused_naming_d2(self) -> None:
        repo, ws = self.repo("app"), self.workspace()
        code, res = self.pf(*self.weekly_args(repo, ws))
        self.assertIn("--deliver is required", res["failures"])
        code, res = self.pf(*self.weekly_args(repo, ws, "--deliver", "gmail-draft"))
        self.assertEqual(code, 2)
        self.assertTrue(any("D2" in f for f in res["failures"]), res["failures"])
        code, res = self.pf(*self.weekly_args(repo, ws, "--deliver", "file"))
        self.assertEqual(code, 0, res["failures"])  # D2 owner decision 2026-10-30: file only
        code, res = self.pf("--task", "slop-audit-sweep", "--root", f"app={repo}", "--deliver", "file",
                            "--tools-root", str(self.tools), "--grrp", str(self.grrp))
        self.assertEqual(code, 0, res["failures"])
        self.assertFalse(L.ACTIVATION["autofix"])  # D1 owner decision 2026-10-30: dropped
        self.assertFalse(L.ACTIVATION["deliver:gmail-draft"])

    def test_autofix_refused_naming_d1(self) -> None:
        repo, ws = self.repo("app"), self.workspace()
        code, res = self.pf(*self.weekly_args(repo, ws, "--deliver", "none", "--autofix"))
        self.assertEqual(code, 2)
        self.assertTrue(any("D1" in f for f in res["failures"]))
        code, res = self.pf("--task", "slop-audit-sweep", "--root", f"app={repo}", "--deliver", "none",
                            "--autofix", "--tools-root", str(self.tools), "--grrp", str(self.grrp))
        self.assertTrue(any("only in weekly-full-audit" in f for f in res["failures"]))

    def test_unusable_optional_addins_root_is_not_run_not_failure(self) -> None:
        repo, ws = self.repo("app"), self.workspace()
        code, res = self.pf(*self.weekly_args(repo, ws, "--deliver", "none", "--root",
                                              f"addins={self.tmp / 'missing'}"))
        self.assertEqual(code, 0, res["failures"])
        self.assertIn("NOT RUN (addins root unusable", res["optional"]["addins-root"]["note"])

    def test_missing_checkers_fail(self) -> None:
        repo = self.repo("app")
        ws = self.repo("ws-bare")
        code, res = self.pf(*self.weekly_args(repo, ws, "--deliver", "none"))
        self.assertEqual(code, 2)
        self.assertTrue(any("checkers missing" in f for f in res["failures"]))

    def test_start_on_failed_preflight_finalizes_failed(self) -> None:
        repo = self.repo("app")
        code, out, _ = run_cli("start", "--task", "weekly-full-audit", "--root", f"repo={repo}",
                               "--deliver", "gmail-draft", "--tools-root", str(self.tools), "--grrp",
                               str(self.grrp), "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 2)
        res = json.loads(out)
        rec = L.load_record(Path(res["record"]))
        self.assertEqual((rec["run"], rec["states"]["publication"], rec["states"]["inspection"]),
                         ("failed", "not-published", "failed"))
        hb = L.read_json(HB.heartbeat_path(self.wd, "weekly-full-audit", res["run_id"]))
        self.assertEqual(hb["outcome"], "failed")
        self.assertTrue(hb["finished_at"])
        self.assertIn("preflight", hb["note"])


class PersonaDiscoveryTests(unittest.TestCase):
    def test_project_agent_wins_over_user_agent_and_absent_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home, repo = Path(d) / "home", Path(d) / "repo"
            (home / ".claude" / "agents").mkdir(parents=True)
            (repo / ".claude" / "agents").mkdir(parents=True)
            (home / ".claude" / "agents" / "tech-lead.md").write_text("u", encoding="utf-8")
            (home / ".claude" / "agents" / "qa-engineer.md").write_text("u", encoding="utf-8")
            (repo / ".claude" / "agents" / "qa-engineer.md").write_text("p", encoding="utf-8")
            found = R.discover_personas([repo], home)
            self.assertIn(str(home), found["tech-lead"] or "")
            self.assertIn(str(repo), found["qa-engineer"] or "")
            self.assertIsNone(found["bim-manager"])
            self.assertEqual(sorted(found), sorted(R.PERSONAS))


# --------------------------------------------------------------------------- end-to-end weekly (fixture)


class WeeklyRunTests(Fixture):
    def test_full_flow_without_handoff_is_degraded_and_published_after_gate(self) -> None:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        code, out, err = run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none"),
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        started = json.loads(out)
        rid = started["run_id"]
        self.assertEqual(started["coverage"]["handoff"]["status"], "handoff-pending")
        self.assertEqual(started["coverage"]["autofix"]["status"], "not-run-flag-off")
        base = ["--run-id", rid, "--state-dir", str(self.state)]
        outdir = self.tmp / "reports"
        for name, path in (("repo", repo), ("workspace", ws)):
            code, out, err = run_cli("baseline", "--root", f"{name}={path}", "--report-dir", str(outdir),
                                     "--pattern", "weekly-full-audit_*.md", *base)
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["window"], "full")
        body = self.tmp / "body.md"
        body.write_text("# Weekly full audit\n", encoding="utf-8")
        stem = f"weekly-full-audit_{L.run_date(rid)}"
        p1 = run_cli("write-report", *base, "--dir", str(outdir), "--stem", stem, "--body-file", str(body))[1].strip()
        p2 = run_cli("write-report", *base, "--dir", str(outdir), "--stem", stem, "--body-file", str(body))[1].strip()
        self.assertTrue(p2.endswith(f"{stem}-2.md"))
        fm = L.parse_front_matter(Path(p1).read_text(encoding="utf-8"))
        assert fm is not None
        self.assertEqual(fm["run_id"], rid)
        self.assertEqual(fm["roots"]["repo"]["identity"], "github.com/O/App")
        code, _, err = run_cli("publish", *base)
        self.assertEqual(code, 2)  # before run outcome / gate
        self.assertEqual(run_cli("record", *base, "--coverage", "lens-1=complete", "--inspection", "complete")[0], 0)
        code, _, err = run_cli("record", *base, "--run", "ok")
        self.assertEqual(code, 2)
        self.assertIn("softer", err)
        self.assertEqual(run_cli("record", *base, "--run", "degraded", "--ledger", str(_touch(self.tmp / "l.ledger.md")))[0], 0)
        self.assertEqual(run_cli("publish", *base)[0], 2)  # still no gate
        gate = self.tmp / "gate.json"
        gate.write_text('{"verdict": "PASS"}', encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--gate-file", str(gate))[0], 0)
        self.assertEqual(run_cli("publish", *base)[0], 0)
        self.assertEqual(run_cli("deliver", *base, "--state", "delivered")[0], 2)  # D2 not activated
        self.assertEqual(run_cli("deliver", *base, "--state", "skipped")[0], 0)
        self.assertEqual(run_cli("finish", *base, "--watchdog-dir", str(self.wd))[0], 0)
        code, out, _ = run_cli("remediation", *base, "start", "--watchdog-dir", str(self.wd))
        self.assertEqual((code, json.loads(out)["reason"]), (1, "flag off"))
        hb = L.read_json(HB.heartbeat_path(self.wd, "weekly-full-audit", rid))
        self.assertEqual((hb["outcome"], hb["remediation"]), ("degraded", "not-run"))
        self.assertIn("HANDOFF PENDING", hb["note"])
        self.assertEqual(L.dirty(repo), [])  # inspection wrote nothing into the target
        # next run: baseline now comes from the report just written
        commit(repo, "n.txt", "n\n")
        code, out, _ = run_cli("baseline", "--root", f"repo={repo}", "--report-dir", str(outdir),
                               "--pattern", "weekly-full-audit_*.md", "--task-id", "weekly-full-audit",
                               "--state-dir", str(self.state))
        self.assertEqual(json.loads(out)["baseline_rev"], started_head(fm))

    def test_finalize_from_running_inspection(self) -> None:
        repo, ws = self.repo("app"), self.workspace()
        code, out, _ = run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none", "--watchdog-task",
                                                          "bimpossible-weekly-full-audit"),
                               "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0)
        rid = json.loads(out)["run_id"]
        code, out, _ = run_cli("finalize", "--run-id", rid, "--state-dir", str(self.state), "--reason",
                               "context exhausted", "--watchdog-dir", str(self.wd), "--watchdog-task",
                               "bimpossible-weekly-full-audit")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["run"], "failed")
        hb = L.read_json(HB.heartbeat_path(self.wd, "bimpossible-weekly-full-audit", rid))
        self.assertEqual((hb["outcome"], hb["task"], hb["task_id"]),
                         ("failed", "bimpossible-weekly-full-audit", "weekly-full-audit"))

    def test_report_refused_on_protected_in_root_path(self) -> None:
        repo, ws = self.repo("app"), self.workspace()
        rid = json.loads(run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none"), "--state-dir",
                                 str(self.state), "--watchdog-dir", str(self.wd))[1])["run_id"]
        base = ["--run-id", rid, "--state-dir", str(self.state)]
        run_cli("baseline", "--root", f"repo={repo}", "--pattern", "x*.md", *base)
        body = self.tmp / "b.md"
        body.write_text("x\n", encoding="utf-8")
        code, _, err = run_cli("write-report", *base, "--dir", str(repo / "99_Archive"), "--stem", "r",
                               "--body-file", str(body), "--roots", "repo", "--in-root", "repo")
        self.assertEqual(code, 2)
        self.assertIn("protected", err)
        self.assertFalse((repo / "99_Archive").exists())


def started_head(fm: dict[str, Any]) -> str:
    return str(fm["roots"]["repo"]["head_rev"])


# --------------------------------------------------------------------------- heartbeat schema


class HeartbeatTests(unittest.TestCase):
    def test_schema_and_phase_order(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            rid = L.mint_run_id("slop-audit-sweep")
            with self.assertRaises(L.AuditError):
                HB.finish(wd, "slop-audit-sweep", rid, "ok")  # not started
            p = HB.start(wd, "slop-audit-sweep", rid)
            with self.assertRaises(L.AuditError):
                HB.start(wd, "slop-audit-sweep", rid)
            with self.assertRaises(L.AuditError):
                HB.remediation_update(wd, "slop-audit-sweep", rid, "ok")  # before finish
            HB.progress(wd, "slop-audit-sweep", rid, "2 of 4 roots")
            self.assertTrue(L.read_json(p)["note"].startswith(HB.PRELIMINARY))
            HB.finish(wd, "slop-audit-sweep", rid, "ok", [str(wd / "ledger.json")], "")
            hb = L.read_json(p)
            for key in ("task", "run_id", "started_at", "finished_at", "outcome", "ledgers", "note"):
                self.assertIn(key, hb)
            self.assertTrue(hb["started_at"].endswith("Z") and hb["finished_at"].endswith("Z"))
            self.assertEqual(hb["outcome"], "ok")
            self.assertFalse(hb["preliminary"])
            self.assertFalse(p.read_bytes().startswith(b"\xef\xbb\xbf"))
            with self.assertRaises(L.AuditError):
                HB.progress(wd, "slop-audit-sweep", rid, "late")
            with self.assertRaises(L.AuditError):
                HB.finish(wd, "slop-audit-sweep", rid, "ok")

    def test_bad_values_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            rid = L.mint_run_id("weekly-full-audit")
            HB.start(wd, "weekly-full-audit", rid)
            with self.assertRaises(L.AuditError):
                HB.finish(wd, "weekly-full-audit", rid, "passed")
            with self.assertRaises(L.AuditError):
                HB.heartbeat_path(wd, "../x", rid)


# --------------------------------------------------------------------------- HYG-8 and startup context


class Hyg8Tests(unittest.TestCase):
    def test_banner_crlf_bom_and_drift(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            live, base = Path(d) / "live", Path(d) / "base"
            for t in ("a", "b", "c"):
                (base / t).mkdir(parents=True)
            (live / "a").mkdir(parents=True)
            (live / "b").mkdir(parents=True)
            (live / "a" / "SKILL.md").write_bytes(b"\xef\xbb\xbfline1\r\nline2\r\n\r\n")
            (base / "a" / "SKILL.md").write_text("<!-- snapshot banner\nmulti -->\nline1\nline2\n", encoding="utf-8")
            (live / "b" / "SKILL.md").write_text("new\n", encoding="utf-8")
            (base / "b" / "SKILL.md").write_text("old\n", encoding="utf-8")
            res = {r.task_id: r for r in H8.check(live, base)}
            self.assertEqual(res["a"].status, "ok")
            self.assertEqual(res["b"].status, "drift")
            self.assertTrue(any(ln.startswith("+new") for ln in res["b"].diff))
            self.assertEqual(res["c"].status, "unreadable")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(H8.main(["--live-root", str(live), "--baseline-root", str(base)]), 1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(H8.main(["--live-root", str(live), "--baseline-root", str(Path(d) / "x")]), 2)


class StartupContextTests(unittest.TestCase):
    def test_imports_rules_memory_and_northstar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            user = root / "userclaude"
            (user / "rules").mkdir(parents=True)
            (user / "CLAUDE.md").write_text("@extra.md\n`@ignored.md`\n```\n@fenced.md\n```\n", encoding="utf-8")
            (user / "extra.md").write_text("x\n" * 170, encoding="utf-8")
            (user / "ignored.md").write_text("no\n", encoding="utf-8")
            (user / "fenced.md").write_text("no\n", encoding="utf-8")
            (user / "rules" / "global.md").write_text("always\n", encoding="utf-8")
            (user / "rules" / "scoped.md").write_text("---\npaths: src/**\n---\n", encoding="utf-8")
            repo = root / "repo"
            (repo / ".claude").mkdir(parents=True)
            (repo / "CLAUDE.md").write_text("y\n" * 205, encoding="utf-8")
            (repo / "NORTHSTAR.md").write_text("status: active\n", encoding="utf-8")
            (repo / "NORTHSTAR.x.draft.md").write_text("draft\n", encoding="utf-8")
            (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [
                {"hooks": [{"command": "python", "args": ["northstar_inject.py"]}, {"command": "other.ps1"}]}]}}),
                encoding="utf-8")
            mem = user / "projects" / SC.project_slug(repo) / "memory"
            mem.mkdir(parents=True)
            (mem / "MEMORY.md").write_text("m\n" * 201, encoding="utf-8")
            res = SC.measure_repo(repo, user)
            by_path = {Path(r["path"]).name: r for r in res["rows"]}
            self.assertIn("extra.md", by_path)
            self.assertNotIn("ignored.md", by_path)
            self.assertNotIn("fenced.md", by_path)
            self.assertIn("global.md", by_path)
            self.assertNotIn("scoped.md", by_path)
            self.assertEqual(by_path["extra.md"]["cls"], "NEAR")
            self.assertEqual(by_path["CLAUDE.md"]["cls"] if by_path["CLAUDE.md"]["kind"] == "project" else "OVER",
                             "OVER")
            self.assertEqual(by_path["MEMORY.md"]["cls"], "TRUNCATED")
            self.assertIn("NORTHSTAR.md", by_path)
            self.assertEqual(res["excluded_drafts"], ["NORTHSTAR.x.draft.md"])
            self.assertEqual(res["hooks_not_measured"], ["SessionStart: other.ps1"])
            self.assertEqual(res["total"]["worst"], "TRUNCATED")
            self.assertIn("TOTAL files=", SC.render(res))


# --------------------------------------------------------------------------- optional integration


WS = os.environ.get("AUDIT_TEST_WORKSPACE_ROOT")


@unittest.skipUnless(WS and shutil.which("node"), "AUDIT_TEST_WORKSPACE_ROOT and node required")
class HeaderCheckerIntegration(unittest.TestCase):
    """The live HYG-13 checker accepts a report rendered with our front matter and -N name."""

    def test_front_matter_keeps_header_lines_within_checker_window(self) -> None:
        checker = Path(WS or "") / "scripts" / "check-audit-report-header.mjs"
        rid = L.mint_run_id("weekly-full-audit")
        roots = {"repo": {"identity": "github.com/O/App", "path_arg": ".", "baseline_rev": None,
                          "head_rev": "a" * 40, "window": "full"}}
        body = ("# Weekly full audit\n\n"
                "Docs-hygiene: BIMpossible G3 ✓ G4 ✓ · Workspace G3 ✓ G4 ✗\n\nbody\n")
        with tempfile.TemporaryDirectory() as d:
            p = L.create_exclusive(Path(d), f"weekly-full-audit_{L.run_date(rid)}", ".md",
                                   L.render_front_matter("weekly-full-audit", rid, roots) + body)
            p2 = L.create_exclusive(Path(d), f"weekly-full-audit_{L.run_date(rid)}", ".md",
                                    L.render_front_matter("weekly-full-audit", rid, roots) + body)
            for path in (p, p2):
                proc = subprocess.run(["node", str(checker), "--file", str(path)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            bad = L.create_exclusive(Path(d), "weekly-full-audit_2026-10-19-9", ".md", "# no header\n")
            proc = subprocess.run(["node", str(checker), "--file", str(bad)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


@unittest.skipUnless(WS and shutil.which("pwsh"), "AUDIT_TEST_WORKSPACE_ROOT and pwsh required")
class WatchdogIntegration(unittest.TestCase):
    """The live watchdog reads heartbeats written by heartbeat.py.

    Temporary StatusRoot only, -NoToast so no desktop notification is raised, and the
    two task names the watchdog already monitors (via --watchdog-task parity).
    """

    def test_watchdog_parses_finished_heartbeats(self) -> None:
        script = Path(WS or "") / "system" / "scheduled-tasks" / "audit-watchdog" / "Watch-ScheduledAudits.ps1"
        grrp = Path(__file__).resolve().parents[2] / "review-to-resolution" / "scripts" / "grrp.py"
        with tempfile.TemporaryDirectory() as d:
            sr = Path(d)
            (sr / "activation.json").write_text(json.dumps({"activated_at": "2026-01-01T00:00:00Z"}),
                                                encoding="utf-8")
            for task, tid, outcome in (("bimpossible-weekly-full-audit", "weekly-full-audit", "ok"),
                                       ("slop-audit-weekly", "slop-audit-sweep", "failed")):
                rid = L.mint_run_id(tid)
                HB.start(sr, tid, rid, task)
                HB.finish(sr, tid, rid, outcome, [], "fixture run", task=task)
            now = (dt.datetime.now() + dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
            proc = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-StatusRoot",
                                   str(sr), "-GrrpPath", str(grrp), "-Python", sys.executable, "-Now", now,
                                   "-NoToast"], capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)  # MALFUNCTION present
            status = json.loads((sr / "status.json").read_bytes().decode("utf-8-sig"))
            by_task = {a["task"]: a for a in status["audits"]}
            kinds = {t: sorted(al["kind"] for al in a["alerts"]) for t, a in by_task.items()}
            # Heartbeats were found (no MISSED): finished weekly run without ledger, failed sweep run.
            self.assertEqual(kinds["bimpossible-weekly-full-audit"], ["NO-LEDGER"], kinds)
            self.assertEqual(kinds["slop-audit-weekly"], ["FAILED"], kinds)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- 1a.iv regressions


HERE = Path(__file__).resolve().parent
SKILLS = HERE.parent.parent


class DatePolicyTests(unittest.TestCase):
    def test_midnight_boundary_one_date_per_run(self) -> None:
        # 2026-10-18T03:30Z is 2026-10-17 in UTC-07:00 and 2026-10-18 in UTC.
        stamp = dt.datetime(2026, 10, 18, 3, 30, tzinfo=dt.timezone.utc)
        rid = L.mint_run_id("weekly-full-audit", stamp)
        pdt = dt.timezone(dt.timedelta(hours=-7))
        self.assertEqual(L.run_date(rid, pdt), "2026-10-17")
        self.assertEqual(L.run_date(rid, dt.timezone.utc), "2026-10-18")
        pol = L.date_policy(rid, pdt)
        self.assertEqual((pol["run_date"], pol["utc_offset"], pol["run_start_utc"]),
                         ("2026-10-17", "-07:00", "2026-10-18T03:30:00Z"))
        # slug and record carry the same host-local date as the run itself
        self.assertEqual(L.run_date(rid), L.date_policy(rid)["run_date"])
        rec = L.new_record("weekly-full-audit", rid, {"deliver": "none"})
        self.assertEqual(rec["run_date"], rec["date_policy"]["run_date"])

    def test_grrp_paths_are_parsed_not_rebuilt(self) -> None:
        self.assertEqual(L.parse_grrp_path("created: C:/l/2026-10-17-x.ledger.md\n"), "C:/l/2026-10-17-x.ledger.md")
        self.assertEqual(L.parse_grrp_path("note\nexisting: /a/2026-10-18-x.batch.md\n"), "/a/2026-10-18-x.batch.md")
        for bad in ("", "created: a\ncreated: b\n"):
            with self.assertRaises(L.AuditError):
                L.parse_grrp_path(bad)


class LedgerPathTests(Fixture):
    def test_record_refuses_missing_ledger(self) -> None:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        rc, out, err = run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none"),
                               "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(rc, 0, err)
        rid = json.loads(out)["run_id"]
        base = ("--run-id", rid, "--state-dir", str(self.state))
        self.assertEqual(run_cli("record", *base, "--ledger", str(self.tmp / "nope.ledger.md"))[0], 2)


class DocumentedCommandTests(unittest.TestCase):
    """Every `RUN <sub> ...` line in both SKILL.md files parses against the real CLI."""

    def commands(self) -> list[tuple[str, str]]:
        out = []
        for skill in ("slop-audit-sweep", "weekly-full-audit"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            for m in re.finditer(r"`RUN ([a-z-]+)([^`]*)`", text):
                out.append((skill, m.group(1) + m.group(2)))
        return out

    CONTEXT = ("--run-id", "slop-audit-sweep-20261017T000000Z-abcdef", "--state-dir", "sd")
    NO_CONTEXT = {"start", "slug", "reuse-check", "dirty-check"}  # documented with their own explicit flags

    @staticmethod
    def argv(cmd: str) -> list[str]:
        cmd = re.sub(r"<([^>|]*)\|[^>]*>", r"\1", cmd)  # <ok|degraded> -> first choice
        cmd = re.sub(r"<[^>]*>", "X", cmd)  # placeholders -> a value
        cmd = re.sub(r"(--[a-z-]+) …", r"\1 X", cmd)  # `--flag …` = flag with a value
        cmd = cmd.replace("…", " ").replace("[", " ").replace("]", " ")
        return shlex.split(cmd)

    def test_documented_run_commands_parse(self) -> None:
        cmds = self.commands()
        self.assertGreater(len(cmds), 20)
        parser = R.build_parser()
        for skill, cmd in cmds:
            argv = self.argv(cmd)
            sub = argv[0]
            if sub not in self.NO_CONTEXT and "--run-id" not in argv:
                argv += self.CONTEXT  # the documented rule: every call after start except slug
            with self.subTest(skill=skill, cmd=cmd):
                with contextlib.redirect_stderr(io.StringIO()):
                    try:
                        parser.parse_args(argv)
                    except SystemExit as exc:
                        self.fail(f"{skill}: `RUN {cmd}` rejected by the CLI (exit {exc.code})")

    def test_documented_coverage_statuses_exist(self) -> None:
        # Regression (1a4-4): SKILL.md documented `not-run-approval`, which set_coverage refused at run time.
        seen = set()
        for skill in ("slop-audit-sweep", "weekly-full-audit"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            for m in re.finditer(r'--coverage "[^"=]+=([a-z-]+)', text):
                seen.add(m.group(1))
                with self.subTest(skill=skill, status=m.group(1)):
                    self.assertIn(m.group(1), L.COVERAGE_STATUSES)
        self.assertIn("not-run-approval", seen)

    def test_approval_gap_leaves_run_ok(self) -> None:
        r = L.new_record("weekly-full-audit", L.mint_run_id("weekly-full-audit"), {"deliver": "none"})
        L.set_inspection(r, "running")
        L.set_coverage(r, "addins-build", "not-run-approval", "Add-Ins build not authorized for this run")
        self.assertEqual(L.derive_run(r), "ok")

    def test_state_dir_claim_matches_cli(self) -> None:
        # Regression (1a.iv): `slug` takes no --state-dir, so the docs must exempt it.
        parser = R.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["slug", "--prefix", "p", *self.CONTEXT])
        for skill in ("slop-audit-sweep", "weekly-full-audit"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("Every RUN call after `start` carries", text, skill)
            self.assertIn("except `slug`", text, skill)


class EnrollmentGateTests(unittest.TestCase):
    """The enrolled ledger passes real `grrp.py validate` and gets a true verdict."""

    def test_enrolled_ledger_validates_and_gates_incomplete(self) -> None:
        grrp = SKILLS / "review-to-resolution" / "scripts" / "grrp.py"
        if not grrp.exists():
            grrp = Path(os.environ.get("GRRP_PY", "F:/Claude-Profile/skills/review-to-resolution/scripts/grrp.py"))
        if not grrp.exists():
            self.skipTest("grrp.py not found")
        enroll = HERE.parent / "scripts" / "grrp_enroll.py"
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            env = {**os.environ, "GRRP_LEDGER_DIR": str(d)}

            def run(*a: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run([sys.executable, *a], capture_output=True, text=True, env=env)

            init = run(str(grrp), "init", "--slug", "t-enroll", "--dir", str(d), "--objective", "test run")
            led = L.parse_grrp_path(init.stdout)
            self.assertEqual(run(str(grrp), "gate", led, "--json").returncode, 3)  # empty brief: invalid
            self.assertEqual(run(str(enroll), "brief", "--ledger", led, "--scope", "repo r",
                                 "--boundaries", "read-only", "--done-means", "AC-1 and AC-2",
                                 "--authority", "report only").returncode, 0)
            crit = d / "c.json"
            crit.write_text(json.dumps([
                {"id": "AC-1", "text": "every check ran over its scope", "status": "met", "evidence": "coverage complete"},
                {"id": "AC-2", "text": "every finding is in the report and ledger", "status": "met", "evidence": "2 of 2"}]))
            self.assertEqual(run(str(enroll), "criteria", "--ledger", led, "--criteria-json", str(crit)).returncode, 0)
            fnd = d / "f.json"
            fnd.write_text(json.dumps([
                {"id": "F-1", "label": "HYPOTHESIS", "summary": "maybe swallowed error", "location": "a.py:1"},
                {"id": "F-2", "label": "REFUTED", "evidence": "exit code checked at a.py:9"}]))
            fargs = ("findings", "--ledger", led, "--findings-json", str(fnd), "--receiver", "owner",
                     "--next-action", "verify", "--acceptance", "verified or refuted", "--follow-up", "next run")
            self.assertEqual(run(str(enroll), *fargs).returncode, 0)
            self.assertFalse(json.loads(run(str(enroll), *fargs).stdout)["changed"])  # idempotent
            g = run(str(grrp), "gate", led, "--json")  # gate validates first (exit 3 = invalid)
            self.assertEqual(json.loads(g.stdout)["validation_errors"], [])
            self.assertEqual(g.returncode, 1)  # unassessed hypothesis: incomplete, not softened
            self.assertEqual(json.loads(g.stdout)["verdict"], "incomplete")



class BatchLifecycleTests(unittest.TestCase):
    """Full GRRP lifecycle: invalid (unfilled) -> valid incomplete (open findings) -> complete."""

    def setUp(self) -> None:
        self.grrp = SKILLS / "review-to-resolution" / "scripts" / "grrp.py"
        if not self.grrp.exists():
            self.grrp = Path(os.environ.get("GRRP_PY", "F:/Claude-Profile/skills/review-to-resolution/scripts/grrp.py"))
        if not self.grrp.exists():
            self.skipTest("grrp.py not found")
        self.enroll = HERE.parent / "scripts" / "grrp_enroll.py"
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.env = {**os.environ, "GRRP_LEDGER_DIR": str(self.d)}

    def tearDown(self) -> None:
        self._td.cleanup()

    def run_py(self, *a: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, *a], capture_output=True, text=True, env=self.env)

    def ledger(self, slug: str, findings: list[dict[str, str]]) -> str:
        led = L.parse_grrp_path(self.run_py(str(self.grrp), "init", "--slug", slug, "--dir", str(self.d),
                                            "--objective", "test run").stdout)
        self.assertEqual(self.run_py(str(self.enroll), "brief", "--ledger", led, "--scope", "repo r",
                                     "--boundaries", "read-only", "--done-means", "AC-1 and AC-2",
                                     "--authority", "report only").returncode, 0, led)
        crit = self.d / f"{slug}.c.json"
        crit.write_text(json.dumps([
            {"id": "AC-1", "text": "every check ran", "status": "met", "evidence": "coverage complete"},
            {"id": "AC-2", "text": "every finding recorded", "status": "met", "evidence": "all"}]))
        self.assertEqual(self.run_py(str(self.enroll), "criteria", "--ledger", led, "--criteria-json", str(crit)).returncode, 0)
        fnd = self.d / f"{slug}.f.json"
        fnd.write_text(json.dumps(findings))
        r = self.run_py(str(self.enroll), "findings", "--ledger", led, "--findings-json", str(fnd), "--receiver", "owner",
                        "--next-action", "verify", "--acceptance", "verified", "--follow-up", "next run")
        self.assertEqual(r.returncode, 0, r.stderr)
        return led

    def batch(self, led: str, report: Path) -> tuple[str, Path]:
        m = L.parse_grrp_path(self.run_py(str(self.grrp), "batch-init", "--slug", "b", "--dir", str(self.d)).stdout)
        self.assertEqual(self.run_py(str(self.grrp), "batch", m).returncode, 3)  # unfilled: invalid
        g = self.d / "g.json"
        g.write_text(self.run_py(str(self.grrp), "gate", led, "--json").stdout)
        args = (str(self.enroll), "batch", "--manifest", m, "--runs", "weekly X", "--follow-through", "owner verifies",
                "--authority", "report only", "--authority-gaps", "none", "--archive-evidence", str(self.d),
                "--add-ledger", f"W={led}", "--gate", f"W={g}", "--report", f"W={report}", "--end", "now")
        r = self.run_py(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(json.loads(self.run_py(*args).stdout)["changed"])  # idempotent re-run
        return m, g

    def test_open_findings_give_valid_incomplete_batch(self) -> None:
        led = self.ledger("open", [{"id": "F-1", "label": "CONFIRMED", "summary": "swallowed error", "location": "a.py:1"}])
        report = self.d / "r.md"
        report.write_text("# r\n")
        m, _ = self.batch(led, report)
        r = self.run_py(str(self.grrp), "batch", m, "--record", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # valid, incomplete — not invalid, not complete
        out = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(out["verdict"], "incomplete")
        self.assertFalse(out.get("validation_errors"))

    def test_resolved_findings_complete_batch(self) -> None:
        led = self.ledger("done", [{"id": "F-1", "label": "REFUTED", "evidence": "checked at a.py:9"}])
        report = self.d / "r.md"
        report.write_text("# r\n")
        m, _ = self.batch(led, report)
        r = self.run_py(str(self.grrp), "batch", m, "--record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_refusals(self) -> None:
        led = self.ledger("ref", [{"id": "F-1", "label": "REFUTED", "evidence": "ok"}])
        m, g = self.batch(led, self.d / "missing.md")  # missing report -> artifact open
        base = (str(self.enroll), "batch", "--manifest", m)
        self.assertEqual(self.run_py(*base, "--runs", "different").returncode, 2)  # pinned endpoint
        self.assertEqual(self.run_py(*base, "--end", "2020-01-01T00:00:00Z").returncode, 2)  # end once
        items = self.d / "i.json"
        items.write_text(json.dumps([{"id": "X", "kind": "obligation", "text": "t", "source": "s", "owner": "o",
                                      "action": "a", "status": "owner_accepted", "evidence": "e"}]))
        self.assertEqual(self.run_py(*base, "--items-json", str(items)).returncode, 2)  # only the owner accepts
        items.write_text(json.dumps([{"id": "W-gate", "kind": "obligation", "text": "t", "source": "s", "owner": "o",
                                      "action": "a", "status": "complete", "evidence": "e"}]))
        self.assertEqual(self.run_py(*base, "--items-json", str(items)).returncode, 2)  # pinned kind/text
        self.assertEqual(self.run_py(str(self.grrp), "batch", m, "--record").returncode, 1)  # report missing: incomplete


class ChecklistPathTests(Fixture):
    def pf(self, *extra: str) -> R.Preflight:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        a = R.build_parser().parse_args(["preflight", *self.weekly_args(repo, ws, "--deliver", "none", *extra)])
        self.ws = ws
        return R.run_preflight(a)

    def test_default_resolves_to_authoritative_checklist(self) -> None:
        pf = self.pf()
        self.assertTrue(pf.ok, pf.failures)
        self.assertEqual(Path(pf.tools["checklist"]), self.ws / R.CHECKLIST_REL)
        self.assertEqual(R.CHECKLIST_REL.name, "BIMpossible_Verification_Checklist.md")

    def test_explicit_path(self) -> None:
        other = _touch(self.tmp / "elsewhere" / "C.md")
        pf = self.pf("--checklist", str(other))
        self.assertTrue(pf.ok, pf.failures)
        self.assertEqual(pf.tools["checklist"], str(other))

    def test_explicit_missing_fails(self) -> None:
        pf = self.pf("--checklist", str(self.tmp / "nope.md"))
        self.assertFalse(pf.ok)
        self.assertTrue(any("checklist not found (--checklist)" in f for f in pf.failures), pf.failures)

    def test_none_is_recorded_not_run(self) -> None:
        pf = self.pf("--checklist", "none")
        self.assertTrue(pf.ok, pf.failures)
        self.assertNotIn("checklist", pf.tools)
        self.assertEqual(pf.optional["checklist"]["coverage_key"], "checklist")
        self.assertIn("isolated validation", pf.optional["checklist"]["note"])

    def test_missing_default_fails_preflight(self) -> None:
        repo = self.repo("app2", "https://github.com/O/App2.git")
        ws = self.repo("ws2", "https://github.com/E/W.git")
        for c in R.WEEKLY_CHECKERS:
            commit(ws, f"scripts/{c}", "// fixture")
        a = R.build_parser().parse_args(["preflight", *self.weekly_args(repo, ws, "--deliver", "none")])
        pf = R.run_preflight(a)
        self.assertTrue(any("checklist not found (default)" in f for f in pf.failures), pf.failures)

    def test_live_workspace_default_exists(self) -> None:
        root = os.environ.get("AUDIT_TEST_WORKSPACE_ROOT")
        if not root:
            self.skipTest("AUDIT_TEST_WORKSPACE_ROOT not set")
        self.assertTrue((Path(root) / R.CHECKLIST_REL).is_file())


RUN_PY = Path(R.__file__).resolve()


class RequiredCheckTests(unittest.TestCase):
    """Lens barrier: the parent waits for every required child before convergence/report/close."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.ev = self.d / "ev.md"
        meth = self.d / "method.md"
        meth.write_text("# method\n")
        self.r = L.new_record("weekly-full-audit", L.mint_run_id("weekly-full-audit"), {"deliver": "none"})
        L.set_inspection(self.r, "running")
        L.require_checks(self.r, ["sec", "re", "fe"])
        lib_brief(self.r, self.d)
        for key in ("sec", "re", "fe"):
            L.dispatch_shard(self.r, key, key.upper() + "1", [str(meth)], [str(self.ev)], self.d)
        self.ev.write_text(attest(meth) + "evidence\n")

    def tearDown(self) -> None:
        self._td.cleanup()

    def plan(self, **kw: Any) -> dict[str, Any]:
        return {"inspected": ["backend/routers"], "remaining": [], "why": "", "evidence": str(self.ev), **kw}

    def test_parent_cannot_converge_report_or_close_before_children(self) -> None:
        L.report_check(self.r, "sec", "returned", self.plan())
        for phase in ("convergence", "refutation", "proving", "report"):
            with self.assertRaisesRegex(L.AuditError, "re, fe"):
                L.barrier(self.r, phase)
        with self.assertRaisesRegex(L.AuditError, "not returned"):
            L.set_inspection(self.r, "complete")

    def test_premature_or_malformed_child_return_refused(self) -> None:
        with self.assertRaisesRegex(L.AuditError, "nothing inspected"):
            L.report_check(self.r, "sec", "returned", self.plan(inspected=[]))
        with self.assertRaisesRegex(L.AuditError, "no why"):
            L.report_check(self.r, "sec", "returned", self.plan(remaining=["frontend/"]))
        with self.assertRaisesRegex(L.AuditError, "evidence"):
            L.report_check(self.r, "sec", "returned", self.plan(evidence=str(self.d / "nope")))
        with self.assertRaisesRegex(L.AuditError, "never required"):
            L.report_check(self.r, "perf", "returned", self.plan())
        with self.assertRaisesRegex(L.AuditError, "required check"):
            L.set_coverage(self.r, "sec", "complete")  # bypassing the return is refused
        self.assertEqual(L.pending_checks(self.r), ["sec", "re", "fe"])  # nothing moved
        L.report_check(self.r, "sec", "returned", self.plan())
        with self.assertRaisesRegex(L.AuditError, "already returned"):
            L.report_check(self.r, "sec", "returned", self.plan())

    def test_failure_and_partial_degrade_but_release_barrier(self) -> None:
        L.report_check(self.r, "sec", "returned", self.plan())
        L.report_check(self.r, "re", "returned", self.plan(remaining=["worker/"], why="budget"))
        with self.assertRaisesRegex(L.AuditError, "reason"):
            L.report_check(self.r, "fe", "failed")
        L.report_check(self.r, "fe", "failed", reason="agent timed out")
        L.barrier(self.r, "report")
        self.assertEqual(self.r["coverage"]["sec"]["status"], "complete")
        self.assertEqual(self.r["coverage"]["re"]["status"], "lost")  # partial is never complete
        self.assertIn("1 remaining (budget)", self.r["coverage"]["re"]["note"])
        self.assertEqual(self.r["coverage"]["fe"]["status"], "lost")
        L.set_inspection(self.r, "complete")
        with self.assertRaises(L.AuditError):
            L.set_run(self.r, "ok")
        L.set_run(self.r, "degraded")


class RequiredCheckCliTests(Fixture):
    def test_cli_wait_check_and_write_report_barrier(self) -> None:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        sd = self.tmp / "state"
        out = subprocess.run([sys.executable, str(RUN_PY), "start", *self.weekly_args(repo, ws, "--deliver", "none"),
                              "--state-dir", str(sd), "--watchdog-dir", str(self.tmp / "wd")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        rid = json.loads(out.stdout)["run_id"]
        base = ["--run-id", rid, "--state-dir", str(sd)]

        def run(*a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(RUN_PY), *a], capture_output=True, text=True)

        self.assertEqual(run("record", *base, "--require", "sec", "fe").returncode, 0)
        w = run("wait", *base, "--phase", "convergence")
        self.assertEqual(w.returncode, 1)
        self.assertEqual(json.loads(w.stdout)["pending"], ["sec", "fe"])
        body = self.tmp / "body.md"
        body.write_text("# body\n")
        wr = run("write-report", *base, "--dir", str(self.tmp / "out"), "--stem", "r", "--body-file", str(body))
        self.assertEqual(wr.returncode, 2)
        self.assertIn("report blocked", wr.stderr)
        ev = Path(run("evidence-dir", *base).stdout.strip()) / "ev.md"
        meth = self.tmp / "method.md"
        meth.write_text("# method\n")
        led = self.tmp / "brief.ledger.md"
        led.write_text(BRIEF_TEXT, encoding="utf-8")
        self.assertEqual(run("record", *base, "--ledger", str(led)).returncode, 0)
        self.assertEqual(run("brief-done", *base, "--ledger", str(led)).returncode, 0)
        self.assertEqual(run("dispatch", *base, "--key", "sec", "--shard", "S1", "--method", str(meth),
                             "--output", str(ev)).returncode, 0)
        ev.write_text(attest(meth) + "x\n")
        cf = self.tmp / "c.json"
        cf.write_text(json.dumps({"inspected": ["a"], "remaining": [], "evidence": str(ev)}))
        self.assertEqual(run("check", *base, "--key", "sec", "--state", "returned", "--completion-file", str(cf)).returncode, 0)
        self.assertEqual(run("check", *base, "--key", "fe", "--state", "failed", "--reason", "no return").returncode, 0)
        w = run("wait", *base, "--phase", "report")
        self.assertEqual(w.returncode, 0, w.stdout)
        self.assertEqual(json.loads(w.stdout)["failed"], ["fe"])


class UnreachableRootTests(Fixture):
    def test_unreachable_workspace_degrades_with_access_note(self) -> None:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        missing = self.tmp / "gone"
        args = ["--task", "weekly-full-audit", "--root", f"repo={repo}", "--root", f"workspace={missing}",
                "--tools-root", str(self.tools), "--grrp", str(self.grrp), "--checker-dir", str(ws / "scripts"),
                "--checklist", "none", "--deliver", "none"]
        sd = self.tmp / "state"
        out = subprocess.run([sys.executable, str(RUN_PY), "start", *args, "--state-dir", str(sd),
                              "--watchdog-dir", str(self.tmp / "wd")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        cov = json.loads(out.stdout)["coverage"]
        self.assertEqual(cov["root-workspace"]["status"], "lost")
        self.assertIn("Access:", cov["root-workspace"]["note"])

    def test_no_reachable_required_root_fails(self) -> None:
        a = R.build_parser().parse_args(["preflight", "--task", "weekly-full-audit", "--root", f"repo={self.tmp / 'x'}",
                                         "--root", f"workspace={self.tmp / 'y'}", "--tools-root", str(self.tools),
                                         "--grrp", str(self.grrp), "--deliver", "none"])
        pf = R.run_preflight(a)
        self.assertFalse(pf.ok)
        self.assertTrue(any("no required root is reachable" in f for f in pf.failures), pf.failures)


class HygieneLineTests(unittest.TestCase):
    def test_line_reflects_real_exit_codes_and_missing_checker(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not on PATH")
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            repo, ws = d / "repo", d / "ws"
            (repo / "scripts").mkdir(parents=True)
            (ws / "scripts").mkdir(parents=True)
            (repo / "scripts" / "check-doc-references.mjs").write_text("process.exit(0)\n")
            (repo / "scripts" / "check-docs-budget.mjs").write_text("process.exit(1)\n")
            (ws / "scripts" / "check-doc-references.mjs").write_text("process.exit(0)\n")
            res = R.hygiene_results({"repo": repo, "workspace": ws}, node, None)
            self.assertEqual(R.hygiene_line(res),
                             "**Docs-hygiene (HYG-13):** BIMpossible G3 ✓ · G4 ✗ — Workspace G3 ✓ · G4 ✗")
            self.assertTrue(res["repo:G4"]["ran"])
            self.assertFalse(res["workspace:G4"]["ran"])


class FixtureEndToEndTests(Fixture):
    """One cheap pass: start (explicit checklist) -> required checks -> report -> real GRRP enroll/gate
    -> batch (valid incomplete with an open finding) -> run outcome -> finish."""

    def test_end_to_end(self) -> None:
        bl = BatchLifecycleTests("test_refusals")
        bl.setUp()
        self.addCleanup(bl.tearDown)
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        cl = _touch(self.tmp / "cl" / "Checklist.md")
        code, out, err = run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none", "--checklist", str(cl)),
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        rid = json.loads(out)["run_id"]
        base = ["--run-id", rid, "--state-dir", str(self.state)]
        rec = L.load_record(self.state / "weekly-full-audit" / f"{rid}.json")
        self.assertEqual(rec["preflight"]["tools"]["checklist"], str(cl))
        outdir = self.tmp / "reports"
        for name, path in (("repo", repo), ("workspace", ws)):
            self.assertEqual(run_cli("baseline", "--root", f"{name}={path}", "--report-dir", str(outdir),
                                     "--pattern", "weekly-full-audit_*.md", *base)[0], 0)
        self.assertEqual(run_cli("record", *base, "--require", "sec", "ws")[0], 0)
        body = self.tmp / "body.md"
        body.write_text("# Weekly\n", encoding="utf-8")
        stem = f"weekly-full-audit_{L.run_date(rid)}"
        self.assertEqual(run_cli("write-report", *base, "--dir", str(outdir), "--stem", stem, "--body-file", str(body))[0], 2)
        evd = Path(run_cli("evidence-dir", *base)[1].strip())
        self.assertEqual(evd, self.state / "weekly-full-audit" / f"{rid}_evidence")
        ev = evd / "sec.md"
        fj = evd / "sec.findings.json"
        meth = self.tmp / "security-audit.md"
        meth.write_text("# sec method\n")
        self.assertEqual(dispatch(base, "sec", "SEC1", meth, ev, fj)[0], 0)
        wsev = evd / "ws.md"
        self.assertEqual(dispatch(base, "ws", "WS1", meth, wsev)[0], 0)
        ev.write_text(attest(meth) + "read backend/\n")
        wsev.write_text(attest(meth) + "read scripts/\n")
        fj.write_text(json.dumps([{"id": "SEC-1", "label": "HYPOTHESIS", "evidence": "a.py:1"}]))
        self.assertEqual(run_cli("check", *base, "--key", "sec", "--state", "returned", "--inspected", "backend/",
                                 "--evidence", str(ev), "--findings", str(fj))[0], 0)
        self.assertEqual(run_cli("check", *base, "--key", "ws", "--state", "returned", "--inspected", "scripts/",
                                 "--remaining", "system/", "--why", "budget", "--evidence", str(wsev))[0], 0)
        self.assertEqual(run_cli("wait", *base, "--phase", "report")[0], 0)
        code, report, err = run_cli("write-report", *base, "--dir", str(outdir), "--stem", stem, "--body-file", str(body))
        self.assertEqual(code, 0, err)
        self.assertEqual(run_cli("record", *base, "--inspection", "complete")[0], 0)
        led = bl.ledger("e2e", [{"id": "SEC-1", "label": "HYPOTHESIS", "summary": "s", "location": "a.py:1"}])
        gate = self.tmp / "gate.json"
        gate.write_text(bl.run_py(str(bl.grrp), "gate", led, "--json").stdout)
        self.assertEqual(run_cli("record", *base, "--ledger", led, "--gate-file", str(gate))[0], 0)
        m, _ = bl.batch(led, Path(report.strip()))
        r = bl.run_py(str(bl.grrp), "batch", m, "--record")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # valid incomplete: inspection done, finding open
        code, _, err = run_cli("record", *base, "--run", "ok")
        self.assertEqual(code, 2)  # partial ws + handoff-pending -> never ok
        self.assertEqual(run_cli("record", *base, "--run", "degraded")[0], 0)
        self.assertEqual(run_cli("finish", *base, "--watchdog-dir", str(self.wd))[0], 0)
        rec = L.load_record(self.state / "weekly-full-audit" / f"{rid}.json")
        self.assertEqual(rec["coverage"]["ws"]["status"], "lost")
        self.assertEqual(L.dirty(repo), [])


class Cp1252StdoutTests(unittest.TestCase):
    def test_non_ascii_json_survives_cp1252_redirect(self) -> None:
        # Regression (1a4-3): `hygiene-line` crashed printing ✓ to a redirected cp1252 stdout.
        env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        code = ("import sys; sys.argv=['x']; import audit_run as R; R.build_parser = lambda: type('P', (), "
                "{'parse_args': lambda s, a: type('A', (), {'fn': staticmethod(lambda a: R._print({'m': '✓'}) or 0)})()})(); "
                "sys.exit(R.main([]))")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, cwd=RUN_PY.parent)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn("✓", r.stdout.decode("utf-8"))


class AuditDefectRegressionTests(Fixture):
    """R1 (2026-10-17): defects seen in rerun 1a4-3."""

    def _run(self) -> tuple[str, list[str], Path]:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        code, out, err = run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none"),
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        rid = json.loads(out)["run_id"]
        base = ["--run-id", rid, "--state-dir", str(self.state)]
        return rid, base, Path(run_cli("evidence-dir", *base)[1].strip())

    def test_unescaped_backslash_json_is_refused_with_diagnostic_not_repaired(self) -> None:
        bad = self.tmp / "bad.json"
        bad.write_text('{"evidence": "F:' + chr(92) + 'x' + chr(92) + 'sec.md"}', encoding="utf-8")
        with self.assertRaises(L.AuditError) as cm:
            L.read_json(bad)
        self.assertIn("invalid backslash escape", str(cm.exception))
        self.assertEqual(sorted(p.name for p in self.tmp.glob("bad*")), ["bad.json"])  # no repair copy
        ok = self.tmp / "ok.json"
        ok.write_text(json.dumps({"evidence": "F:" + chr(92) + "x"}), encoding="utf-8")
        self.assertEqual(L.read_json(ok)["evidence"], "F:" + chr(92) + "x")

    def test_cli_args_serialize_windows_paths(self) -> None:
        _, base, evd = self._run()
        self.assertEqual(run_cli("record", *base, "--require", "sec")[0], 0)
        ev = evd / "sec.md"
        meth = self.tmp / "m.md"
        meth.write_text("# m\n")
        self.assertEqual(dispatch(base, "sec", "S1", meth, ev)[0], 0)
        ev.write_text(attest(meth) + "x\n")
        code, out, err = run_cli("check", *base, "--key", "sec", "--state", "returned",
                                 "--inspected", "backend" + chr(92) + "api", "--evidence", str(ev).replace("/", chr(92)))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["check"]["inspected"], ["backend" + chr(92) + "api"])

    def test_completion_claims_need_real_artifacts_in_the_run_dir(self) -> None:
        _, base, evd = self._run()
        self.assertEqual(run_cli("record", *base, "--require", "sec")[0], 0)
        good = evd / "sec.md"
        meth = self.tmp / "m.md"
        meth.write_text("# m\n")
        self.assertEqual(dispatch(base, "sec", "S1", meth, good)[0], 0)
        good.write_text(attest(meth) + "x\n")
        empty = evd / "empty.md"
        empty.write_text("")
        outside = self.tmp / "outside.md"
        outside.write_text("x\n")
        badf = evd / "f.json"
        badf.write_text(json.dumps([{"id": "S-1", "label": "MAYBE", "evidence": "e"}]))
        dup = evd / "d.json"
        dup.write_text(json.dumps([{"id": "S-1", "label": "CONFIRMED", "evidence": "e"}] * 2))
        cases = {
            "outside": ["--evidence", str(outside)],
            "empty": ["--evidence", str(empty)],
            "missing artifact": ["--evidence", str(good), "--artifact", str(evd / "startup-context.txt")],
            "bad label": ["--evidence", str(good), "--findings", str(badf)],
            "duplicate id": ["--evidence", str(good), "--findings", str(dup)],
            "both forms": ["--evidence", str(good), "--completion-file", str(good)],
        }
        for name, extra in cases.items():
            with self.subTest(name):
                code, _, err = run_cli("check", *base, "--key", "sec", "--state", "returned", "--inspected", "a", *extra)
                self.assertEqual(code, 2, name)
        rec = L.load_record(self.state / "weekly-full-audit" / f"{base[1]}.json")
        self.assertEqual(rec["checks"]["sec"]["state"], "pending")  # refusals leave the check pending

    def test_ctxcheck_header_line_carries_confirmed_count_and_is_enforced(self) -> None:
        rid, base, evd = self._run()
        cj = evd / "ctxcheck.json"
        cj.write_text(json.dumps({"summary": {"PASS": 73, "WARN": 2, "FAIL": 0}}))
        v = evd / "warns.json"
        v.write_text(json.dumps([{"id": "W1", "verdict": "CONFIRMED"}]))
        self.assertEqual(run_cli("ctxcheck-line", *base, "--ctxcheck-json", str(cj), "--verdicts", str(v))[0], 2)
        v.write_text(json.dumps([{"id": "W1", "verdict": "CONFIRMED"}, {"id": "W2", "verdict": "REFUTED"}]))
        code, line, err = run_cli("ctxcheck-line", *base, "--ctxcheck-json", str(cj), "--verdicts", str(v))
        self.assertEqual(code, 0, err)
        self.assertEqual(line.strip(), "**ctxcheck:** PASS 73 · WARN 2 · FAIL 0 (WARN-confirmed 1)")
        outdir = self.tmp / "out"
        rec = L.load_record(self.state / "weekly-full-audit" / f"{rid}.json")
        for name, meta in rec["preflight"]["roots"].items():
            self.assertEqual(run_cli("baseline", "--root", f"{name}={meta['path']}", "--report-dir", str(outdir),
                                     "--pattern", "weekly-full-audit_*.md", *base)[0], 0)
        body = self.tmp / "body.md"
        body.write_text("# W\n\n**ctxcheck:** PASS 73 · WARN 2 · FAIL 0\n", encoding="utf-8")
        code, _, err = run_cli("write-report", *base, "--dir", str(outdir), "--stem", "r", "--body-file", str(body))
        self.assertEqual(code, 2)
        self.assertIn("verbatim", err)
        body.write_text("# W\n\n" + line.strip() + "\n", encoding="utf-8")
        self.assertEqual(run_cli("write-report", *base, "--dir", str(outdir), "--stem", "r", "--body-file", str(body))[0], 0)


class DispatchContractTests(Fixture):
    """R2 (2026-10-18): rerun 1a4-4 children wrote to undeclared scratch locations and skipped the
    method files they were given. A check now returns only against dispatched shards whose declared
    outputs exist under the evidence dir and attest every declared method by sha256."""

    def _run(self) -> tuple[list[str], Path, Path]:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        code, out, err = run_cli("start", *self.weekly_args(repo, ws, "--deliver", "none"),
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        base = ["--run-id", json.loads(out)["run_id"], "--state-dir", str(self.state)]
        self.assertEqual(run_cli("record", *base, "--require", "sec")[0], 0)
        evd = Path(run_cli("evidence-dir", *base)[1].strip())
        meth = self.tmp / "security-audit.md"
        meth.write_text("# lenses\n1. authn\n")
        return base, evd, meth

    def _returned(self, base: list[str], ev: Path, *extra: str) -> tuple[int, str, str]:
        return run_cli("check", *base, "--key", "sec", "--state", "returned", "--inspected", "backend/",
                       "--evidence", str(ev), *extra)

    def _state(self, base: list[str]) -> str:
        return L.load_record(self.state / "weekly-full-audit" / f"{base[1]}.json")["checks"]["sec"]["state"]

    def test_attested_declared_outputs_are_accepted(self) -> None:
        base, evd, meth = self._run()
        ev, fj = evd / "mp-S1.md", evd / "mp-S1.findings.json"
        code, out, err = dispatch(base, "sec", "S1", meth, ev, fj)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["methods"][0]["sha256"], L.file_sha256(meth))
        ev.write_text(attest(meth) + "## Areas\n", encoding="utf-8")
        fj.write_text("[]")
        code, _, err = self._returned(base, ev, "--findings", str(fj))
        self.assertEqual(code, 0, err)
        self.assertEqual(self._state(base), "returned")

    def test_repeated_require_flags_accumulate(self) -> None:
        """1a4-4 sweep: `--require a --require b` kept only b, so a and its dispatch were silently dropped."""
        base, evd, meth = self._run()
        self.assertEqual(run_cli("record", *base, "--require", "ra", "--require", "rb", "rc")[0], 0)
        checks = L.load_record(self.state / "weekly-full-audit" / f"{base[1]}.json")["checks"]
        self.assertTrue({"sec", "ra", "rb", "rc"} <= set(checks))
        for key in ("ra", "rb", "rc"):
            self.assertEqual(dispatch(base, key, key.upper(), meth, evd / f"{key}.md")[0], 0)

    def test_return_without_any_dispatch_is_refused(self) -> None:
        base, evd, _ = self._run()
        ev = evd / "sec.md"
        ev.write_text("x\n")
        code, _, err = self._returned(base, ev)
        self.assertEqual(code, 2)
        self.assertIn("no dispatched shard", err)
        self.assertEqual(self._state(base), "pending")

    def test_dispatch_refuses_outputs_the_parent_cannot_hold_the_child_to(self) -> None:
        base, evd, meth = self._run()
        ensure_brief(base)
        pre = evd / "old.md"
        pre.write_text("x\n")
        cases = {
            "outside": ("sec", meth, self.tmp / "scratch" / "out.md", "outside"),
            "pre-existing": ("sec", meth, pre, "already exists"),
            "missing method": ("sec", self.tmp / "nope.md", evd / "a.md", "method file missing"),
            "unregistered key": ("arch", meth, evd / "b.md", "never required"),
        }
        for name, (key, m, out, msg) in cases.items():
            with self.subTest(name):
                code, _, err = run_cli("dispatch", *base, "--key", key, "--shard", f"X-{name}",
                                       "--method", str(m), "--output", str(out))
                self.assertEqual(code, 2, name)
                self.assertIn(msg, err)
        self.assertEqual(dispatch(base, "sec", "S1", meth, evd / "c.md")[0], 0)
        code, _, err = dispatch(base, "sec", "S1", meth, evd / "d.md")
        self.assertEqual(code, 2)
        self.assertIn("already declared", err)

    def test_missing_or_wrong_attestation_and_undeclared_claims_are_refused(self) -> None:
        base, evd, meth = self._run()
        other = self.tmp / "other.md"
        other.write_text("# other\n")
        ev, ev2 = evd / "mp-S1.md", evd / "mp-S2.md"
        self.assertEqual(dispatch(base, "sec", "S1", meth, ev)[0], 0)  # also records the brief
        self.assertEqual(run_cli("dispatch", *base, "--key", "sec", "--shard", "S2", "--method", str(meth),
                                 "--method", str(other), "--output", str(ev2))[0], 0)
        ev.write_text(attest(meth) + "x\n")
        wrong = attest(meth).replace(L.file_sha256(meth), "0" * 64)
        cases = {
            "declared output missing": (None, "declared output missing"),
            "method omitted": (attest(meth) + "x\n", "does not attest method"),
            "wrong sha": (wrong + attest(other) + "x\n", "wrong sha256"),
        }
        for name, (body, msg) in cases.items():
            with self.subTest(name):
                if body is not None:
                    ev2.write_text(body)
                code, _, err = self._returned(base, ev)
                self.assertEqual(code, 2, name)
                self.assertIn(msg, err)
        ev2.write_text(attest(meth) + attest(other) + "x\n")
        undeclared = evd / "scratch-pick.md"
        undeclared.write_text(attest(meth) + "x\n")
        code, _, err = self._returned(base, undeclared)
        self.assertEqual(code, 2)
        self.assertIn("not a declared output", err)
        meth.write_text("# lenses, edited after dispatch\n")
        code, _, err = self._returned(base, ev)
        self.assertEqual(code, 2)
        self.assertIn("changed after dispatch", err)
        self.assertEqual(self._state(base), "pending")


# --------------------------------------------------------------------------- 2026-10-30 run-tooling fixes


class _StartedRun(Fixture):
    def _start(self, deliver: str = "none", state: Path | None = None) -> list[str]:
        sd = state or self.state
        tag = "" if state is None else state.name
        repo = self.repo(f"app{tag}", "https://github.com/O/App.git")
        ws = make_repo(self.tmp, f"ws{tag}", "https://github.com/Example/Workspace.git")
        for c in R.WEEKLY_CHECKERS:
            commit(ws, f"scripts/{c}", "// fixture\n")
        commit(ws, R.CHECKLIST_REL.as_posix(), "checklist")
        code, out, err = run_cli("start", *self.weekly_args(repo, ws, "--deliver", deliver),
                                 "--state-dir", str(sd), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        return ["--run-id", json.loads(out)["run_id"], "--state-dir", str(sd)]

    def _rec(self, base: list[str]) -> dict[str, Any]:
        return L.load_record(Path(base[3]) / "weekly-full-audit" / f"{base[1]}.json")


class FileDeliveryTests(_StartedRun):
    """Item 1 (D2 2026-10-30): delivery is a create-only RESULTS.md under the run's state dir."""

    def _publish(self, base: list[str]) -> Path:
        led = _touch(self.tmp / "l.ledger.md")
        self.assertEqual(run_cli("record", *base, "--coverage", "lens-1=complete", "--inspection", "complete")[0], 0)
        self.assertEqual(run_cli("record", *base, "--run", "degraded", "--ledger", str(led))[0], 0)
        gate = self.tmp / "gate.json"
        gate.write_text('{"verdict": "PASS", "note": "a · b — c"}', encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--gate-file", str(gate))[0], 0)
        self.assertEqual(run_cli("publish", *base)[0], 0)
        return led

    def test_deliver_file_writes_results_and_records_delivered(self) -> None:
        base = self._start("file")
        code, _, err = run_cli("deliver-file", *base)
        self.assertEqual(code, 2)
        self.assertIn("publication", err)
        led = self._publish(base)
        code, _, err = run_cli("deliver", *base, "--state", "delivered", "--ref", "x")
        self.assertEqual(code, 2)
        self.assertIn("deliver-file", err)
        log = _touch(self.tmp / "run.log")
        code, out, err = run_cli("deliver-file", *base, "--run-log", str(log))
        self.assertEqual(code, 0, err)
        ref = json.loads(out)["ref"]
        path = Path(ref)
        self.assertEqual((path.name, path.parent.name), ("RESULTS.md", base[1]))
        body = path.read_text(encoding="utf-8")
        self.assertIn(f"# Results: {base[1]}", body)
        self.assertIn('"note": "a · b — c"', body)  # gate verbatim, UTF-8
        self.assertIn(str(log), body)
        self.assertIn(str(led), body)
        self.assertEqual(self._rec(base)["states"]["delivery"], {"mode": "file", "state": "delivered", "ref": ref})
        code, _, err = run_cli("deliver-file", *base)
        self.assertEqual(code, 2)
        self.assertIn("already delivered", err)

    def test_existing_results_gets_suffix_and_other_modes_refused(self) -> None:
        base = self._start("file")
        self._publish(base)
        out_dir = self.state / "weekly-full-audit" / base[1]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "RESULTS.md").write_text("earlier\n", encoding="utf-8")
        code, out, err = run_cli("deliver-file", *base)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["ref"].endswith("RESULTS-2.md"))
        self.assertEqual((out_dir / "RESULTS.md").read_text(encoding="utf-8"), "earlier\n")
        none_base = self._start("none", self.tmp / "s2")
        code, _, err = run_cli("deliver-file", *none_base)
        self.assertEqual(code, 2)
        self.assertIn("--deliver file", err)


class BriefBeforeDispatchTests(_StartedRun):
    """Item 2: no child is dispatched until a recorded ledger's brief is filled."""

    def test_dispatch_refused_until_brief_done(self) -> None:
        base = self._start()
        self.assertEqual(run_cli("record", *base, "--require", "sec")[0], 0)
        evd = Path(run_cli("evidence-dir", *base)[1].strip())
        meth = self.tmp / "m.md"
        meth.write_text("# m\nline\n", encoding="utf-8")
        argv = ("dispatch", *base, "--key", "sec", "--shard", "S1", "--method", str(meth), "--output", str(evd / "o.md"))
        code, _, err = run_cli(*argv)
        self.assertEqual(code, 2)
        self.assertIn("brief-done", err)
        led = self.tmp / "b.ledger.md"
        led.write_text(BRIEF_TEXT, encoding="utf-8")
        code, _, err = run_cli("brief-done", *base, "--ledger", str(led))
        self.assertEqual(code, 2)
        self.assertIn("not recorded", err)
        self.assertEqual(run_cli("record", *base, "--ledger", str(led))[0], 0)
        bads = {
            "blank": BRIEF_TEXT.replace("AC-1", ""),
            "placeholder": BRIEF_TEXT.replace("report only", "TBD"),
            "missing": BRIEF_TEXT.replace("- Scope: repo r\n", ""),
        }
        for name, bad in bads.items():
            with self.subTest(name):
                led.write_text(bad, encoding="utf-8")
                code, _, err = run_cli("brief-done", *base, "--ledger", str(led))
                self.assertEqual(code, 2)
                self.assertIn("empty", err)
        self.assertEqual(run_cli(*argv)[0], 2)  # failed brief-done records nothing
        led.write_text(BRIEF_TEXT, encoding="utf-8")
        self.assertEqual(run_cli("brief-done", *base, "--ledger", str(led))[0], 0)
        self.assertEqual(self._rec(base)["briefs"][0]["fields"]["Done means"], "AC-1")
        code, _, err = run_cli(*argv)
        self.assertEqual(code, 0, err)


class MethodLineProofTests(_StartedRun):
    """Item 3: the child proves it read the method by hashing a random line k chosen at dispatch."""

    def test_line_attestation_required_and_checked(self) -> None:
        base = self._start()
        self.assertEqual(run_cli("record", *base, "--require", "sec")[0], 0)
        evd = Path(run_cli("evidence-dir", *base)[1].strip())
        meth = self.tmp / "m.md"
        meth.write_text("# secret-method-heading\n\n1. authn\n2. authz\n", encoding="utf-8")
        ev = evd / "S1.md"
        code, out, err = dispatch(base, "sec", "S1", meth, ev)
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        k = res["methods"][0]["line"]
        self.assertIsInstance(k, int)
        self.assertIn(k, (1, 3, 4))  # never the blank line
        for text in ("secret-method-heading", "authn", "authz"):
            self.assertNotIn(text, out)  # dispatch names k, never the line itself
        self.assertTrue(any(s.startswith("method-line: ") and f" {k} " in s for s in res["child_must_attest"]))
        sha_only = f"method-sha256: {meth.as_posix()} {L.file_sha256(meth)}\n"
        line_k = L.method_lines(meth)[k - 1]
        cases = {
            "sha only": (sha_only, "does not attest line"),
            "wrong line hash": (sha_only + f"method-line: {meth.as_posix()} {k} {'0' * 64}\n", "wrong line hash"),
        }
        check = ("check", *base, "--key", "sec", "--state", "returned", "--inspected", "x/", "--evidence", str(ev))
        for name, (body, msg) in cases.items():
            with self.subTest(name):
                ev.write_text(body, encoding="utf-8")
                code, _, err = run_cli(*check)
                self.assertEqual(code, 2)
                self.assertIn(msg, err)
        ev.write_text(sha_only + f"method-line: {meth.as_posix()} {k} {L.line_sha256(line_k)}\n", encoding="utf-8")
        code, _, err = run_cli(*check)
        self.assertEqual(code, 0, err)

    def test_line_hash_strips_whitespace_and_crlf(self) -> None:
        p = self.tmp / "crlf.md"
        p.write_bytes(b"a\r\n  indented line  \r\n")
        self.assertEqual(L.method_lines(p)[1], "  indented line  ")
        self.assertEqual(L.line_sha256("  indented line  "), L.line_sha256("indented line"))


class OutputsAuditTests(_StartedRun):
    """Item 4: files a run wrote outside its declared outputs are a defect."""

    def test_stray_file_is_defect_declared_and_old_files_ignored(self) -> None:
        base = self._start()
        scan = self.tmp / "scan"
        scan.mkdir()
        old = scan / "old.md"
        old.write_text("x\n", encoding="utf-8")
        os.utime(old, (1_000_000_000, 1_000_000_000))
        evd = Path(run_cli("evidence-dir", *base)[1].strip())
        evd.mkdir(parents=True, exist_ok=True)
        (evd / "child.md").write_text("x\n", encoding="utf-8")
        report = scan / "report.md"
        report.write_text("r\n", encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--artifact", f"report={report}")[0], 0)
        allowed = scan / "allowed.txt"
        allowed.write_text("a\n", encoding="utf-8")
        code, out, err = run_cli("outputs-audit", *base, "--scan-dir", str(scan), "--scan-dir", str(self.state),
                                 "--exclude", str(allowed))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["undeclared"], [])
        stray = scan / "sub" / "scratch-pick.md"
        stray.parent.mkdir()
        stray.write_text("s\n", encoding="utf-8")
        code, out, _ = run_cli("outputs-audit", *base, "--scan-dir", str(scan), "--exclude", str(allowed))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["undeclared"], [stray.resolve().as_posix()])
        rec = self._rec(base)
        self.assertEqual(rec["undeclared_outputs"], [stray.resolve().as_posix()])
        self.assertIn("defect:undeclared-outputs", [e["event"] for e in rec["events"]])
        self.assertEqual(run_cli("record", *base, "--coverage", "lens-1=complete", "--inspection", "complete")[0], 0)
        code, _, err = run_cli("record", *base, "--run", "ok")
        self.assertEqual(code, 2)
        self.assertIn("softer", err)

    def test_scan_dir_must_exist(self) -> None:
        base = self._start()
        code, _, err = run_cli("outputs-audit", *base, "--scan-dir", str(self.tmp / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)


class Utf8LineTests(_StartedRun):
    """Item 5: ctxcheck-line text is decoded and stored as UTF-8, never mojibake."""

    def test_non_ascii_round_trip_and_mojibake_refused(self) -> None:
        base = self._start()
        mid, dash = "·", "—"
        cj, vj = self.tmp / "ctx.json", self.tmp / "v.json"
        cj.write_text(json.dumps({"summary": {"PASS": 3, "WARN": 2, "FAIL": 0}}), encoding="utf-8")
        vj.write_text(json.dumps([{"id": f"doc{mid}ref", "verdict": "CONFIRMED"},
                                  {"id": f"a{dash}b", "verdict": "REFUTED"}], ensure_ascii=False), encoding="utf-8")
        code, out, err = run_cli("ctxcheck-line", *base, "--ctxcheck-json", str(cj), "--verdicts", str(vj))
        self.assertEqual(code, 0, err)
        self.assertIn(f"PASS 3 {mid} WARN 2", out)
        raw = (self.state / "weekly-full-audit" / f"{base[1]}.json").read_bytes()
        for ch in (mid, dash):
            self.assertIn(ch.encode("utf-8"), raw)
        self.assertNotIn(b"\\u00b7", raw)
        self.assertNotIn("Â·".encode("utf-8"), raw)
        self.assertEqual(self._rec(base)["ctxcheck"]["verdicts"][1]["id"], f"a{dash}b")
        u16 = self.tmp / "u16.json"
        u16.write_bytes(json.dumps({"x": mid + dash}, ensure_ascii=False).encode("utf-16"))
        self.assertEqual(L.read_json(u16)["x"], mid + dash)
        vj.write_text(json.dumps([{"id": "docÂ·ref", "verdict": "CONFIRMED"},
                                  {"id": "aâ€”b", "verdict": "REFUTED"}], ensure_ascii=False),
                      encoding="utf-8")
        code, _, err = run_cli("ctxcheck-line", *base, "--ctxcheck-json", str(cj), "--verdicts", str(vj))
        self.assertEqual(code, 2)
        self.assertIn("mis-decoded", err)
        cp = self.tmp / "cp.json"
        cp.write_bytes(('{"x": "' + mid + '"}').encode("cp1252"))
        with self.assertRaises(L.AuditError) as cm:
            L.read_json(cp)
        self.assertIn("not UTF-8", str(cm.exception))


class G4RevisionTests(unittest.TestCase):
    """Item 6: G4 measures the root's inspected revision, not whatever the working tree holds now."""

    def test_g4_measures_inspected_revision(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not on PATH")
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            repo = make_repo(d, "repo")
            commit(repo, "scripts/check-doc-references.mjs", "process.exit(0)\n")
            rev1 = commit(repo, "scripts/check-docs-budget.mjs",
                          "import { execFileSync } from 'node:child_process';\n"
                          "const files = execFileSync('git', ['ls-files'], { encoding: 'utf8' });\n"
                          "process.exit(files.split('\\n').includes('big.md') ? 1 : 0);\n")
            rev2 = commit(repo, "big.md", "too big\n")
            at_rev1 = R.hygiene_results({"repo": repo}, node, None, {"repo": rev1})["repo:G4"]
            self.assertTrue(at_rev1["pass"], at_rev1)
            self.assertEqual(at_rev1["measured"], rev1)
            self.assertIn("inspected revision", at_rev1["note"])
            no_rev = R.hygiene_results({"repo": repo}, node, None)["repo:G4"]
            self.assertFalse(no_rev["pass"])
            self.assertEqual(no_rev["measured"], "working-tree")
            self.assertIn("no inspected revision recorded", no_rev["note"])
            at_head = R.hygiene_results({"repo": repo}, node, None, {"repo": rev2})["repo:G4"]
            self.assertFalse(at_head["pass"])
            self.assertIn("working tree is the inspected revision", at_head["note"])
            (repo / "notes.md").write_text("untracked\n", encoding="utf-8")  # dirty tree at HEAD
            dirty = R.hygiene_results({"repo": repo}, node, None, {"repo": rev2})["repo:G4"]
            self.assertFalse(dirty["pass"])
            self.assertIn("working tree differs", dirty["note"])
            self.assertEqual(sh(repo, "rev-parse", "HEAD"), rev2)  # the target was not touched
            self.assertEqual(sh(repo, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_checkout_revision_is_a_git_tree_at_rev_with_origin_refs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            d = Path(td)
            repo = make_repo(d, "repo")
            rev1 = commit(repo, "a/b.txt", "one \u00b7 two\n")
            sh(repo, "update-ref", "refs/remotes/origin/main", rev1)
            commit(repo, "a/b.txt", "changed\n")
            out = d / "out"
            L.checkout_revision(repo, rev1, out)
            self.assertEqual((out / "a" / "b.txt").read_text(encoding="utf-8"), "one \u00b7 two\n")
            self.assertEqual(sh(out, "rev-parse", "HEAD"), rev1)
            self.assertEqual(sh(out, "rev-parse", "origin/main"), rev1)
            self.assertIn("a/b.txt", sh(out, "ls-files"))
            self.assertEqual(L.dirty(repo), [])
            with self.assertRaises(L.AuditError):
                L.checkout_revision(repo, "0" * 40, d / "bad")


# --------------------------------------------------------------------------- 1a.iv cutover-readiness fixes


class ChecklistReadableTests(Fixture):
    """Fix b: a present Checklist is resolved, echoed as tools.checklist and proven readable;
    `none` is recorded NOT RUN in the run record's coverage at start."""

    def _args(self, *extra: str) -> list[str]:
        repo, ws = self.repo("app", "https://github.com/O/App.git"), self.workspace()
        self.ws = ws
        return self.weekly_args(repo, ws, "--deliver", "none", *extra)

    def test_unreadable_checklist_fails_preflight(self) -> None:
        args = self._args()
        target = (self.ws / R.CHECKLIST_REL).resolve()
        real = Path.read_bytes

        def guarded(p: Path) -> bytes:
            if p.resolve() == target:
                raise PermissionError(13, "Permission denied", str(p))
            return real(p)

        with unittest.mock.patch.object(Path, "read_bytes", guarded):
            pf = R.run_preflight(R.build_parser().parse_args(["preflight", *args]))
        self.assertFalse(pf.ok)
        self.assertTrue(any("checklist unreadable" in f for f in pf.failures), pf.failures)

    def test_non_utf8_checklist_fails_preflight(self) -> None:
        bad = self.tmp / "bad" / "C.md"
        bad.parent.mkdir()
        bad.write_bytes(b"\x80\x81 not utf-8")
        pf = R.run_preflight(R.build_parser().parse_args(["preflight", *self._args("--checklist", str(bad))]))
        self.assertTrue(any("checklist unreadable" in f for f in pf.failures), pf.failures)

    def test_present_checklist_echoed(self) -> None:
        pf = R.run_preflight(R.build_parser().parse_args(["preflight", *self._args()]))
        self.assertTrue(pf.ok, pf.failures)
        self.assertEqual(pf.as_dict()["tools"]["checklist"], str(self.ws / R.CHECKLIST_REL))

    def test_none_recorded_not_run_at_start(self) -> None:
        code, out, err = run_cli("start", *self._args("--checklist", "none"), "--state-dir", str(self.state),
                                 "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        cov = json.loads(out)["coverage"]["checklist"]
        self.assertEqual(cov["status"], "not-run-optional")
        self.assertIn("NOT RUN", cov["note"])


class IsolatedSourceTests(_StartedRun):
    """Fix c: children read a detached checkout of the exact revision, never the live tree."""

    def test_isolate_checks_out_exact_revision_without_touching_source(self) -> None:
        base = self._start()
        repo = self.tmp / "app"
        head = commit(repo, "src/a.py", "print('a')\n")
        (repo / "README.md").write_text("dirty edit\n", encoding="utf-8")
        (repo / "untracked.txt").write_text("u\n", encoding="utf-8")
        before = sh(repo, "status", "--porcelain=v1", "--untracked-files=all")
        worktrees_before = sh(repo, "worktree", "list", "--porcelain")
        code, out, err = run_cli("isolate", *base, "--root", f"repo={repo}")
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertEqual(res["sha"], head)
        checkout = Path(res["checkout"])
        run_dir = (self.state / "weekly-full-audit" / base[1]).resolve()
        self.assertEqual(checkout.resolve(), (run_dir / "src" / "repo").resolve())
        self.assertEqual((checkout / "README.md").read_text(encoding="utf-8"), "seed\n")
        self.assertFalse((checkout / "untracked.txt").exists())
        self.assertTrue((checkout / "src" / "a.py").is_file())
        self.assertEqual(sh(checkout, "rev-parse", "HEAD"), head)
        self.assertEqual(L.root_info("repo", checkout).identity, L.root_info("repo", repo).identity)
        self.assertEqual(sh(repo, "status", "--porcelain=v1", "--untracked-files=all"), before)
        self.assertEqual(sh(repo, "worktree", "list", "--porcelain"), worktrees_before)
        entry = self._rec(base)["isolated"]["repo"]
        self.assertEqual({k: entry[k] for k in ("name", "path_arg", "sha")},
                         {"name": "repo", "path_arg": ".", "sha": head})
        self.assertEqual(entry["checkout"], checkout.resolve().as_posix())
        code, _, err = run_cli("isolate", *base, "--root", f"repo={repo}")
        self.assertEqual(code, 2)
        self.assertIn("already isolated", err)
        ws = self.tmp / "ws"
        code, out, err = run_cli("isolate", *base, "--root", f"workspace={ws}", "--rev", "HEAD~1")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["sha"], sh(ws, "rev-parse", "HEAD~1"))

    def test_baseline_and_report_use_isolated_sha(self) -> None:
        base = self._start()
        repo = self.tmp / "app"
        iso = json.loads(run_cli("isolate", *base, "--root", f"repo={repo}")[1])["sha"]
        commit(repo, "later.txt", "moved on\n")  # live HEAD moves after isolation
        code, _, err = run_cli("baseline", *base, "--root", f"repo={repo}", "--report-dir", str(self.tmp / "r"),
                               "--pattern", "*.md")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._rec(base)["roots"]["repo"]["head_rev"], iso)

    def test_isolate_refused_after_baseline_and_for_unknown_root(self) -> None:
        base = self._start()
        repo = self.tmp / "app"
        code, _, err = run_cli("isolate", *base, "--root", f"nope={repo}")
        self.assertEqual(code, 2)
        self.assertIn("not a preflighted root", err)
        self.assertEqual(run_cli("baseline", *base, "--root", f"repo={repo}", "--report-dir", str(self.tmp / "r"),
                                 "--pattern", "*.md")[0], 0)
        code, _, err = run_cli("isolate", *base, "--root", f"repo={repo}")
        self.assertEqual(code, 2)
        self.assertIn("before its baseline", err)

    def test_release_src_removes_only_run_created_dirs(self) -> None:
        base = self._start()
        repo = self.tmp / "app"
        checkout = Path(json.loads(run_cli("isolate", *base, "--root", f"repo={repo}")[1])["checkout"])
        code, _, err = run_cli("release-src", *base)
        self.assertEqual(code, 2)
        self.assertIn("outputs-audit", err)
        self.assertEqual(run_cli("outputs-audit", *base)[0], 0)
        code, _, err = run_cli("release-src", *base)
        self.assertEqual(code, 0, err)
        self.assertFalse(checkout.exists())
        self.assertTrue((repo / ".git").is_dir())
        self.assertTrue(self._rec(base)["isolated"]["repo"]["released"])

    def test_release_src_refuses_dest_outside_run_dir(self) -> None:
        base = self._start()
        self.assertEqual(run_cli("isolate", *base, "--root", f"repo={self.tmp / 'app'}")[0], 0)
        self.assertEqual(run_cli("outputs-audit", *base)[0], 0)
        outside = self.tmp / "keep-me"
        outside.mkdir()
        rp = Path(base[3]) / "weekly-full-audit" / f"{base[1]}.json"
        rec = L.load_record(rp)
        rec["isolated"]["repo"]["dest"] = outside.as_posix()
        L.save_record(rp, rec)
        code, _, err = run_cli("release-src", *base)
        self.assertEqual(code, 2)
        self.assertIn("outside", err)
        self.assertTrue(outside.is_dir())


class OutputsAttributionTests(_StartedRun):
    """Fix e: run-owned dirs are audited strictly; live target roots are only observed."""

    def test_unrelated_live_root_writer_is_unattributed_not_defect(self) -> None:
        base = self._start()
        app = self.tmp / "app"
        (app / "notes.txt").write_text("another session\n", encoding="utf-8")
        (app / "README.md").write_text("edited by a hook\n", encoding="utf-8")
        scan = self.tmp / "scan"
        scan.mkdir()
        code, out, err = run_cli("outputs-audit", *base, "--scan-dir", str(scan))
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertFalse(res["defect"])
        paths = sorted(x["path"] for x in res["unattributed"])
        self.assertEqual(paths, sorted([(app / "notes.txt").resolve().as_posix(),
                                        (app / "README.md").resolve().as_posix()]))
        rec = self._rec(base)
        self.assertIn("observed:unattributed-target-changes", [e["event"] for e in rec["events"]])
        self.assertNotIn("undeclared_outputs", rec)
        self.assertEqual(run_cli("record", *base, "--coverage", "lens-1=complete", "--inspection", "complete")[0], 0)
        rec = self._rec(base)
        without = {k: v for k, v in rec.items() if k != "unattributed_target_changes"}
        self.assertEqual(L.derive_run(rec), L.derive_run(without))  # observation never moves the floor
        md = L.results_markdown(self._rec(base), None)
        self.assertIn("Unattributed target changes (informational)", md)
        self.assertIn("notes.txt", md)

    def test_preexisting_dirty_state_is_not_reported(self) -> None:
        base = self._start()
        app = self.tmp / "app"
        (app / "README.md").write_text("dirty before the second run\n", encoding="utf-8")
        self.assertIn("repo", self._rec(base)["target_snapshots"])
        sd2 = self.tmp / "s2"
        code, out, err = run_cli("start", *self.weekly_args(app, self.tmp / "ws", "--deliver", "none"),
                                 "--state-dir", str(sd2), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        base2 = ["--run-id", json.loads(out)["run_id"], "--state-dir", str(sd2)]
        code, out, err = run_cli("outputs-audit", *base2)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["unattributed"], [])

    def test_undeclared_file_in_isolated_checkout_is_defect(self) -> None:
        base = self._start()
        checkout = Path(json.loads(run_cli("isolate", *base, "--root", f"repo={self.tmp / 'app'}")[1])["checkout"])
        (checkout / "scratch.py").write_text("x\n", encoding="utf-8")
        code, out, _ = run_cli("outputs-audit", *base)
        self.assertEqual(code, 1)
        res = json.loads(out)
        self.assertTrue(res["defect"])
        self.assertEqual(res["undeclared"], [(checkout / "scratch.py").resolve().as_posix()])
        self.assertIn("defect:undeclared-outputs", [e["event"] for e in self._rec(base)["events"]])

    def test_nested_checkout_skipped_with_note(self) -> None:
        base = self._start()
        scan = self.tmp / "scan"
        nested = scan / "wt"
        nested.mkdir(parents=True)
        (nested / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        (nested / "busy.txt").write_text("other session\n", encoding="utf-8")
        code, out, err = run_cli("outputs-audit", *base, "--scan-dir", str(scan))
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertEqual(res["undeclared"], [])
        self.assertEqual(res["skipped_nested"], [nested.resolve().as_posix()])

    def test_declared_outputs_pass_and_live_root_scan_refused(self) -> None:
        base = self._start()
        audits = self.tmp / "app" / "audits"
        audits.mkdir()
        report = audits / "r.md"
        report.write_text("r\n", encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--artifact", f"report={report}")[0], 0)
        code, out, err = run_cli("outputs-audit", *base, "--scan-dir", str(audits))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"undeclared": [], "defect": False, "unattributed": [],
                                           "skipped_nested": [], "modified_after_collect": []})
        code, _, err = run_cli("outputs-audit", *base, "--scan-dir", str(self.tmp))
        self.assertEqual(code, 2)
        self.assertIn("live target root", err)


class ChildRuleToolingTests(_StartedRun):
    """Fix g: a quoted method line must be line k; outputs are frozen at collect."""

    def _returned(self, base: list[str]) -> tuple[Path, int, Path]:
        self.assertEqual(run_cli("record", *base, "--require", "sec")[0], 0)
        evd = Path(run_cli("evidence-dir", *base)[1].strip())
        meth = self.tmp / "m.md"
        meth.write_text("alpha rule\nbeta rule\ngamma rule\ndelta rule\n", encoding="utf-8")
        ev = evd / "S1.md"
        code, out, err = dispatch(base, "sec", "S1", meth, ev)
        self.assertEqual(code, 0, err)
        return meth, json.loads(out)["methods"][0]["line"], ev

    @staticmethod
    def _body(meth: Path, k: int, quote: str = "") -> str:
        line = L.method_lines(meth)[k - 1]
        tail = f" {quote}" if quote else ""
        return (f"method-sha256: {meth.as_posix()} {L.file_sha256(meth)}\n"
                f"method-line: {meth.as_posix()} {k} {L.line_sha256(line)}{tail}\n")

    def test_quoted_text_must_be_line_k(self) -> None:
        base = self._start()
        meth, k, ev = self._returned(base)
        lines = L.method_lines(meth)
        other = lines[k % 4]  # a different non-blank line
        check = ("check", *base, "--key", "sec", "--state", "returned", "--inspected", "x/", "--evidence", str(ev))
        ev.write_text(self._body(meth, k, f'"{other}"'), encoding="utf-8")
        code, _, err = run_cli(*check)
        self.assertEqual(code, 2)
        self.assertIn("quoted text does not match line", err)
        ev.write_text(self._body(meth, k, f"`{lines[k - 1]}`"), encoding="utf-8")
        code, _, err = run_cli(*check)
        self.assertEqual(code, 0, err)

    def _collect(self, base: list[str]) -> Path:
        meth, k, ev = self._returned(base)
        ev.write_text(self._body(meth, k), encoding="utf-8")
        code, _, err = run_cli("check", *base, "--key", "sec", "--state", "returned", "--inspected", "x/",
                               "--evidence", str(ev))
        self.assertEqual(code, 0, err)
        self.assertEqual(self._rec(base)["checks"]["sec"]["output_sha256"],
                         {ev.resolve().as_posix(): L.file_sha256(ev)})
        return ev

    def test_output_modified_after_collect_is_defect(self) -> None:
        base = self._start()
        ev = self._collect(base)
        self.assertEqual(run_cli("outputs-audit", *base)[0], 0)
        with ev.open("a", encoding="utf-8") as fh:
            fh.write("self-edit after collect\n")
        code, out, _ = run_cli("outputs-audit", *base)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["modified_after_collect"], [ev.resolve().as_posix()])
        rec = self._rec(base)
        self.assertIn("defect:output-modified-after-collect", [e["event"] for e in rec["events"]])
        self.assertEqual(run_cli("record", *base, "--inspection", "complete")[0], 0)
        code, _, err = run_cli("record", *base, "--run", "ok")
        self.assertEqual(code, 2)
        self.assertIn("softer", err)
        self.assertEqual(run_cli("record", *base, "--run", "degraded")[0], 0)

    def test_set_run_detects_modification_without_outputs_audit(self) -> None:
        base = self._start()
        ev = self._collect(base)
        ev.write_text("replaced\n", encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--inspection", "complete")[0], 0)
        code, _, _ = run_cli("record", *base, "--run", "ok")
        self.assertEqual(code, 2)
        self.assertEqual(run_cli("record", *base, "--run", "degraded")[0], 0)
        rec = self._rec(base)
        self.assertEqual(rec["modified_after_collect"], [ev.resolve().as_posix()])
        self.assertIn("modified after collect", L.results_markdown(rec, None))


class SweepRunLogTests(Fixture):
    """Fix d: the sweep writes a create-only run log and RESULTS.md names it."""

    def test_run_log_written_create_only_and_in_results(self) -> None:
        repo = self.repo("app", "https://github.com/O/App.git")
        code, out, err = run_cli("start", "--task", "slop-audit-sweep", "--root", f"app={repo}",
                                 "--tools-root", str(self.tools), "--grrp", str(self.grrp), "--deliver", "file",
                                 "--state-dir", str(self.state), "--watchdog-dir", str(self.wd))
        self.assertEqual(code, 0, err)
        base = ["--run-id", json.loads(out)["run_id"], "--state-dir", str(self.state)]
        body = self.tmp / "log-body.md"
        body.write_text("# sweep run log\n- step 0 ok\n", encoding="utf-8")
        logs = self.tmp / "01_BuildLog"
        made = []
        for _ in range(2):
            code, out, err = run_cli("write-run-log", *base, "--dir", str(logs),
                                     "--stem", "2026-10-30__slop-audit-sweep-run", "--body-file", str(body))
            self.assertEqual(code, 0, err)
            made.append(Path(out.strip()))
        self.assertEqual([p.name for p in made], ["2026-10-30__slop-audit-sweep-run.md",
                                                  "2026-10-30__slop-audit-sweep-run-2.md"])
        self.assertEqual(run_cli("record", *base, "--coverage", "app=complete", "--inspection", "complete")[0], 0)
        self.assertEqual(run_cli("record", *base, "--run", "ok")[0], 0)
        gate = self.tmp / "gate.json"
        gate.write_text("{}", encoding="utf-8")
        self.assertEqual(run_cli("record", *base, "--gate-file", str(gate))[0], 0)
        self.assertEqual(run_cli("publish", *base)[0], 0)
        code, out, err = run_cli("deliver-file", *base)
        self.assertEqual(code, 0, err)
        results = Path(json.loads(out)["ref"]).read_text(encoding="utf-8")
        self.assertIn(f"- run log: {made[1]}", results)
        self.assertNotIn("not recorded", results)

    def test_sweep_skill_documents_run_log(self) -> None:
        text = (SKILLS / "slop-audit-sweep" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Outputs", text)
        self.assertIn("--run-log-dir", text)
        self.assertIn("RUN write-run-log", text)


ARCHIVE_PATHSPECS = ("':(exclude,glob)**/99_Archive/**'", "':(exclude,glob)**/_backups/**'",
                     "':(exclude,glob)**/alembic/versions/**'")
ARCHIVE_RG = ("--glob '!**/99_Archive/**'", "--glob '!**/_backups/**'", "--glob '!**/alembic/versions/**'")


class ArchivePathspecTests(unittest.TestCase):
    """Fix f: the documented exclusions also cover a top-level 99_Archive (1a4-5 miss)."""

    def test_skills_carry_the_pathspecs(self) -> None:
        for skill in ("slop-audit-sweep", "weekly-full-audit"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            for spec in (*ARCHIVE_PATHSPECS, *ARCHIVE_RG):
                with self.subTest(skill=skill, spec=spec):
                    self.assertIn(spec, text)

    def test_pathspecs_exclude_top_level_and_nested_archives(self) -> None:
        with tempfile.TemporaryDirectory(prefix="archive-spec-", ignore_cleanup_errors=True) as d:
            repo = make_repo(Path(d), "r")
            for rel in ("99_Archive/x.txt", "a/99_Archive/y.txt", "_backups/z.txt",
                        "backend/alembic/versions/v1.py", "src/keep.txt"):
                commit(repo, rel, "needle\n")
            specs = [shlex.split(s)[0] for s in ARCHIVE_PATHSPECS]
            listed = sh(repo, "ls-files", "--", *specs).splitlines()
            grepped = sh(repo, "grep", "-l", "needle", "--", *specs).splitlines()
            self.assertEqual(sorted(listed), ["README.md", "src/keep.txt"])
            self.assertEqual(grepped, ["src/keep.txt"])
            rg = shutil.which("rg")
            if rg is None:
                self.skipTest("rg not on PATH (git pathspecs verified)")
            globs = [x for s in ARCHIVE_RG for x in shlex.split(s)]
            cp = subprocess.run([rg, "-l", "needle", *globs, "."], cwd=repo, capture_output=True, text=True)
            names = [n.replace("\\", "/").removeprefix("./") for n in cp.stdout.splitlines()]
            self.assertEqual(names, ["src/keep.txt"])


class ChildRuleBriefTests(unittest.TestCase):
    """Fix g (brief text) and fix a (G4 wording) are documented in the SKILLs."""

    def test_child_rules_in_both_briefs(self) -> None:
        for skill in ("slop-audit-sweep", "weekly-full-audit"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            for phrase in ("no `cd`", "git -C", ".addendum-N.md", "hash-only"):
                with self.subTest(skill=skill, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_g4_wording_is_inspected_revision(self) -> None:
        text = (SKILLS / "weekly-full-audit" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("the working tree at HEAD and nothing else", text)
        self.assertNotIn("Do not claim G4 state for any commit other than HEAD", text)
        self.assertIn("exact inspected revision", text)
