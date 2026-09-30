"""Self-test driver for evidence_evacuate.py — runs the hook exactly as Claude
Code would (JSON on stdin, check exit code and stdout) against synthetic
checkouts and a throwaway archive under the OS temp dir, using the real
Claude-Tools evacuator. Nothing touches real packet stores or EC-LIVE.

Run either way:
  python hooks/tests/test_evidence_evacuate.py
  python -m pytest hooks/tests/test_evidence_evacuate.py

The evacuator is an external dependency (EVIDENCE_TEST_EVACUATOR overrides the
default path). A missing evacuator is a hard failure, never a skip or a pass.
"""
import contextlib
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "evidence_evacuate.py")
EVACUATOR = os.environ.get("EVIDENCE_TEST_EVACUATOR",
                           "F:/Claude-Tools/evidence-archive/evacuate_worktree.py")

results = []


def require_evacuator() -> str:
    """Fail loudly when the evacuator is absent; return its sha256 for the record."""
    path = Path(EVACUATOR)
    if not path.is_file():
        raise RuntimeError(f"evacuator not found at {EVACUATOR}; these tests exercise the real "
                           "evacuator (set EVIDENCE_TEST_EVACUATOR to point at it)")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def make_packet(store: Path, pid: str) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / f"2026-09-25T00-00-00.000000Z_{pid}.json").write_text(
        json.dumps({"packet_id": pid, "schema_version": "1"}), encoding="utf-8")


@contextlib.contextmanager
def in_process_hook(root: Path, env_extra=None, clock=None, module="evidence_evacuate"):
    """Import the hook in-process with this case's env, log path and (optionally) a
    fake clock; restore os.environ, sys.path and the module's globals on exit so no
    case inherits another's state, including when the import itself raises. LOG_PATH
    is read at import, and the module stays cached across cases, so it is set
    explicitly here. The fake clock only drives Run's deadline: a case that passes
    one and reaches the evacuator gets a subprocess timeout computed in fake time."""
    env = {"EVIDENCE_LIVE_ARCHIVE": str(root / "archive"), "EVIDENCE_EVACUATOR": EVACUATOR,
           "EVIDENCE_EVACUATE_LOG": str(root / "hook.log"), **(env_extra or {})}
    saved_env = {k: os.environ.get(k) for k in env}
    hook_dir = str(Path(HOOK).parent)
    added = False
    hook = None
    saved = {}
    try:
        if hook_dir not in sys.path:
            sys.path.insert(0, hook_dir)
            added = True
        os.environ.update(env)
        hook = importlib.import_module(module)
        saved = {"LOG_PATH": hook.LOG_PATH, "_sha256": hook._sha256, "time": hook.time}
        hook.LOG_PATH = root / "hook.log"
        if clock is not None:
            hook.time = types.SimpleNamespace(monotonic=clock)
        yield hook
    finally:
        for name, value in saved.items():
            setattr(hook, name, value)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if added:
            sys.path.remove(hook_dir)


def case_import_failure_restores_state(root: Path) -> None:
    """in_process_hook restores env and sys.path even when the import raises."""
    keys = ("EVIDENCE_LIVE_ARCHIVE", "EVIDENCE_EVACUATOR", "EVIDENCE_EVACUATE_LOG")
    before_env = {k: os.environ.get(k) for k in keys}
    before_path = list(sys.path)
    raised = False
    try:
        with in_process_hook(root, module="evidence_evacuate_does_not_exist"):
            pass
    except ModuleNotFoundError:
        raised = True
    check("import failure: env and sys.path restored",
          raised and before_env == {k: os.environ.get(k) for k in keys}
          and sys.path == before_path,
          f"raised={raised}")

def run(root: Path, payload, args=(), env_extra=None, raw=None):
    env = os.environ.copy()
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update({
        "EVIDENCE_LIVE_ARCHIVE": str(root / "archive"),
        "EVIDENCE_EVACUATOR": EVACUATOR,
        "EVIDENCE_EVACUATE_LOG": str(root / "hook.log"),
    })
    env.update(env_extra or {})
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, HOOK, *args], input=data,
                       capture_output=True, env=env, timeout=60)
    return p, time.perf_counter() - t0


