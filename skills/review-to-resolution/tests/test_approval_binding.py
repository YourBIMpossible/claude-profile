"""F-029 verified approval binding: grrp.verify_approval / approval_request / gate activation.

Keys are throwaway ed25519 pairs generated per test run in a temp dir (never committed, never
an owner credential). Hardware (sk-*) keys cannot be generated without a device, so the
signature tests relax require_hardware or widen HARDWARE_KEY_TYPES; a separate test proves a
software key is rejected under the real rule.

Run: python skills/review-to-resolution/tests/test_approval_binding.py   (or pytest)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import grrp  # noqa: E402
from test_review_to_resolution_conformance import BRIEF, OK_CRIT, block, finding  # noqa: E402

KEYGEN = shutil.which("ssh-keygen")
OWNER = "owner-login"


def _keypair(d: Path, name: str) -> tuple[Path, str]:
    key = d / name
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)], check=True)
    pub = " ".join(key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2])
    return key, pub


@unittest.skipUnless(KEYGEN, "ssh-keygen not available")
class ApprovalBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.owner_key, self.owner_pub = _keypair(self.td, "owner")
        self.agent_key, self.agent_pub = _keypair(self.td, "agent")

    # -- helpers -------------------------------------------------------------------------
    def ledger(self, text_finding: str, name: str = "t.ledger.md") -> grrp.Ledger:
        p = self.td / name
        p.write_text(BRIEF + "\n## Acceptance criteria\n" + block("criterion", OK_CRIT)
                     + "\n## Findings\n" + text_finding, encoding="utf-8")
        led = grrp.parse(p)
        self.assertEqual(led.errors, [])
        return led

    def protected(self, fid: str = "F-100", stmt: str = "approve: edit guard.py allowlist only",
                  sig: str = "none", **kw: str) -> str:
        rec = dict(authority="protected", execution_state="implementing", resolution="open",
                   location="backend/guard.py:10", owner_action=stmt, owner_action_sig=sig)
        rec.update(kw)
        return finding(fid, **rec)

    def sign(self, led: grrp.Ledger, fid: str, kind: str = "owner_action", key: Path | None = None,
             out: str = "F-100.owner_action.sig") -> str:
        f = next(x for x in led.of("finding") if x.get("id") == fid)
        req = self.td / "req.txt"
        req.write_bytes(grrp.approval_request(led, f, kind).encode("utf-8"))
        subprocess.run([KEYGEN, "-q", "-Y", "sign", "-f", str(key or self.owner_key), "-n", grrp.APPROVAL_NAMESPACE,
                        str(req)], check=True)
        (self.td / "req.txt.sig").replace(self.td / out)
        return out

    def signed_ledger(self, **kw: str) -> grrp.Ledger:
        """Owner signs the pre-approval ledger, then the sig locator is recorded."""
        stmt = kw.pop("stmt", "approve: edit guard.py allowlist only")
        pre = self.ledger(self.protected(stmt=stmt, **kw))
        sig = self.sign(pre, "F-100")
        return self.ledger(self.protected(stmt=stmt, sig=sig, **kw))

    def check(self, led: grrp.Ledger, signers: list[str] | None = None, kind: str = "owner_action",
              owner: str | None = OWNER) -> tuple[str, str]:
        f = next(x for x in led.of("finding") if x.get("id") == "F-100")
        return grrp.verify_approval(led, f, kind, owner,
                                    [self.owner_pub] if signers is None else signers, require_hardware=False)

    # -- positive ------------------------------------------------------------------------
    def test_valid_owner_signature_verifies(self) -> None:
        self.assertEqual(self.check(self.signed_ledger())[0], "verified")

    def test_recording_the_signature_and_progress_does_not_invalidate_revision(self) -> None:
        led = self.signed_ledger()
        f = dict(next(x for x in led.of("finding") if x.get("id") == "F-100"))
        f.update(execution_state="implemented", evidence="commit abc", obligations="review=pending")
        self.assertEqual(grrp.verify_approval(led, f, "owner_action", OWNER, [self.owner_pub], False)[0], "verified")

    # -- negative ------------------------------------------------------------------------
    def test_missing_and_legacy_are_not_verified(self) -> None:
        self.assertEqual(self.check(self.ledger(self.protected(stmt="none")))[0], "missing")
        self.assertEqual(self.check(self.ledger(self.protected(stmt="owner ok 2026-10-06")))[0], "legacy")

    def test_pending_or_refused_statement_rejected_even_if_signed(self) -> None:
        for stmt in ("pending owner review", "refused: do not edit guard.py"):
            with self.subTest(stmt=stmt):
                self.assertEqual(self.check(self.signed_ledger(stmt=stmt))[0], "unverified")

    def test_wrong_actor_rejected(self) -> None:
        pre = self.ledger(self.protected())
        sig = self.sign(pre, "F-100", key=self.agent_key)
        self.assertEqual(self.check(self.ledger(self.protected(sig=sig)))[0], "unverified")

    def test_wrong_target_scope_revision_type_rejected(self) -> None:
        pre = self.ledger(self.protected())
        sig = self.sign(pre, "F-100")
        cases = {
            "target": self.protected(sig=sig, location="backend/other.py:1"),
            "statement": self.protected(sig=sig, stmt="approve: edit guard.py entirely"),
            "classification": self.protected(sig=sig, classification="enhancement"),
            "finding id": self.protected(fid="F-101", sig=sig),
        }
        for what, text in cases.items():
            with self.subTest(what=what):
                led = self.ledger(text)
                f = led.of("finding")[0]
                self.assertEqual(grrp.verify_approval(led, f, "owner_action", OWNER, [self.owner_pub], False)[0],
                                 "unverified")
        # same sig reused as a different approval type
        led = self.ledger(self.protected(sig=sig, scope_approval="approve: edit guard.py allowlist only",
                                         scope_approval_sig=sig))
        self.assertEqual(self.check(led, kind="scope_approval")[0], "unverified")
        # brief (run scope) changed after approval
        p = self.td / "t.ledger.md"
        p.write_text(p.read_text(encoding="utf-8").replace("billing module", "billing and auth"), encoding="utf-8")
        self.assertEqual(self.check(grrp.parse(p))[0], "unverified")
        # different ledger file name
        led = self.ledger(self.protected(sig=sig), name="other.ledger.md")
        self.assertEqual(self.check(led)[0], "unverified")

    def test_tampered_signature_rejected(self) -> None:
        led = self.signed_ledger()
        sig = self.td / "F-100.owner_action.sig"
        lines = sig.read_text(encoding="ascii").splitlines()
        mid = len(lines) // 2
        lines[mid] = lines[mid][:-2] + ("AA" if not lines[mid].endswith("AA") else "BB")
        sig.write_text("\n".join(lines) + "\n", encoding="ascii")
        self.assertEqual(self.check(led)[0], "unverified")

    def test_unavailable_evidence_rejected(self) -> None:
        led = self.signed_ledger()
        self.assertEqual(self.check(led, signers=[]), ("unverified", "owner has no hardware-backed SSH signing key"))
        self.assertEqual(grrp.verify_approval(led, led.of("finding")[0], "owner_action", OWNER, None, False)[0],
                         "unverified")
        self.assertEqual(self.check(led, owner=None)[0], "unverified")
        (self.td / "F-100.owner_action.sig").unlink()
        self.assertEqual(self.check(led)[1], "owner_action_sig file not found")

    def test_software_key_rejected_under_hardware_rule(self) -> None:
        led = self.signed_ledger()
        f = led.of("finding")[0]
        st, why = grrp.verify_approval(led, f, "owner_action", OWNER, [self.owner_pub])
        self.assertEqual((st, why), ("unverified", "owner has no hardware-backed SSH signing key"))

    # -- gate activation boundary --------------------------------------------------------
    def test_inactive_binding_keeps_legacy_rule_and_never_reports_verified(self) -> None:
        self.assertIsNone(grrp.APPROVAL_OWNER, "binding must stay inactive until owner setup")
        led = self.signed_ledger()
        r = grrp.gate(led, queue=None)
        self.assertEqual(r["approval_binding"], "inactive")
        self.assertEqual(r["approvals"][0]["status"], "unverified")  # signed, but nothing to verify against
        self.assertIn("no dated owner act", r["validation_errors"][0])  # legacy rule still governs
        legacy = grrp.gate(self.ledger(self.protected(stmt="owner ratified 2026-10-06")), queue=None)
        self.assertEqual(legacy["approvals"], [{"finding": "F-100", "type": "owner_action", "status": "legacy"}])

    def test_active_binding_requires_verified_signature(self) -> None:
        old = (grrp.APPROVAL_OWNER, grrp.fetch_owner_signers, grrp.HARDWARE_KEY_TYPES)
        self.addCleanup(lambda: setattr(grrp, "APPROVAL_OWNER", old[0]) or setattr(grrp, "fetch_owner_signers", old[1])
                        or setattr(grrp, "HARDWARE_KEY_TYPES", old[2]))
        grrp.APPROVAL_OWNER = OWNER
        grrp.HARDWARE_KEY_TYPES = {"ssh-ed25519"}  # test keys stand in for sk-* keys
        grrp.fetch_owner_signers = lambda login: [self.owner_pub]
        ok = grrp.gate(self.signed_ledger(), queue=None)
        self.assertEqual(ok["validation_errors"], [])
        self.assertEqual(ok["approvals"][0]["status"], "verified")
        legacy = grrp.gate(self.ledger(self.protected(stmt="owner ratified 2026-10-06")), queue=None)
        self.assertEqual(legacy["verdict"], "invalid")
        self.assertIn("legacy", legacy["validation_errors"][0])
        grrp.fetch_owner_signers = lambda login: None  # anchor unreachable: fail closed
        down = grrp.gate(self.signed_ledger(), queue=None)
        self.assertEqual(down["verdict"], "invalid")


if __name__ == "__main__":
    unittest.main()
