"""Self-test driver for evidence_evacuate.py — runs the hook exactly as Claude
Code would (JSON on stdin, check exit code and stdout) against synthetic
checkouts and a throwaway archive under the OS temp dir, using the real
Claude-Tools evacuator. Nothing touches real packet stores or EC-LIVE.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "evidence_evacuate.py")
EVACUATOR = "F:/Claude-Tools/evidence-archive/evacuate_worktree.py"

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def make_packet(store: Path, pid: str) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / f"2026-09-25T00-00-00.000000Z_{pid}.json").write_text(
        json.dumps({"packet_id": pid, "schema_version": "1"}), encoding="utf-8")


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


def clean_stop(root: Path) -> dict:
    wt, _, stop = lane(root, "clean")
    wt.mkdir(parents=True, exist_ok=True)
    return stop


def test_two_session_end_failures_both_shown(root: Path) -> None:
    """Every pending SessionEnd failure is shown, once; none overwrites another."""
    session_end_failure(root, "first")
    session_end_failure(root, "second")
    check("two SessionEnd failures: two pending entries", len(pending(root)) == 2)
    stop = clean_stop(root)
    p, _ = run(root, stop)
    msg = system_message(p)
    check("next Stop shows both carried failures",
          p.returncode == 0 and "Earlier session end" in msg
          and "worktrees" in msg and "first" in msg and "second" in msg, msg)
    check("shown entries are acknowledged", pending(root) == [])
    p, _ = run(root, stop)
    check("a successful Stop does not replay acknowledged failures", p.returncode == 0 and not p.stdout)


def test_interrupted_stop_keeps_pending(root: Path) -> None:
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
    check("Stop whose output fails: both entries still pending", pending(root) == before)

    p, _ = run(root, stop)
    msg = system_message(p)
    check("the next Stop shows both", "first" in msg and "second" in msg and pending(root) == [], msg)


def test_append_during_stop_survives(root: Path) -> None:
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
    check("Stop shows the older entry", "older" in system_message(p), system_message(p))
    check("older entry acknowledged, the one appended during Stop kept",
          len(after) == 1 and after[0] not in older, str(after))
    shutil.rmtree(wt)
    p, _ = run(root, clean_stop(root))
    check("the next Stop shows the newer entry, then it is cleared",
          "busy" in system_message(p) and pending(root) == [], system_message(p))


def test_unreadable_pending_is_visible(root: Path) -> None:
    """An unreadable entry or pending directory is a visible failure, never silent."""
    session_end_failure(root, "readable")
    bad = root / "hook.pending" / "0-0-bad.msg"
    bad.write_bytes(b"\xff\xfe\xfa not utf-8")
    stop = clean_stop(root)
    p, _ = run(root, stop)
    msg = system_message(p)
    check("unreadable entry is shown; the readable one too",
          "0-0-bad.msg unreadable" in msg and "readable" in msg, msg)
    check("unreadable entry is kept, readable one acknowledged", pending(root) == [bad])
    bad.unlink()
    (root / "hook.pending").rmdir()
    (root / "hook.pending").write_text("not a directory", encoding="utf-8")
    p, _ = run(root, stop)
    check("unreadable pending directory is shown", "pending failure records unreadable" in system_message(p),
          system_message(p))


def test_budget_covers_precheck(root: Path) -> None:
    """The pre-check is inside the budget: a spent budget defers before hashing."""
    wt, store, stop = lane(root)
    for i in range(3):
        make_packet(store, f"ep_{i:016x}")
    run(root, stop)
    p, _ = run(root, stop, env_extra={"EVIDENCE_EVACUATE_BUDGET_S": "-1"})
    check("spent budget during pre-check is a surfaced deferral",
          p.returncode == 0 and "while checking" in system_message(p))


def test_precheck_hashes_only_candidates(root: Path) -> None:
    """An unarchived packet costs a stat, not a hash; an archived one is hashed once each side."""
    sys.path.insert(0, str(Path(HOOK).parent))
    import evidence_evacuate as hook
    wt, store, stop = lane(root)
    make_packet(store, "ep_aaaaaaaaaaaaaaaa")
    calls: list[Path] = []
    real = hook._sha256

    def counted(path: Path) -> str:
        calls.append(path)
        return real(path)

    hook._sha256 = counted
    try:
        first = hook.missing_ids(wt, root / "archive")
        run(root, stop)
        n = len(calls)
        second = hook.missing_ids(wt, root / "archive")
    finally:
        hook._sha256 = real
    check("unarchived packet: missing, no hash", first == ["ep_aaaaaaaaaaaaaaaa"] and n == 0)
    check("archived packet: settled with one hash per side", second == [] and len(calls) == 2)


def test_partial_copy_is_retried(root: Path) -> None:
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


def test_id_mismatch_is_settled(root: Path) -> None:
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


def test_budget_is_shared(root: Path) -> None:
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


def test_session_end_sweeps_siblings(root: Path) -> None:
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


def test_bad_checkout_does_not_stop_sweep(root: Path) -> None:
    """L5: an exception in one checkout is recorded; later checkouts still run."""
    a, a_store, _ = lane(root, "a-bad")
    b, b_store, _ = lane(root, "b-good")
    make_packet(a_store, "ep_8888888888888888")
    make_packet(b_store, "ep_9999999999999999")
    def unreadable(checkout: Path, *args) -> list[str]:
        if "a-bad" in str(checkout):
            raise PermissionError(13, "denied", str(checkout))
        return real(checkout, *args)

    env = {"EVIDENCE_LIVE_ARCHIVE": str(root / "archive"), "EVIDENCE_EVACUATOR": EVACUATOR,
           "EVIDENCE_EVACUATE_LOG": str(root / "hook.log")}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.path.insert(0, str(Path(HOOK).parent))
    import evidence_evacuate as hook  # LOG_PATH is read at import
    real = hook.missing_ids
    hook.missing_ids = unreadable
    try:
        code = hook.main(["--sweep", str(root / "repo")])
    finally:
        hook.missing_ids = real
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("sweep: bad checkout recorded, later checkout still archived, exit 1",
          code == 1 and (root / "archive" / "packets" / "ep_9999999999999999.json").is_file()
          and "PermissionError" in (root / "hook.log").read_text(encoding="utf-8"))
    p, _ = run(root, None, args=["--sweep", str(root / "repo")], raw=b"",
               env_extra={"EVIDENCE_EVACUATOR": str(root / "missing.py")})
    check("sweep CLI: a failure exits 1 and prints it",
          p.returncode == 1 and b"FAIL" in p.stdout)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ec-evacuate-selftest-"))
    try:
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
    finally:
        shutil.rmtree(root, ignore_errors=True)

    for case in (test_two_session_end_failures_both_shown, test_interrupted_stop_keeps_pending,
                 test_append_during_stop_survives, test_unreadable_pending_is_visible, test_partial_copy_is_retried, test_id_mismatch_is_settled,
                 test_budget_is_shared, test_session_end_sweeps_siblings,
                 test_bad_checkout_does_not_stop_sweep, test_budget_covers_precheck,
                 test_precheck_hashes_only_candidates):
        root = Path(tempfile.mkdtemp(prefix="ec-evacuate-selftest-"))
        try:
            case(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