def receipts(root: Path) -> list:
    d = root / "archive" / "receipts"
    return sorted(d.iterdir()) if d.is_dir() else []


def system_message(p) -> str:
    """The systemMessage the hook printed, or "" (stdout must be empty or that JSON)."""
    out = p.stdout.decode("utf-8").strip()
    return json.loads(out)["systemMessage"] if out else ""


def lane(root: Path, name: str = "lane") -> tuple[Path, Path, dict]:
    wt = root / "repo" / ".claude" / "worktrees" / name
    return wt, wt / ".evidence-compiler" / "packets", {"hook_event_name": "Stop", "cwd": str(wt)}


def case_partial_copy_is_retried(root: Path) -> None:
    """M1: an empty or truncated archive copy is not 'archived'."""
    wt, store, stop = lane(root)
    make_packet(store, "ep_1111111111111111")
    archived = root / "archive" / "packets"
    archived.mkdir(parents=True)
    (archived / "ep_1111111111111111.json").write_bytes(b"")
    p, _ = run(root, stop)
    check("partial copy: evacuator re-runs and reports the conflict (exit 0)",
          p.returncode == 0 and len(receipts(root)) == 1
          and "exit 1" in system_message(p))
    (archived / "ep_1111111111111111.json").unlink()
    p, _ = run(root, stop)
    check("partial copy removed: next Stop archives the packet, silent",
          p.returncode == 0 and not p.stdout
          and (archived / "ep_1111111111111111.json").read_bytes()
          == next(store.glob("*ep_1111111111111111.json")).read_bytes())


def case_id_mismatch_is_settled(root: Path) -> None:
    """L3: a quarantined ep_-named file does not re-run the evacuator every turn."""
    wt, store, stop = lane(root)
    store.mkdir(parents=True)
    (store / "2026-09-25T00-00-00.000000Z_ep_2222222222222222.json").write_text(
        json.dumps({"packet_id": "ep_3333333333333333", "schema_version": "1"}), encoding="utf-8")
    run(root, stop)
    n = len(receipts(root))
    for _ in range(2):
        run(root, stop)
    check("id mismatch: quarantined once, no receipt churn",
          n == 1 and len(receipts(root)) == 1)


def case_budget_is_shared(root: Path) -> None:
    """L4: one budget across checkouts; the unreached checkout is a surfaced deferral."""
    wt, store, stop = lane(root)
    make_packet(store, "ep_4444444444444444")
    p, _ = run(root, stop, env_extra={"EVIDENCE_EVACUATE_BUDGET_S": "0"})
    check("spent budget: nothing started, deferral surfaced, exit 0",
          p.returncode == 0 and not (root / "archive" / "packets").exists()
          and "deferred" in system_message(p))
    slow = root / "slow_evacuator.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    other, other_store, _ = lane(root, "other")
    make_packet(other_store, "ep_5555555555555555")
    p, secs = run(root, stop, env_extra={"EVIDENCE_EVACUATOR": str(slow),
                                         "EVIDENCE_EVACUATE_BUDGET_S": "3",
                                         "CLAUDE_PROJECT_DIR": str(other)})
    msg = system_message(p)
    check(f"slow evacuator on two checkouts stays inside one budget ({secs:.1f}s)",
          p.returncode == 0 and secs < 6 and "timed out" in msg and "deferred" in msg, msg)


def case_session_end_sweeps_siblings(root: Path) -> None:
    """L2: SessionEnd sweeps the repo root and every sibling worktree."""
    wt, store, stop = lane(root)
    wt.mkdir(parents=True)
    other, other_store, _ = lane(root, "orphaned")
    make_packet(other_store, "ep_6666666666666666")
    make_packet(root / "repo" / ".evidence-compiler" / "packets", "ep_7777777777777777")
    archived = root / "archive" / "packets"
    run(root, stop)
    check("Stop does not sweep siblings", not archived.exists())
    p, _ = run(root, {"hook_event_name": "SessionEnd", "cwd": str(wt)})
    check("SessionEnd archives an orphaned sibling worktree and the repo root",
          p.returncode == 0 and not p.stdout
          and (archived / "ep_6666666666666666.json").is_file()
          and (archived / "ep_7777777777777777.json").is_file())


