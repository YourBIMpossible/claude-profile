"""Fixture tests for nl_filter_drift_monitor.py. Every test uses a temp ledger and a
fake monitor script; nothing touches the real repo, container, baseline or shared ledger."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "nl_filter_drift_monitor.py"
spec = importlib.util.spec_from_file_location("nlmon", SCRIPT)
nlmon = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(nlmon)

FAKE = '''
import json, sys
rc = {rc}
args = sys.argv[1:]
out = args[args.index("--json") + 1]
summary = {{"schema": "nl-filter-monitor/1", "mode": "offline", "sample_count": 37,
           "score": {score}, "flips": 0, "transport_errors": 0, "exit_code": rc}}
if rc in (0, 1):
    open(out, "w", encoding="utf-8").write(json.dumps(summary))
    print("nl-filter-monitor mode=offline samples=37 score={score} verdict=" + ("PASS" if rc == 0 else "FAIL"))
else:
    print("nl-filter-monitor: RuntimeError -- the run proved nothing", file=sys.stderr)
sys.exit(rc)
'''


def fake_monitor(tmp_path: Path, rc: int, score: float = 1.0) -> Path:
    d = tmp_path / "repo" / "backend" / "tests" / "eval"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "nl_filter_monitor.py"
    p.write_text(FAKE.format(rc=rc, score=score), encoding="utf-8")
    return p


def run(tmp_path: Path, *extra: str, rc: int = 0, score: float = 1.0) -> tuple[int, Path]:
    ledger = tmp_path / "ledger"
    mon = fake_monitor(tmp_path, rc, score)
    code = nlmon.main(["--mode", "offline", "--ledger-dir", str(ledger), "--monitor-script", str(mon),
                       "--repo", str(tmp_path / "repo"), "--python", sys.executable, *extra])
    return code, ledger


def evidence(ledger: Path) -> list[Path]:
    return sorted((ledger / "_evidence" / "nl-filter-monitor").glob("*.json"))


def test_green_appends_log_writes_evidence_and_clears_alert(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / nlmon.ALERT_NAME).write_text("stale", encoding="utf-8")
    (ledger / nlmon.LEDGER_NAME).write_text(nlmon.LEDGER_HEADER + "\n| old row |\n", encoding="utf-8")
    code, ledger = run(tmp_path)
    assert code == 0
    log = (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert "| old row |" in log  # appended, not rewritten
    assert "| PASS |" in log and "1.000" in log and "(alert cleared)" in log
    assert not (ledger / nlmon.ALERT_NAME).exists()
    ev = evidence(ledger)
    assert len(ev) == 1 and json.loads(ev[0].read_text())["sample_count"] == 37


def test_green_creates_ledger_with_header(tmp_path: Path) -> None:
    code, ledger = run(tmp_path)
    assert code == 0
    log = (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert log.startswith("# NL-filter drift monitor")
    assert "(alert cleared)" not in log


def test_red_writes_alert(tmp_path: Path) -> None:
    code, ledger = run(tmp_path, rc=1, score=0.9)
    assert code == 1
    alert = (ledger / nlmon.ALERT_NAME).read_text(encoding="utf-8")
    assert "NL-FILTER DRIFT ALERT" in alert and "never re-pins" in alert
    assert "**FAIL (regression)**" in (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")


@pytest.mark.parametrize("monitor_rc", [2, 3, 137])
def test_not_run_maps_to_2_and_alerts(tmp_path: Path, monitor_rc: int) -> None:
    code, ledger = run(tmp_path, rc=monitor_rc)
    assert code == 2
    assert (ledger / nlmon.ALERT_NAME).exists()
    log = (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert "**RED (did not run)**" in log and "(none)" in log


def test_missing_monitor_is_not_run(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    code = nlmon.main(["--ledger-dir", str(ledger), "--monitor-script", str(tmp_path / "nope.py")])
    assert code == 2 and (ledger / nlmon.ALERT_NAME).exists()


def test_live_container_down_is_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nlmon, "_container_running", lambda c: False)
    ledger = tmp_path / "ledger"
    code = nlmon.main(["--mode", "live", "--ledger-dir", str(ledger), "--container", "no-such"])
    assert code == 2
    assert "is not running" in (ledger / nlmon.ALERT_NAME).read_text(encoding="utf-8")


LIVE_PASS = "nl-filter-monitor mode=live samples=37 score=1.0 verdict=PASS\n"
LIVE_SUMMARY = {"schema": "nl-filter-monitor/1", "mode": "live", "sample_count": 37,
                "score": 1.0, "flips": 0, "transport_errors": 0, "exit_code": 0}


def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exec_rc: int = 0, exec_out: str = LIVE_PASS,
         cp_ok: bool = True, rm_rc: int = 0) -> tuple[int, Path, list[list[str]]]:
    """Run --mode live against an in-process `_run` stub: no docker, no git, no network.
    A standing alert from an earlier red run is seeded so each test can see its fate."""
    calls: list[list[str]] = []

    def stub(cmd, *, cwd=None, env=None, timeout=1800):  # type: ignore[no-untyped-def]
        cmd = list(cmd)
        calls.append(cmd)
        if cmd[0] == "git":
            return 0, "0123456789abcdef\n"
        if cmd[:2] == ["docker", "cp"]:
            if not cp_ok:
                return 1, "Error response from daemon: no such file\n"
            Path(cmd[3]).write_text(json.dumps(LIVE_SUMMARY), encoding="utf-8")
            return 0, ""
        if "rm" in cmd:
            return rm_rc, ""
        return exec_rc, exec_out

    monkeypatch.setattr(nlmon, "_run", stub)
    monkeypatch.setattr(nlmon, "_container_running", lambda c: True)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / nlmon.ALERT_NAME).write_text("standing", encoding="utf-8")
    code = nlmon.main(["--mode", "live", "--repeats", "2", "--ledger-dir", str(ledger),
                       "--repo", str(tmp_path / "repo")])
    return code, ledger, calls


def is_eval(cmd: list[str]) -> bool:
    return "tests/eval/nl_filter_monitor.py" in cmd


def test_live_green_records_pass_and_clears_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, ledger, calls = live(tmp_path, monkeypatch)
    assert code == 0
    log = (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert "| PASS |" in log and "| 37 |" in log and "(alert cleared)" in log
    assert not (ledger / nlmon.ALERT_NAME).exists()
    assert sum(is_eval(c) for c in calls) == 1


def test_live_cp_failure_is_not_run_and_keeps_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, ledger, _ = live(tmp_path, monkeypatch, cp_ok=False)
    assert code == 2
    log = (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert "| PASS |" not in log and "(alert cleared)" not in log
    assert "**RED (did not run)**" in log and "docker cp" in log
    assert (ledger / nlmon.ALERT_NAME).exists()


@pytest.mark.parametrize("cp_ok", [False, True])
@pytest.mark.parametrize("exec_rc", [0, 1])
def test_live_missing_verdict_is_not_run_and_keeps_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         cp_ok: bool, exec_rc: int) -> None:
    code, ledger, _ = live(tmp_path, monkeypatch, exec_rc=exec_rc, exec_out="some unrelated output\n",
                           cp_ok=cp_ok)
    assert code == 2
    log = (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert "| PASS |" not in log and "(alert cleared)" not in log
    assert "**RED (did not run)**" in log
    assert "UNPARSEABLE" in (ledger / nlmon.ALERT_NAME).read_text(encoding="utf-8")


def test_live_stale_artifact_not_cleared_is_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, ledger, calls = live(tmp_path, monkeypatch, rm_rc=1)
    assert code == 2
    assert not any(is_eval(c) for c in calls)  # the paid evaluator never ran
    assert "| PASS |" not in (ledger / nlmon.LEDGER_NAME).read_text(encoding="utf-8")
    assert (ledger / nlmon.ALERT_NAME).exists()


def test_green_keeps_alert_and_fails_when_ledger_append_fails(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    (ledger / nlmon.LEDGER_NAME).mkdir(parents=True)  # the append raises OSError
    (ledger / nlmon.ALERT_NAME).write_text("standing", encoding="utf-8")
    code, ledger = run(tmp_path)
    assert code != 0
    assert (ledger / nlmon.ALERT_NAME).read_text(encoding="utf-8") == "standing"


@pytest.mark.parametrize("flag", ["--write-baseline", "--write-baseline=1", "-WriteBaseline"])
def test_refuses_write_baseline(tmp_path: Path, flag: str) -> None:
    code, ledger = run(tmp_path, flag)
    assert code == nlmon.EXIT_REFUSED
    assert not ledger.exists()


def test_refuses_shared_ledger_without_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "shared"
    monkeypatch.setattr(nlmon, "SHARED_LEDGER", str(shared))
    mon = fake_monitor(tmp_path, 0)
    assert nlmon.main(["--monitor-script", str(mon)]) == nlmon.EXIT_REFUSED
    assert nlmon.main(["--ledger-dir", str(shared) + "\\.", "--monitor-script", str(mon)]) == nlmon.EXIT_REFUSED
    assert not shared.exists()
    # With --live the shared ledger is accepted (still a fake monitor + temp dir).
    assert nlmon.main(["--live", "--monitor-script", str(mon), "--python", sys.executable]) == 0
    assert (shared / nlmon.LEDGER_NAME).exists()


def test_bad_usage_refused(tmp_path: Path) -> None:
    assert nlmon.main(["--mode", "bogus", "--ledger-dir", str(tmp_path)]) == nlmon.EXIT_REFUSED
    assert nlmon.main(["--repeats", "0", "--ledger-dir", str(tmp_path)]) == nlmon.EXIT_REFUSED


def test_whatif_writes_nothing(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    assert nlmon.main(["--mode", "live", "--whatif-dry-run", "--ledger-dir", str(ledger)]) == 0
    assert not ledger.exists()


@pytest.mark.parametrize("rc,expected", [(0, 0), (1, 1), (2, 2), (-1, 2), (5, 2)])
def test_map_exit(rc: int, expected: int) -> None:
    assert nlmon.map_exit(rc) == expected
