"""GRRP coordinator: worker receipts (grrp-event) are ingested, reconciled and handed off.

Sanitized from the 2026-10-12/13 weekly-audit incident: a deploy lane outside the parent
session finished a deployment the ledger never learned about; a late status report said the
deployment was still pending; the same report was delivered twice; a behaviour check was
denied by the permission classifier; a check waited on an unavailable fixture; and every lane
wrote its own decision table for the owner to reconcile.

Run: python skills/review-to-resolution/tests/test_grrp_coordinator_events.py   (or pytest)
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
from test_review_to_resolution_conformance import BRIEF, OK_CRIT, block, finding  # noqa: E402

MERGED_NOT_LIVE = "review=done:review receipt; integration=done:merge 8c0ffee; deployment=pending; verification=pending"
FULL_PENDING_VERIFY = ("review=done:review receipt; integration=done:merge 8c0ffee; deployment=done:release r1; "
                       "verification=pending; cleanup=n/a:nothing created")


def ev(**kw: str) -> str:
    return block("event", "\n".join(f"{k}: {v}" for k, v in kw.items()))


DEPLOYED = dict(target="SEC-X60", track="deployment", outcome="done", at="2026-10-13T21:12:47Z",
                by="deploy-lane", evidence="backend image f7b7, health 200, smoke PASSED")
LATE_PENDING = dict(target="SEC-X60", track="deployment", outcome="pending", at="2026-10-13T07:36:00Z",
                    by="delivery-lane", evidence="precondition stop; nothing changed")
DENIED = dict(target="SEC-X60", track="verification", outcome="denied", at="2026-10-13T21:40:00Z",
              by="deploy-lane", operation="container exec pytest", evidence="classifier: Remote Shell Writes")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queue = self.tmp / "queue.yaml"
        self.path = self.tmp / "2026-10-12-wfa.ledger.md"
        self.queue.write_text(f"items:\n- id: Q-RES\n  status: ready\n  source: {self.path.name}\n", encoding="utf-8")

    def ledger(self, *parts: str) -> Path:
        self.path.write_text(BRIEF + block("criterion", OK_CRIT) + "".join(parts), encoding="utf-8")
        return self.path

    def incident(self, *extra: str) -> Path:
        """SEC-X60: fixed and merged, not yet deployed, transferred to the resolution queue (as on 2026-10-12)."""
        return self.ledger(
            finding("SEC-X60", classification="independent", resolution="open", execution_state="integrated",
                    owner="queue:Q-RES", lane="external", requirement="n/a: independent defect",
                    obligations=MERGED_NOT_LIVE),
            block("transfer", "id: T-2\nfindings: SEC-X60\nreceiver: queue:Q-RES\n"
                              "next_action: owner restarts the backend from main 8c0ffee\n"
                              "acceptance: fix live and verified\nfollow_up: /next sync"),
            *extra)

    def receipt(self, name: str, *events: dict[str, str]) -> Path:
        box = grrp.inbox_dir(self.path)
        box.mkdir(exist_ok=True)
        p = box / name
        p.write_text("# lane receipt (prose is ignored)\n" + "".join(ev(**e) for e in events), encoding="utf-8")
        return p

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = grrp.main(list(argv) + (["--queue", str(self.queue)] if argv[0] != "where" else []))
        return rc, out.getvalue()

    def apply(self, *receipts: Path, dry: bool = False) -> tuple[int, str]:
        return self.run_cli("apply", str(self.path), *map(str, receipts), *(["--dry-run"] if dry else []))

    def state(self, target: str, track: str) -> str:
        return grrp.reconcile(grrp.parse(self.path)).states[(target, track)].outcome

    def handoff(self) -> dict:
        return grrp.handoff(grrp.parse(self.path), self.queue)


class LateHistoricalReport(Base):
    def test_late_report_cannot_reopen_completed_deployment(self) -> None:
        self.incident()
        self.receipt("deploy-lane.md", DEPLOYED)
        self.apply()
        self.receipt("remaining-delivery.md", LATE_PENDING)  # older `at`, arrives after
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual(self.state("SEC-X60", "deployment"), "done")
        h = self.handoff()
        self.assertTrue(any(x.endswith(f"-> {grrp.event_id(DEPLOYED)}") for x in h["superseded_events"]))

    def test_stale_pending_cannot_reopen_a_done_baseline(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY.replace("verification=pending",
                                                                          "verification=done:check log")))
        newer_pending = {**LATE_PENDING, "target": "F-1", "at": "2026-10-14T00:00:00Z"}
        self.receipt("r.md", newer_pending)
        self.apply()
        self.assertEqual(self.state("F-1", "deployment"), "done")
        self.assertEqual(len(self.handoff()["stale_events"]), 1)

    def test_explicit_supersede_may_reopen(self) -> None:
        self.incident()
        self.receipt("a.md", DEPLOYED)
        self.apply()
        rollback = dict(target="SEC-X60", track="deployment", outcome="pending", at="2026-10-14T09:00:00Z",
                        by="deploy-lane", evidence="rolled back to anchor", supersedes=grrp.event_id(DEPLOYED))
        self.receipt("b.md", rollback)
        self.apply()
        self.assertEqual(self.state("SEC-X60", "deployment"), "pending")

    def test_superseded_transfer_instruction_is_reported_not_repeated(self) -> None:
        self.incident()
        self.receipt("a.md", DEPLOYED, dict(target="T-2", track="transfer", outcome="superseded",
                                            at="2026-10-13T21:12:47Z", by="deploy-lane",
                                            evidence="deploy done by the deploy lane"))
        self.apply()
        h = self.handoff()
        self.assertEqual([t["transfer"] for t in h["superseded_transfers"]], ["T-2"])
        self.assertIn("superseded by", grrp.render_handoff(h))


class DuplicateResults(Base):
    def test_same_receipt_twice_is_one_event(self) -> None:
        self.incident()
        self.receipt("deploy-lane.md", DEPLOYED)
        self.receipt("deploy-lane-resend.md", DEPLOYED)  # the report was sent twice
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertIn("added 1", out)
        self.assertIn("duplicates 1", out)
        before = self.path.read_text(encoding="utf-8")
        rc, out = self.apply()
        self.assertIn("added 0", out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(len(grrp.parse(self.path).of("event")), 1)

    def test_same_content_under_another_id_is_one_event(self) -> None:
        self.incident()
        self.receipt("a.md", DEPLOYED)
        self.apply()
        self.receipt("b.md", {**DEPLOYED, "id": "E-lane-copy"})
        _, out = self.apply()
        self.assertIn("added 0", out)

    def test_reused_id_with_new_content_is_rejected(self) -> None:
        self.incident()
        self.receipt("a.md", {**DEPLOYED, "id": "E-1"})
        self.apply()
        self.receipt("b.md", {**LATE_PENDING, "id": "E-1"})
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("different content", out)


class ConflictingEvidence(Base):
    def test_same_time_disagreement_stays_unresolved(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        a = dict(target="F-1", track="verification", outcome="done", at="2026-10-13T22:00:00Z",
                 by="lane-a", evidence="toast shown")
        b = dict(target="F-1", track="verification", outcome="failed", at="2026-10-13T22:00:00Z",
                 by="lane-b", evidence="alert dialog shown")
        self.receipt("r.md", a, b)
        self.apply()
        self.assertEqual(self.state("F-1", "verification"), "conflict")
        g = grrp.gate(grrp.parse(self.path), self.queue)
        self.assertEqual(g["verdict"], "incomplete")
        self.assertEqual(g["conflicts"], ["F-1: verification"])
        # A newer claim that ignores the conflict does not settle it.
        self.receipt("r2.md", {**a, "at": "2026-10-13T23:00:00Z"})
        self.apply()
        self.assertEqual(self.state("F-1", "verification"), "conflict")
        # Naming the losing side does.
        self.receipt("r3.md", {**a, "at": "2026-10-14T08:00:00Z", "evidence": "re-run: toast shown",
                               "supersedes": grrp.event_id(b)})
        self.apply()
        self.assertEqual(self.state("F-1", "verification"), "done")
        self.assertEqual(grrp.gate(grrp.parse(self.path), self.queue)["verdict"], "complete")

    def test_same_time_disagreement_is_a_conflict_in_either_order(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        at = "2026-10-13T22:00:00Z"
        self.receipt("r.md", dict(target="F-1", track="verification", outcome="failed", at=at, by="lane-b",
                                  evidence="alert dialog shown"),
                     dict(target="F-1", track="verification", outcome="done", at=at, by="lane-a",
                          evidence="toast shown"))
        self.apply()
        self.assertEqual(self.state("F-1", "verification"), "conflict")

    def test_hand_merged_duplicate_events_count_once(self) -> None:
        # Two lanes' copies of one receipt pasted into the ledger under different ids are one event,
        # so the copy cannot pose as a same-time second opinion or a newer replacement.
        fail = dict(target="F-1", track="verification", outcome="failed", at="2026-10-13T22:00:00Z",
                    by="lane", evidence="alert dialog shown")
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY), "\n## Events\n",
                    ev(id="E-a", **fail), ev(id="E-b", **fail))
        rec = grrp.reconcile(grrp.parse(self.path))
        self.assertEqual(rec.states[("F-1", "verification")].event_id, "E-a")
        self.assertEqual(rec.superseded, [])

    def test_new_failure_after_done_is_a_conflict_not_an_overwrite(self) -> None:
        self.incident()
        self.receipt("a.md", DEPLOYED, dict(target="SEC-X60", track="deployment", outcome="failed",
                                            at="2026-10-14T01:00:00Z", by="monitor", evidence="health 500"))
        self.apply()
        self.assertEqual(self.state("SEC-X60", "deployment"), "conflict")
        self.assertTrue(self.handoff()["conflicts"])


class UnavailableFixture(Base):
    def test_deferred_check_does_not_hold_a_complete_audit_open(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        self.assertEqual(grrp.gate(grrp.parse(self.path), self.queue)["verdict"], "incomplete")
        self.receipt("r.md", dict(target="F-1", track="verification", outcome="deferred",
                                  at="2026-10-13T21:50:00Z", by="deploy-lane", owner="owner",
                                  prerequisite="member-firm test fixture on a non-production project"))
        self.apply()
        g = grrp.gate(grrp.parse(self.path), self.queue)
        self.assertEqual(g["verdict"], "complete")
        self.assertTrue(g["closeable"])
        self.assertEqual(g["deferred"], ["F-1: verification deferred"])
        self.assertEqual(g["tracks"]["acceptance"], {"deferred": 1})
        self.assertEqual(g["tracks"]["deployment"], {"done": 1})
        d = self.handoff()["deferred"][0]
        self.assertIn("fixture", d["prerequisite"])
        self.assertEqual(d["since"], "2026-10-13T21:50:00Z")

    def test_deferral_without_owner_or_prerequisite_is_rejected(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        self.receipt("r.md", dict(target="F-1", track="verification", outcome="deferred",
                                  at="2026-10-13T21:50:00Z", by="lane"))
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("deferred needs owner and prerequisite", out)
        self.assertEqual(grrp.gate(grrp.parse(self.path), self.queue)["verdict"], "incomplete")


class DeniedOperation(Base):
    def test_denied_operation_is_not_retried_through_another_route(self) -> None:
        self.incident()
        other_route = dict(target="SEC-X60", track="verification", outcome="done", at="2026-10-13T22:10:00Z",
                           by="agent", evidence="ran the same tests via a different shell")
        self.receipt("r.md", DENIED, other_route)
        self.apply()
        self.assertEqual(self.state("SEC-X60", "verification"), "denied")
        h = self.handoff()
        self.assertEqual(len(h["rejected_events"]), 1)
        self.assertIn("without a dated owner approval", h["rejected_events"][0])
        self.assertEqual([d["kind"] for d in h["decisions_now"]], ["denied"])

    def test_dated_owner_approval_lifts_the_denial(self) -> None:
        self.incident()
        approved = dict(target="SEC-X60", track="verification", outcome="done", at="2026-10-14T10:00:00Z",
                        by="agent", evidence="6 tests passed in the deployed runtime",
                        approval="owner approved exec in chat 2026-10-14")
        self.receipt("r.md", DENIED, approved)
        self.apply()
        self.assertEqual(self.state("SEC-X60", "verification"), "done")
        self.assertEqual(self.handoff()["rejected_events"], [])

    def test_undated_approval_claim_is_rejected_at_ingest(self) -> None:
        self.incident()
        self.receipt("r.md", DENIED, dict(target="SEC-X60", track="verification", outcome="done",
                                          at="2026-10-14T10:00:00Z", by="agent", evidence="x",
                                          approval="the grant covers this"))
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("approval must cite the owner", out)

    def test_deferral_after_denial_is_allowed_and_ends_the_ask(self) -> None:
        self.incident()
        self.receipt("r.md", DENIED, dict(target="SEC-X60", track="verification", outcome="deferred",
                                          at="2026-10-13T21:45:00Z", by="deploy-lane", owner="owner",
                                          prerequisite="exec approval or a member-firm fixture"))
        self.apply()
        h = self.handoff()
        self.assertEqual(h["decisions_now"], [])
        self.assertEqual(len(h["deferred"]), 1)


class IndependentWorkContinues(Base):
    def test_one_blocked_item_does_not_stop_independent_authorized_work(self) -> None:
        self.ledger(
            finding("F-BLOCKED", resolution="open", authority="approval_required", obligations=MERGED_NOT_LIVE),
            finding("F-FREE", resolution="open", obligations=MERGED_NOT_LIVE),
            finding("F-APPROVED", resolution="open", authority="approval_required",
                    action_approval="owner grant 2026-10-12", obligations=MERGED_NOT_LIVE),
            finding("F-PROT", resolution="open", authority="protected", obligations=MERGED_NOT_LIVE),
        )
        h = self.handoff()
        cont = " ".join(x for v in h["continuing"].values() for x in v)
        self.assertIn("F-FREE(deployment,verification)", cont)
        self.assertIn("F-APPROVED", cont)
        self.assertNotIn("F-BLOCKED", cont)  # approval_required without action_approval is not continued
        self.assertNotIn("F-PROT", cont)     # protected surface is never continued by an agent
        self.assertEqual(sorted((d["target"], d["kind"]) for d in h["decisions_now"]),
                         [("F-BLOCKED", "approval"), ("F-PROT", "protected")])

    def test_a_denial_on_one_finding_leaves_others_continuing(self) -> None:
        self.incident(finding("F-FREE", resolution="open", owner="queue:Q-RES", lane="external",
                              classification="independent", requirement="n/a: independent",
                              obligations=MERGED_NOT_LIVE))
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("findings: SEC-X60", "findings: SEC-X60, F-FREE"),
                             encoding="utf-8")
        self.receipt("r.md", DENIED)
        self.apply()
        h = self.handoff()
        self.assertIn("F-FREE(deployment,verification)", " ".join(h["continuing"]["queue:Q-RES"]))


class RestartResume(Base):
    def test_restart_preserves_pending_and_repeats_nothing(self) -> None:
        self.incident()
        self.receipt("deploy-lane.md", DEPLOYED, DENIED)
        self.apply()
        rc, first = self.run_cli("handoff", str(self.path), "--record")
        self.assertIn("denied operation", first)
        recorded = self.path.read_text(encoding="utf-8")
        # restart: a fresh process re-reads the inbox and the ledger
        self.assertIn("added 0", self.apply()[1])
        rc, second = self.run_cli("handoff", str(self.path), "--record")
        self.assertEqual(self.path.read_text(encoding="utf-8"), recorded)  # nothing re-recorded
        h = self.handoff()
        self.assertEqual(h["decisions_now"], [])
        self.assertEqual(len(h["awaiting_owner"]), 1)
        self.assertRegex(h["awaiting_owner"][0]["asked_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(self.state("SEC-X60", "deployment"), "done")
        self.assertEqual(self.state("SEC-X60", "verification"), "denied")

    def test_changed_blocker_is_asked_again(self) -> None:
        self.incident()
        self.receipt("a.md", DENIED)
        self.apply()
        self.run_cli("handoff", str(self.path), "--record")
        self.receipt("b.md", {**DENIED, "at": "2026-10-15T09:00:00Z", "operation": "fixture provisioning"})
        self.apply()
        self.assertEqual(len(self.handoff()["decisions_now"]), 1)

    def test_dry_run_writes_nothing(self) -> None:
        self.incident()
        before = self.path.read_text(encoding="utf-8")
        self.receipt("a.md", DEPLOYED)
        rc, out = self.apply(dry=True)
        self.assertIn("dry-run (nothing written)", out)
        self.assertIn("added 1", out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


class ConsolidatedHandoff(Base):
    def test_owner_gets_one_decision_request_not_a_report_bundle(self) -> None:
        self.incident()
        self.receipt("followups.md", LATE_PENDING)
        self.receipt("deploy-lane.md", DEPLOYED, DENIED)
        self.receipt("deploy-lane-resend.md", DEPLOYED)
        self.apply()
        rc, text = self.run_cli("handoff", str(self.path))
        self.assertEqual(text.count("## Decisions required now"), 1)
        self.assertEqual(len(grrp.handoff(grrp.parse(self.path), self.queue)["decisions_now"]), 1)
        for heading in ("## Completed / superseded",):
            self.assertIn(heading, text)
        self.assertNotIn("followups.md", text.split("## Decisions required now")[1].split("##")[0])

    def test_no_decisions_says_none(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY.replace("verification=pending",
                                                                          "verification=done:check")))
        rc, text = self.run_cli("handoff", str(self.path))
        self.assertEqual(rc, 0)
        self.assertIn("## Decisions required now\n- none", text)


class Boundaries(Base):
    def test_receipt_cannot_write_findings(self) -> None:
        self.incident()
        box = grrp.inbox_dir(self.path)
        box.mkdir()
        (box / "x.md").write_text(finding("SEC-X99"), encoding="utf-8")
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("grrp-finding ignored", out)
        self.assertNotIn("SEC-X99", self.path.read_text(encoding="utf-8"))

    def test_event_with_unknown_key_or_target_is_rejected(self) -> None:
        self.incident()
        self.receipt("r.md", {**DEPLOYED, "note": "free prose"}, {**DEPLOYED, "target": "NOPE-1"})
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("unknown key", out)
        self.assertIn("not a finding id", out)
        self.assertEqual(grrp.parse(self.path).of("event"), [])

    def test_naive_timestamp_is_rejected(self) -> None:
        self.incident()
        self.receipt("r.md", {**DEPLOYED, "at": "2026-10-13 21:12"})
        self.assertIn("timezone", self.apply()[1])

    def test_hand_edited_invalid_event_makes_the_ledger_invalid(self) -> None:
        self.incident(ev(target="SEC-X60", track="deployment", outcome="done", at="2026-10-13T21:12:47Z", by="x"))
        self.assertEqual(grrp.gate(grrp.parse(self.path), self.queue)["verdict"], "invalid")

    def test_event_may_supply_a_missing_obligation(self) -> None:
        obl = "review=done:r; integration=done:m; deployment=done:d; verification=done:v"
        self.ledger(finding("F-1", obligations=obl))
        self.assertEqual(grrp.gate(grrp.parse(self.path), self.queue)["verdict"], "invalid")
        self.receipt("r.md", dict(target="F-1", track="cleanup", outcome="n/a", at="2026-10-13T22:00:00Z",
                                  by="parent", evidence="nothing created"))
        self.apply()
        self.assertEqual(grrp.gate(grrp.parse(self.path), self.queue)["verdict"], "complete")


class Where(unittest.TestCase):
    def test_reports_loaded_revision(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        head = subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], capture_output=True, text=True)
        if head.returncode != 0:
            self.skipTest("not a git checkout")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = grrp.main(["where", "--expect", head.stdout.strip()])
        self.assertEqual(rc, 0)
        self.assertIn('"loaded": true', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(grrp.main(["where", "--expect", "0" * 40]), 3)


FULL_DONE = FULL_PENDING_VERIFY.replace("verification=pending", "verification=done:check v1")


def v(eid: str, outcome: str, at: str, **kw: str) -> dict[str, str]:
    return dict(id=eid, target="F-1", track="verification", outcome=outcome, at=f"2026-10-13T{at}:00Z",
                by=kw.pop("by", "lane-a"), **({"evidence": "x"} if outcome in grrp.NEEDS_EVIDENCE else {}), **kw)


class ReviewRegressions(Base):
    """Independent review of de7fb90: each case was reproduced before the fix."""

    def gate(self) -> dict:
        return grrp.gate(grrp.parse(self.path), self.queue)

    def test_denial_landing_in_a_conflict_still_blocks_a_retry(self) -> None:  # C1, baseline done
        self.ledger(finding("F-1", obligations=FULL_DONE))
        self.receipt("r.md", v("E-D", "denied", "02:00", operation="exec pytest"),
                     v("E-R", "done", "03:00", supersedes="E-D"))
        self.apply()
        rec = grrp.reconcile(grrp.parse(self.path))
        self.assertEqual([x for x, _ in rec.rejected], ["E-R"])
        self.assertNotEqual(self.gate()["verdict"], "complete")

    def test_denial_after_a_done_event_still_blocks_a_retry(self) -> None:  # C1, event done
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        self.receipt("r.md", v("E-A", "done", "01:00"), v("E-D", "denied", "02:00", operation="exec pytest"),
                     v("E-R", "done", "03:00", supersedes="E-D"))
        self.apply()
        self.assertIn("E-R", [x for x, _ in grrp.reconcile(grrp.parse(self.path)).rejected])
        self.assertNotEqual(self.gate()["verdict"], "complete")

    def test_late_intermediate_receipt_cannot_reclose_an_explicit_reopen(self) -> None:  # C2
        for outcome in ("pending", "failed"):
            with self.subTest(outcome):
                self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
                shutil.rmtree(grrp.inbox_dir(self.path), ignore_errors=True)
                self.receipt("a.md", v("E-A", "done", "01:00"), v("E-C", outcome, "03:00", supersedes="E-A"))
                self.receipt("b.md", v("E-B", "done", "02:00", by="lane-b"))  # late, sorts in between
                self.apply()
                self.assertEqual(self.state("F-1", "verification"), outcome)
                self.assertEqual(self.gate()["verdict"], "incomplete")

    def test_pending_after_denial_keeps_the_ask(self) -> None:  # I2
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        self.receipt("r.md", v("E-D", "denied", "02:00", operation="exec pytest"), v("E-P", "pending", "03:00", by="lane-b"))
        self.apply()
        self.assertEqual(self.state("F-1", "verification"), "denied")
        h = self.handoff()
        self.assertEqual([(d["target"], d["kind"]) for d in h["decisions_now"]], [("F-1", "denied")])
        self.assertEqual(h["continuing"], {})

    def test_by_owner_without_dated_approval_is_not_authority(self) -> None:  # I3
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        self.receipt("r.md", v("E-D", "denied", "02:00", operation="exec pytest"), v("E-R", "done", "03:00", by="owner"))
        self.apply()
        self.assertIn("E-R", [x for x, _ in grrp.reconcile(grrp.parse(self.path)).rejected])

    def test_open_work_is_never_missing_from_the_handoff(self) -> None:  # I1
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY),
                    finding("F-2", resolution="open"),
                    finding("F-3", authority="protected", owner_action="owner applied it 2026-10-12",
                            obligations=FULL_PENDING_VERIFY))
        self.receipt("r.md", dict(target="F-1", track="verification", outcome="deferred", at="2026-10-13T01:00:00Z",
                                  by="lane-a", owner="owner", prerequisite="fixture"))
        self.apply()
        h = self.handoff()
        self.assertEqual(self.gate()["verdict"], "incomplete")
        self.assertEqual([x.split(":")[0] for x in h["open_work"]], ["F-2"])
        self.assertEqual(h["continuing"], {"owner (protected surface)": ["F-3(verification)"]})

    def test_apply_does_not_count_as_a_scope_change(self) -> None:  # I4
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        for n, at in enumerate(("01:00", "02:00", "03:00")):
            self.receipt(f"r{n}.md", v(f"E-{n}", "pending", at))
            self.apply()
        self.assertEqual(grrp.metrics(grrp.parse(self.path), self.queue)["scope_changes"], 0)

    def test_receipt_cannot_record_an_escalation(self) -> None:
        self.ledger(finding("F-1", obligations=FULL_PENDING_VERIFY))
        self.receipt("r.md", dict(target="F-1", track="verification", outcome="escalated", at="2026-10-13T01:00:00Z",
                                  by="lane-a", fingerprint="X-000000000000"))
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("handoff --record", out)
        self.assertEqual(grrp.parse(self.path).of("event"), [])

    def test_conflicts_on_prefix_sharing_ids_are_both_reported(self) -> None:
        self.ledger(finding("F-10", resolution="open", obligations=FULL_PENDING_VERIFY),
                    finding("F-1", resolution="open", obligations=FULL_PENDING_VERIFY))
        for fid in ("F-10", "F-1"):  # F-10 first: its line must not hide F-1's
            self.receipt(f"{fid}.md", {**v(f"E-{fid}a", "done", "01:00"), "target": fid},
                         {**v(f"E-{fid}b", "failed", "01:00"), "target": fid})
        self.apply()
        inc = self.gate()["incomplete"]
        self.assertTrue(any(x.startswith("F-1: verification") for x in inc), inc)
        self.assertTrue(any(x.startswith("F-10: verification") for x in inc), inc)


if __name__ == "__main__":
    unittest.main()
