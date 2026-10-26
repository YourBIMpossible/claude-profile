"""Regression tests for slop-audit 2026-10-26 CP-1 (HIGH): the closure audit was completely and silently dead.

Root cause: commit 1b5dd62 ("Resolve machine roots at runtime") removed the local `repos` list from
`build_report`, but its verdict line still formatted `len(repos)`. Every call raised NameError; the
top-level `except Exception: sys.exit(0)` of the advisory hook swallowed it, so SessionStart and SessionEnd
printed nothing and exited 0, which reads as "nothing to externalize". No test called `build_report`, so
the suite stayed green (tested-but-dead).

These tests run `build_report` and `main` end to end on a throwaway git repository, so a name error (or any
other exception) in the report path fails a test instead of vanishing into the fail-open handler.

Imports the hook module directly; the only subprocess is the real `git` against a temp repository.

Run: python hooks/tests/test_closure_audit_build_report.py  (or: python -m pytest hooks/tests -q)
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "session_closure_audit.py"

spec = importlib.util.spec_from_file_location("session_closure_audit", HOOK_PATH)
sca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sca)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, timeout=60, env=env)


def make_repo(root: Path, untracked: bool, name: str = "scratch-repo") -> Path:
    repo = root / name
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("scratch\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    if untracked:
        (repo / "notes.md").write_text("an uncommitted note\n", encoding="utf-8")
    return repo


def config_for(*repos):
    """The real config's globs and markers, with the repo scope replaced by the given (name, path) pairs."""
    cfg = dict(sca.load_config())
    cfg["repos"] = [{"name": n, "path": str(p)} for n, p in repos]
    return cfg


def run_checks() -> list:
    results.clear()
    with tempfile.TemporaryDirectory(prefix="closure-audit-") as tmp:
        root = Path(tmp)
        repo = make_repo(root, untracked=True)
        cfg = config_for(("scratch", repo))

        # -- build_report runs end to end and reports the repository
        try:
            report = sca.build_report(cfg)
            err = ""
        except Exception as e:  # the CP-1 failure was a NameError here
            report, err = "", f"{type(e).__name__}: {e}"
        check("build_report on a temp repository does not raise", not err, err)
        check("the report carries the verdict line", "Verdict:" in report, report[-200:])
        check("the verdict counts the one configured repository", "across 1 configured repo(s)" in report, report[-200:])
        check("the report has a section for the scratch repository", "## scratch" in report, report[:300])
        check("the untracked file is reported (the audit actually ran git)", "notes.md" in report or "local edits" in report
              or "WARNING" in report or "ATTENTION" in report, report[:400])

        # -- main() end to end: it must emit the report, and an exception must not escape as silence
        emitted = []
        saved = (sca.read_event, sca.load_config, sca.emit)
        sca.read_event = lambda: "SessionStart"
        sca.load_config = lambda: cfg
        sca.emit = lambda text: emitted.append(text)
        raised = ""
        try:
            try:
                sca.main()
            except SystemExit as e:
                check("main exits 0", e.code in (0, None), f"code={e.code!r}")
        except Exception as e:
            raised = f"{type(e).__name__}: {e}"
        finally:
            sca.read_event, sca.load_config, sca.emit = saved
        check("main() emits the audit instead of raising (an exception here is swallowed to silence by the "
              "fail-open handler)", not raised and len(emitted) == 1 and "<closure-audit>" in emitted[0], raised)
        check("the emitted audit carries the verdict", bool(emitted) and "Verdict:" in emitted[0])

        # -- CP-9 slice: a machine where NO configured repository exists is an ATTENTION item, never an empty
        #    report that reads as clean ("0 ATTENTION REQUIRED, 0 WARNING across 0 configured repo(s)").
        ghost = root / "no-such-repos-root" / "ghost"
        rep = sca.build_report(config_for(("ghost", ghost)))
        check("only a missing (optional) repository: the report says ATTENTION REQUIRED",
              "ATTENTION REQUIRED" in rep and "Verdict: 1 ATTENTION REQUIRED" in rep, rep[-260:])
        check("... and says the audit checked nothing, not that all is clean",
              "checked nothing" in rep, rep[-260:])
        rep = sca.build_report(config_for())
        check("an empty repository list is also an ATTENTION item", "Verdict: 1 ATTENTION REQUIRED" in rep, rep[-260:])
        cfg_req = config_for(("ghost", ghost))
        cfg_req["repos"][0]["required"] = True
        rep = sca.build_report(cfg_req)
        check("a missing REQUIRED repository is still exactly one ATTENTION item (no duplicate)",
              "Verdict: 1 ATTENTION REQUIRED" in rep and rep.count("ATTENTION REQUIRED:") == 1, rep[-300:])
        clean = make_repo(root, untracked=False, name="clean-repo")
        rep = sca.build_report(config_for(("clean", clean), ("ghost", ghost)))
        check("one repository present and an optional one missing: the shared-config rule holds (no attention)",
              "Verdict: 0 ATTENTION REQUIRED" in rep and "across 1 configured repo(s)" in rep, rep[-300:])
    return list(results)


def test_closure_audit_build_report() -> None:
    """pytest entry point - same checks as running this file directly."""
    res = run_checks()
    failed = [(n, d) for n, ok, d in res if not ok]
    assert res, "suite ran zero checks"
    assert not failed, "failing checks:\n  " + "\n  ".join(n + (f" ({d})" if d else "") for n, d in failed)


def main() -> int:
    res = run_checks()
    failed = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(failed)}/{len(res)} passed")
    if failed:
        print("FAILURES:")
        for name, _, detail in failed:
            print(f"  - {name}" + (f" ({detail})" if detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
