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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import machine_config  # noqa: E402

# The real evacuator in this machine's claude-tools clone; EVIDENCE_TEST_EVACUATOR overrides.
EVACUATOR = os.environ.get("EVIDENCE_TEST_EVACUATOR") or (
    Path(machine_config.load().get("tools_root") or "missing-claude-tools")
    / "evidence-archive" / "evacuate_worktree.py").as_posix()

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
    and PENDING_DIR are read at import, and the module stays cached across cases, so
    they are set explicitly here. The fake clock only drives Run's deadline: a case that passes
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
        saved = {"LOG_PATH": hook.LOG_PATH, "PENDING_DIR": hook.PENDING_DIR,
                 "_sha256": hook._sha256, "missing_ids": hook.missing_ids, "time": hook.time}
        hook.LOG_PATH = root / "hook.log"
        hook.PENDING_DIR = root / "hook.pending"
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


def run(root: Path, payload, args=(), env_extra=None, raw=None, via=None):
    """Run the hook as Claude Code would; `via` is a wrapper script that runs the
    real hook (its first argument) as __main__ after arranging a failure."""
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
    p = subprocess.run([sys.executable, *([str(via)] if via else []), HOOK, *args], input=data,
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


def pending(root: Path) -> list[Path]:
    d = root / "hook.pending"
    return sorted(d.glob("*.msg")) if d.is_dir() else []


def session_end_failure(root: Path, name: str) -> None:
    """A real SessionEnd whose evacuator is missing: appends one pending entry."""
    wt, store, _ = lane(root, name)
    make_packet(store, f"ep_{abs(hash(name)) % 16**16:016x}")
    run(root, {"hook_event_name": "SessionEnd", "cwd": str(wt)},
        env_extra={"EVIDENCE_EVACUATOR": str(root / "missing.py"), "EVIDENCE_EVACUATE_BUDGET_S": "25"})
    shutil.rmtree(wt)  # the lane is gone; only the pending entry remembers it


def shown(msg: str, name: str) -> bool:
    """The carried message names that lane's checkout path (not just the word)."""
    return str(Path(".claude") / "worktrees" / name) in msg


def clean_stop(root: Path) -> dict:
    wt, _, stop = lane(root, "clean")
    wt.mkdir(parents=True, exist_ok=True)
    return stop


def case_two_session_end_failures_both_shown(root: Path) -> None:
    """Every pending SessionEnd failure is shown, once; none overwrites another."""
    session_end_failure(root, "first")
    session_end_failure(root, "second")
    check("two SessionEnd failures: two pending entries", len(pending(root)) == 2)
    stop = clean_stop(root)
    p, _ = run(root, stop)
    msg = system_message(p)
    check("next Stop shows both carried failures",
          p.returncode == 0 and "Earlier session end" in msg
          and shown(msg, "first") and shown(msg, "second"), msg)
    check("shown entries are acknowledged", pending(root) == [])
    p, _ = run(root, stop)
    check("a successful Stop does not replay acknowledged failures", p.returncode == 0 and not p.stdout)


def case_interrupted_stop_keeps_pending(root: Path) -> None:
    """A Stop killed, or failing to write its output, acknowledges nothing."""
    session_end_failure(root, "first")
    session_end_failure(root, "second")
    before = pending(root)
    check("interrupted Stop: two entries pending beforehand", len(before) == 2)
    started = root / "evacuator.pid"
    slow = root / "slow_evacuator.py"
    slow.write_text("import os, sys, time\n"
                    "open(os.environ['TEST_STARTED'], 'w').write(str(os.getpid()))\n"
                    "time.sleep(30)\n", encoding="utf-8")
    wt, store, stop = lane(root, "busy")
    make_packet(store, "ep_5555555555555555")
    env = {**os.environ, "EVIDENCE_LIVE_ARCHIVE": str(root / "archive"),
           "EVIDENCE_EVACUATOR": str(slow), "EVIDENCE_EVACUATE_LOG": str(root / "hook.log"),
           "TEST_STARTED": str(started)}
    env.pop("CLAUDE_PROJECT_DIR", None)
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=env)
    proc.stdin.write(json.dumps(stop).encode("utf-8"))
    proc.stdin.close()
    deadline = time.monotonic() + 20
    while not (started.is_file() and started.read_text()) and time.monotonic() < deadline:
        time.sleep(0.05)
    check("interrupted Stop: killed while its evacuator runs", started.is_file())
    proc.kill()  # the harness timeout, mid-evacuation
    proc.wait()
    proc.stdout.close()
    if started.is_file():
        os.kill(int(started.read_text()), 9)
    check("Stop killed before output: both entries still pending", pending(root) == before)

    fast = {**env, "EVIDENCE_EVACUATOR": EVACUATOR}
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=fast)
    proc.stdout.close()  # nobody reads: the systemMessage write fails
    proc.stdin.write(json.dumps(stop).encode("utf-8"))
    proc.stdin.close()
    proc.wait(timeout=60)
    check("Stop whose output fails: exit 0, both entries still pending",
          proc.returncode == 0 and pending(root) == before, f"rc={proc.returncode}")

    p, _ = run(root, stop)
    msg = system_message(p)
    check("the next Stop shows both", shown(msg, "first") and shown(msg, "second") and pending(root) == [], msg)


