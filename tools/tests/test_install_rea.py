"""Install-Rea.ps1 under Windows PowerShell 5.1: a first install, where `claude mcp get rea`
writes to stderr and exits 1, must reach the registration step instead of dying on a
NativeCommandError (ErrorActionPreference=Stop turns redirected native stderr into a
terminating error under 5.1).

node, npm and claude are stubbed with .cmd files in a temp folder put first on PATH, and
the script runs with -DryRun, so nothing is installed or registered.

Run: python tools/tests/test_install_rea.py   (or pytest tools/tests)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "Install-Rea.ps1"


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell 5.1 only")
class FirstInstallUnderWindowsPowerShell(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="install-rea-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        bin_dir = self.root / "bin"
        pkg = self.root / "npmprefix" / "node_modules" / "rea-agents"
        (pkg / "scripts").mkdir(parents=True)
        bin_dir.mkdir()
        (pkg / "package.json").write_text('{"version": "0.0.0-stub"}', encoding="utf-8")
        (pkg / "scripts" / "rea.mjs").write_text("", encoding="utf-8")
        (bin_dir / "node.cmd").write_text("@echo off\r\necho v24.11.0\r\n", encoding="ascii")
        (bin_dir / "npm.cmd").write_text(f'@echo off\r\nif "%1"=="prefix" echo {self.root / "npmprefix"}\r\n',
                                         encoding="ascii")
        (bin_dir / "claude.cmd").write_text(
            "@echo off\r\necho No MCP server found with name: %3 1>&2\r\nexit /b 1\r\n", encoding="ascii")
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    def test_unregistered_server_reaches_the_registration_step(self) -> None:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                               "-DryRun"], env=self.env, capture_output=True, text=True, timeout=120)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("NativeCommandError", out)
        self.assertIn("[rea] claude mcp add --scope user rea -- node", out)


if __name__ == "__main__":
    unittest.main()
