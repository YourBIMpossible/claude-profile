"""vendor/ holds byte-identical pins of upstream code: every file must match the sha256
recorded in vendor/README.md, and every file must be recorded. A repath, reformat or
line-ending change of a pinned file fails here (the drift fixed by 6d5c9e2), so the
guard exemption for vendor/** can never hide an in-place edit.

Run: python hooks/tests/test_vendor_pins.py   (or pytest hooks/tests)
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[2] / "vendor"
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|\s*$")


def recorded() -> dict[str, str]:
    rows = {}
    for line in (VENDOR / "README.md").read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = m.group(2)
    return rows


def present() -> dict[str, str]:
    return {p.relative_to(VENDOR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in VENDOR.rglob("*")
            if p.is_file() and p.name != "README.md" and "__pycache__" not in p.parts}


class VendorPins(unittest.TestCase):
    def test_every_pinned_file_matches_its_recorded_sha256(self) -> None:
        rows, files = recorded(), present()
        self.assertTrue(rows, "vendor/README.md has no sha256 table")
        drifted = sorted(f for f in rows if f in files and files[f] != rows[f])
        self.assertEqual(drifted, [], "pinned files edited in place - fix upstream and re-pin instead")
        self.assertEqual(sorted(set(rows) - set(files)), [], "recorded pins missing from vendor/")

    def test_every_vendored_file_is_recorded(self) -> None:
        self.assertEqual(sorted(set(present()) - set(recorded())), [], "unrecorded files under vendor/")


if __name__ == "__main__":
    unittest.main()
