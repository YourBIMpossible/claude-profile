"""CT-SLOP1026 / N-3: every run of test_ctxdex_hook.py has its own ctxdex copy, database, working directory and fake home.

The hook self-test used a fixed project name, a fixed temp working directory, a fixed tool-results directory under the real home and
the database of the real claude-tools clone. Two gates running on one machine at once therefore deleted and locked each other's
database (PermissionError / WinError 32) and failed checks that had nothing wrong with them; three workers hit it in one day. The
hook already honours CTXDEX_DIR, and Path.home() follows HOME and USERPROFILE, so the test can give the hook (and its own calls of
ctxdex.py) a throwaway copy and a throwaway home without changing the hook at all.

These tests pin that: each sandbox is distinct and lives under one fresh temp root, nothing in it points into the claude-tools clone or
the real ~/.claude, the hook is handed those locations, a teardown removes everything, and two concurrent runs of the whole self-test
both pass and leave the real locations as they found them.

Run: python hooks/tests/test_ctxdex_hook_isolation.py  (or: python -m pytest hooks/tests -q)
"""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK_TEST = HERE / "test_ctxdex_hook.py"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def load(tag: str):
    """A fresh module object for the hook self-test (its sandbox state is module-level)."""
    spec = importlib.util.spec_from_file_location(f"ctxdex_hook_selftest_{tag}", HOOK_TEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def under(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def real_artifacts(mod) -> list:
    """Anything the old test left behind in the places it must no longer touch."""
    found = []
    home_projects = Path.home() / ".claude" / "projects"
    found += [str(p) for p in home_projects.glob("_ctxdex-hook-selftest*")] if home_projects.is_dir() else []
    data = Path(mod.SOURCE_CTXDEX_DIR) / "data"
    found += [str(p) for p in data.glob("ctxdex-hook-selftest*")] if data.is_dir() else []
    return found


def run_checks() -> list:
    results.clear()
    a, b = load("a"), load("b")
    real_claude = Path.home() / ".claude"   # the OS temp dir is under the home folder, so only ~/.claude is off limits
    clone = Path(a.SOURCE_CTXDEX_DIR)

    # -- 1. every sandbox is its own, under one fresh temp root --------------------------------------------------------
    ra, rb = a.setup_sandbox(), b.setup_sandbox()
    try:
        check("two sandboxes get different roots", Path(ra) != Path(rb), f"{ra} {rb}")
        for tag, m, root in (("a", a, ra), ("b", b, rb)):
            for what, value in (("database", m.DB_PATH), ("working directory", m.CWD), ("fake home", m.TEST_HOME),
                                ("ctxdex copy", m.CTXDEX_DIR)):
                check(f"sandbox {tag}: the {what} is under its own root", under(Path(value), Path(root)), str(value))
                check(f"sandbox {tag}: the {what} is not in the claude-tools clone or the real ~/.claude",
                      not under(Path(value), clone) and not under(Path(value), real_claude), str(value))
            check(f"sandbox {tag}: the root is under the OS temp dir", under(Path(root), Path(tempfile.gettempdir())), str(root))
            check(f"sandbox {tag}: it carries a copy of ctxdex.py to run against", (Path(m.CTXDEX_DIR) / "ctxdex.py").is_file())
        check("the two databases and the two project names differ", a.DB_PATH != b.DB_PATH and a.PROJECT != b.PROJECT,
              f"{a.PROJECT} {b.PROJECT}")
        check("the project name is the working directory's name (the hook derives it from the cwd)",
              Path(a.CWD).name == a.PROJECT and Path(b.CWD).name == b.PROJECT)

        # -- 2. the hook is handed those locations ----------------------------------------------------------------------
        env = a.hook_env({"CTXDEX_AUTO_INDEX": "0"})
        check("the hook's environment names the sandbox's ctxdex dir", Path(env["CTXDEX_DIR"]) == Path(a.CTXDEX_DIR), env.get("CTXDEX_DIR"))
        check("the hook's HOME and USERPROFILE are the sandbox's fake home",
              Path(env["HOME"]) == Path(a.TEST_HOME) and Path(env["USERPROFILE"]) == Path(a.TEST_HOME))
        check("extra variables still pass through", env.get("CTXDEX_AUTO_INDEX") == "0")
    finally:
        a.teardown_sandbox()
        b.teardown_sandbox()

    # -- 3. teardown removes everything ---------------------------------------------------------------------------------
    check("teardown removes both sandbox roots", not Path(ra).exists() and not Path(rb).exists())

    # -- 4. two concurrent runs of the whole self-test both pass, and the real places are left as found ----------------------
    before = real_artifacts(a)
    procs = [subprocess.Popen([sys.executable, str(HOOK_TEST)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
             for _ in range(2)]
    outs = []
    for p in procs:
        try:
            out, _ = p.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
        outs.append((p.returncode, out))
    for i, (rc, out) in enumerate(outs, 1):
        tail = out.strip().splitlines()[-6:]
        check(f"concurrent run {i} of the self-test passes", rc == 0, f"rc={rc} tail={tail}")
    check("the runs touched neither the claude-tools clone's data folder nor the real projects folder",
          real_artifacts(a) == before, f"before={before} after={real_artifacts(a)}")
    return list(results)


def test_ctxdex_hook_isolation() -> None:
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
