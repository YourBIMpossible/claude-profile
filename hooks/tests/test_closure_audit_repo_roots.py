"""Regression tests for slop-audit 2026-10-26 CP-9 (the rest after PR #53): a configured repository
whose machine root cannot be resolved must not vanish from the closure audit.

Before: `resolve_repos` caught every `machine_config.expand` failure with `except Exception: continue`.
  (a) A malformed machine config made expand raise for EVERY entry, so the report blamed "none of the
      configured repositories exists" and never showed the config error.
  (b) With tools_root unset, the `{tools}` entry was dropped silently while the others resolved.
  (c) A machine_config import failure left the `{repos}` tokens literal, so every entry was skipped as
      "not found", again without the cause.

Each case runs `build_report` on a throwaway git repository with the machine config pointed at a temp
file (CLAUDE_PROFILE_MACHINE_CONFIG), so the live ~/.claude is never read.

Run: python hooks/tests/test_closure_audit_repo_roots.py  (or: python -m pytest hooks/tests -q)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_closure_audit.py"

spec = importlib.util.spec_from_file_location("session_closure_audit", HOOK_PATH)
sca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sca)

CONFIG_ENV = "CLAUDE_PROFILE_MACHINE_CONFIG"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (("init", "-q"), ("commit", "-q", "--allow-empty", "-m", "base")):
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                        "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, timeout=60, env=env)
    return repo


def config_for(*entries):
    cfg = dict(sca.load_config())
    cfg["repos"] = [{"name": n, "path": p} for n, p in entries]
    return cfg


def report_with_machine_config(machine_cfg_text: str, cfg: dict, cfg_file: Path) -> str:
    cfg_file.write_text(machine_cfg_text, encoding="utf-8")
    saved = os.environ.get(CONFIG_ENV)
    os.environ[CONFIG_ENV] = str(cfg_file)
    try:
        return sca.build_report(cfg)
    finally:
        if saved is None:
            os.environ.pop(CONFIG_ENV, None)
        else:
            os.environ[CONFIG_ENV] = saved


def run_checks() -> list:
    results.clear()
    if sca.machine_config is None:
        check("machine_config imports in the test environment", False, "cannot exercise CP-9 without it")
        return list(results)
    with tempfile.TemporaryDirectory(prefix="closure-audit-roots-") as tmp:
        root = Path(tmp)
        repos_root = root / "repos"
        repos_root.mkdir()
        make_repo(repos_root, "present")
        cfg_file = root / "profile-machine.json"
        entries = config_for(("present", "{repos}/present"), ("claude-tools", "{tools}"))

        # (a) malformed machine config: the error is shown, not "none of the configured repositories exists".
        rep = report_with_machine_config("{not json", entries, cfg_file)
        check("(a) a malformed machine config is an ATTENTION item", "ATTENTION REQUIRED" in rep, rep[-400:])
        check("(a) ... that carries the config error text", "not valid JSON" in rep, rep[:600])
        check("(a) ... and names the entries it could not check", "present" in rep and "claude-tools" in rep, rep[:600])
        check("(a) ... and is not blamed on missing repositories",
              "none of the configured repositories exists" not in rep, rep[:600])
        check("(a) ... counted once in the verdict", "Verdict: 1 ATTENTION REQUIRED" in rep, rep[-300:])

        # (b) tools_root unset: the {tools} entry is named as not checked, the rest still runs, nothing alarms.
        good = json.dumps({"repos_root": str(repos_root), "tools_root": ""})
        rep = report_with_machine_config(good, entries, cfg_file)
        check("(b) the present repository is still audited", "## present" in rep and "across 1 configured repo(s)" in rep,
              rep[-300:])
        check("(b) the {tools} entry is named as not checked, not dropped silently",
              "claude-tools" in rep and "{tools} is not set" in rep, rep[:600])
        check("(b) an unset root is a note, not an attention item", "Verdict: 0 ATTENTION REQUIRED, 0 WARNING" in rep,
              rep[-300:])

        # (c) machine_config failed to import: the import error is shown, not "none ... exists".
        saved = sca.machine_config, sca.MACHINE_CONFIG_ERROR
        sca.machine_config, sca.MACHINE_CONFIG_ERROR = None, "ImportError: simulated"
        try:
            rep = sca.build_report(entries)
        finally:
            sca.machine_config, sca.MACHINE_CONFIG_ERROR = saved
        check("(c) a machine_config import failure is an ATTENTION item with its cause",
              "ATTENTION REQUIRED" in rep and "ImportError: simulated" in rep, rep[:600])
        check("(c) ... and is not blamed on missing repositories",
              "none of the configured repositories exists" not in rep, rep[:600])

        # Unchanged: a resolvable entry whose folder is absent stays silent (one config serves every machine).
        rep = report_with_machine_config(good, config_for(("present", "{repos}/present"),
                                                          ("absent", "{repos}/absent")), cfg_file)
        check("a resolvable but absent optional entry is still silent",
              "absent" not in rep.split("Verdict:")[0] and "Verdict: 0 ATTENTION REQUIRED, 0 WARNING" in rep, rep[-400:])

        # (d) the audit's own CP-9 verify step: "set the scratch config's repos root to a non-existent folder.
        #     Expect an ATTENTION line, not '0 configured repo(s)'." Run on the shipped entry list, with the
        #     {profile} entry pointed at a temp repo (it always resolves; this keeps git off the live clone).
        shipped = dict(sca.load_config())
        shipped["repos"] = [dict(r, path=str(repos_root / "present")) if r["path"] == "{profile}" else dict(r)
                            for r in shipped["repos"]]
        dead = root / "nope"
        rep = report_with_machine_config(json.dumps({"repos_root": str(dead)}), shipped, cfg_file)
        head = rep.split("Verdict:")[0]
        check("(d) verify step: a repos root that is not a folder is an ATTENTION item",
              "## (machine roots)" in head and "{repos}" in head and str(dead) in head, rep[:900])
        check("(d) ... naming the entries it blocked", all(r["name"] in head for r in shipped["repos"]
                                                           if r["path"].startswith("{repos}")), rep[:900])
        check("(d) ... counted once in the verdict", "Verdict: 1 ATTENTION REQUIRED" in rep, rep[-300:])
        rep = report_with_machine_config(json.dumps({"repos_root": str(repos_root), "tools_root": str(root / "no-tools")}),
                                         entries, cfg_file)
        check("(d) a tools root that is not a folder is an ATTENTION item too",
              "{tools}" in rep.split("Verdict:")[0] and "claude-tools" in rep.split("Verdict:")[0]
              and "Verdict: 1 ATTENTION REQUIRED" in rep and "## present" in rep, rep[:900])
    return list(results)


def test_closure_audit_repo_roots() -> None:
    """pytest entry point - same checks as running this file directly."""
    res = run_checks()
    failed = [(n, d) for n, ok, d in res if not ok]
    assert res, "suite ran zero checks"
    assert not failed, "failing checks:\n  " + "\n  ".join(n + (f" ({d})" if d else "") for n, d in failed)


def main() -> int:
    res = run_checks()
    failed = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(failed)}/{len(res)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
