"""GRRP v1 rollout: every enrolled audit workflow loads GRRP before discovery, gates at exit,
and keeps its own substantive sections; representative audit-shaped ledgers gate correctly.

Workflow files live in several repos and in the scheduled-task store. Each root resolves from
GRRP_ROOT_<KEY> (to test a worktree before merge), else its live location. A root that does not
exist on this machine skips; a root that exists but is not enrolled FAILS — that is the
"automatically uses GRRP" check against what actually runs.

Run: python skills/review-to-resolution/tests/test_enrollment_rollout.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPT = SKILL / "scripts" / "grrp.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPT.parent))

import grrp  # noqa: E402
from test_review_to_resolution_conformance import BRIEF, REVIEW_ONLY_OBL, block, finding  # noqa: E402

ROOTS = {
    "PROFILE": str(HERE.parents[2]),
    "BIMPOSSIBLE": r"F:\BIMpossible",
    "ADDINS": r"F:\BIMpossible-AddIns",
    "WORKSPACE": r"F:\BIMpossible-Workspace",
    "TASKS": str(Path.home() / ".claude" / "scheduled-tasks"),
}

# name: (root key, relative path, slug prefix, substantive anchors that must survive enrollment)
ENROLLED = {
    "slop-audit": ("PROFILE", "skills/slop-audit/SKILL.md", "slop-audit-",
                   ["## The three checks", "Silent-catch", "Counter-integrity", "Tested-but-dead", "## Output contract"]),
    "local-audit": ("PROFILE", "skills/local-audit/SKILL.md", "local-audit-",
                    ["## Standing constraints", "HYPOTHESIS** — the local model is", "## Run it", "## Scope discipline"]),
    "revit-functionality-audit": ("ADDINS", ".claude/skills/revit-functionality-audit/SKILL.md",
                                  "revit-functionality-audit-", ["## Mode", "## Non-negotiable method", "## Output contract"]),
    "revit-tool-review": ("ADDINS", ".claude/skills/revit-tool-review/SKILL.md", "revit-tool-review-",
                          ["## Hard constraints", "## Method — three passes", "## The 10-section checklist"]),
    "audit": ("BIMPOSSIBLE", ".claude/skills/audit/SKILL.md", "audit-bimpossible-",
              ["## Scope", "## INCREMENTAL mode", "## FULL mode"]),
    "bim-code-review": ("BIMPOSSIBLE", ".claude/skills/bim-code-review/SKILL.md", "bim-code-review-",
                        ["## Step 1 — Pin the fixed point", "## Step 4 — Spawn parallel subagents"]),
    "synthetic-concurrency-audit": ("BIMPOSSIBLE", ".claude/skills/synthetic-concurrency-audit/SKILL.md",
                                    "synthetic-concurrency-audit-", ["## Hard constraints", "## How to run"]),
    "security-review-full": ("BIMPOSSIBLE", ".claude/commands/security-review-full.md", "security-review-full-",
                             ["## Scope — what to scan", "## What to look for", "## Avoid re-flagging known findings"]),
    "revit-project-audit": ("WORKSPACE", ".claude/commands/revit-project-audit.md", "revit-project-audit-",
                            ["## Critical API Rules", "## Step 1 — Worksharing"]),
    "bimpossible-weekly-full-audit": ("TASKS", "bimpossible-weekly-full-audit/SKILL.md", "weekly-full-audit-",
                                      ["REALITY-CHECK (ctxcheck)", "ADVERSARIAL VERIFY", "DIRTY-TREE GUARD",
                                       "AUTO-FIX PASS", "HANDOFF (mandatory)"]),
}


def resolve(key: str, rel: str) -> Path:
    return Path(os.environ.get(f"GRRP_ROOT_{key}", ROOTS[key])) / rel


class EnrolledWorkflows(unittest.TestCase):
    def read(self, name: str) -> str:
        key, rel, _, _ = ENROLLED[name]
        if not Path(os.environ.get(f"GRRP_ROOT_{key}", ROOTS[key])).exists():
            self.skipTest(f"{key} root absent on this machine")
        p = resolve(key, rel)
        self.assertTrue(p.is_file(), f"{name}: definition missing at {p}")
        return p.read_text(encoding="utf-8")

    def test_entry_precedes_discovery_and_exit_gates(self) -> None:
        for name, (_, _, prefix, anchors) in ENROLLED.items():
            with self.subTest(name):
                text = self.read(name)
                begin, end = text.find("<!-- grrp-enrollment:begin -->"), text.find("<!-- grrp-enrollment:end -->")
                self.assertNotEqual(begin, -1, f"{name}: not enrolled (no GRRP entry block)")
                entry = text[begin:end]
                self.assertIn(f"grrp.py init --slug {prefix}", entry)
                self.assertIn("references/enrollment.md", entry)
                body = text.find("\n---", 3) + 4 if text.startswith("---") else 0  # skip frontmatter
                first = min(text.find(a, body) for a in anchors)
                self.assertLess(end, first, f"{name}: GRRP entry must precede the workflow's own steps")
                exit_ = text[text.find("<!-- grrp-exit:begin -->"):]
                self.assertIn("gate --json", exit_, name)
                self.assertIn("transfers_ok", exit_, name)
                self.assertIn("attestations", exit_, name)

    def test_substantive_checks_retained(self) -> None:
        for name, (_, _, _, anchors) in ENROLLED.items():
            with self.subTest(name):
                text = self.read(name)
                body = text[text.find("\n---", 3) + 4:] if text.startswith("---") else text
                for a in anchors:
                    self.assertIn(a, body, f"{name}: substantive section '{a}' lost")

    def test_one_entry_and_exit_block_each(self) -> None:
        for name in ENROLLED:
            with self.subTest(name):
                text = self.read(name)
                self.assertEqual(text.count("<!-- grrp-enrollment:begin -->"), 1)
                self.assertEqual(text.count("<!-- grrp-exit:begin -->"), 1)

    def test_weekly_audit_ledger_stays_inside_its_roots(self) -> None:
        text = self.read("bimpossible-weekly-full-audit")
        self.assertIn(r'--dir "F:\BIMpossible-Workspace\02_Reference\Audit and Scan Info\grrp"', text)

    def test_slop_audit_weekly_reports_ledger_verdicts(self) -> None:
        root = Path(os.environ.get("GRRP_ROOT_TASKS", ROOTS["TASKS"]))
        p = root / "slop-audit-weekly" / "SKILL.md"
        if not p.exists():
            self.skipTest("scheduled-task store absent")
        text = p.read_text(encoding="utf-8")
        self.assertIn("slop-audit", text)
        self.assertIn("GRRP", text)
        self.assertIn("transfers_ok", text)

    def test_skill_md_lists_every_enrolled_workflow(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for name in ENROLLED:
            self.assertIn(f"`{name}`", text, name)
        self.assertIn("references/enrollment.md", text)


CRIT_RAN = "id: AC-1\ntext: every check/lens ran over its scope\nstatus: met\nevidence: report coverage section"
CRIT_RAN_UNMET = "id: AC-1\ntext: every check/lens ran over its scope\nstatus: unmet\nevidence: Security lens NOT RUN"
AC2 = block("criterion", "id: AC-2\ntext: every finding in report and ledger\nstatus: met\nevidence: report findings table")


def audit_finding(fid: str, **kw: str) -> str:
    base = {"source": "discovered", "classification": "independent", "assessment": "confirmed_defect",
            "resolution": "open", "requirement": "n/a: report-only audit finding", "severity": "high",
            "evidence": "backend/x.py:12 re-demonstrated by grep", "obligations": REVIEW_ONLY_OBL.replace(
                "review=done:lens receipts", "review=done:adversarial verify")}
    base.update(kw)
    return finding(fid, **base)


def transfer(findings: str, ack: str | None) -> str:
    body = (f"id: T-1\nfindings: {findings}\nreceiver: audit-resolution\n"
            "next_action: triage in audit-resolution_<date>.md\nacceptance: each row terminal\n"
            "follow_up: next weekly run diffs against it")
    return block("transfer", body + (f"\nack: {ack}" if ack else ""))


class RepresentativeAuditRuns(unittest.TestCase):
    """Audit-shaped ledgers (enrollment.md mapping) through the real gate and CLI."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.queue = self.tmp / "queue.yaml"
        self.queue.write_text("schema_version: 1\nitems: []\n", encoding="utf-8")

    def run_gate(self, *parts: str, crit: str = CRIT_RAN) -> dict:
        p = self.tmp / "2026-10-12-weekly-full-audit-2026-10-12.ledger.md"
        p.write_text(BRIEF + block("criterion", crit) + AC2 + "".join(parts), encoding="utf-8")
        api = grrp.gate(grrp.parse(p), self.queue)
        cli = subprocess.run([sys.executable, str(SCRIPT), "gate", "--json", "--queue", str(self.queue), str(p)],
                             capture_output=True, text=True)
        out = json.loads(cli.stdout)
        self.assertEqual(out["verdict"], api["verdict"])
        self.assertEqual(cli.returncode, grrp.EXIT[api["verdict"]])
        return out

    def test_verified_findings_handed_to_resolution_close(self) -> None:
        dismissed = finding("F-002", source="discovered", assessment="disproved", requirement="n/a: dismissed",
                            evidence="refuted: guard at api.py:40", obligations=REVIEW_ONLY_OBL)
        out = self.run_gate(audit_finding("F-001"), dismissed,
                            transfer("F-001", "audit-resolution accepted F-001 in "
                                     "02_Reference/Audit and Scan Info/audit-resolution_2026-10-12.md"))
        self.assertEqual((out["verdict"], out["transfers_ok"], out["closeable"]), ("complete", True, True))
        self.assertEqual(out["portfolio_open"], ["F-001"])

    def test_handoff_not_yet_accepted_is_not_closeable(self) -> None:
        out = self.run_gate(audit_finding("F-001"), transfer("F-001", None))
        self.assertEqual((out["verdict"], out["transfers_ok"], out["closeable"]), ("complete", False, False))

    def test_hypothesis_finding_keeps_run_incomplete(self) -> None:
        out = self.run_gate(audit_finding("F-001", assessment="unassessed", evidence="local model HYPOTHESIS"),
                            transfer("F-001", "owner queue entry Q-9 2026-10-12"))
        self.assertEqual(out["verdict"], "incomplete")

    def test_lens_not_run_keeps_run_incomplete(self) -> None:
        out = self.run_gate(crit=CRIT_RAN_UNMET)
        self.assertEqual(out["verdict"], "incomplete")

    def test_open_critical_blocking_scope_is_blocked(self) -> None:
        out = self.run_gate(audit_finding("F-001", severity="critical", classification="blocker", blocks="AC-1",
                                          requirement="AC-1"))
        self.assertEqual(out["verdict"], "blocked")

    def test_untransferred_open_finding_holds_run(self) -> None:
        out = self.run_gate(audit_finding("F-001"))
        self.assertEqual(out["verdict"], "incomplete")

    def test_free_text_ack_is_shape_checked_only(self) -> None:
        # Accepted limitation: any non-blank ack passes. Pinned so a later change is deliberate;
        # enrollment.md and every exit block state that this is an attestation, not authorization.
        out = self.run_gate(audit_finding("F-001"), transfer("F-001", "owner said fine"))
        self.assertTrue(out["transfers_ok"])
        text = (SKILL / "references" / "enrollment.md").read_text(encoding="utf-8")
        self.assertIn("attestations, not verified authorization", " ".join(text.split()))

    def test_dated_slug_starts_fresh_same_slug_resumes(self) -> None:
        d = self.tmp / "grrp"

        def init(slug: str) -> str:
            return subprocess.run([sys.executable, str(SCRIPT), "init", "--slug", slug, "--dir", str(d)],
                                  capture_output=True, text=True, check=True).stdout

        self.assertIn("created:", init("weekly-full-audit-2026-10-12"))
        self.assertIn("existing:", init("weekly-full-audit-2026-10-12"))
        self.assertIn("created:", init("weekly-full-audit-2026-10-19"))


if __name__ == "__main__":
    unittest.main()
