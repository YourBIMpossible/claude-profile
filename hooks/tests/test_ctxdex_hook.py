"""Self-test driver for ctxdex_autoindex.py — exercises it exactly as Claude
Code would (JSON on stdin, inspect stdout/exit code), then checks ctxdex's
own DB for what did/didn't get indexed. Uses an isolated synthetic project
so nothing touches real data.
"""
import json
import subprocess
import sys
import sqlite3
import tempfile
import time
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "ctxdex_autoindex.py")
PROFILE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROFILE / "tools"))
import machine_config  # noqa: E402

# The real ctxdex in this machine's claude-tools clone (CTXDEX_DIR overrides, as for the hook).
import os  # noqa: E402
CTXDEX_DIR = Path(os.environ.get("CTXDEX_DIR") or Path(machine_config.load().get("tools_root") or "missing-claude-tools") / "ctxdex")
CTXDEX = (CTXDEX_DIR / "ctxdex.py").as_posix()
PROJECT = "ctxdex-hook-selftest"
# Isolated synthetic scratch root under the OS temp dir — the self-test writes
# fixture files at f"{CWD}-*.txt", so this MUST be a writable, throwaway location
# outside any real tree (previously {repos}/AI-Dev/_scratch, which both created an
# operative dependency on the retired AI-Dev root and littered that archive).
CWD = str(Path(tempfile.gettempdir()) / "ctxdex-hook-selftest")
DB_PATH = CTXDEX_DIR / "data" / f"{PROJECT}.db"

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def run_hook(payload, env_extra=None):
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return p


def db_rows(source=None):
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    if source:
        rows = conn.execute("SELECT id, source, path, title, indexed_at FROM docs WHERE source=?", (source,)).fetchall()
    else:
        rows = conn.execute("SELECT id, source, path, title, indexed_at FROM docs").fetchall()
    conn.close()
    return rows


def clean_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    for ext in ("-wal", "-shm"):
        p = Path(str(DB_PATH) + ext)
        if p.exists():
            p.unlink()


