"""CT-SLOP1026 / N-2: the ctxdex PostToolUse hook's output secret scan must run in linear time.

The credential-URL pattern `\\w+://[^\\s/:@]+:[^\\s/@]+@` was quadratic on a long run of letters or digits: `\\w+` was tried from
every position of the run and each try scanned to the end of the run before failing on `://`. A review measured 1.6 s at 20 000
characters; a 200 000-character single-token Bash output kept the hook running until its timeout. The second pattern that scans
across the text, the ODBC-style `server=...;password=` match, rescanned the whole tail for every `server=` it found.

These tests pin (1) the time on the shapes that were quadratic, with a wide margin, (2) that every credential the old pattern caught is
still caught, and no new match appears, on a few thousand strings (the old pattern is written out here to compare against), and
(3) the match and no-match cases for real credential URLs and connection strings.

Imports the hook module directly; the timing runs in a child process so a regression fails in seconds instead of hanging the suite.
Run: python hooks/tests/test_ctxdex_secret_scan.py  (or: python -m pytest hooks/tests -q)
"""
import importlib.util
import itertools
import random
import re
import subprocess
import sys
import time
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parents[1] / "ctxdex_autoindex.py"

spec = importlib.util.spec_from_file_location("ctxdex_autoindex_under_test", HOOK_PATH)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

# The pattern exactly as it was before N-2, kept here only to prove the replacement matches the same strings.
OLD_URL_RE = re.compile(r"\w+://[^\s/:@]+:[^\s/@]+@")
OLD_ODBC_RE = re.compile(r"(?is)\bserver\s*=.*?;\s*password\s*=")

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


def scan_seconds(text_expr: str, limit: float = 20.0):
    """Run hook._output_looks_sensitive on the generated text in a child process. -> (seconds, verdict) or (None, None) on timeout."""
    code = (
        "import importlib.util, time\n"
        f"s = importlib.util.spec_from_file_location('h', {str(HOOK_PATH)!r})\n"
        "h = importlib.util.module_from_spec(s); s.loader.exec_module(h)\n"
        f"text = {text_expr}\n"
        "t = time.perf_counter(); v = h._output_looks_sensitive(text); print(time.perf_counter() - t, v)\n"
    )
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return None, None
    secs, verdict = p.stdout.split()
    return float(secs), verdict == "True"


# Shapes that were quadratic (each 200 000 characters or about that), none of which holds a credential.
SLOW_SHAPES = {
    "one long run of letters": '"a" * 200000',
    "one long run of digits": '"1" * 200000',
    "a short scheme then a very long user part with no colon": '"a" * 1000 + "://" + "b" * 200000',
    "many scheme starts": '"a://" * 50000',
    "many server= with no password": '"server=a;" * 22000',
}


def run_checks() -> list:
    results.clear()

    # -- 1. time -----------------------------------------------------------------------------------------------------
    for name, expr in SLOW_SHAPES.items():
        secs, verdict = scan_seconds(expr)
        check(f"scan of {name} (about 200k chars) takes under 1 s and finds nothing",
              secs is not None and secs < 1.0 and verdict is False, f"seconds={secs} verdict={verdict}")

    # -- 2. the same credentials are still caught ----------------------------------------------------------------------
    caught = [
        "https://user:pass@example.com/x",
        "postgres://admin:s3cr3t@db.internal:5432/app",
        "ftp://a:b@c",
        "redis://default:p%40ss@cache",
        "my_scheme2://u:p@h",
        "see https://user:pass@example.com for details",
        "x1http://u:p@h",                       # the scheme run starts mid-token: still a credential URL
        "(postgres://u:p@h)",
        "https://example.com:8080@",             # host:port@ reads as user:pass@ - the old pattern flagged it too
        "line one\nmysql://root:toor@127.0.0.1/db\nline three",
        "Server=tcp:db.example.net;Database=app;User Id=u;Password=hunter2",
        "SERVER = a ;\n  PASSWORD = b",
        "server=a;;;password=b",
    ]
    for text in caught:
        check(f"still flagged: {text[:40]!r}", hook._output_looks_sensitive(text))
    not_caught = [
        "https://example.com/path",
        "https://example.com:8080/path",
        "https://user@example.com/x",             # a user without a password
        "://u:p@h",                                # no scheme
        "https://u:p/@h",                          # a slash inside the password part
        "mailto:a@b.c",
        "Server=a;Database=b",                     # no password
        "password=b; server=a",                    # password before the server
        "plain output line\n" * 20,
    ]
    for text in not_caught:
        check(f"not flagged: {text[:40]!r}", not hook._output_looks_sensitive(text))

    # -- 3. the replacement matches exactly what the old patterns matched --------------------------------------------
    url_re = hook._CREDENTIAL_URL_RE
    alphabet = ["a", "1", "_", ":", "/", "@", " ", "."]
    bad = []
    for n in range(0, 6):                                           # every string up to 5 characters
        for tup in itertools.product(alphabet, repeat=n):
            s = "".join(tup)
            if bool(url_re.search(s)) != bool(OLD_URL_RE.search(s)):
                bad.append(s)
    rnd = random.Random(1026)
    pieces = ["a", "1", "x_", "://", ":", "/", "@", " ", "\n", ".", "http", "u:p@", "Server=", ";", "Password=", "=", "-"]
    for _ in range(40000):
        s = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 14)))
        if bool(url_re.search(s)) != bool(OLD_URL_RE.search(s)):
            bad.append(s)
    check("the credential-URL pattern agrees with the old pattern on every string up to 5 chars and 40000 random ones",
          not bad, f"{len(bad)} differ, first: {bad[:3]!r}")
    bad = []
    for _ in range(40000):
        s = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 14)))
        old = bool(OLD_ODBC_RE.search(s))
        new = hook._looks_like_odbc_secret(s)
        if old != new:
            bad.append(s)
    check("the connection-string check agrees with the old pattern on 40000 random strings", not bad,
          f"{len(bad)} differ, first: {bad[:3]!r}")

    # -- 4. the other patterns and the verdict on a real-sized mixed output are unchanged ------------------------------
    big = ("line of harmless build/log output here, nothing sensitive\n" * 2000)
    t = time.perf_counter()
    harmless = hook._output_looks_sensitive(big)
    check("a 110 KB harmless output is clean and scans fast", harmless is False and time.perf_counter() - t < 1.0)
    check("an AWS-key-shaped string is still caught", hook._output_looks_sensitive(big + "\nAKIAABCDEFGHIJKLMNOP\n"))
    check("a PEM header is still caught", hook._output_looks_sensitive(big + "\n-----BEGIN RSA PRIVATE KEY-----\n"))
    return list(results)


def test_ctxdex_secret_scan() -> None:
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
