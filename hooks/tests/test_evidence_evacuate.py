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

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