def run_checks() -> list:
    """Drive ctxdex_autoindex.py through every scenario. Returns the (name, ok, detail) result tuples."""
    results.clear()
    # fresh start
    clean_db()

    BIG = ("line of harmless build/log output here, nothing sensitive\n" * 2000)  # ~110KB
    SMALL = "short output\n" * 5  # well under threshold

    base_payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "npm run build"},
        "tool_output": BIG,
        "tool_output_is_error": False,
        "cwd": CWD,
    }

    # --- Scenario 1: large plain output gets captured ---------------------------
    p = run_hook(base_payload)
    rows = db_rows("bash-output")
    check("1a hook exits 0", p.returncode == 0, f"returncode={p.returncode} stderr={p.stderr[:300]}")
    check("1b hook stdout is empty", p.stdout == "", repr(p.stdout[:200]))
    check("1c large plain output got indexed", len(rows) >= 1, f"rows={rows}")
    if rows:
        check("1d path uses bash:// scheme", rows[0][2].startswith("bash://"), rows[0][2])
        check("1e title carries auto-captured marker + command", "[auto-captured]" in rows[0][3] and "npm run build" in rows[0][3], rows[0][3])

    # --- Scenario 2: small output does NOT get captured --------------------------
    clean_db()
    small_payload = dict(base_payload, tool_output=SMALL)
    p = run_hook(small_payload)
    rows = db_rows("bash-output")
    check("2a hook exits 0 on small output", p.returncode == 0)
    check("2b small output NOT indexed", len(rows) == 0, f"rows={rows}")

    # --- Scenario 3: sensitive command is skipped (command gate) -----------------
    clean_db()
    sensitive_cmd_payload = dict(base_payload, tool_input={"command": "printenv | sort"})
    p = run_hook(sensitive_cmd_payload)
    rows = db_rows("bash-output")
    check("3a hook exits 0 on sensitive command", p.returncode == 0)
    check("3b sensitive-command output NOT indexed", len(rows) == 0, f"rows={rows}")
    # also check a realistic false-positive-prone command is NOT over-blocked
    safe_cmd_payload = dict(base_payload, tool_input={"command": "git push --set-upstream origin feature/x"})
    p = run_hook(safe_cmd_payload)
    rows = db_rows("bash-output")
    # BIG (~110KB) legitimately splits into many ~4KB chunks under one heading - assert
    # presence, not an exact count.
    check("3c '--set-upstream' command NOT falsely blocked", len(rows) >= 1, f"rows={rows}")
    clean_db()

    # --- Scenario 4: sensitive output content is skipped (output gate) -----------
    secret_output = BIG + "\nAKIAABCDEFGHIJKLMNOP\n"
    secret_payload = dict(base_payload, tool_output=secret_output)
    p = run_hook(secret_payload)
    rows = db_rows("bash-output")
    check("4a hook exits 0 on secret-bearing output", p.returncode == 0)
    check("4b output with AWS-key-shaped string NOT indexed", len(rows) == 0, f"rows={rows}")

    pem_output = BIG + "\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----\n"
    pem_payload = dict(base_payload, tool_output=pem_output)
    p = run_hook(pem_payload)
    rows = db_rows("bash-output")
    check("4c output with PEM private key NOT indexed", len(rows) == 0, f"rows={rows}")

    # --- Scenario 5: opt-out env var ---------------------------------------------
    clean_db()
    p = run_hook(base_payload, env_extra={"CTXDEX_AUTO_INDEX": "0"})
    rows = db_rows("bash-output")
    check("5a hook exits 0 with opt-out set", p.returncode == 0)
    check("5b opt-out env var suppresses capture", len(rows) == 0, f"rows={rows}")

    # --- Scenario 6: retention / purge scoping (ctxdex.py --older-than + --source composed) ---
    clean_db()
    run_hook(base_payload)  # fresh bash-output rows, indexed "now"
    manual_doc = Path(f"{CWD}-manual-doc.txt")
    manual_doc.parent.mkdir(parents=True, exist_ok=True)
    manual_doc.write_text("plain prose fixture, no markdown headings, single chunk expected.", encoding="utf-8")
    subprocess.run([sys.executable, CTXDEX, "index", str(manual_doc), "--source", "manual-doc", "--project", PROJECT], capture_output=True, text=True)
    conn = sqlite3.connect(DB_PATH)
    old_cutoff = int(time.time()) - 20 * 86400  # 20 days ago -> older than default 14d TTL
    conn.execute("UPDATE docs SET indexed_at = ? WHERE source = 'bash-output'", (old_cutoff,))
    conn.commit()
    conn.close()
    bash_before = db_rows("bash-output")
    manual_before = db_rows("manual-doc")
    check("6a fixture has both an old bash-output row set and a manual-doc row", len(bash_before) >= 1 and len(manual_before) == 1, f"bash={len(bash_before)} manual={len(manual_before)}")

    purge = subprocess.run(
        [sys.executable, CTXDEX, "purge", "--project", PROJECT, "--source", "bash-output", "--older-than", "14"],
        capture_output=True, text=True,
    )
    bash_rows_after = db_rows("bash-output")
    manual_rows_after = db_rows("manual-doc")
    check("6b purge --source bash-output --older-than 14 removes the old bash-output rows", len(bash_rows_after) == 0, f"stdout={purge.stdout!r} bash_rows={bash_rows_after}")
    check("6c purge with composed filters leaves manual-doc row untouched", len(manual_rows_after) == len(manual_before), f"manual_before={manual_before} manual_after={manual_rows_after}")
    manual_doc.unlink(missing_ok=True)

    # back-compat: --source alone still deletes regardless of age (no age filter)
    clean_db()
    run_hook(base_payload)
    subprocess.run([sys.executable, CTXDEX, "purge", "--project", PROJECT, "--source", "bash-output"], capture_output=True, text=True)
    check("6d --source alone (no --older-than) still purges with no age filter (back-compat)", len(db_rows("bash-output")) == 0)

    # back-compat: age-only purge (neither flag) still defaults to 14d TTL, ignores source
    clean_db()
    run_hook(base_payload)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE docs SET indexed_at = ?", (int(time.time()) - 20 * 86400,))
    conn.commit()
    conn.close()
    subprocess.run([sys.executable, CTXDEX, "purge", "--project", PROJECT], capture_output=True, text=True)
    check("6e bare purge (no flags) still age-purges at default TTL (back-compat)", len(db_rows()) == 0)

    # --- Scenario 7: non-Bash tool ignored + self-index guard ---------------------
    clean_db()
    non_bash_payload = dict(base_payload, tool_name="Read")
    p = run_hook(non_bash_payload)
    rows = db_rows("bash-output")
    check("7a non-Bash tool_name exits 0", p.returncode == 0)
    check("7b non-Bash tool_name never indexed", len(rows) == 0, f"rows={rows}")

    self_index_payload = dict(base_payload, tool_input={"command": f"python {CTXDEX} search \"foo\""})
    p = run_hook(self_index_payload)
    rows = db_rows("bash-output")
    check("7c command invoking ctxdex itself is skipped (no recursive self-index)", len(rows) == 0, f"rows={rows}")
    clean_db()

    # backslash path + bare-name-at-start variants must also trip the guard
    self_index_bs_payload = dict(base_payload, tool_input={"command": "python " + CTXDEX.replace("/", "\\") + " stats"})
    p = run_hook(self_index_bs_payload)
    rows = db_rows("bash-output")
    check("7d backslash-path ctxdex.py invocation is skipped", len(rows) == 0, f"rows={rows}")
    clean_db()

    bare_exe_payload = dict(base_payload, tool_input={"command": "ctxdex search \"foo\""})
    p = run_hook(bare_exe_payload)
    rows = db_rows("bash-output")
    check("7e bare 'ctxdex' at command-start is skipped (resolved-executable case)", len(rows) == 0, f"rows={rows}")
    clean_db()

    chained_exe_payload = dict(base_payload, tool_input={"command": "echo hi && ctxdex stats"})
    p = run_hook(chained_exe_payload)
    rows = db_rows("bash-output")
    check("7f bare 'ctxdex' right after a shell separator is skipped", len(rows) == 0, f"rows={rows}")
    clean_db()

    # --- Scenario 7g-7i: merely MENTIONING ctxdex must NOT trip the guard -------
    # This is the precision fix (2026-08-07) - the old guard suppressed any
    # command containing the substring "ctxdex" anywhere, including reading this
    # hook's own source or grepping for mentions. Neither is a real self-index
    # risk, so both should now capture normally when large enough.
    mention_prose_payload = dict(base_payload, tool_input={"command": "echo \"check the ctxdex search results\""})
    p = run_hook(mention_prose_payload)
    rows = db_rows("bash-output")
    check("7g prose mention of 'ctxdex' (not an invocation) is NOT suppressed", len(rows) >= 1, f"rows={rows}")
    clean_db()

    mention_grep_payload = dict(base_payload, tool_input={"command": f"grep -r ctxdex {PROFILE.as_posix()}"})
    p = run_hook(mention_grep_payload)
    rows = db_rows("bash-output")
    check("7h grepping FOR 'ctxdex' (not invoking it) is NOT suppressed", len(rows) >= 1, f"rows={rows}")
    clean_db()

    mention_read_own_source_payload = dict(base_payload, tool_input={"command": f"cat {PROFILE.as_posix()}/hooks/ctxdex_autoindex.py"})
    p = run_hook(mention_read_own_source_payload)
    rows = db_rows("bash-output")
    check("7i reading this hook's own source (ctxdex_autoindex.py, not ctxdex.py) is NOT suppressed", len(rows) >= 1, f"rows={rows}")
    clean_db()

    # --- Scenario 8: native persisted-output pointer is followed (and only when safe) ---
    import os

    TOOL_RESULTS_DIR = Path.home() / ".claude" / "projects" / "_ctxdex-hook-selftest" / "tool-results"
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    real_big_file = TOOL_RESULTS_DIR / "selftest-real.txt"
    MARKER = "UNIQUE_MARKER_ONLY_IN_REAL_FILE_9f3a"
    real_big_file.write_text(f"{MARKER}\n" + ("real persisted content line\n" * 4500), encoding="utf-8")

    wrapper_text = (
        f"Output too large (146.5KB). Full output saved to: {real_big_file}\n\n"
        "Preview (first 2KB):\nreal persisted content line\n..."
    )
    wrapper_payload = dict(base_payload, tool_output=wrapper_text)
    p = run_hook(wrapper_payload)
    rows = db_rows("bash-output")
    check("8a hook exits 0 on persisted-output wrapper", p.returncode == 0)
    check("8b wrapper pointer under tool-results IS followed (real content indexed)", len(rows) >= 1, f"rows={rows}")
    clean_db()

    # negative: wrapper-shaped text pointing OUTSIDE the tool-results tree must NOT be followed
    outside_file = Path(f"{CWD}-outside-file.txt")
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_text(MARKER + "\n" + ("should never be read\n" * 3000), encoding="utf-8")
    spoof_text = f"Output too large (146.5KB). Full output saved to: {outside_file}\n\nPreview (first 2KB):\n..."
    spoof_payload = dict(base_payload, tool_output=spoof_text)
    p = run_hook(spoof_payload)
    rows = db_rows("bash-output")
    check("8c hook exits 0 on spoofed (outside-tree) pointer", p.returncode == 0)
    check("8d pointer outside tool-results tree NOT followed (wrapper text alone is small, skipped)", len(rows) == 0, f"rows={rows}")
    outside_file.unlink(missing_ok=True)

    real_big_file.unlink(missing_ok=True)
    try:
        TOOL_RESULTS_DIR.rmdir()
        TOOL_RESULTS_DIR.parent.rmdir()
    except OSError:
        pass
    clean_db()

    # --- Scenario 9: real dict-shaped tool_response with persistedOutputPath ----
    # This is the ACTUAL confirmed live payload shape (2026-08-07 live payload
    # dump) - the primary mechanism. Scenario 8 above tests the secondary
    # string-wrapper fallback only; this is what real Bash calls produce.
    TOOL_RESULTS_DIR2 = Path.home() / ".claude" / "projects" / "_ctxdex-hook-selftest2" / "tool-results"
    TOOL_RESULTS_DIR2.mkdir(parents=True, exist_ok=True)
    real_big_file2 = TOOL_RESULTS_DIR2 / "selftest-real2.txt"
    MARKER2 = "UNIQUE_MARKER_ONLY_IN_REAL_FILE_DICT_7e2b"
    full_content = f"{MARKER2}\n" + ("real persisted content via dict field\n" * 4500)
    real_big_file2.write_text(full_content, encoding="utf-8")

    dict_payload = dict(
        base_payload,
        tool_output=None,
        tool_response={
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
            "persistedOutputPath": str(real_big_file2),
            "persistedOutputSize": len(full_content.encode("utf-8")),
            "stderr": "",
            "stdout": full_content[:30000],  # harness truncates stdout to 30000 chars
        },
    )
    p = run_hook(dict_payload)
    rows = db_rows("bash-output")
    check("9a hook exits 0 on dict-shaped tool_response with persistedOutputPath", p.returncode == 0, f"stderr={p.stderr[:300]}")
    check("9b persistedOutputPath dict field IS followed (real full content indexed)", len(rows) >= 1, f"rows={rows}")
    clean_db()

    # negative: persistedOutputPath pointing OUTSIDE the tool-results tree must NOT be followed
    outside_file2 = Path(f"{CWD}-outside-file2.txt")
    outside_file2.parent.mkdir(parents=True, exist_ok=True)
    outside_file2.write_text(MARKER2 + "\n" + ("should never be read via dict field\n" * 3000), encoding="utf-8")
    spoof_dict_payload = dict(
        base_payload,
        tool_output=None,
        tool_response={
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
            "persistedOutputPath": str(outside_file2),
            "persistedOutputSize": 999999,
            "stderr": "",
            "stdout": "short stdout, well under threshold\n",
        },
    )
    p = run_hook(spoof_dict_payload)
    rows = db_rows("bash-output")
    check("9c hook exits 0 on spoofed (outside-tree) persistedOutputPath", p.returncode == 0)
    check("9d persistedOutputPath outside tool-results tree NOT followed (falls back to small dict text, skipped)", len(rows) == 0, f"rows={rows}")
    outside_file2.unlink(missing_ok=True)

    # dict-shaped tool_response WITHOUT persistedOutputPath (no persistence
    # occurred) - falls back to _as_text(output), the dict as a whole; since real
    # stdout is hard-capped at 30000 chars (<100KiB), this can never reach the
    # size gate on its own, which is expected/correct.
    small_dict_payload = dict(
        base_payload,
        tool_output=None,
        tool_response={
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
            "stderr": "",
            "stdout": "short output\n" * 5,
        },
    )
    p = run_hook(small_dict_payload)
    rows = db_rows("bash-output")
    check("9e hook exits 0 on dict-shaped tool_response with no persistedOutputPath", p.returncode == 0)
    check("9f small dict output (no persisted pointer) NOT indexed", len(rows) == 0, f"rows={rows}")

    real_big_file2.unlink(missing_ok=True)
    try:
        TOOL_RESULTS_DIR2.rmdir()
        TOOL_RESULTS_DIR2.parent.rmdir()
    except OSError:
        pass
    clean_db()
    return list(results)


def test_ctxdex_hook() -> None:
    """pytest entry point - same checks as running this file directly."""
    res = run_checks()
    failed = [(n, d) for n, ok, d in res if not ok]
    assert res, "suite ran zero checks"
    assert not failed, "failing checks:\n  " + "\n  ".join(
        n + (f" ({d})" if d else "") for n, d in failed)


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
