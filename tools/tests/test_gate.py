"""The local gate script: one command to run before a push, with one verdict line.

`tools/gate.py` runs the profile's pytest set, then the machine-paths guard (self-test and scan), and exits non-zero on
any failure. These tests pin what it runs (so a step cannot be dropped or narrowed quietly) and how it fails (a failing
or unstartable step fails the gate, every step still runs, the last line is the verdict). They never run the real
default steps: that would start this suite inside itself.

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_gate.py)
"""
import importlib.util
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "tools" / "gate.py"


def load():
    spec = importlib.util.spec_from_file_location("profile_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def py(code: str) -> list:
    return [sys.executable, "-c", code]


def run_main(gate, steps):
    out = io.StringIO()
    code = gate.main(["--root", str(REPO)], steps=steps, out=out)
    return code, out.getvalue()


def test_the_default_steps_are_the_profile_checks():
    gate = load()
    steps = dict(gate.default_steps(REPO, "python"))
    pytest_args = next(argv for label, argv in steps.items() if "pytest" in label)
    assert pytest_args[1:3] == ["-m", "pytest"], pytest_args
    # everything CI's install-tests workflow runs, and the rest of hooks/tests (CP-1 lived in a test CI did not run)
    for target in ("hooks/tests", "tools/tests"):
        assert target in pytest_args, pytest_args
    assert "-p" in pytest_args and "no:cacheprovider" in pytest_args, "no .pytest_cache left in the tree"
    scripts = [argv for argv in steps.values() if any(a.endswith("check_machine_paths.py") for a in argv)]
    assert len(scripts) == 2, scripts
    assert any("--self-test" in argv for argv in scripts), "the guard's self-test, as CI's machine-paths workflow runs it"
    assert any("--root" in argv and str(REPO) in argv for argv in scripts), "the scan of this repository"
    assert len(steps) == 3, list(steps)


def test_every_default_step_names_a_file_that_exists():
    gate = load()
    for label, argv in gate.default_steps(REPO, "python"):
        for part in argv[1:]:
            if part.endswith(".py"):
                assert (REPO / part).is_file(), f"{label}: {part}"


def test_all_steps_passing_is_a_pass_and_the_last_line_says_so():
    gate = load()
    code, text = run_main(gate, [("one", py("print('one ran')")), ("two", py("print('two ran')"))])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert code == 0, text
    assert lines[-1].startswith("GATE PASS"), lines[-1]
    assert "one ran" in text and "two ran" in text


def test_a_failing_step_fails_the_gate_names_it_and_does_not_stop_the_others():
    gate = load()
    code, text = run_main(gate, [("first", py("print('first ran')")),
                                 ("broken", py("import sys; print('broken ran'); sys.exit(3)")),
                                 ("last", py("print('last ran')"))])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert code == 1, text
    assert lines[-1].startswith("GATE FAIL") and "broken" in lines[-1] and "first" not in lines[-1], lines[-1]
    assert "last ran" in text, "a step after the failure must still run, so one run shows every problem"


def test_a_step_that_cannot_start_fails_the_gate_instead_of_crashing_it():
    gate = load()
    code, text = run_main(gate, [("missing program", ["this-program-does-not-exist-anywhere"]),
                                 ("fine", py("print('fine ran')"))])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert code == 1 and lines[-1].startswith("GATE FAIL") and "missing program" in lines[-1], text
    assert "fine ran" in text


def test_no_steps_is_not_a_pass():
    gate = load()
    code, text = run_main(gate, [])
    assert code == 1 and "GATE FAIL" in text.splitlines()[-1], text


def test_missing_pytest_is_reported_as_a_failure_with_the_fix():
    gate = load()
    steps = gate.preflight(REPO, have_pytest=False)
    assert steps and "pip install pytest" in steps[0][1][-1], steps


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS:", name)
            except Exception as exc:  # noqa: BLE001 - a plain runner for the no-pytest case
                failed += 1
                print("FAIL:", name, "--", str(exc)[:300])
    sys.exit(1 if failed else 0)