def case_bad_checkout_does_not_stop_sweep(root: Path) -> None:
    """L5: an exception in one checkout is recorded; later checkouts still run."""
    a, a_store, _ = lane(root, "a-bad")
    b, b_store, _ = lane(root, "b-good")
    make_packet(a_store, "ep_8888888888888888")
    make_packet(b_store, "ep_9999999999999999")
    def unreadable(path: Path) -> str:
        if "a-bad" in str(path):
            raise PermissionError(13, "denied", str(path))
        return real(path)

    with in_process_hook(root) as hook:
        real = hook._sha256
        hook._sha256 = unreadable
        code = hook.main(["--sweep", str(root / "repo")])
    check("sweep: bad checkout recorded, later checkout still archived, exit 1",
          code == 1 and (root / "archive" / "packets" / "ep_9999999999999999.json").is_file()
          and "PermissionError" in (root / "hook.log").read_text(encoding="utf-8"))
    p, _ = run(root, None, args=["--sweep", str(root / "repo")], raw=b"",
               env_extra={"EVIDENCE_EVACUATOR": str(root / "missing.py")})
    check("sweep CLI: a failure exits 1 and prints it",
          p.returncode == 1 and b"FAIL" in p.stdout)


def case_budget_covers_hashing(root: Path) -> None:
    """The pre-check hashing draws on the shared budget: once it is spent, remaining
    checkouts are deferred (and surfaced) without hashing another file."""
    for name in ("a", "b"):
        _, store, _ = lane(root, name)
        make_packet(store, f"ep_{name * 16}")
        make_packet(store, f"ep_{name * 8}{'0' * 8}")
    # Controlled clock: time advances only inside the stubbed hash, so the outcome
    # is exact regardless of machine speed. Budget 2.5 s, 0.6 s per hash: the first
    # file is hashed (2.5 >= MIN_CALL_S), then 1.9 s remain and nothing else may be.
    now = [0.0]
    hashed = []
    out = io.StringIO()
    with in_process_hook(root, {"EVIDENCE_EVACUATE_BUDGET_S": "2.5"},
                         clock=lambda: now[0]) as hook:
        real = hook._sha256

        def slow_hash(path: Path) -> str:
            hashed.append(path)
            now[0] += 0.6
            return real(path)

        hook._sha256 = slow_hash
        with contextlib.redirect_stdout(out):
            code = hook.main(["--sweep", str(root / "repo")])
    lines = out.getvalue().splitlines()
    deferred = [ln for ln in lines if "deferred" in ln and "rest unchecked" in ln]
    check("spent budget stops hashing: one file hashed, both checkouts deferred, exit 1",
          code == 1 and len(hashed) == 1 and len(deferred) == 2
          and not (root / "archive" / "packets").exists(), f"hashed={len(hashed)} out={lines}")


CASES = (case_partial_copy_is_retried, case_id_mismatch_is_settled, case_budget_is_shared,
         case_session_end_sweeps_siblings, case_bad_checkout_does_not_stop_sweep,
         case_budget_covers_hashing, case_import_failure_restores_state)


