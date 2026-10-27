"""Install-Rea.ps1 under Windows PowerShell 5.1 and PowerShell 7, with every external tool stubbed.

node, npm and claude are .cmd stubs in a temp folder. PATH holds only that folder and System32,
so no real Node.js, npm or Claude CLI can be reached and nothing is installed or registered.
The npm stub's `install` is a no-op, and the claude stub keeps its "registration" as a marker
file in the temp folder.

Under 5.1, ErrorActionPreference=Stop turns redirected native stderr into a terminating error,
so each case runs on both shells.

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
SHELLS = [s for s in ("powershell.exe", "pwsh") if shutil.which(s)] if os.name == "nt" else []

CLAUDE_STUB = r"""@echo off
echo %*>>"{log}"
if "%2"=="get" goto get
if "%2"=="remove" goto remove
if "%2"=="add" goto add
exit /b 9
:get
if exist "{marker}" goto registered
echo No MCP server found with name: %3 1>&2
exit /b 1
:registered
echo rea:
echo   Scope: User config (available in all your projects)
echo   Status: %STUB_STATUS%
echo   Command: %STUB_COMMAND%
echo   Args: %STUB_ARGS%
exit /b 0
:remove
if "%STUB_REMOVE_FAIL%"=="1" goto removefail
del "{marker}"
exit /b 0
:removefail
echo Failed to remove MCP server rea: config is locked 1>&2
exit /b 1
:add
type nul > "{marker}"
echo Added stdio MCP server rea
exit /b 0
"""

NPM_STUB = r"""@echo off
echo %*>>"{log}"
if "%1"=="prefix" goto prefix
exit /b 0
:prefix
if "%STUB_NPM_WARN%"=="1" echo npm warn config global --global, --local are deprecated. 1>&2
echo {prefix}
exit /b 0
"""

NODE_STUB = r"""@echo off
if "%STUB_NODE_WARN%"=="1" echo node warning: stub stderr line 1>&2
echo %STUB_NODE_VERSION%
"""


@unittest.skipUnless(SHELLS, "Windows with powershell.exe and/or pwsh")
class InstallReaStubbed(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="install-rea-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.bin = self.root / "bin"
        self.prefix = self.root / "npmprefix"
        pkg = self.prefix / "node_modules" / "rea-agents"
        (pkg / "scripts").mkdir(parents=True)
        self.bin.mkdir()
        (pkg / "package.json").write_text('{"version": "0.0.0-stub"}', encoding="utf-8")
        (pkg / "scripts" / "rea.mjs").write_text("", encoding="utf-8")
        self.entry_forward = str(pkg / "scripts" / "rea.mjs").replace("\\", "/")
        self.log = self.root / "calls.log"
        self.marker = self.root / "registered.marker"
        self.write_stub("node", NODE_STUB)
        self.write_stub("npm", NPM_STUB.replace("{prefix}", str(self.prefix)))
        self.write_stub("claude", CLAUDE_STUB)
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{system32}", "STUB_NODE_VERSION": "v24.11.0",
                    "STUB_STATUS": "Connected", "STUB_COMMAND": "node", "STUB_ARGS": f"{self.entry_forward} --mcp"}

    def write_stub(self, name: str, body: str) -> None:
        text = body.replace("{log}", str(self.log)).replace("{marker}", str(self.marker))
        (self.bin / f"{name}.cmd").write_text(text.replace("\n", "\r\n"), encoding="ascii")

    def run_script(self, shell: str, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
                              env={**self.env, **env}, capture_output=True, text=True, timeout=120)

    def calls(self) -> str:
        return self.log.read_text(encoding="ascii") if self.log.exists() else ""

    def test_unregistered_server_reaches_the_registration_step(self) -> None:
        for shell in SHELLS:
            with self.subTest(shell=shell):
                proc = self.run_script(shell, "-DryRun")
                out = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 0, out)
                self.assertNotIn("NativeCommandError", out)
                self.assertIn("[rea] claude mcp add --scope user rea -- node", out)

    # CT-SLOP1026 / CP-7: a native stderr line under EAP Stop must not abort the run on 5.1.
    def test_npm_prefix_stderr_warning_does_not_abort(self) -> None:
        for shell in SHELLS:
            with self.subTest(shell=shell):
                proc = self.run_script(shell, "-DryRun", STUB_NPM_WARN="1")
                out = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 0, out)
                self.assertNotIn("RemoteException", out)
                self.assertIn(f"[rea] claude mcp add --scope user rea -- node {self.entry_forward} --mcp", out)

    def test_node_version_stderr_line_does_not_abort(self) -> None:
        for shell in SHELLS:
            with self.subTest(shell=shell):
                proc = self.run_script(shell, "-DryRun", STUB_NODE_WARN="1")
                out = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 0, out)
                self.assertNotIn("RemoteException", out)
                self.assertIn("[rea] claude mcp add --scope user rea -- node", out)


if __name__ == "__main__":
    unittest.main()
