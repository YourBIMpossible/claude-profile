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
import os
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

    def batch(self, *parts: str, metrics: str = METRICS_DONE, endpoint: str = ENDPOINT, ledger: bool = True,
              keep_record: bool = False) -> Path:
        led = block("ledger", f"id: L1\npath: {self.ledger_path}") if ledger else ""
        kept = ""
        if keep_record and self.path.exists():
            old = self.path.read_text(encoding="utf-8")
            kept = old[old.index("## Record\n") + len("## Record\n"):old.index("## Checkpoint")]
        self.path.write_text(endpoint + "\n## Ledgers\n" + led + "\n## Items\n" + "".join(parts)
                             + "\n## Metrics\n" + block("metrics", metrics) + "\n## Record\n" + (kept or "\n")
                             + "## Checkpoint\n- next\n", encoding="utf-8")
        return self.path

    def check(self) -> dict:
        return grrp.batch_check(self.path, self.queue)

    def record(self) -> None:
        rc, _ = self.run_cli("batch", str(self.path), "--record")
        self.assertIn(rc, (0, 1, 2, 3))

    def recorded_check(self) -> dict:
        """The archive claim needs the endpoint and inventory recorded: record, then check."""
        self.record()
        return self.check()

    def raise_then_accept(self, iid: str, oid: str, *others: str, **kw: str) -> None:
        """The honest acceptance path: the ask, naming what it accepts, is raised while open; then the
        owner resolves it."""
        self.batch(item(iid, "blocked_owner", owner_action=oid, **kw),
                   owner_action(oid, f"accept exclusion of {iid}", accepts=iid), *others)
        self.record()
        self.batch(item(iid, "owner_accepted", acceptance=oid, **kw),
                   owner_action(oid, f"accept exclusion of {iid}", status="done", accepts=iid,
                                resolution="owner accepted in chat 2026-10-14"), *others, keep_record=True)

    def run_cli(self, *args: str) -> tuple[int, str]:
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = grrp.main([*args, "--queue", str(self.queue)])
        return rc, buf.getvalue()


class CompletePath(Base):
    def test_all_cleared_is_archive_ready(self) -> None:
        self.raise_then_accept("B-4", "OA-9", item("B-1", "complete"), item("B-2", "superseded", replacement="B-1"),
                               item("B-3", "inapplicable", reviewed="independent reviewer 2026-10-14"))
        r = self.recorded_check()
        self.assertEqual(r["verdict"], "complete", r)
        self.assertEqual(r["claim"], "100% COMPLETE — READY TO ARCHIVE")
        rc, out = self.run_cli("batch", str(self.path))
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("100% COMPLETE — READY TO ARCHIVE"))