def case_append_during_stop_survives(root: Path) -> None:
    """An entry appended while a Stop runs is not acknowledged by that Stop."""
    session_end_failure(root, "older")
    older = pending(root)
    during = root / "appending_evacuator.py"
    during.write_text(
        "import json, os, subprocess, sys\n"
        "subprocess.run([sys.executable, os.environ['TEST_HOOK']],\n"
        "               input=json.dumps({'hook_event_name': 'SessionEnd', 'cwd': sys.argv[2]}).encode(),\n"
        "               env={**os.environ, 'EVIDENCE_EVACUATOR': os.environ['TEST_MISSING']})\n",
        encoding="utf-8")
    wt, store, stop = lane(root, "busy")
    make_packet(store, "ep_6666666666666666")
    p, _ = run(root, stop, env_extra={"EVIDENCE_EVACUATOR": str(during), "TEST_HOOK": HOOK,
                                      "TEST_MISSING": str(root / "missing.py")})
    after = pending(root)
    check("Stop shows the older entry", shown(system_message(p), "older"), system_message(p))
    check("older entry acknowledged, the one appended during Stop kept",
          len(after) == 1 and after[0] not in older, str(after))
    shutil.rmtree(wt)
    p, _ = run(root, clean_stop(root))
    check("the next Stop shows the newer entry, then it is cleared",
          shown(system_message(p), "busy") and pending(root) == [], system_message(p))