def run_case(case) -> list:
    """Run one case in its own temp root; return its failed checks."""
    start = len(results)
    root = Path(tempfile.mkdtemp(prefix="ec-evacuate-selftest-"))
    try:
        case(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return [r for r in results[start:] if not r[1]]


def assert_case(case) -> None:
    require_evacuator()
    failed = run_case(case)
    assert not failed, "; ".join(f"{name} -- {detail}" for name, _, detail in failed)


def test_core_flow() -> None:
    assert_case(case_core_flow)


def test_partial_copy_is_retried() -> None:
    assert_case(case_partial_copy_is_retried)


def test_id_mismatch_is_settled() -> None:
    assert_case(case_id_mismatch_is_settled)


def test_budget_is_shared() -> None:
    assert_case(case_budget_is_shared)


def test_session_end_sweeps_siblings() -> None:
    assert_case(case_session_end_sweeps_siblings)


def test_bad_checkout_does_not_stop_sweep() -> None:
    assert_case(case_bad_checkout_does_not_stop_sweep)


def test_budget_covers_hashing() -> None:
    assert_case(case_budget_covers_hashing)


def test_import_failure_restores_state() -> None:
    assert_case(case_import_failure_restores_state)


def case_core_flow(root: Path) -> None:
    """The ordinary Stop/SessionEnd/sweep path, end to end in one checkout."""
    wt = root / "repo" / ".claude" / "worktrees" / "lane"
    store = wt / ".evidence-compiler" / "packets"
    stop = {"hook_event_name": "Stop", "cwd": str(wt)}

    p, secs = run(root, stop)
    check("no store: exit 0, silent", p.returncode == 0 and not p.stdout)
    check("no store: archive not created", not (root / "archive").exists())

    make_packet(store, "ep_0123456789abcdef")
    make_packet(store, "ep_fedcba9876543210")
    p, secs = run(root, stop)
    archived = root / "archive" / "packets"
    check("new packets: exit 0, silent", p.returncode == 0 and not p.stdout)
    check("new packets: both archived",
          (archived / "ep_0123456789abcdef.json").is_file()
          and (archived / "ep_fedcba9876543210.json").is_file())
    check("new packets: one receipt", len(receipts(root)) == 1)
    rc = subprocess.run([sys.executable, EVACUATOR, "verify", str(receipts(root)[0])],
                        capture_output=True, text=True)
    check("receipt verifies", rc.returncode == 0, rc.stdout[-300:] + rc.stderr[-300:])

    p, secs = run(root, stop)
    check("nothing new: no second receipt", len(receipts(root)) == 1)
    check(f"nothing new: fast ({secs:.2f}s incl. interpreter start)", secs < 1.0)

    make_packet(store, "ep_aaaaaaaaaaaaaaaa")
    p, _ = run(root, {"hook_event_name": "SessionEnd"},
               env_extra={"CLAUDE_PROJECT_DIR": str(wt)})
    check("CLAUDE_PROJECT_DIR used when payload has no cwd",
          (archived / "ep_aaaaaaaaaaaaaaaa.json").is_file())

    shutil.rmtree(wt)
    check("worktree deleted: archived packets survive",
          len(list(archived.glob("ep_*.json"))) == 3)

    make_packet(store, "ep_bbbbbbbbbbbbbbbb")
    p, _ = run(root, stop, env_extra={"EVIDENCE_EVACUATE": "0"})
    check("opt-out: nothing copied", not (archived / "ep_bbbbbbbbbbbbbbbb.json").exists())

    p, _ = run(root, stop, env_extra={"EVIDENCE_EVACUATOR": str(root / "missing.py")})
    log = (root / "hook.log").read_text(encoding="utf-8")
    check("missing evacuator: exit 0 and logged",
          p.returncode == 0 and "evacuator not found" in log)
    check("missing evacuator: surfaced to the user as a systemMessage",
          "evacuator not found" in system_message(p))

    p, _ = run(root, None, raw=b"\xef\xbb\xbf" + json.dumps(stop).encode("utf-8"))
    check("BOM-prefixed stdin still parsed",
          p.returncode == 0 and (archived / "ep_bbbbbbbbbbbbbbbb.json").is_file())

    p, _ = run(root, None, raw=b"not json")
    check("garbage stdin: exit 0, silent", p.returncode == 0 and not p.stdout)

    (store / "stray.json").write_text("{not json", encoding="utf-8")
    p, _ = run(root, stop)
    quarantine = root / "archive" / "quarantine"
    check("unrecognised file reaches evacuator and is quarantined, exit 0",
          p.returncode == 0 and quarantine.is_dir() and any(quarantine.rglob("*stray.json")))

    n = len(receipts(root))
    run(root, stop)
    check("quarantined stray does not re-trigger every turn", len(receipts(root)) == n)

    make_packet(store, "ep_cccccccccccccccc")
    p, _ = run(root, None, args=["--sweep", str(root / "repo")], raw=b"")
    check("sweep finds .claude/worktrees/* checkouts",
          p.returncode == 0 and (archived / "ep_cccccccccccccccc.json").is_file())


def main() -> int:
    try:
        digest = require_evacuator()
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"evacuator: {EVACUATOR} sha256={digest[:12]}")
    for case in (case_core_flow, *CASES):
        run_case(case)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