class CoordinatorLaneIsNotTheBatch(Base):
    """(1) A complete coordinator-repair lane cannot imply a complete audit batch."""

    def test_complete_lane_and_complete_ledger_leave_batch_open(self) -> None:
        self.batch(item("B-COORD", "complete", kind="activation", text="coordinator repair merged and active",
                        check="ledger L1 complete"),
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
        self.assertEqual(self.recorded_check()["verdict"], "complete")

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
        self.assertEqual(self.recorded_check()["verdict"], "complete")

    def test_open_mirror_item_is_outstanding(self) -> None:
        self.batch(item("B-MIRROR", "open", kind="mirror"))
        r = self.check()
        self.assertTrue(any(x.startswith("B-MIRROR: open (mirror") for x in r["outstanding"]))


class SupersededDeployment(Base):
    """(4) Superseded deployment instructions do not trigger another deployment."""

    def test_superseded_instruction_is_cleared_not_actionable(self) -> None:
        self.batch(item("B-DEPLOY", "complete", kind="delivery", text="deploy 8ccdeec0", check="ledger L1 complete"),
                   item("B-T2", "superseded", kind="instruction", text="owner restarts the backend", replacement="B-DEPLOY"))
        r = self.recorded_check()
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
        self.raise_then_accept("B-SIGNIN", "OA-1", kind="behavior", covers="L1:F-001")
        self.assertEqual(self.recorded_check()["verdict"], "complete")

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

    def errors(self) -> list[str]:
        r = self.check()
        self.assertEqual(r["verdict"], "invalid", r)
        return r["validation_errors"]

    def test_repeated_ledger_id_is_invalid_not_overwritten(self) -> None:
        bad = self.tmp / "bad.ledger.md"
        bad.write_text(BRIEF + block("criterion", OK_CRIT)
                       + finding("F-001", execution_state="integrated", obligations=""), encoding="utf-8")
        self.batch(block("ledger", f"id: L1\npath: {bad}"), item("B-1", "complete"))
        self.assertTrue(any("grrp-ledger L1: id repeated" in e for e in self.errors()))

    def test_two_ids_for_one_ledger_file_is_invalid(self) -> None:
        self.batch(block("ledger", f"id: L2\npath: {self.ledger_path}"), item("B-1", "complete"))
        self.assertTrue(any("same file as L1" in e for e in self.errors()))

    def test_empty_inventory_is_invalid(self) -> None:
        self.batch()
        self.assertTrue(any("empty inventory" in e for e in self.errors()))
        self.batch(item("B-1", "complete"), ledger=False)
        self.assertTrue(any("no grrp-ledger" in e for e in self.errors()))

    def test_unsafe_item_id_is_invalid(self) -> None:
        for bad in ("B 1", "B-1,B-2"):
            self.batch(item(bad, "open"), item("B-5", "blocked_owner", owner_action="OA-1"),
                       owner_action("OA-1", "sign in"))
            self.assertTrue(any("id must be" in e for e in self.errors()), bad)

    def test_repeated_owner_action_id_is_invalid(self) -> None:
        self.batch(item("B-1", "complete"), owner_action("OA-1", "sign in"),
                   owner_action("OA-1", "sign in", status="done", resolution="owner 2026-10-13"))
        self.assertTrue(any("owner action OA-1: id repeated" in e for e in self.errors()))

    def test_replacement_must_resolve(self) -> None:
        self.batch(item("B-1", "complete"), item("B-2", "superseded", replacement="NOPE-999"))
        self.assertTrue(any("replacement" in e for e in self.errors()))
        self.batch(item("B-1", "complete"), item("B-2", "superseded", replacement="L1:F-001"))
        self.assertEqual(self.recorded_check()["verdict"], "complete")

    def test_checked_kind_needs_a_check(self) -> None:
        self.batch(item("B-M", "complete", kind="mirror"))
        self.assertTrue(any("needs a check" in e for e in self.errors()))
        self.batch(item("B-M", "complete", kind="mirror", check="none"))
        self.assertTrue(any("needs a check" in e for e in self.errors()))
        self.batch(item("B-M", "complete", kind="mirror", check="none (mirror lives on another machine)"))
        self.assertEqual(self.recorded_check()["verdict"], "complete")

    def test_relative_paths_resolve_against_the_manifest(self) -> None:
        (self.tmp / "same.txt").write_text("x\n", encoding="utf-8")
        (self.tmp / "same2.txt").write_text("x\n", encoding="utf-8")
        self.batch(item("B-M", "complete", kind="mirror", check="same same.txt == same2.txt; file same.txt"))
        text = self.path.read_text(encoding="utf-8").replace(f"path: {self.ledger_path}",
                                                             f"path: {self.ledger_path.name}")
        self.path.write_text(text, encoding="utf-8")
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with contextlib.chdir(elsewhere):
            self.assertEqual(self.recorded_check()["verdict"], "complete")

    def test_vacuous_checks_fail(self) -> None:
        f = self.tmp / "same.txt"
        f.write_text("x\n", encoding="utf-8")
        for spec in (f"same {f} == {f}", f"git-ancestor HEAD HEAD {self.tmp}", f"git-ancestor --all HEAD {self.tmp}"):
            self.batch(item("B-M", "complete", kind="mirror", check=spec))
            self.assertEqual(self.check()["verdict"], "incomplete", spec)

    def test_endpoint_none_is_only_for_authority_gaps(self) -> None:
        for key in ("Runs", "Archive evidence"):
            ep = "\n".join(f"- {key}: none" if ln.startswith(f"- {key}:") else ln for ln in ENDPOINT.splitlines()) + "\n"
            self.batch(item("B-1", "complete"), endpoint=ep)
            self.assertTrue(any(key in e for e in self.errors()), key)
        ep = ENDPOINT.replace("- Authority gaps: owner sign-in for acceptance checks", "- Authority gaps: none")
        self.batch(item("B-1", "complete"), endpoint=ep)
        self.assertEqual(self.recorded_check()["verdict"], "complete")

    def test_owner_accepted_needs_a_done_owner_action(self) -> None:
        self.batch(item("B-1", "owner_accepted", acceptance="OA-1"), owner_action("OA-1", "accept exclusion"))
        self.assertTrue(any("owner_accepted: its owner action is not done" in e for e in self.errors()))
        self.batch(item("B-1", "owner_accepted", acceptance="owner accepted 2026-10-14"))
        self.assertTrue(any("owner_accepted: acceptance names no" in e for e in self.errors()))

    def test_blocked_owner_on_a_closed_owner_action_is_invalid(self) -> None:
        for st in ("done", "declined"):
            self.batch(item("B-5", "blocked_owner", owner_action="OA-1"),
                       owner_action("OA-1", "sign in", status=st, resolution="owner 2026-10-14"))
            self.assertTrue(any("no longer open" in e for e in self.errors()), st)

    def test_metrics_end_before_start_is_invalid(self) -> None:
        self.batch(item("B-1", "complete"), metrics=METRICS_OPEN.replace("end: none", "end: 2000-01-01T00:00:00Z"))
        self.assertTrue(any("earlier than start" in e for e in self.errors()))


class NoCrash(Base):
    """A crash would exit 1, which reads as "incomplete"; every failure is invalid (3)."""

    def test_out_of_range_datetime_is_invalid(self) -> None:
        for start in ("0001-01-01T00:00:00+05:00", "9999-12-31T23:00:00-05:00"):
            self.batch(item("B-1", "complete"), metrics=METRICS_DONE.replace("2026-10-14T01:50:25Z", start))
            self.assertEqual(self.run_cli("batch", str(self.path))[0], 3, start)

    def test_nul_in_check_fails_the_claim(self) -> None:
        self.batch(item("B-M", "complete", kind="mirror", check=f"git-ancestor a\x00 HEAD {self.tmp}"))
        self.assertEqual(self.check()["verdict"], "incomplete")

    def test_unexpected_error_exits_invalid(self) -> None:
        self.batch(item("B-1", "complete"))
        orig = grrp.batch_check

        def boom(*_a: object, **_k: object) -> dict:
            raise KeyError("boom")
        grrp.batch_check = boom
        self.addCleanup(setattr, grrp, "batch_check", orig)
        self.assertEqual(self.run_cli("batch", str(self.path))[0], 3)


class LedgerCoverage(Base):
    """While a ledger still shows a deferral or open transfer, only an owner acceptance clears it."""

    def setUp(self) -> None:
        super().setUp()
        TransferredOrDeferredCannotDisappear.ledger_with_deferral(self)  # type: ignore[arg-type]

    DEFERRED = TransferredOrDeferredCannotDisappear.DEFERRED

    def test_complete_item_cannot_clear_a_live_deferral(self) -> None:
        self.batch(item("B-1", "complete", covers="L1:F-001"))
        r = self.check()
        self.assertEqual(r["verdict"], "incomplete")
        self.assertTrue(any("still deferred" in x for x in r["failed_claims"]), r["failed_claims"])

    def test_superseded_item_cannot_clear_a_live_deferral(self) -> None:
        self.batch(item("B-0", "complete"), item("B-1", "superseded", covers="L1:F-001", replacement="B-0"))
        self.assertEqual(self.check()["verdict"], "incomplete")


class RecordedThingsCannotDisappear(Base):
    def test_deleting_a_recorded_ledger_is_invalid(self) -> None:
        self.batch(item("B-1", "complete"))
        self.record()
        self.batch(item("B-1", "complete"), ledger=False)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("grrp-record", text)  # batch() rewrote the file; restore the record by hand
        self.path.write_text(text.replace("## Record\n", "## Record\n" + block("record", "at: x\nledgers: L1")),
                             encoding="utf-8")
        self.assertTrue(any("grrp-ledger L1 was recorded but is gone" in e
                            for e in self.check()["validation_errors"]))

    def test_deleting_a_recorded_owner_action_is_invalid(self) -> None:
        self.batch(item("B-5", "blocked_owner", owner_action="OA-1"), owner_action("OA-1", "sign in"))
        self.record()
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("owner_actions: OA-1", text)
        start = text.index("\n```grrp-owner-action\nid: OA-1")
        end = text.index("```\n", start + 5) + 4
        self.path.write_text(text[:start] + text[end:], encoding="utf-8")
        self.assertTrue(any("owner action OA-1 was recorded but is gone" in e
                            for e in self.check()["validation_errors"]))

    def test_endpoint_is_pinned_by_record(self) -> None:
        self.batch(item("B-1", "complete"))
        self.record()
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("endpoint: EP-", text)
        changed = text.replace("- Runs: weekly full audit 2026-10-12;", "- Runs: weekly full audit only;")
        self.path.write_text(changed, encoding="utf-8")
        self.assertTrue(any("endpoint changed" in e for e in self.check()["validation_errors"]))
        pin = text.split("endpoint: ", 1)[1].split()[0]
        amended = changed.replace("- Archive evidence:",
                                  f"- Amended: owner narrowed the runs 2026-10-14 (from {pin})\n- Archive evidence:")
        self.path.write_text(amended, encoding="utf-8")
        r = self.check()
        self.assertEqual(r["validation_errors"], [])
        self.assertEqual(r["verdict"], "incomplete")  # the amended endpoint is not recorded yet
        self.record()  # the amended endpoint is re-pinned
        self.assertEqual(self.path.read_text(encoding="utf-8").count("endpoint: EP-"), 2)
        self.assertEqual(self.check()["verdict"], "complete")


class RealRestart(Base):
    """(7) Separate processes: the contract, inventory and raised asks survive a restart."""

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        script = HERE.parent / "scripts" / "grrp.py"
        return subprocess.run([sys.executable, str(script), *args, "--queue", str(self.queue)],
                              capture_output=True, text=True, encoding="utf-8")

    def test_restart_keeps_contract_and_asks(self) -> None:
        self.batch(item("B-1", "complete"), item("B-5", "blocked_owner", owner_action="OA-1"),
                   owner_action("OA-1", "sign in and run the hub check"))
        first = self.cli("batch", str(self.path), "--record")
        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertIn("Owner actions, new (1)", first.stdout)
        second = self.cli("batch", str(self.path), "--record")
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertNotIn("Owner actions, new", second.stdout)
        self.assertIn("awaiting (already raised", second.stdout)
        self.assertEqual(self.path.read_text(encoding="utf-8").count("```grrp-record"), 1)

    def test_batch_init_matches_the_exact_slug(self) -> None:
        d = self.tmp / "grrp"
        d.mkdir()
        (d / "2026-10-01-nightly-audit.batch.md").write_text("x", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(grrp.main(["batch-init", "--slug", "audit", "--dir", str(d)]), 0)
        self.assertIn("created: ", buf.getvalue())  # not "existing: ...nightly-audit.batch.md"


class RecordIsTheContract(Base):
    """The archive claim stands on a recorded contract: ids, item content, ledger files and every
    ledger obligation any record saw are binding afterwards (second-review findings N1-N8)."""

    DEFERRED = TransferredOrDeferredCannotDisappear.DEFERRED

    def deferral(self) -> None:
        TransferredOrDeferredCannotDisappear.ledger_with_deferral(self)  # type: ignore[arg-type]

    def test_never_recorded_is_not_archive_ready(self) -> None:
        self.batch(item("B-1", "complete"))
        r = self.check()
        self.assertEqual(r["verdict"], "incomplete")
        self.assertTrue(any("not recorded" in x for x in r["record_open"]), r["record_open"])

    def test_deleted_record_block_is_not_archive_ready(self) -> None:
        self.batch(item("B-1", "complete"))
        self.assertEqual(self.recorded_check()["verdict"], "complete")
        text = self.path.read_text(encoding="utf-8")
        s = text.index("```grrp-record")
        self.path.write_text(text[:s] + text[text.index("```\n", s + 5) + 4:], encoding="utf-8")
        self.assertEqual(self.check()["verdict"], "incomplete")

    def test_edited_earlier_record_breaks_the_chain(self) -> None:
        self.batch(item("B-1", "complete"))
        self.record()
        self.batch(item("B-1", "complete"), item("B-2", "complete"), keep_record=True)
        self.record()
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("```grrp-record"), 2)
        self.path.write_text(text.replace("items: B-1\n", "items: B-1, B-9\n", 1), encoding="utf-8")
        self.assertTrue(any("chain broken" in e for e in self.check()["validation_errors"]))

    def test_ledger_path_swap_after_record_is_invalid(self) -> None:  # N1
        self.deferral()
        clean = self.tmp / "clean.ledger.md"
        clean.write_text(BRIEF + block("criterion", OK_CRIT)
                         + finding("F-001", execution_state="reviewed", obligations=REVIEW_ONLY_OBL), encoding="utf-8")
        self.batch(item("B-1", "complete"), item("B-DEF", "deferred", covers="L1:F-001"))
        self.record()
        text = self.path.read_text(encoding="utf-8").replace(f"path: {self.ledger_path}", f"path: {clean}")
        self.path.write_text(text.replace("status: deferred", "status: complete"), encoding="utf-8")
        self.assertTrue(any("path changed" in e for e in self.check()["validation_errors"]))

    def test_recorded_ledger_obligation_must_stay_carried(self) -> None:  # N1
        self.deferral()
        self.batch(item("B-1", "complete"))
        self.record()
        self.assertIn("obligations: L1:F-001", self.path.read_text(encoding="utf-8"))
        self.complete_ledger()  # the same ledger file, now without the deferral
        r = self.recorded_check()
        self.assertEqual(r["verdict"], "incomplete")
        self.assertTrue(any("recorded as open" in x for x in r["ledger_open"]), r["ledger_open"])

    def test_ledger_update_lets_its_carrier_clear(self) -> None:
        self.deferral()
        self.batch(item("B-1", "complete"), item("B-DEF", "deferred", covers="L1:F-001"))
        self.record()
        self.complete_ledger()  # verification done and written to the ledger: the legitimate route
        self.batch(item("B-1", "complete"), item("B-DEF", "complete", covers="L1:F-001"), keep_record=True)
        self.assertEqual(self.recorded_check()["verdict"], "complete")

    def test_done_owner_action_never_raised_cannot_accept(self) -> None:  # N2
        self.batch(item("B-1", "owner_accepted", acceptance="OA-X"),
                   owner_action("OA-X", "unrelated: approve font change", status="done", accepts="B-1",
                                resolution="owner said ok 2026-10-13"))
        self.record()
        self.assertTrue(any("never raised" in e for e in self.check()["validation_errors"]))

    def test_acceptance_must_name_the_item(self) -> None:  # N2
        self.raise_then_accept("B-1", "OA-1")
        text = self.path.read_text(encoding="utf-8").replace("accepts: B-1\n", "accepts: B-7\n")
        self.path.write_text(text, encoding="utf-8")
        self.assertTrue(any("does not list this item" in e for e in self.check()["validation_errors"]))

    def test_replacement_cycle_and_dangling_ref_are_invalid(self) -> None:  # N4
        self.batch(item("B-1", "superseded", replacement="B-2"), item("B-2", "superseded", replacement="B-1"))
        self.assertTrue(any("loops back" in e for e in self.check()["validation_errors"]))
        self.batch(item("B-1", "complete"), item("B-2", "duplicate", replacement="L1:NOPE-999"))
        self.assertTrue(any("has no finding or transfer" in e for e in self.check()["validation_errors"]))

    def test_empty_or_mixed_none_check_runs_nothing_free(self) -> None:  # N5
        self.batch(item("B-M", "complete", kind="mirror", check=";"))
        self.assertTrue(any("needs a check" in e for e in self.check()["validation_errors"]))
        self.batch(item("B-M", "complete", kind="mirror", check="none (n/a); file does-not-exist.txt"))
        self.assertEqual(self.check()["verdict"], "incomplete")
        self.batch(item("B-M", "complete", kind="mirror", check="nonexistent_tool --verify"))
        self.assertEqual(self.check()["verdict"], "incomplete")

    def test_amendment_binds_to_the_pin_it_replaces(self) -> None:  # N6
        self.batch(item("B-1", "complete"))
        self.record()
        text = self.path.read_text(encoding="utf-8")
        pin = text.split("endpoint: ", 1)[1].split()[0]
        step = text.replace("- Runs: weekly full audit 2026-10-12;", "- Runs: weekly full audit only;").replace(
            "- Archive evidence:", f"- Amended: owner narrowed the runs 2026-10-14 (from {pin})\n- Archive evidence:")
        self.path.write_text(step, encoding="utf-8")
        self.record()  # re-pinned
        again = self.path.read_text(encoding="utf-8").replace("changes merged and active", "a report exists")
        self.path.write_text(again, encoding="utf-8")
        self.assertTrue(any("endpoint changed" in e for e in self.check()["validation_errors"]))

    def test_backdated_amendment_does_not_count(self) -> None:  # N6
        self.batch(item("B-1", "complete"))
        self.record()
        text = self.path.read_text(encoding="utf-8")
        pin = text.split("endpoint: ", 1)[1].split()[0]
        self.path.write_text(text.replace("- Runs: weekly full audit 2026-10-12;", "- Runs: x;").replace(
            "- Archive evidence:", f"- Amended: owner 2026-01-01 (from {pin})\n- Archive evidence:"), encoding="utf-8")
        self.assertTrue(any("endpoint changed" in e for e in self.check()["validation_errors"]))

    def test_endpoint_can_be_pinned_before_execution(self) -> None:  # N7
        self.batch()  # endpoint and ledger stated, no items yet: invalid, but recordable
        self.assertEqual(self.check()["verdict"], "invalid")
        self.record()
        self.assertIn("endpoint: EP-", self.path.read_text(encoding="utf-8"))

    def test_invalid_run_still_never_re_asks(self) -> None:  # N7
        self.batch(item("B-5", "blocked_owner", owner_action="OA-1"), item("B-X", "complete", kind="bogus"),
                   owner_action("OA-1", "sign in and run the hub check"))
        rc, out = self.run_cli("batch", str(self.path), "--record")
        self.assertEqual(rc, 3)
        self.assertIn("Owner actions, new (1)", out)
        rc, out = self.run_cli("batch", str(self.path), "--record")
        self.assertNotIn("Owner actions, new", out)

    def test_item_dropped_while_invalid_is_still_caught(self) -> None:  # N7
        self.batch(item("B-1", "complete"), item("B-2", "open"), item("B-X", "complete", kind="bogus"))
        self.record()
        self.batch(item("B-1", "complete"), keep_record=True)
        self.assertTrue(any("B-2 was recorded but is gone" in e for e in self.check()["validation_errors"]))

    def test_item_content_is_pinned(self) -> None:  # N8
        self.batch(item("B-M", "open", kind="mirror", text="publish Workspace mirror"))
        self.record()
        self.batch(item("B-M", "complete", kind="obligation", text="n/a"), keep_record=True)
        self.assertTrue(any("changed after it was recorded" in e for e in self.check()["validation_errors"]))

    def test_repeated_endpoint_key_and_fenced_version_are_invalid(self) -> None:  # m2
        ep = ENDPOINT.replace("- Runs: weekly", "- Runs: tbd\n- Runs: weekly")
        self.batch(item("B-1", "complete"), endpoint=ep)
        self.assertTrue(any("stated more than once" in e for e in self.check()["validation_errors"]))
        ep = ENDPOINT.replace("grrp-batch: v1\n", "~~~\ngrrp-batch: v1\n~~~\n")
        self.batch(item("B-1", "complete"), endpoint=ep)
        self.assertTrue(any("grrp-batch: v1" in e for e in self.check()["validation_errors"]))

    def test_hard_link_compare_is_vacuous(self) -> None:  # m3
        a, b = self.tmp / "a.txt", self.tmp / "b.txt"
        a.write_text("x\n", encoding="utf-8")
        try:
            os.link(a, b)
        except OSError:
            self.skipTest("no hard links here")
        self.batch(item("B-M", "complete", kind="mirror", check=f"same {a} == {b}"))
        self.assertEqual(self.check()["verdict"], "incomplete")

    def test_future_end_is_invalid(self) -> None:  # m4
        self.batch(item("B-1", "complete"), metrics=METRICS_OPEN.replace("end: none", "end: 2099-01-01T00:00:00Z"))
        self.assertTrue(any("in the future" in e for e in self.check()["validation_errors"]))

    def test_accepts_added_after_the_ask_does_not_accept(self) -> None:  # NEW-1 case 1
        self.deferral()
        self.batch(item("B-DEF", "blocked_owner", covers="L1:F-001", owner_action="OA-1"),
                   owner_action("OA-1", "sign in and run the hub check"))
        self.record()
        self.batch(item("B-DEF", "owner_accepted", covers="L1:F-001", acceptance="OA-1"),
                   owner_action("OA-1", "sign in and run the hub check", status="done", accepts="B-DEF",
                                resolution="owner ran it 2026-10-14"), keep_record=True)
        self.record()
        r = self.check()
        self.assertNotEqual(r["verdict"], "complete")
        self.assertTrue(any("never raised with this action and accepts" in e for e in r["validation_errors"]))

    def test_same_text_under_another_id_does_not_accept(self) -> None:  # NEW-1 case 2
        self.deferral()
        self.batch(item("B-DEF", "blocked_owner", covers="L1:F-001", owner_action="OA-1"),
                   owner_action("OA-1", "sign in and run the hub check", accepts="B-DEF"))
        self.record()
        self.batch(item("B-DEF", "owner_accepted", covers="L1:F-001", acceptance="OA-Z"),
                   owner_action("OA-1", "sign in and run the hub check", status="done", accepts="B-DEF",
                                resolution="owner ran it 2026-10-14"),
                   owner_action("OA-Z", "Sign in and  run the hub check", status="done", accepts="B-DEF",
                                resolution="owner ran it 2026-10-14"), keep_record=True)
        self.record()
        self.assertTrue(any("never raised with this action and accepts" in e
                            for e in self.check()["validation_errors"]))

    def test_widened_accepts_on_an_open_ask_is_asked_again(self) -> None:  # NEW-1
        self.batch(item("B-1", "blocked_owner", owner_action="OA-1"), item("B-2", "blocked_owner", owner_action="OA-1"),
                   owner_action("OA-1", "accept the exclusions", accepts="B-1"))
        self.record()
        self.batch(item("B-1", "blocked_owner", owner_action="OA-1"), item("B-2", "blocked_owner", owner_action="OA-1"),
                   owner_action("OA-1", "accept the exclusions", accepts="B-1, B-2"), keep_record=True)
        self.assertEqual([a["id"] for a in self.check()["owner_actions_new"]], ["OA-1"])

    def test_none_with_semicolon_runs_its_specs(self) -> None:  # review 3 minor
        self.batch(item("B-M", "complete", kind="mirror", check="none (a); file nope.txt (b)"))
        self.assertNotEqual(self.recorded_check()["verdict"], "complete")

    def test_second_endpoint_heading_is_invalid(self) -> None:  # review 3 minor
        self.batch(item("B-1", "complete"), endpoint=ENDPOINT + "\n## Endpoint\n- Runs: other\n")
        self.assertTrue(any("appears more than once" in e for e in self.check()["validation_errors"]))


if __name__ == "__main__":
    unittest.main()