def case_interrupted_record_is_shown(root: Path) -> None:
    """A SessionEnd killed between write and rename leaves a .tmp: shown once stale, never lost."""
    pend = root / "hook.pending"
    pend.mkdir()
    stale = pend / "1-1-stale.tmp"
    stale.write_text("evacuator not found: lost-lane", encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    fresh = pend / "2-2-fresh.tmp"
    fresh.write_text("still being written", encoding="utf-8")
    stop = clean_stop(root)
    p, _ = run(root, stop)
    msg = system_message(p)
    check("stale .tmp is shown as an interrupted record",
          "(interrupted record) evacuator not found: lost-lane" in msg and "still being written" not in msg, msg)
    check("stale .tmp acknowledged, a fresh one (a write in progress) left alone",
          not stale.exists() and fresh.exists())


def case_unreadable_pending_is_visible(root: Path) -> None:
    """An unreadable entry or pending directory is a visible failure, never silent."""
    session_end_failure(root, "readable")
    bad = root / "hook.pending" / "0-0-bad.msg"
    bad.write_bytes(b"\xff\xfe\xfa not utf-8")
    stop = clean_stop(root)
    p, _ = run(root, stop)
    msg = system_message(p)
    check("unreadable entry is shown; the readable one too",
          "0-0-bad.msg unreadable" in msg and shown(msg, "readable"), msg)
    check("unreadable entry is kept, readable one acknowledged", pending(root) == [bad])
    bad.unlink()
    (root / "hook.pending").rmdir()
    (root / "hook.pending").write_text("not a directory", encoding="utf-8")
    p, _ = run(root, stop)
    check("unreadable pending directory is shown", "pending failure records unreadable" in system_message(p),
          system_message(p))


def case_entry_cleared_by_parallel_stop(root: Path) -> None:
    """An entry a parallel Stop acknowledges after this Stop listed it is skipped, not a failure."""
    session_end_failure(root, "first")
    session_end_failure(root, "second")
    entries = pending(root)
    check("parallel Stop: two entries pending beforehand", len(entries) == 2)
    raced = root / "raced"
    racing = root / "racing_stop.py"
    racing.write_text(
        "import os, pathlib, runpy, sys\n"
        "hook = sys.argv.pop(1)\n"
        "sys.argv[0] = hook\n"
        "real = pathlib.Path.read_text\n"
        "def cleared_meanwhile(self, *a, **k):\n"
        "    if self.name == os.environ['TEST_CLEARED']:\n"
        "        self.unlink()  # the other Stop showed and acknowledged it after this one listed it\n"
        "        open(os.environ['TEST_RACED'], 'w').close()\n"
        "    return real(self, *a, **k)\n"
        "pathlib.Path.read_text = cleared_meanwhile\n"
        "runpy.run_path(hook, run_name='__main__')\n", encoding="utf-8")
    p, _ = run(root, clean_stop(root), via=racing,
               env_extra={"TEST_CLEARED": entries[0].name, "TEST_RACED": str(raced)})
    msg = system_message(p)
    check("parallel Stop: the entry was cleared between listing and read", raced.is_file())
    check("parallel Stop: exit 0, the cleared entry is not repeated, the other is shown",
          p.returncode == 0 and shown(msg, "second") and not shown(msg, "first")
          and "unreadable" not in msg, f"rc={p.returncode} msg={msg} err={p.stderr[-300:]!r}")
    check("parallel Stop: the shown entry is acknowledged", pending(root) == [])


def case_closed_stdout_fails_open(root: Path) -> None:
    """A closed stdout (ValueError, not OSError) still exits 0 and acknowledges nothing."""
    session_end_failure(root, "first")
    before = pending(root)
    closed = root / "closed_stdout.py"
    closed.write_text(
        "import runpy, sys\n"
        "hook = sys.argv.pop(1)\n"
        "sys.argv[0] = hook\n"
        "sys.stdout.close()  # writes now raise ValueError\n"
        "runpy.run_path(hook, run_name='__main__')\n", encoding="utf-8")
    p, _ = run(root, clean_stop(root), via=closed)
    log = (root / "hook.log").read_text(encoding="utf-8")
    check("closed stdout: exit 0 (fail-open), crash logged",
          p.returncode == 0 and "unhandled ValueError" in log,
          f"rc={p.returncode} err={p.stderr[-300:]!r}")
    check("closed stdout: the unshown entry is still pending", len(before) == 1 and pending(root) == before)


def case_precheck_hashes_only_candidates(root: Path) -> None:
    """An unarchived packet costs a stat, not a hash; an archived one is hashed once each side."""
    wt, store, stop = lane(root)
    make_packet(store, "ep_aaaaaaaaaaaaaaaa")
    calls: list[Path] = []
    with in_process_hook(root) as hook:
        real = hook._sha256

        def counted(path: Path) -> str:
            calls.append(path)
            return real(path)

        hook._sha256 = counted
        first = hook.missing_ids(wt, root / "archive")
        run(root, stop)
        n = len(calls)
        second = hook.missing_ids(wt, root / "archive")
    check("unarchived packet: missing, no hash", first == ["ep_aaaaaaaaaaaaaaaa"] and n == 0)
    check("archived packet: settled with one hash per side", second == [] and len(calls) == 2)


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
    def unreadable(checkout: Path, *args) -> list[str]:
        if "a-bad" in str(checkout):
            raise PermissionError(13, "denied", str(checkout))
        return real(checkout, *args)

    with in_process_hook(root) as hook:
        real = hook.missing_ids
        hook.missing_ids = unreadable
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
    decoys = root / "archive" / "packets"
    decoys.mkdir(parents=True)
    for name in ("a", "b"):
        _, store, _ = lane(root, name)
        for pid in (f"ep_{name * 16}", f"ep_{name * 8}{'0' * 8}"):
            make_packet(store, pid)
            # a same-size, differing archive copy: only a candidate is hashed, so each
            # packet must be one for the pre-check to hash it
            (decoys / f"{pid}.json").write_text(
                json.dumps({"packet_id": pid, "schema_version": "2"}), encoding="utf-8")
    before = {f.name: f.read_bytes() for f in decoys.iterdir()}
    # Controlled clock: time advances only inside the stubbed hash, so the outcome
    # is exact regardless of machine speed. Budget 2.5 s, 0.6 s per hash: the first
    # packet and its candidate are hashed (2.5 >= MIN_CALL_S), then 1.3 s remain and
    # nothing else may be.
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
    check("spent budget stops hashing: one file and its candidate hashed, both checkouts "
          "deferred, archive untouched, exit 1",
          code == 1 and len(hashed) == 2 and len(deferred) == 2
          and {f.name: f.read_bytes() for f in decoys.iterdir()} == before,
          f"hashed={len(hashed)} out={lines}")


def case_non_ascii_output_is_success(root: Path) -> None:
    """L3: a successful evacuation whose output names a non-ASCII path is logged as OK,
    never as a failure. U+2010's UTF-8 bytes include 0x90, which cp1252 cannot decode,
    so a locale-decoded read of the child's output used to lose stdout entirely."""
    wt, store, stop = lane(root, "lane‐é")
    make_packet(store, "ep_1010101010101010")
    fake = root / "fake_evacuator.py"
    fake.write_text(
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "print('archive: ' + sys.argv[sys.argv.index('--archive') + 1])\n"
        "print('files: copied=1 checkout=' + sys.argv[2])\n",
        encoding="utf-8")
    p, _ = run(root, stop, env_extra={"EVIDENCE_EVACUATOR": str(fake), "PYTHONUTF8": "0"})
    log = (root / "hook.log").read_text(encoding="utf-8")
    ok = [ln for ln in log.splitlines() if " OK " in ln]
    check("non-ASCII evacuator output: exit 0, no systemMessage, logged OK with the path",
          p.returncode == 0 and system_message(p) == "" and "ERROR" not in log
          and len(ok) == 1 and "lane‐é" in ok[0],
          f"stdout={p.stdout!r} log={log!r}")
    check("OK record labels the pre-check count, not a copy count",
          len(ok) == 1 and "precheck_unarchived=1" in ok[0] and " new=" not in ok[0], log)


CASES = (case_non_ascii_output_is_success, case_two_session_end_failures_both_shown, case_interrupted_stop_keeps_pending,
         case_append_during_stop_survives, case_interrupted_record_is_shown,
         case_unreadable_pending_is_visible, case_entry_cleared_by_parallel_stop,
         case_closed_stdout_fails_open, case_precheck_hashes_only_candidates,
         case_partial_copy_is_retried, case_id_mismatch_is_settled, case_budget_is_shared,
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


def test_non_ascii_output_is_success() -> None:
    assert_case(case_non_ascii_output_is_success)


def test_two_session_end_failures_both_shown() -> None:
    assert_case(case_two_session_end_failures_both_shown)


def test_interrupted_stop_keeps_pending() -> None:
    assert_case(case_interrupted_stop_keeps_pending)


def test_append_during_stop_survives() -> None:
    assert_case(case_append_during_stop_survives)


def test_interrupted_record_is_shown() -> None:
    assert_case(case_interrupted_record_is_shown)


def test_unreadable_pending_is_visible() -> None:
    assert_case(case_unreadable_pending_is_visible)


def test_entry_cleared_by_parallel_stop() -> None:
    assert_case(case_entry_cleared_by_parallel_stop)


def test_closed_stdout_fails_open() -> None:
    assert_case(case_closed_stdout_fails_open)


def test_precheck_hashes_only_candidates() -> None:
    assert_case(case_precheck_hashes_only_candidates)


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
