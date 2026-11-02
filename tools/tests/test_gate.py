"""The local gate script: one command the pre-push hook and CI both run, with one verdict line.

`tools/gate.py` runs pytest over hooks/tests, tools/tests and skills/graphify/scripts, then the machine-paths guard
(self-test and scan), in parallel, and exits non-zero on any failure. These tests pin what it runs (so a step cannot be
dropped or narrowed quietly) and how it fails (a failing or unstartable step fails the gate, every step still runs, a
failing step's output is shown, the last line is the verdict). They never run the real default steps: that would start
this suite inside itself.

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_gate.py)
"""
import importlib.util
import io
import sys
import tempfile
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


def run_main(gate, steps, *extra):
    out = io.StringIO()
    code = gate.main(["--root", str(REPO), *extra], steps=steps, out=out)
    return code, out.getvalue()


def test_the_default_steps_are_the_profile_checks():
    gate = load()
    steps = dict(gate.default_steps(REPO, "python"))
    pytest_runs = [argv for label, argv in steps.items() if "pytest" in label]
    for argv in pytest_runs:
        assert argv[1:3] == ["-m", "pytest"], argv
        assert "-p" in argv and "no:cacheprovider" in argv, "no .pytest_cache left in the tree"
    # everything CI's install-tests workflow runs (CI runs this gate), and all of hooks/tests (CP-1 lived in a test
    # CI did not run)
    targets = sorted(a for argv in pytest_runs for a in argv[3:] if not a.startswith("-") and a != "no:cacheprovider")
    assert targets == ["hooks/tests", "skills/graphify/scripts", "tools/tests"], targets
    scripts = [argv for argv in steps.values() if any(a.endswith("check_machine_paths.py") for a in argv)]
    assert len(scripts) == 2, scripts
    assert any("--self-test" in argv for argv in scripts), "the guard's self-test, as CI's machine-paths workflow runs it"
    assert any("--root" in argv and str(REPO) in argv for argv in scripts), "the scan of this repository"
    assert len(steps) == 5, list(steps)


def test_tests_needing_claude_tools_are_left_out_only_where_there_is_no_clone():
    gate = load()
    for test in gate.NEEDS_CLAUDE_TOOLS:
        assert (REPO / test).is_file(), test
    assert gate.not_runnable_here("claude-tools", environ={}) == []
    skip = gate.not_runnable_here("", environ={})
    assert sorted(skip) == sorted(gate.NEEDS_CLAUDE_TOOLS), skip
    assert "hooks/tests/test_evidence_evacuate.py" not in gate.not_runnable_here(
        "", environ={"EVIDENCE_TEST_EVACUATOR": "evacuate_worktree.py"}), "the test's own override makes it runnable"
    hooks_run = dict(gate.default_steps(REPO, "python", skip=skip))["pytest hooks/tests"]
    assert sorted(a for a in hooks_run if a.startswith("--ignore=")) == sorted(f"--ignore={t}" for t in skip)
    assert not any(a.startswith("--ignore") for a in dict(gate.default_steps(REPO, "python"))["pytest hooks/tests"])


def test_ci_runs_this_gate_on_both_operating_systems():
    workflow = (REPO / ".github" / "workflows" / "install-tests.yml").read_text(encoding="utf-8")
    assert "python tools/gate.py --verbose" in workflow, "CI must run the same gate the pre-push hook runs"
    assert "ubuntu-latest" in workflow and "windows-latest" in workflow


def test_ci_cost_posture():
    # The private repo bills every minute: the full gate is manual only, and the one automatic check fires once per PR.
    workflows = REPO / ".github" / "workflows"
    assert (workflows / "install-tests.yml").read_text(encoding="utf-8").split("\non:\n", 1)[1].startswith(
        "  workflow_dispatch:\n\n"), "install-tests must be workflow_dispatch only"
    for path in workflows.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "pull_request:" in text:
            assert "types: [opened, reopened, ready_for_review]" in text, f"{path.name} re-fires on every push"
            assert "github.event.pull_request.draft == false" in text, f"{path.name} runs on drafts"
            assert "windows-latest" not in text, f"{path.name} runs Windows on PRs"


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
    assert "one ran" not in text, "a passing step prints one line, not its output"
    assert lines[0].startswith("<== one: ok") and lines[1].startswith("<== two: ok"), lines


def test_verbose_prints_every_steps_output():
    gate = load()
    code, text = run_main(gate, [("one", py("print('one ran')")), ("two", py("print('two ran')"))], "--verbose")
    assert code == 0 and "one ran" in text and "two ran" in text, text


def test_steps_run_at_the_same_time_each_with_its_own_temp_dir():
    gate = load()
    # each step waits for the other's marker file: run one after the other, the first would time out and fail
    wait = ("import sys, time, tempfile, pathlib; me, other = sys.argv[1], sys.argv[2]; d = pathlib.Path({!r}); "
            "(d / me).write_text(tempfile.gettempdir()); end = time.time() + 20\n"
            "while not (d / other).exists() and time.time() < end: time.sleep(0.05)\n"
            "sys.exit(0 if (d / other).exists() else 1)")
    with tempfile.TemporaryDirectory() as d:
        code, text = run_main(gate, [("a", [sys.executable, "-c", wait.format(d), "a", "b"]),
                                     ("b", [sys.executable, "-c", wait.format(d), "b", "a"])])
        temps = {(Path(d) / n).read_text() for n in "ab"}
    assert code == 0, text
    assert len(temps) == 2, f"two steps shared one temp dir: {temps}"


def test_a_failing_step_fails_the_gate_names_it_shows_its_output_and_does_not_stop_the_others():
    gate = load()
    code, text = run_main(gate, [("first", py("print('first ran')")),
                                 ("broken", py("import sys; print('broken ran'); sys.exit(3)")),
                                 ("last", py("print('last ran')"))])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert code == 1, text
    assert lines[-1].startswith("GATE FAIL") and "broken" in lines[-1] and "first" not in lines[-1], lines[-1]
    assert "broken ran" in text, "a failing step's output is shown"
    assert "<== last: ok" in text, "a step after the failure must still run, so one run shows every problem"


def test_a_step_that_cannot_start_fails_the_gate_instead_of_crashing_it():
    gate = load()
    code, text = run_main(gate, [("missing program", ["this-program-does-not-exist-anywhere"]),
                                 ("fine", py("print('fine ran')"))])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert code == 1 and lines[-1].startswith("GATE FAIL") and "missing program" in lines[-1], text
    assert "could not start" in text and "<== fine: ok" in text, text


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
