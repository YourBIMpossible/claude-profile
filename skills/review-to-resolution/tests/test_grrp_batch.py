"""GRRP batch: owner-archive-ready completion of one audit batch, distinct from any ledger gate.

Sanitized from the 2026-10-12 audit batch: a coordinator-repair lane finished and was read as
the batch finishing; a ledger-repair PR sat open behind a "complete" closeout; a Workspace mirror
waited unpublished; a superseded "owner restarts the backend" instruction kept resurfacing; four
behaviour checks were deferred and then dropped from later closeouts; every closeout re-assigned
the same owner asks; and each resumed session rebuilt its own inventory.

Run: python skills/review-to-resolution/tests/test_grrp_batch.py   (or pytest)
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import grrp  # noqa: E402
from test_review_to_resolution_conformance import BRIEF, OK_CRIT, REVIEW_ONLY_OBL, block, finding  # noqa: E402

ENDPOINT = """# GRRP batch — wfa-batch
grrp-batch: v1

## Endpoint
- Runs: weekly full audit 2026-10-12; weekly slop audit 2026-10-12
- Follow-through: ledger backfill PR, mirror publication, deployment acceptance, coordinator repair
- Authority: implement, test, review, PR + merge after checks, supported activation; no production failures
- Authority gaps: owner sign-in for acceptance checks
- Archive evidence: every item cleared with evidence; ledger gates complete; changes merged and active
"""
METRICS_OPEN = "start: 2026-10-14T01:50:25Z\nend: none\nblocked_on_owner: 0\navoidable_continuations: 0\nrelay_requests: 0"
METRICS_DONE = METRICS_OPEN.replace("end: none", "end: 2026-10-14T09:00:00Z")


def item(iid: str, status: str, **kw: str) -> str:
    rec = {"kind": "obligation", "text": "obligation text", "source": "closeout 2026-10-13", "owner": "parent",
           "action": "do it and record evidence", "status": status,
           "evidence": "commit abc123; test log" if status != "open" else "none"}
    rec.update(kw)
    return block("item", f"id: {iid}\n" + "\n".join(f"{k}: {v}" for k, v in rec.items()))


def owner_action(oid: str, action: str, status: str = "open", **kw: str) -> str:
    rec = {"action": action, "recommendation": "approve", "why_not_agent": "owner sign-in",
           "enables": "acceptance evidence for B-5", "status": status}
    rec.update(kw)
    return block("owner-action", f"id: {oid}\n" + "\n".join(f"{k}: {v}" for k, v in rec.items()))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queue = self.tmp / "queue.yaml"
        self.queue.write_text("items: []\n", encoding="utf-8")
        self.path = self.tmp / "2026-10-14-wfa-batch.batch.md"
        self.ledger_path = self.tmp / "2026-10-12-wfa.ledger.md"
        self.complete_ledger()

    def complete_ledger(self, *parts: str) -> None:
        body = parts or (finding("F-001", execution_state="reviewed", obligations=REVIEW_ONLY_OBL),)
        self.ledger_path.write_text(BRIEF + block("criterion", OK_CRIT) + "".join(body), encoding="utf-8")

    def batch(self, *parts: str, metrics: str = METRICS_DONE, endpoint: str = ENDPOINT, ledger: bool = True) -> Path:
        led = block("ledger", f"id: L1\npath: {self.ledger_path}") if ledger else ""
        self.path.write_text(endpoint + "\n## Ledgers\n" + led + "\n## Items\n" + "".join(parts)
                             + "\n## Metrics\n" + block("metrics", metrics) + "\n## Record\n\n## Checkpoint\n- next\n",
                             encoding="utf-8")
        return self.path

    def check(self) -> dict:
        return grrp.batch_check(self.path, self.queue)

    def run_cli(self, *args: str) -> tuple[int, str]:
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = grrp.main([*args, "--queue", str(self.queue)])
        return rc, buf.getvalue()


class CompletePath(Base):
    def test_all_cleared_is_archive_ready(self) -> None:
        self.batch(item("B-1", "complete"), item("B-2", "superseded", replacement="B-1"),
                   item("B-3", "inapplicable", reviewed="independent reviewer 2026-10-14"),
                   item("B-4", "owner_accepted", acceptance="owner accepted exclusion 2026-10-14"))
        r = self.check()
        self.assertEqual(r["verdict"], "complete", r)
        self.assertEqual(r["claim"], "100% COMPLETE — READY TO ARCHIVE")
        rc, out = self.run_cli("batch", str(self.path))
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("100% COMPLETE — READY TO ARCHIVE"))


class CoordinatorLaneIsNotTheBatch(Base):
    """(1) A complete coordinator-repair lane cannot imply a complete audit batch."""

    def test_complete_lane_and_complete_ledger_leave_batch_open(self) -> None:
        self.batch(item("B-COORD", "complete", kind="activation", text="coordinator repair merged and active"),
                   item("B-BEHAV", "open", kind="behavior"))
        r = self.check()
        self.assertEqual(r["ledgers"], {"L1": "complete"})
        self.assertEqual(r["verdict"], "incomplete")
        self.assertEqual(r["claim"], "NOT COMPLETE")
        self.assertTrue(any(x.startswith("B-BEHAV:") for x in r["outstanding"]))

    def test_batch_without_endpoint_is_invalid(self) -> None:
        bare = ENDPOINT.replace("- Archive evidence: every item cleared with evidence; ledger gates complete; "
                                "changes merged and active", "- Archive evidence: tbd")
        self.batch(item("B-1", "complete"), endpoint=bare)
        r = self.check()
        self.assertEqual(r["verdict"], "invalid")
        self.assertTrue(any("Archive evidence" in e for e in r["validation_errors"]))

    def test_end_not_recorded_blocks_the_claim(self) -> None:
        self.batch(item("B-1", "complete"), metrics=METRICS_OPEN)
        self.assertEqual(self.check()["verdict"], "incomplete")


class OpenLedgerRepairPR(Base):
    """(2) An open ledger-repair PR prevents archive-ready completion."""

    def test_ledger_invalid_holds_batch_even_if_items_say_complete(self) -> None:
        self.complete_ledger(finding("F-001", execution_state="integrated", obligations=""))  # SA-5-style gap
        self.batch(item("B-PR212", "complete", kind="integration", text="ledger backfill PR"))
        r = self.check()
        self.assertNotEqual(r["verdict"], "complete")
        self.assertTrue(any(x.startswith("L1: ledger gate invalid") for x in r["ledger_open"]), r["ledger_open"])

    def test_claimed_merge_whose_check_fails_is_not_cleared(self) -> None:
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        g = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run([*g, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        subprocess.run([*g, "checkout", "-q", "-b", "pr"], check=True)
        subprocess.run([*g, "commit", "-q", "--allow-empty", "-m", "fix"], check=True)
        pr_head = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run([*g, "checkout", "-q", "-"], check=True)
        self.batch(item("B-PR212", "complete", kind="integration",
                        check=f"git-ancestor {pr_head} HEAD {repo}"))
        r = self.check()
        self.assertEqual(r["verdict"], "incomplete")
        self.assertTrue(r["failed_claims"] and r["failed_claims"][0].startswith("B-PR212: claimed complete"))
        subprocess.run([*g, "merge", "-q", "--ff-only", "pr"], check=True)
        self.assertEqual(self.check()["verdict"], "complete")

    def test_in_progress_pr_is_outstanding(self) -> None:
        self.batch(item("B-PR212", "in_progress", kind="integration"))
        self.assertEqual(self.check()["verdict"], "incomplete")


class PendingMirror(Base):
    """(3) Pending mirror publication stays visible."""

    def test_unpublished_mirror_is_listed(self) -> None:
        canon, mirror = self.tmp / "queue.canon.md", self.tmp / "queue.mirror.md"
        canon.write_text("v2\n", encoding="utf-8")
        mirror.write_text("v1\n", encoding="utf-8")
        self.batch(item("B-MIRROR", "complete", kind="mirror", check=f"same {canon} == {mirror}"))
        r = self.check()
        self.assertEqual(r["verdict"], "incomplete")
        self.assertIn("files differ", r["failed_claims"][0])
        _, out = self.run_cli("batch", str(self.path))
        self.assertIn("B-MIRROR", out)
        mirror.write_text("v2\n", encoding="utf-8")
        self.assertEqual(self.check()["verdict"], "complete")

    def test_open_mirror_item_is_outstanding(self) -> None:
        self.batch(item("B-MIRROR", "open", kind="mirror"))
        r = self.check()
        self.assertTrue(any(x.startswith("B-MIRROR: open (mirror") for x in r["outstanding"]))


class SupersededDeployment(Base):
    """(4) Superseded deployment instructions do not trigger another deployment."""

    def test_superseded_instruction_is_cleared_not_actionable(self) -> None:
        self.batch(item("B-DEPLOY", "complete", kind="delivery", text="deploy 8ccdeec0"),
                   item("B-T2", "superseded", kind="instruction", text="owner restarts the backend", replacement="B-DEPLOY"))
        r = self.check()
        self.assertEqual(r["verdict"], "complete")
        self.assertFalse(any("B-T2" in x for x in r["outstanding"]))
        self.assertEqual(r["owner_actions_new"], [])

    def test_superseded_needs_its_replacement(self) -> None:
        self.batch(item("B-T2", "superseded", kind="instruction"))
        r = self.check()
        self.assertEqual(r["verdict"], "invalid")
        self.assertTrue(any("replacement" in e for e in r["validation_errors"]))


class TransferredOrDeferredCannotDisappear(Base):
    """(5) A transferred or deferred requirement cannot disappear."""

    DEFERRED = dict(target="F-001", track="verification", outcome="deferred", at="2026-10-13T21:44:44Z",
                    by="deploy-lane", owner="owner", prerequisite="owner sign-in")

    def ledger_with_deferral(self) -> None:
        obl = ("review=done:r; integration=done:merge 1; deployment=done:release r1; verification=pending; "
               "cleanup=n/a:nothing created")
        ev = block("event", "\n".join(f"{k}: {v}" for k, v in self.DEFERRED.items()))
        self.complete_ledger(finding("F-001", execution_state="deployed", obligations=obl), "\n## Events\n" + ev)

    def test_ledger_deferral_must_be_carried_by_an_item(self) -> None:
        self.ledger_with_deferral()
        self.batch(item("B-1", "complete"))
        r = self.check()
        self.assertEqual(r["ledgers"], {"L1": "complete"})  # the ledger gate lets a deferral pass ...
        self.assertEqual(r["verdict"], "incomplete")         # ... the batch does not
        self.assertTrue(any("L1:F-001: deferred" in x for x in r["ledger_open"]), r["ledger_open"])

    def test_deferred_item_stays_outstanding_until_owner_accepts(self) -> None:
        self.ledger_with_deferral()
        self.batch(item("B-SIGNIN", "deferred", kind="behavior", covers="L1:F-001"))
        self.assertEqual(self.check()["verdict"], "incomplete")
        self.batch(item("B-SIGNIN", "transferred", kind="behavior", covers="L1:F-001"))
        self.assertEqual(self.check()["verdict"], "incomplete")
        self.batch(item("B-SIGNIN", "owner_accepted", kind="behavior", covers="L1:F-001",
                        acceptance="owner accepted exclusion of sign-in check 2026-10-14"))
        self.assertEqual(self.check()["verdict"], "complete")

    def test_owner_accepted_needs_a_dated_owner_act(self) -> None:
        self.batch(item("B-1", "owner_accepted", acceptance="looks fine"))
        self.assertEqual(self.check()["verdict"], "invalid")

    def test_deleting_a_recorded_item_is_invalid(self) -> None:
        self.batch(item("B-1", "complete"), item("B-DEFER", "deferred"))
        rc, _ = self.run_cli("batch", str(self.path), "--record")
        self.assertEqual(rc, 1)
        text = self.path.read_text(encoding="utf-8")
        start = text.index("\n```grrp-item\nid: B-DEFER")
        end = text.index("```\n", start + 5) + 4
        self.path.write_text(text[:start] + text[end:], encoding="utf-8")
        r = self.check()
        self.assertEqual(r["verdict"], "invalid")
        self.assertTrue(any("B-DEFER was recorded but is gone" in e for e in r["validation_errors"]))


class RepeatedCloseouts(Base):
    """(6) Repeated closeouts cannot generate repeated owner assignments."""

    def test_same_ask_twice_is_one_ask(self) -> None:
        self.batch(item("B-5", "blocked_owner", owner_action="OA-1"),
                   owner_action("OA-1", "Sign in and run the all-rvts foreign hub check"),
                   owner_action("OA-2", "sign in  and run the ALL-RVTS foreign hub check"))
        r = self.check()
        self.assertEqual(r["verdict"], "blocked")
        self.assertEqual(len(r["owner_actions_new"]), 1)
        self.assertEqual(r["duplicate_owner_actions"], ["OA-2 repeats OA-1"])

    def test_recorded_ask_is_not_raised_again(self) -> None:
        self.batch(item("B-5", "blocked_owner", owner_action="OA-1"),
                   owner_action("OA-1", "Sign in and run the all-rvts foreign hub check"))
        rc, out = self.run_cli("batch", str(self.path), "--record")
        self.assertEqual(rc, 2)
        self.assertIn("Owner actions, new (1)", out)
        rc, out = self.run_cli("batch", str(self.path), "--record")
        self.assertEqual(rc, 2)
        self.assertNotIn("Owner actions, new", out)
        self.assertIn("awaiting (already raised", out)
        self.assertEqual(self.path.read_text(encoding="utf-8").count("```grrp-record"), 1)

    def test_blocked_owner_needs_a_listed_owner_action(self) -> None:
        self.batch(item("B-5", "blocked_owner", owner_action="OA-9"))
        self.assertEqual(self.check()["verdict"], "invalid")

    def test_resolved_owner_action_needs_dated_owner_resolution(self) -> None:
        self.batch(item("B-1", "complete"), owner_action("OA-1", "decide", status="done", resolution="handled"))
        self.assertEqual(self.check()["verdict"], "invalid")


class RestartKeepsContract(Base):
    """(7) A restarted session keeps the same contract and inventory."""

    def test_batch_init_returns_existing_manifest(self) -> None:
        d = self.tmp / "grrp"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(grrp.main(["batch-init", "--slug", "wfa-batch", "--dir", str(d)]), 0)
        created = Path(buf.getvalue().split("created: ", 1)[1].strip())
        created.write_text(created.read_text(encoding="utf-8").replace("- Runs:", "- Runs: weekly full audit", 1),
                           encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(grrp.main(["batch-init", "--slug", "wfa-batch", "--dir", str(d)]), 0)
        self.assertIn(f"existing: {created}", buf.getvalue())
        self.assertIn("- Runs: weekly full audit", created.read_text(encoding="utf-8"))
        # A fresh manifest has no endpoint yet: execution cannot be claimed against it.
        self.assertEqual(grrp.batch_check(created, self.queue)["verdict"], "invalid")

    def test_check_is_deterministic_across_restarts(self) -> None:
        self.batch(item("B-1", "complete"), item("B-2", "open"),
                   item("B-5", "blocked_owner", owner_action="OA-1"), owner_action("OA-1", "sign in"))
        first = self.check()
        second = grrp.batch_check(Path(str(self.path)), self.queue)
        self.assertEqual(first, second)
        self.assertEqual(first["verdict"], "incomplete")


class Validation(Base):
    def test_unknown_status_and_placeholder_are_invalid(self) -> None:
        self.batch(item("B-1", "queued"), item("B-2", "complete", evidence="tbd"))
        r = self.check()
        self.assertEqual(r["verdict"], "invalid")
        self.assertEqual(len([e for e in r["validation_errors"] if e.startswith(("item B-1", "item B-2"))]), 2)

    def test_complete_needs_evidence(self) -> None:
        self.batch(item("B-1", "complete", evidence="none"))
        self.assertEqual(self.check()["verdict"], "invalid")

    def test_missing_ledger_file_is_invalid(self) -> None:
        self.ledger_path.unlink()
        self.batch(item("B-1", "complete"))
        self.assertEqual(self.check()["verdict"], "invalid")

    def test_covers_must_name_a_listed_ledger(self) -> None:
        self.batch(item("B-1", "complete", covers="L9:F-001"))
        self.assertEqual(self.check()["verdict"], "invalid")


if __name__ == "__main__":
    unittest.main()
